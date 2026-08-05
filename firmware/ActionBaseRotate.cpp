// ActionBaseRotate.cpp
//
// Base rotation motion and MQTT command dispatch. The calibration routines that
// used to live here now sit in CalibrateRotation.cpp and are built on the
// primitives and shared state published via ActionBaseRotate_Internal.h.
#include "ActionBaseRotate.h"
#include "ActionBaseRotate_Internal.h"
#include "CalibrateRotation.h"
#include "BuddyMQTT.h"
#include <ESP32Servo.h>
#include <ArduinoJson.h>
#include <Arduino.h>
#include <Preferences.h>
#include <math.h>
#include <stdlib.h>
#include <strings.h>
#include <limits.h>

namespace {
  constexpr uint8_t SERVO_PIN = 21;
  constexpr uint8_t AS5600_OUT_PIN = 1;
  constexpr uint8_t TRUE_NORTH_PIN = 14;
  constexpr const char CONFIG_NAMESPACE[] = "config";
  constexpr const char KEY_ROTATION_OFFSET_DEGREES[] = "rot_off_deg";

  constexpr const char PREF_NAMESPACE[] = "rot";
  constexpr const char KEY_CALIBRATED[] = "calibrated";
  constexpr const char KEY_LEFT_COUNTS_PER_REV[] = "left_cpr";
  constexpr const char KEY_RIGHT_COUNTS_PER_REV[] = "right_cpr";
  constexpr const char KEY_LAST_BASE_COUNTS[] = "last_counts";
  constexpr const char KEY_LAST_KNOWN_VALID[] = "last_valid";
  constexpr const char KEY_PROFILE_CALIBRATED[] = "prof_cal";
  constexpr const char KEY_RESTING_VALUE[] = "rest";
  constexpr const char KEY_LEFT_FULL_REV_MS[] = "left_ms";
  constexpr const char KEY_RIGHT_FULL_REV_MS[] = "right_ms";
  constexpr const char KEY_ENCODER_SIGN[] = "enc_sign";
  constexpr const char KEY_VERY_SLOW_VALIDATED[] = "vs_valid";

  constexpr unsigned long MOVE_TIMEOUT_MS = 15000;
  // Minimum encoder travel that counts as progress within STUCK_TIMEOUT_MS.
  // Sized from measured hardware: real rotation covers several thousand counts
  // per second even at a weak near-deadband drive, while a base stalled on the
  // home switch managed 2 counts in 3 seconds. A per-sample threshold (the old
  // value of 3) could not tell those apart, because a stalled base still
  // jitters past 3 counts and reset the stall timer indefinitely.
  constexpr long ENCODER_PROGRESS_MIN_COUNTS = 100;
  constexpr unsigned long STUCK_TIMEOUT_MS = 1000;
  constexpr unsigned long POSITION_SAVE_INTERVAL_MS = 5000;
  constexpr unsigned long TRUE_NORTH_CORRECTION_INTERVAL_MS = 1000;

  const char* const KEY_LEFT_SPEED_ANGLES[BaseRotateInternal::SPEED_PROFILE_COUNT] = {
    "l_vslow", "l_slow", "l_reg", "l_fast", "l_sfast"
  };
  const char* const KEY_RIGHT_SPEED_ANGLES[BaseRotateInternal::SPEED_PROFILE_COUNT] = {
    "r_vslow", "r_slow", "r_reg", "r_fast", "r_sfast"
  };

  Servo baseServo;
  Preferences prefs;
  bool inited = false;

  int encoderRaw = 0;
  int lastEncoderRaw = 0;
  unsigned long lastTrueNorthCorrectionMs = 0;
  volatile uint32_t trueNorthHitCount = 0;
  bool usingEstimatedStepCounts = false;
}

using namespace BaseRotateInternal;

// ---- Shared state definitions (declared in ActionBaseRotate_Internal.h) -----
namespace BaseRotateInternal {
  const char* const SPEED_PROFILE_NAMES[SPEED_PROFILE_COUNT] = {
    "veryslow", "slow", "regular", "fast", "superfast"
  };
  const int SPEED_PROFILE_OFFSETS[SPEED_PROFILE_COUNT] = {
    8, 12, 18, 20, 30
  };

  int restingValue = 90;
  long basePositionCounts = 0;
  long encoderUnwrapped = 0;
  long leftCountsPerRev = 0;
  long rightCountsPerRev = 0;
  bool calibrated = false;
  bool profileCalibrated = false;
  bool positionTrusted = false;
  unsigned long leftFullRevMs = 0;
  unsigned long rightFullRevMs = 0;
  int leftSpeedAngles[SPEED_PROFILE_COUNT] = {0};
  int rightSpeedAngles[SPEED_PROFILE_COUNT] = {0};

  int calibrationPasses = 0;
  long calibrationLastDiffMs = 0;
  bool calibrationBalanced = false;
  const char* calibrationPhase = "idle";
  bool calibrationLastPulseAccepted = false;
  unsigned long calibrationLastPulseMs = 0;
  long calibrationLastPulseCounts = 0;
  long calibrationLastPulseMinCounts = 0;
  uint32_t calibrationIgnoredPulseCount = 0;
  bool verySlowValidationPassed = false;
}

namespace {
  void IRAM_ATTR onTrueNorthFalling() {
    ++trueNorthHitCount;
  }
}

// ---- Encoder / true north primitives ---------------------------------------

int BaseRotateInternal::readEncoderRaw() {
  encoderRaw = analogRead(AS5600_OUT_PIN);
  return encoderRaw;
}

bool BaseRotateInternal::readTrueNorthPin() {
  return digitalRead(TRUE_NORTH_PIN) == LOW;
}

bool BaseRotateInternal::isTrueNorthPressed() {
  return readTrueNorthPin();
}

uint32_t BaseRotateInternal::getTrueNorthHitCount() {
  noInterrupts();
  uint32_t count = trueNorthHitCount;
  interrupts();
  return count;
}

bool BaseRotateInternal::trueNorthHitDetected(uint32_t baselineCount) {
  return readTrueNorthPin() || getTrueNorthHitCount() != baselineCount;
}

namespace {
  long unwrapDelta(int currentRaw, int previousRaw) {
    int delta = currentRaw - previousRaw;
    if (delta > 2048) {
      delta -= 4096;
    } else if (delta < -2048) {
      delta += 4096;
    }
    return static_cast<long>(delta) * ENCODER_SIGN;
  }
}

void BaseRotateInternal::resetEncoderTracking() {
  encoderRaw = readEncoderRaw();
  lastEncoderRaw = encoderRaw;
  encoderUnwrapped = 0;
}

long BaseRotateInternal::updateEncoderTracking() {
  int currentRaw = readEncoderRaw();
  long delta = unwrapDelta(currentRaw, lastEncoderRaw);
  encoderUnwrapped += delta;
  basePositionCounts += delta;
  lastEncoderRaw = currentRaw;
  return delta;
}

// Progress is a *rate*: enough travel within a fixed window, not merely some
// travel at some point.
//
// The prior version asked only whether the encoder had moved
// ENCODER_STUCK_THRESHOLD (3) counts since the last check, re-anchoring and
// resetting the stall timer whenever it had. A base stalled against its home
// switch still creeps and jitters by a few counts, so it cleared that bar
// indefinitely, reset the timer forever, and the watchdog could never fire:
// a stall became an unbounded hang rather than an "encoder stuck" error.
//
// Measured against the real failure: healthy rotation runs ~4 counts/ms, while
// the stalled base managed 2 counts in 3 seconds. Demanding
// ENCODER_PROGRESS_MIN_COUNTS within STUCK_TIMEOUT_MS sits far below the
// former and far above the latter, so genuine motion (including the slowest
// "veryslow" speed) always passes and a creep is correctly judged stuck.
bool BaseRotateInternal::encoderIsStuck(long& windowStartCounts,
                                        unsigned long& windowStartMs) {
  if (labs(encoderUnwrapped - windowStartCounts) >= ENCODER_PROGRESS_MIN_COUNTS) {
    // Enough ground covered: this window counts as progress, open the next.
    windowStartCounts = encoderUnwrapped;
    windowStartMs = millis();
    return false;
  }

  // Too little travel so far. Only a fault once the full window has elapsed
  // without meeting the bar, so a brief slow patch is never punished.
  return millis() - windowStartMs > STUCK_TIMEOUT_MS;
}

// ---- Servo primitives -------------------------------------------------------

int BaseRotateInternal::clampServoAngle(int angle) {
  if (angle < 0) return 0;
  if (angle > 180) return 180;
  return angle;
}

void BaseRotateInternal::writeServo(int angle) {
  baseServo.write(angle);
}

void BaseRotateInternal::stopServo() {
  baseServo.write(restingValue);
  Serial.println("[Rotate] stop");
  // Long profile calibration is synchronous. Service/reconnect MQTT only
  // after motion stops so network delays can never extend a powered move.
  BuddyMQTT::maintain();
}

int BaseRotateInternal::speedIndex(BaseSpeed speed) {
  switch (speed) {
    case BASE_VERY_SLOW: return 0;
    case BASE_SLOW: return 1;
    case BASE_REGULAR: return 2;
    case BASE_FAST: return 3;
    case BASE_SUPERFAST: return 4;
  }
  return 2;
}

namespace {
  int fallbackDriveAngle(bool rotateLeft, BaseSpeed speed) {
    int offsetAngle = SPEED_PROFILE_OFFSETS[speedIndex(speed)];
    return clampServoAngle(rotateLeft ? (restingValue - offsetAngle) : (restingValue + offsetAngle));
  }

  int profileDriveAngle(bool rotateLeft, BaseSpeed speed) {
    int idx = speedIndex(speed);
    int driveAngle = rotateLeft ? leftSpeedAngles[idx] : rightSpeedAngles[idx];
    if (profileCalibrated && driveAngle >= 0 && driveAngle <= 180 && driveAngle != restingValue) {
      return driveAngle;
    }
    return fallbackDriveAngle(rotateLeft, speed);
  }
}

void BaseRotateInternal::initFixedSpeedProfileAngles() {
  for (size_t i = 0; i < SPEED_PROFILE_COUNT; ++i) {
    leftSpeedAngles[i] = clampServoAngle(restingValue - SPEED_PROFILE_OFFSETS[i]);
    rightSpeedAngles[i] = clampServoAngle(restingValue + SPEED_PROFILE_OFFSETS[i]);
  }
}

int BaseRotateInternal::calibrationLeftAngle() {
  return clampServoAngle(restingValue - CALIBRATION_DRIVE_OFFSET);
}

int BaseRotateInternal::calibrationRightAngle() {
  return clampServoAngle(restingValue + CALIBRATION_DRIVE_OFFSET);
}

void BaseRotateInternal::driveServo(bool rotateLeft, BaseSpeed speed) {
  int driveAngle = profileDriveAngle(rotateLeft, speed);
  baseServo.write(driveAngle);
  Serial.printf("[Rotate] drive dir=%s speed=%d angle=%d profile=%d\n",
                rotateLeft ? "LEFT" : "RIGHT", speed, driveAngle, profileCalibrated);
}

void BaseRotateInternal::driveServoAngle(bool rotateLeft, int driveAngle) {
  baseServo.write(clampServoAngle(driveAngle));
  Serial.printf("[Rotate] drive raw dir=%s angle=%d\n",
                rotateLeft ? "LEFT" : "RIGHT", clampServoAngle(driveAngle));
}

// ---- Persistence -------------------------------------------------------------

// Persists only the neutral servo angle. Calibration writes this as soon as the
// operator's value is applied, so the entered angle survives a run that fails
// partway rather than reverting to the previously saved one.
void BaseRotateInternal::saveNeutralValue() {
  prefs.begin(PREF_NAMESPACE, false);
  prefs.putInt(KEY_RESTING_VALUE, restingValue);
  prefs.end();
  Serial.printf("[Rotate] saved neutral=%d\n", restingValue);
}

void BaseRotateInternal::saveLastPosition() {
  prefs.begin(PREF_NAMESPACE, false);
  prefs.putLong(KEY_LAST_BASE_COUNTS, basePositionCounts);
  prefs.putBool(KEY_LAST_KNOWN_VALID, positionTrusted);
  prefs.putInt(KEY_ENCODER_SIGN, ENCODER_SIGN);
  prefs.putBool(KEY_VERY_SLOW_VALIDATED, verySlowValidationPassed);
  prefs.end();
}

void BaseRotateInternal::saveCalibration() {
  prefs.begin(PREF_NAMESPACE, false);
  prefs.putBool(KEY_CALIBRATED, calibrated);
  prefs.putLong(KEY_LEFT_COUNTS_PER_REV, leftCountsPerRev);
  prefs.putLong(KEY_RIGHT_COUNTS_PER_REV, rightCountsPerRev);
  prefs.putBool(KEY_PROFILE_CALIBRATED, profileCalibrated);
  prefs.putInt(KEY_RESTING_VALUE, restingValue);
  prefs.putULong(KEY_LEFT_FULL_REV_MS, leftFullRevMs);
  prefs.putULong(KEY_RIGHT_FULL_REV_MS, rightFullRevMs);
  prefs.putInt(KEY_ENCODER_SIGN, ENCODER_SIGN);
  for (size_t i = 0; i < SPEED_PROFILE_COUNT; ++i) {
    prefs.putInt(KEY_LEFT_SPEED_ANGLES[i], leftSpeedAngles[i]);
    prefs.putInt(KEY_RIGHT_SPEED_ANGLES[i], rightSpeedAngles[i]);
  }
  prefs.end();
}

// ---- Shared math -------------------------------------------------------------

long BaseRotateInternal::getAverageCountsPerRev() {
  if (leftCountsPerRev > 0 && rightCountsPerRev > 0) {
    return (leftCountsPerRev + rightCountsPerRev) / 2;
  }
  if (rightCountsPerRev > 0) return rightCountsPerRev;
  if (leftCountsPerRev > 0) return leftCountsPerRev;
  return 0;
}

long BaseRotateInternal::signedCalibrationDiffMs(unsigned long leftMs, unsigned long rightMs) {
  if (leftMs > static_cast<unsigned long>(LONG_MAX)) leftMs = LONG_MAX;
  if (rightMs > static_cast<unsigned long>(LONG_MAX)) rightMs = LONG_MAX;
  return static_cast<long>(leftMs) - static_cast<long>(rightMs);
}

namespace {

  void loadRotationPrefs() {
    initFixedSpeedProfileAngles();
    prefs.begin(PREF_NAMESPACE, true);
    calibrated = prefs.getBool(KEY_CALIBRATED, false);
    leftCountsPerRev = prefs.getLong(KEY_LEFT_COUNTS_PER_REV, 0);
    rightCountsPerRev = prefs.getLong(KEY_RIGHT_COUNTS_PER_REV, 0);
    basePositionCounts = prefs.getLong(KEY_LAST_BASE_COUNTS, 0);
    positionTrusted = prefs.getBool(KEY_LAST_KNOWN_VALID, false);
    int savedEncoderSign = prefs.getInt(KEY_ENCODER_SIGN, 0);
    profileCalibrated = prefs.getBool(KEY_PROFILE_CALIBRATED, false);
    verySlowValidationPassed = prefs.getBool(KEY_VERY_SLOW_VALIDATED, false);
    restingValue = prefs.getInt(KEY_RESTING_VALUE, restingValue);
    leftFullRevMs = prefs.getULong(KEY_LEFT_FULL_REV_MS, 0);
    rightFullRevMs = prefs.getULong(KEY_RIGHT_FULL_REV_MS, 0);
    for (size_t i = 0; i < SPEED_PROFILE_COUNT; ++i) {
      leftSpeedAngles[i] = prefs.getInt(KEY_LEFT_SPEED_ANGLES[i], leftSpeedAngles[i]);
      rightSpeedAngles[i] = prefs.getInt(KEY_RIGHT_SPEED_ANGLES[i], rightSpeedAngles[i]);
    }
    prefs.end();

    if (savedEncoderSign != ENCODER_SIGN) {
      positionTrusted = false;
      basePositionCounts = 0;
      Serial.printf("[Rotate] saved position sign mismatch saved=%d current=%d; position marked untrusted\n",
                    savedEncoderSign, ENCODER_SIGN);
    }

    // A stored position is only meaningful alongside a counts-per-rev to
    // interpret it with. These were persisted independently, so a robot could
    // boot claiming a trusted position while calibrated=false and both counts
    // were zero -- every angle derived from it came out of a divide by an
    // unknown circle size.
    if (!calibrated || getAverageCountsPerRev() <= 0) {
      if (positionTrusted) {
        Serial.println("[Rotate] no usable counts per revolution; stored position marked untrusted");
      }
      positionTrusted = false;
      basePositionCounts = 0;
    }

    if (isTrueNorthPressed()) {
      basePositionCounts = 0;
      positionTrusted = true;
      saveLastPosition();
      Serial.println("[Rotate] true north pressed at boot: position set to 0");
    }
  }

  void initAll() {
    if (inited) return;

    baseServo.attach(SERVO_PIN);
    baseServo.write(restingValue);
    pinMode(TRUE_NORTH_PIN, INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(TRUE_NORTH_PIN), onTrueNorthFalling, FALLING);
    analogReadResolution(12);
    resetEncoderTracking();
    loadRotationPrefs();
    baseServo.write(restingValue);

    inited = true;
    Serial.printf("[Rotate] initialized servo=%u as5600=%u true_north=%u resting=%d raw=%d calibrated=%d profile=%d trusted=%d counts=%ld left_cpr=%ld right_cpr=%ld sign=%d\n",
                  SERVO_PIN, AS5600_OUT_PIN, TRUE_NORTH_PIN, restingValue, encoderRaw,
                  calibrated, profileCalibrated, positionTrusted, basePositionCounts, leftCountsPerRev,
                  rightCountsPerRev, ENCODER_SIGN);
  }

  float getBaseAngleDegrees() {
    long cpr = getAverageCountsPerRev();
    if (cpr <= 0) return 0.0f;

    float angle = fmod((float(basePositionCounts) / float(cpr)) * 360.0f, 360.0f);
    if (angle < 0) angle += 360.0f;
    return angle;
  }

  float normalizeDegrees(float angle) {
    float normalized = fmod(angle, 360.0f);
    if (normalized < 0.0f) normalized += 360.0f;
    return normalized;
  }

  float loadStencilRotationOffsetDegrees() {
    Preferences configPrefs;
    float offset = 0.0f;
    if (configPrefs.begin(CONFIG_NAMESPACE, true)) {
      offset = configPrefs.getFloat(KEY_ROTATION_OFFSET_DEGREES, 0.0f);
      configPrefs.end();
    }
    return offset;
  }

  long degreesToCounts(float degrees, bool rotateLeft) {
    long cpr = rotateLeft && leftCountsPerRev > 0 ? leftCountsPerRev : rightCountsPerRev;
    if (cpr <= 0) cpr = getAverageCountsPerRev();
    if (cpr <= 0) return 0;
    return labs(lround((degrees / 360.0f) * float(cpr)));
  }

  long countsPerStep(long countsPerRev) {
    if (countsPerRev <= 0) return 0;
    return lround(float(countsPerRev) / float(BASE_STEPS_PER_REV));
  }

  long stepsToCounts(long steps, bool rotateLeft) {
    long cpr = rotateLeft && leftCountsPerRev > 0 ? leftCountsPerRev : rightCountsPerRev;
    if (cpr <= 0) cpr = getAverageCountsPerRev();
    if (cpr <= 0) return 0;
    return labs(lround((float(steps) / float(BASE_STEPS_PER_REV)) * float(cpr)));
  }

  long estimatedStepsToCounts(long steps) {
    return labs(lround((float(steps) / float(BASE_STEPS_PER_REV)) * float(ESTIMATED_COUNTS_PER_BASE_REV)));
  }

  long stepsToCountsWithEstimate(long steps, bool rotateLeft, bool& usedEstimate) {
    usedEstimate = false;
    long counts = stepsToCounts(steps, rotateLeft);
    if (counts > 0) return counts;

    usedEstimate = true;
    return estimatedStepsToCounts(steps);
  }

  // ---- Command parsing -------------------------------------------------------

  BaseSpeed parseSpeed(const char* s) {
    if (!s) return BASE_VERY_SLOW;
    if      (strcasecmp(s, "veryslow") == 0) return BASE_VERY_SLOW;
    else if (strcasecmp(s, "slow") == 0) return BASE_SLOW;
    else if (strcasecmp(s, "regular") == 0) return BASE_REGULAR;
    else if (strcasecmp(s, "fast") == 0) return BASE_FAST;
    else if (strcasecmp(s, "superfast") == 0) return BASE_SUPERFAST;
    return BASE_VERY_SLOW;
  }

  BaseSpeed stepMovementSpeed(JsonVariantConst speedValue, BaseSpeed parsedSpeed) {
    if (speedValue.isNull()) return BASE_SLOW;
    return parsedSpeed;
  }

  bool parseDirection(const char* dir, bool& rotateLeft) {
    if (!dir) return false;
    if (strcasecmp(dir, "LEFT") == 0) {
      rotateLeft = true;
      return true;
    }
    if (strcasecmp(dir, "RIGHT") == 0) {
      rotateLeft = false;
      return true;
    }
    return false;
  }

  bool extractFloat(JsonVariantConst var, float& out) {
    if (var.is<float>()) {
      out = var.as<float>();
      return true;
    }
    if (var.is<int>()) {
      out = static_cast<float>(var.as<int>());
      return true;
    }
    if (var.is<long>()) {
      out = static_cast<float>(var.as<long>());
      return true;
    }
    if (var.is<const char*>()) {
      const char* s = var.as<const char*>();
      if (s && *s) {
        out = static_cast<float>(atof(s));
        return true;
      }
    }
    return false;
  }

  bool extractLong(JsonVariantConst var, long& out) {
    if (var.is<int>()) {
      out = var.as<int>();
      return true;
    }
    if (var.is<long>()) {
      out = var.as<long>();
      return true;
    }
    if (var.is<const char*>()) {
      const char* s = var.as<const char*>();
      if (s && *s) {
        char* end = nullptr;
        long value = strtol(s, &end, 10);
        if (end && *end == '\0') {
          out = value;
          return true;
        }
      }
    }
    return false;
  }

  bool parseNeutralOverride(JsonVariantConst var, long& neutralOverride, const char*& error) {
    neutralOverride = NO_NEUTRAL_OVERRIDE;
    if (var.isNull()) return true;

    long parsed = 0;
    if (!extractLong(var, parsed) || parsed < 0 || parsed > 180) {
      error = "invalid neutralServoAngle";
      return false;
    }

    neutralOverride = parsed;
    return true;
  }

  // ---- Motion ----------------------------------------------------------------

  void correctIfAtTrueNorth() {
    if (!isTrueNorthPressed()) return;
    unsigned long now = millis();
    if (now - lastTrueNorthCorrectionMs < TRUE_NORTH_CORRECTION_INTERVAL_MS) return;

    basePositionCounts = 0;
    positionTrusted = true;
    lastTrueNorthCorrectionMs = now;
    saveLastPosition();
    Serial.println("[Rotate] true north detected: position corrected to 0");
  }

  bool moveByCounts(long targetCounts, bool rotateLeft, BaseSpeed speed, const char*& error) {
    if (targetCounts <= TARGET_TOLERANCE_COUNTS) {
      Serial.printf("[Rotate] target %ld within tolerance\n", targetCounts);
      return true;
    }

    long startCounts = encoderUnwrapped;
    long lastObservedCounts = encoderUnwrapped;
    unsigned long startMs = millis();
    unsigned long lastMovementMs = startMs;
    unsigned long lastSaveMs = startMs;

    driveServo(rotateLeft, speed);
    while (labs(encoderUnwrapped - startCounts) + TARGET_TOLERANCE_COUNTS < targetCounts) {
      updateEncoderTracking();
      correctIfAtTrueNorth();

      if (millis() - startMs > MOVE_TIMEOUT_MS) {
        error = "move timeout";
        Serial.println("[Rotate] move timeout");
        stopServo();
        saveLastPosition();
        return false;
      }

      if (encoderIsStuck(lastObservedCounts, lastMovementMs)) {
        error = "encoder stuck";
        Serial.println("[Rotate] encoder stuck while moving");
        stopServo();
        saveLastPosition();
        return false;
      }

      if (millis() - lastSaveMs > POSITION_SAVE_INTERVAL_MS) {
        saveLastPosition();
        lastSaveMs = millis();
      }

      delay(5);
    }

    stopServo();
    saveLastPosition();
    return true;
  }

  bool moveToAngleTarget(float targetAngle, BaseSpeed speed, bool applyStoredOffset, const char*& error) {
    if (!calibrated || getAverageCountsPerRev() <= 0) {
      error = "base rotation is not calibrated";
      return false;
    }
    if (!positionTrusted) {
      error = "base position is not trusted";
      return false;
    }

    updateEncoderTracking();
    float currentAngle = getBaseAngleDegrees();
    float storedOffset = applyStoredOffset ? loadStencilRotationOffsetDegrees() : 0.0f;
    float adjustedTarget = normalizeDegrees(targetAngle + storedOffset);
    float rightDelta = fmod((adjustedTarget - currentAngle + 360.0f), 360.0f);
    float leftDelta = fmod((currentAngle - adjustedTarget + 360.0f), 360.0f);
    bool rotateLeft = leftDelta < rightDelta;
    float moveDegrees = rotateLeft ? leftDelta : rightDelta;
    Serial.printf("[Rotate] ANGLE current=%.2f target=%.2f offset=%.2f adjusted=%.2f dir=%s degrees=%.2f\n",
                  currentAngle, targetAngle, storedOffset, adjustedTarget,
                  rotateLeft ? "LEFT" : "RIGHT", moveDegrees);
    return moveByCounts(degreesToCounts(moveDegrees, rotateLeft), rotateLeft, speed, error);
  }
}

bool BaseRotateInternal::rotateUntilTrueNorth(bool rotateLeft, BaseSpeed speed,
                                              unsigned long timeoutMs, const char*& error) {
  long lastObservedCounts = encoderUnwrapped;
  unsigned long startMs = millis();
  unsigned long lastMovementMs = startMs;
  uint32_t startHits = getTrueNorthHitCount();

  driveServo(rotateLeft, speed);
  while (!trueNorthHitDetected(startHits)) {
    updateEncoderTracking();

    if (millis() - startMs > timeoutMs) {
      error = "true north timeout";
      Serial.println("[Rotate] true north timeout");
      stopServo();
      saveLastPosition();
      return false;
    }

    if (encoderIsStuck(lastObservedCounts, lastMovementMs)) {
      error = "encoder stuck";
      Serial.println("[Rotate] encoder stuck while homing");
      stopServo();
      saveLastPosition();
      return false;
    }

    delay(TRUE_NORTH_POLL_DELAY_MS);
  }

  stopServo();
  basePositionCounts = 0;
  positionTrusted = true;
  saveLastPosition();
  Serial.println("[Rotate] true north reached: position set to 0");
  return true;
}

namespace {
  void buildStatusJson(String& out, const char* error = nullptr) {
    StaticJsonDocument<2304> doc;
    doc["calibrated"] = calibrated;
    doc["profileCalibrated"] = profileCalibrated;
    doc["positionTrusted"] = positionTrusted;
    doc["baseAngleDegrees"] = getBaseAngleDegrees();
    doc["basePositionCounts"] = basePositionCounts;
    doc["leftCountsPerRev"] = leftCountsPerRev;
    doc["rightCountsPerRev"] = rightCountsPerRev;
    doc["driveGearTeeth"] = DRIVE_GEAR_TEETH;
    doc["baseGearTeeth"] = BASE_GEAR_TEETH;
    doc["baseStepsPerRev"] = BASE_STEPS_PER_REV;
    doc["encoderSign"] = ENCODER_SIGN;
    doc["leftCountsPerStep"] = countsPerStep(leftCountsPerRev);
    doc["rightCountsPerStep"] = countsPerStep(rightCountsPerRev);
    doc["averageCountsPerStep"] = countsPerStep(getAverageCountsPerRev());
    doc["estimatedCountsPerRev"] = ESTIMATED_COUNTS_PER_BASE_REV;
    doc["estimatedCountsPerStep"] = countsPerStep(ESTIMATED_COUNTS_PER_BASE_REV);
    doc["usingEstimatedStepCounts"] = usingEstimatedStepCounts;
    doc["rotationOffsetDegrees"] = loadStencilRotationOffsetDegrees();
    doc["neutralServoAngle"] = restingValue;
    doc["calibrationDriveOffset"] = CALIBRATION_DRIVE_OFFSET;
    doc["calibrationLeftAngle"] = calibrationLeftAngle();
    doc["calibrationRightAngle"] = calibrationRightAngle();
    doc["calibrationPasses"] = calibrationPasses;
    doc["calibrationLastDiffMs"] = calibrationLastDiffMs;
    doc["calibrationBalanced"] = calibrationBalanced;
    doc["calibrationPhase"] = calibrationPhase;
    doc["calibrationLastPulseAccepted"] = calibrationLastPulseAccepted;
    doc["calibrationLastPulseMs"] = calibrationLastPulseMs;
    doc["calibrationLastPulseCounts"] = calibrationLastPulseCounts;
    doc["calibrationLastPulseMinCounts"] = calibrationLastPulseMinCounts;
    doc["calibrationIgnoredPulseCount"] = calibrationIgnoredPulseCount;
    doc["leftFullRevMs"] = leftFullRevMs;
    doc["rightFullRevMs"] = rightFullRevMs;
    // Calibration measures counts-per-rev only; veryslow angles are fixed
    // offsets from neutral, so there are no separate veryslow measurements to
    // report. "validated" is kept because clients read it, and now simply
    // mirrors whether usable counts exist.
    doc["verySlowValidated"] = verySlowValidationPassed;
    doc["trueNorthPressed"] = readTrueNorthPin();
    doc["trueNorthPinLevel"] = digitalRead(TRUE_NORTH_PIN);
    doc["trueNorthHitCount"] = getTrueNorthHitCount();
    doc["rawEncoder"] = readEncoderRaw();
    JsonObject speedProfile = doc.createNestedObject("speedProfile");
    for (size_t i = 0; i < SPEED_PROFILE_COUNT; ++i) {
      JsonObject speed = speedProfile.createNestedObject(SPEED_PROFILE_NAMES[i]);
      speed["left"] = leftSpeedAngles[i];
      speed["right"] = rightSpeedAngles[i];
    }
    if (error && error[0]) doc["error"] = error;
    serializeJson(doc, out);
  }
}

// ---- Public API --------------------------------------------------------------

bool ActionBaseRotate::run(const String& message, String& statusJson) {
  initAll();
  statusJson = "";

  StaticJsonDocument<384> doc;
  DeserializationError jsonError = deserializeJson(doc, message);
  if (jsonError != DeserializationError::Ok) {
    const char* error = "invalid JSON";
    Serial.print("[Rotate] JSON parse error: ");
    Serial.println(message);
    stopServo();
    buildStatusJson(statusJson, error);
    return false;
  }

  const char* ctl = doc["controlType"].as<const char*>();
  BaseSpeed speed = parseSpeed(doc["speed"].as<const char*>());
  const char* error = nullptr;
  bool ok = false;

  if (!ctl) {
    error = "missing controlType";
    Serial.println("[Rotate] missing controlType");
  } else if (strcasecmp(ctl, "STATUS") == 0) {
    updateEncoderTracking();
    correctIfAtTrueNorth();
    ok = true;
  } else if (strcasecmp(ctl, "HOME") == 0) {
    bool rotateLeft = false;
    if (!parseDirection(doc["direction"].as<const char*>(), rotateLeft)) {
      error = "invalid direction";
    } else {
      ok = rotateUntilTrueNorth(rotateLeft, speed, HOME_TIMEOUT_MS, error);
    }
  } else if (strcasecmp(ctl, "CALIBRATE") == 0) {
    bool rotateLeft = false;
    if (!parseDirection(doc["direction"].as<const char*>(), rotateLeft)) {
      error = "invalid direction";
    } else {
      ok = CalibrateRotation::calibrateBase(rotateLeft, speed, error);
    }
  } else if (strcasecmp(ctl, "CALIBRATE_PROFILE") == 0 ||
             strcasecmp(ctl, "CALIBRATE_BOTH") == 0) {
    long neutralOverride = NO_NEUTRAL_OVERRIDE;
    if (parseNeutralOverride(doc["neutralServoAngle"], neutralOverride, error)) {
      ok = CalibrateRotation::calibrateRotationProfile(neutralOverride, error);
    }
  } else if (strcasecmp(ctl, "ENCODER") == 0 || strcasecmp(ctl, "STEPS") == 0) {
    bool rotateLeft = false;
    long value = 0;
    usingEstimatedStepCounts = false;
    if (!parseDirection(doc["direction"].as<const char*>(), rotateLeft)) {
      error = "invalid direction";
    } else if ((!extractLong(doc["value"], value) && !extractLong(doc["steps"], value)) || value <= 0) {
      error = "invalid value";
    } else {
      BaseSpeed moveSpeed = stepMovementSpeed(doc["speed"], speed);
      long targetCounts = stepsToCountsWithEstimate(value, rotateLeft, usingEstimatedStepCounts);
      Serial.printf("[Rotate] %s steps=%ld counts=%ld estimated=%d calibrated=%d speed=%d omittedSpeed=%d\n",
                    ctl, value, targetCounts, usingEstimatedStepCounts, calibrated, moveSpeed,
                    doc["speed"].isNull());
      ok = moveByCounts(targetCounts, rotateLeft, moveSpeed, error);
    }
  } else if (strcasecmp(ctl, "DEGREES") == 0) {
    bool rotateLeft = false;
    float value = 0.0f;
    if (!calibrated || getAverageCountsPerRev() <= 0) {
      error = "base rotation is not calibrated";
    } else if (!parseDirection(doc["direction"].as<const char*>(), rotateLeft)) {
      error = "invalid direction";
    } else if (!extractFloat(doc["value"], value) || value <= 0.0f) {
      error = "invalid value";
    } else {
      ok = moveByCounts(degreesToCounts(value, rotateLeft), rotateLeft, speed, error);
    }
  } else if (strcasecmp(ctl, "ANGLE") == 0) {
    float targetAngle = 0.0f;
    if (!extractFloat(doc["value"], targetAngle) || targetAngle < 0.0f || targetAngle >= 360.0f) {
      error = "invalid value";
    } else {
      ok = moveToAngleTarget(targetAngle, speed, true, error);
    }
  } else {
    error = "unknown controlType";
    Serial.print("[Rotate] unknown controlType: ");
    Serial.println(ctl);
  }

  if (!ok && error) {
    stopServo();
    Serial.print("[Rotate] failed: ");
    Serial.println(error);
  }

  buildStatusJson(statusJson, ok ? nullptr : (error ? error : "base rotation failed"));
  return ok;
}

bool ActionBaseRotate::calibrateProfile(String& statusJson, long neutralServoAngle) {
  initAll();
  statusJson = "";

  const char* error = nullptr;
  bool ok = CalibrateRotation::calibrateRotationProfile(neutralServoAngle, error);
  if (!ok && error) {
    stopServo();
    Serial.print("[Rotate] failed: ");
    Serial.println(error);
  }

  buildStatusJson(statusJson, ok ? nullptr : (error ? error : "base rotation failed"));
  return ok;
}

bool ActionBaseRotate::moveToAbsoluteAngle(float targetAngleDegrees,
                                           const char* speedLabel,
                                           bool applyStoredOffset,
                                           String& statusJson) {
  initAll();
  statusJson = "";

  const char* error = nullptr;
  BaseSpeed speed = parseSpeed(speedLabel);
  bool ok = moveToAngleTarget(targetAngleDegrees, speed, applyStoredOffset, error);
  if (!ok && error) {
    stopServo();
    Serial.print("[Rotate] failed: ");
    Serial.println(error);
  }

  buildStatusJson(statusJson, ok ? nullptr : (error ? error : "base rotation failed"));
  return ok;
}

bool ActionBaseRotate::homeToTrueNorth(const char* directionLabel,
                                       const char* speedLabel,
                                       String& statusJson) {
  initAll();
  statusJson = "";

  const char* error = nullptr;
  bool rotateLeft = false;
  bool ok = false;
  if (!parseDirection(directionLabel, rotateLeft)) {
    error = "invalid direction";
  } else {
    BaseSpeed speed = parseSpeed(speedLabel);
    ok = rotateUntilTrueNorth(rotateLeft, speed, HOME_TIMEOUT_MS, error);
  }

  if (!ok && error) {
    stopServo();
    Serial.print("[Rotate] failed: ");
    Serial.println(error);
  }

  buildStatusJson(statusJson, ok ? nullptr : (error ? error : "base rotation failed"));
  return ok;
}

bool ActionBaseRotate::isAbsoluteAngleReady(String& reason) {
  initAll();
  if (!calibrated || getAverageCountsPerRev() <= 0) {
    reason = "base rotation is not calibrated";
    return false;
  }
  if (!positionTrusted) {
    reason = "base position is not trusted";
    return false;
  }
  reason = "";
  return true;
}
