// ActionBaseRotate.cpp
#include "ActionBaseRotate.h"
#include "Network_MQTT.h"
#include "Network_Wifi.h"
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
  constexpr int ENCODER_SIGN = -1;
  constexpr int AS5600_COUNTS_PER_REV = 4096;
  constexpr int DRIVE_GEAR_TEETH = 18;
  constexpr int BASE_GEAR_TEETH = 108;
  constexpr int BASE_STEPS_PER_REV = BASE_GEAR_TEETH * 2;
  constexpr long ESTIMATED_COUNTS_PER_BASE_REV =
      static_cast<long>(AS5600_COUNTS_PER_REV) * BASE_GEAR_TEETH / DRIVE_GEAR_TEETH;
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

  constexpr unsigned long MOVE_TIMEOUT_MS = 15000;
  constexpr unsigned long HOME_TIMEOUT_MS = 20000;
  constexpr unsigned long CALIBRATION_TIMEOUT_MS = 45000;
  constexpr unsigned long PROFILE_CALIBRATION_TIMEOUT_MS = 180000;
  constexpr long ENCODER_STUCK_THRESHOLD = 3;
  constexpr unsigned long STUCK_TIMEOUT_MS = 1000;
  constexpr long TARGET_TOLERANCE_COUNTS = 10;
  constexpr unsigned long POSITION_SAVE_INTERVAL_MS = 5000;
  constexpr unsigned long TRUE_NORTH_CORRECTION_INTERVAL_MS = 1000;
  constexpr unsigned long TRUE_NORTH_POLL_DELAY_MS = 1;
  constexpr unsigned long TRUE_NORTH_BOUNCE_IGNORE_MS = 75;
  constexpr long TRUE_NORTH_MIN_COUNTS_AFTER_RELEASE = 50;
  constexpr long CALIBRATION_UNKNOWN_MIN_TRAVEL_COUNTS = 4096;
  constexpr int CALIBRATION_MIN_TRAVEL_PERCENT = 50;
  constexpr int CALIBRATION_DRIVE_OFFSET = 10;
  constexpr int CALIBRATION_NEUTRAL_MIN = 70;
  constexpr int CALIBRATION_NEUTRAL_MAX = 110;
  constexpr int CALIBRATION_MIN_PASSES = 2;
  constexpr int CALIBRATION_MAX_PASSES = 8;
  constexpr unsigned long CALIBRATION_BALANCE_TOLERANCE_MS = 2000;
  constexpr long NO_NEUTRAL_OVERRIDE = -1;
  constexpr size_t SPEED_PROFILE_COUNT = 5;

  const char* const SPEED_PROFILE_NAMES[SPEED_PROFILE_COUNT] = {
    "veryslow", "slow", "regular", "fast", "superfast"
  };
  const int SPEED_PROFILE_OFFSETS[SPEED_PROFILE_COUNT] = {
    8, 12, 18, 20, 30
  };
  const char* const KEY_LEFT_SPEED_ANGLES[SPEED_PROFILE_COUNT] = {
    "l_vslow", "l_slow", "l_reg", "l_fast", "l_sfast"
  };
  const char* const KEY_RIGHT_SPEED_ANGLES[SPEED_PROFILE_COUNT] = {
    "r_vslow", "r_slow", "r_reg", "r_fast", "r_sfast"
  };

  static Servo baseServo;
  static Preferences prefs;
  static bool inited = false;

  int restingValue = 90;
  enum BaseSpeed {
    BASE_VERY_SLOW = 5,
    BASE_SLOW = 10,
    BASE_REGULAR = 20,
    BASE_FAST = 30,
    BASE_SUPERFAST = 40
  };

  int encoderRaw = 0;
  int lastEncoderRaw = 0;
  long encoderUnwrapped = 0;
  long basePositionCounts = 0;
  long leftCountsPerRev = 0;
  long rightCountsPerRev = 0;
  bool calibrated = false;
  bool profileCalibrated = false;
  bool positionTrusted = false;
  unsigned long leftFullRevMs = 0;
  unsigned long rightFullRevMs = 0;
  int leftSpeedAngles[SPEED_PROFILE_COUNT] = {0};
  int rightSpeedAngles[SPEED_PROFILE_COUNT] = {0};
  unsigned long lastTrueNorthCorrectionMs = 0;
  volatile uint32_t trueNorthHitCount = 0;
  int calibrationPasses = 0;
  long calibrationLastDiffMs = 0;
  bool calibrationBalanced = false;
  const char* calibrationPhase = "idle";
  bool calibrationLastPulseAccepted = false;
  unsigned long calibrationLastPulseMs = 0;
  long calibrationLastPulseCounts = 0;
  long calibrationLastPulseMinCounts = 0;
  uint32_t calibrationIgnoredPulseCount = 0;
  bool usingEstimatedStepCounts = false;

  struct RotationProfileBackup {
    int resting;
    long leftCpr;
    long rightCpr;
    bool calibratedFlag;
    bool profileCalibratedFlag;
    unsigned long leftMs;
    unsigned long rightMs;
    int leftAngles[SPEED_PROFILE_COUNT];
    int rightAngles[SPEED_PROFILE_COUNT];
  };

  void saveLastPosition();

  void IRAM_ATTR onTrueNorthFalling() {
    ++trueNorthHitCount;
  }

  int readEncoderRaw() {
    encoderRaw = analogRead(AS5600_OUT_PIN);
    return encoderRaw;
  }

  bool readTrueNorthPin() {
    return digitalRead(TRUE_NORTH_PIN) == LOW;
  }

  bool isTrueNorthPressed() {
    return readTrueNorthPin();
  }

  uint32_t getTrueNorthHitCount() {
    noInterrupts();
    uint32_t count = trueNorthHitCount;
    interrupts();
    return count;
  }

  bool trueNorthHitDetected(uint32_t baselineCount) {
    return readTrueNorthPin() || getTrueNorthHitCount() != baselineCount;
  }

  long unwrapDelta(int currentRaw, int previousRaw) {
    int delta = currentRaw - previousRaw;
    if (delta > 2048) {
      delta -= 4096;
    } else if (delta < -2048) {
      delta += 4096;
    }
    return static_cast<long>(delta) * ENCODER_SIGN;
  }

  void resetEncoderTracking() {
    encoderRaw = readEncoderRaw();
    lastEncoderRaw = encoderRaw;
    encoderUnwrapped = 0;
  }

  // Every blocking wait loop below used to spin on nothing but delay(1) for
  // up to PROFILE_CALIBRATION_TIMEOUT_MS (3 minutes), never giving WiFi/MQTT
  // a chance to run. loop() in firmware.ino is single-threaded — it only
  // pumps BuddyWifi::maintain()/BuddyMQTT::maintain() between top-level MQTT
  // messages, so a single long-running action (rotate, home, calibrate)
  // silently starved the connection: no mqttClient.loop() meant no keepalive
  // ping, so the broker/peer would drop the connection and heartbeats would
  // stop, even though the motor itself kept running. Throttled to roughly
  // once every 20ms so it doesn't meaningfully slow the encoder polling.
  void maintainConnectionDuringWait() {
    static unsigned long lastMaintainMs = 0;
    unsigned long now = millis();
    if (now - lastMaintainMs < 20) return;
    lastMaintainMs = now;

    BuddyWifi::maintain();
    BuddyMQTT::maintain();
  }

  long updateEncoderTracking() {
    int currentRaw = readEncoderRaw();
    long delta = unwrapDelta(currentRaw, lastEncoderRaw);
    encoderUnwrapped += delta;
    basePositionCounts += delta;
    lastEncoderRaw = currentRaw;
    return delta;
  }

  void stopServo() {
    baseServo.write(restingValue);
    Serial.println("[Rotate] stop");
  }

  int clampServoAngle(int angle) {
    if (angle < 0) return 0;
    if (angle > 180) return 180;
    return angle;
  }

  int calibrationLeftAngle() {
    return clampServoAngle(restingValue - CALIBRATION_DRIVE_OFFSET);
  }

  int calibrationRightAngle() {
    return clampServoAngle(restingValue + CALIBRATION_DRIVE_OFFSET);
  }

  int speedIndex(BaseSpeed speed) {
    switch (speed) {
      case BASE_VERY_SLOW: return 0;
      case BASE_SLOW: return 1;
      case BASE_REGULAR: return 2;
      case BASE_FAST: return 3;
      case BASE_SUPERFAST: return 4;
    }
    return 2;
  }

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

  void initFixedSpeedProfileAngles() {
    for (size_t i = 0; i < SPEED_PROFILE_COUNT; ++i) {
      leftSpeedAngles[i] = clampServoAngle(restingValue - SPEED_PROFILE_OFFSETS[i]);
      rightSpeedAngles[i] = clampServoAngle(restingValue + SPEED_PROFILE_OFFSETS[i]);
    }
  }

  RotationProfileBackup makeProfileBackup() {
    RotationProfileBackup backup;
    backup.resting = restingValue;
    backup.leftCpr = leftCountsPerRev;
    backup.rightCpr = rightCountsPerRev;
    backup.calibratedFlag = calibrated;
    backup.profileCalibratedFlag = profileCalibrated;
    backup.leftMs = leftFullRevMs;
    backup.rightMs = rightFullRevMs;
    for (size_t i = 0; i < SPEED_PROFILE_COUNT; ++i) {
      backup.leftAngles[i] = leftSpeedAngles[i];
      backup.rightAngles[i] = rightSpeedAngles[i];
    }
    return backup;
  }

  void restoreProfileBackup(const RotationProfileBackup& backup) {
    restingValue = backup.resting;
    leftCountsPerRev = backup.leftCpr;
    rightCountsPerRev = backup.rightCpr;
    calibrated = backup.calibratedFlag;
    profileCalibrated = backup.profileCalibratedFlag;
    leftFullRevMs = backup.leftMs;
    rightFullRevMs = backup.rightMs;
    for (size_t i = 0; i < SPEED_PROFILE_COUNT; ++i) {
      leftSpeedAngles[i] = backup.leftAngles[i];
      rightSpeedAngles[i] = backup.rightAngles[i];
    }
    positionTrusted = false;
    baseServo.write(restingValue);
    saveLastPosition();
  }

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

  void driveServo(bool rotateLeft, BaseSpeed speed) {
    int driveAngle = profileDriveAngle(rotateLeft, speed);
    baseServo.write(driveAngle);
    Serial.printf("[Rotate] drive dir=%s speed=%d angle=%d profile=%d\n",
                  rotateLeft ? "LEFT" : "RIGHT", speed, driveAngle, profileCalibrated);
  }

  void driveServoAngle(bool rotateLeft, int driveAngle) {
    baseServo.write(clampServoAngle(driveAngle));
    Serial.printf("[Rotate] drive raw dir=%s angle=%d\n",
                  rotateLeft ? "LEFT" : "RIGHT", clampServoAngle(driveAngle));
  }

  void saveLastPosition() {
    prefs.begin(PREF_NAMESPACE, false);
    prefs.putLong(KEY_LAST_BASE_COUNTS, basePositionCounts);
    prefs.putBool(KEY_LAST_KNOWN_VALID, positionTrusted);
    prefs.putInt(KEY_ENCODER_SIGN, ENCODER_SIGN);
    prefs.end();
  }

  void saveCalibration() {
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
    restingValue = prefs.getInt(KEY_RESTING_VALUE, restingValue);
    leftFullRevMs = prefs.getULong(KEY_LEFT_FULL_REV_MS, 0);
    rightFullRevMs = prefs.getULong(KEY_RIGHT_FULL_REV_MS, 0);
    for (size_t i = 0; i < SPEED_PROFILE_COUNT; ++i) {
      leftSpeedAngles[i] = prefs.getInt(KEY_LEFT_SPEED_ANGLES[i], leftSpeedAngles[i]);
      rightSpeedAngles[i] = prefs.getInt(KEY_RIGHT_SPEED_ANGLES[i], rightSpeedAngles[i]);
    }
    prefs.end();
    initFixedSpeedProfileAngles();

    if (savedEncoderSign != ENCODER_SIGN) {
      positionTrusted = false;
      basePositionCounts = 0;
      Serial.printf("[Rotate] saved position sign mismatch saved=%d current=%d; position marked untrusted\n",
                    savedEncoderSign, ENCODER_SIGN);
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

  long getAverageCountsPerRev() {
    if (leftCountsPerRev > 0 && rightCountsPerRev > 0) {
      return (leftCountsPerRev + rightCountsPerRev) / 2;
    }
    if (rightCountsPerRev > 0) return rightCountsPerRev;
    if (leftCountsPerRev > 0) return leftCountsPerRev;
    return 0;
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

  bool encoderIsStuck(long& lastObservedEncoderCounts, unsigned long& lastMovementMs) {
    long moved = labs(encoderUnwrapped - lastObservedEncoderCounts);
    if (moved >= ENCODER_STUCK_THRESHOLD) {
      lastObservedEncoderCounts = encoderUnwrapped;
      lastMovementMs = millis();
      return false;
    }
    return millis() - lastMovementMs > STUCK_TIMEOUT_MS;
  }

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
      maintainConnectionDuringWait();

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

  bool rotateUntilTrueNorth(bool rotateLeft, BaseSpeed speed, unsigned long timeoutMs, const char*& error) {
    long lastObservedCounts = encoderUnwrapped;
    unsigned long startMs = millis();
    unsigned long lastMovementMs = startMs;
    uint32_t startHits = getTrueNorthHitCount();

    driveServo(rotateLeft, speed);
    while (!trueNorthHitDetected(startHits)) {
      updateEncoderTracking();
      maintainConnectionDuringWait();

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

  bool seekTrueNorthRaw(bool rotateLeft, int driveAngle, unsigned long timeoutMs, const char*& error) {
    long lastObservedCounts = encoderUnwrapped;
    unsigned long startMs = millis();
    unsigned long lastMovementMs = startMs;
    uint32_t startHits = getTrueNorthHitCount();

    calibrationPhase = "seeking_initial_true_north";
    calibrationLastPulseAccepted = false;
    Serial.println("[Rotate] seeking initial true north");
    driveServoAngle(rotateLeft, driveAngle);
    while (!trueNorthHitDetected(startHits)) {
      updateEncoderTracking();
      maintainConnectionDuringWait();

      if (millis() - startMs > timeoutMs) {
        error = "true north timeout";
        Serial.println("[Rotate] true north timeout");
        stopServo();
        saveLastPosition();
        return false;
      }

      if (encoderIsStuck(lastObservedCounts, lastMovementMs)) {
        error = "encoder stuck";
        Serial.println("[Rotate] encoder stuck while raw homing");
        stopServo();
        saveLastPosition();
        return false;
      }

      delay(TRUE_NORTH_POLL_DELAY_MS);
    }

    basePositionCounts = 0;
    positionTrusted = true;
    calibrationLastPulseAccepted = true;
    calibrationLastPulseMs = millis() - startMs;
    calibrationLastPulseCounts = labs(encoderUnwrapped);
    calibrationLastPulseMinCounts = 0;
    Serial.println("[Rotate] initial true north pulse detected");
    return true;
  }

  long calibrationMinimumTravelCounts(bool rotateLeft) {
    long expectedCountsPerRev = rotateLeft ? leftCountsPerRev : rightCountsPerRev;
    if (expectedCountsPerRev <= 0 && leftCountsPerRev > 0 && rightCountsPerRev > 0) {
      expectedCountsPerRev = (leftCountsPerRev + rightCountsPerRev) / 2;
    }
    if (expectedCountsPerRev <= 0) {
      expectedCountsPerRev = rotateLeft ? rightCountsPerRev : leftCountsPerRev;
    }

    long minCounts = CALIBRATION_UNKNOWN_MIN_TRAVEL_COUNTS;
    if (TRUE_NORTH_MIN_COUNTS_AFTER_RELEASE > minCounts) {
      minCounts = TRUE_NORTH_MIN_COUNTS_AFTER_RELEASE;
    }
    if (expectedCountsPerRev > 0) {
      long fractionCounts = lround(float(expectedCountsPerRev) * (float(CALIBRATION_MIN_TRAVEL_PERCENT) / 100.0f));
      if (fractionCounts > minCounts) minCounts = fractionCounts;
    }
    return minCounts;
  }

  bool measureFullRevolution(bool rotateLeft, int driveAngle, const char* phaseName,
                             unsigned long& revMs, long& countsPerRev, const char*& error) {
    basePositionCounts = 0;
    positionTrusted = true;
    calibrationPhase = rotateLeft ? "measuring_left_revolution" : "measuring_right_revolution";
    calibrationLastPulseAccepted = false;

    long lastObservedCounts = encoderUnwrapped;
    unsigned long startMs = millis();
    unsigned long lastMovementMs = startMs;

    Serial.printf("[Rotate] measuring %s revolution\n", phaseName);
    driveServoAngle(rotateLeft, driveAngle);
    while (isTrueNorthPressed()) {
      updateEncoderTracking();
      maintainConnectionDuringWait();

      if (millis() - startMs > PROFILE_CALIBRATION_TIMEOUT_MS) {
        error = "profile timeout while leaving true north";
        Serial.println("[Rotate] profile timeout while leaving true north");
        stopServo();
        saveLastPosition();
        return false;
      }

      if (encoderIsStuck(lastObservedCounts, lastMovementMs)) {
        error = "encoder stuck";
        Serial.println("[Rotate] encoder stuck while leaving true north");
        stopServo();
        saveLastPosition();
        return false;
      }

      delay(TRUE_NORTH_POLL_DELAY_MS);
    }

    resetEncoderTracking();
    long startCounts = encoderUnwrapped;
    unsigned long timingStartMs = millis();
    uint32_t baselineHits = getTrueNorthHitCount();
    long minTravelCounts = calibrationMinimumTravelCounts(rotateLeft);
    lastObservedCounts = encoderUnwrapped;
    lastMovementMs = timingStartMs;
    Serial.printf("[Rotate] %s min travel before next pulse=%ld counts\n", phaseName, minTravelCounts);

    while (true) {
      updateEncoderTracking();
      maintainConnectionDuringWait();

      if (millis() - startMs > PROFILE_CALIBRATION_TIMEOUT_MS) {
        error = "profile full revolution timeout";
        Serial.println("[Rotate] profile full revolution timeout");
        stopServo();
        saveLastPosition();
        return false;
      }

      if (encoderIsStuck(lastObservedCounts, lastMovementMs)) {
        error = "encoder stuck";
        Serial.println("[Rotate] encoder stuck during profile revolution");
        stopServo();
        saveLastPosition();
        return false;
      }

      uint32_t currentHits = getTrueNorthHitCount();
      bool pulseSeen = readTrueNorthPin() || currentHits != baselineHits;
      if (pulseSeen) {
        unsigned long elapsedMs = millis() - timingStartMs;
        long movedCounts = labs(encoderUnwrapped - startCounts);
        if (elapsedMs >= TRUE_NORTH_BOUNCE_IGNORE_MS &&
            movedCounts >= minTravelCounts) {
          stopServo();
          revMs = elapsedMs;
          countsPerRev = movedCounts;
          if (countsPerRev <= TARGET_TOLERANCE_COUNTS) {
            error = "profile revolution too small";
            Serial.printf("[Rotate] profile revolution invalid ms=%lu counts=%ld\n", revMs, countsPerRev);
            saveLastPosition();
            return false;
          }

          basePositionCounts = 0;
          positionTrusted = true;
          saveLastPosition();
          calibrationLastPulseAccepted = true;
          calibrationLastPulseMs = elapsedMs;
          calibrationLastPulseCounts = movedCounts;
          calibrationLastPulseMinCounts = minTravelCounts;
          Serial.printf("[Rotate] %s pulse detected ms=%lu counts=%ld angle=%d\n",
                        phaseName, revMs, countsPerRev, driveAngle);
          return true;
        }

        baselineHits = currentHits;
        calibrationLastPulseAccepted = false;
        calibrationLastPulseMs = elapsedMs;
        calibrationLastPulseCounts = movedCounts;
        calibrationLastPulseMinCounts = minTravelCounts;
        ++calibrationIgnoredPulseCount;
        Serial.printf("[Rotate] ignored %s pulse before full travel ms=%lu counts=%ld minCounts=%ld\n",
                      phaseName, elapsedMs, movedCounts, minTravelCounts);
        while (isTrueNorthPressed()) {
          updateEncoderTracking();
          maintainConnectionDuringWait();
          delay(TRUE_NORTH_POLL_DELAY_MS);
        }
      }

      delay(TRUE_NORTH_POLL_DELAY_MS);
    }
  }

  void deriveSpeedProfile() {
    initFixedSpeedProfileAngles();
    for (size_t i = 0; i < SPEED_PROFILE_COUNT; ++i) {
      Serial.printf("[Rotate] profile speed=%s offset=%d leftAngle=%d rightAngle=%d\n",
                    SPEED_PROFILE_NAMES[i], SPEED_PROFILE_OFFSETS[i],
                    leftSpeedAngles[i], rightSpeedAngles[i]);
    }
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

  bool applyCalibrationNeutralOverride(long neutralOverride, const char*& error) {
    if (neutralOverride == NO_NEUTRAL_OVERRIDE) return true;
    if (neutralOverride < CALIBRATION_NEUTRAL_MIN || neutralOverride > CALIBRATION_NEUTRAL_MAX) {
      error = "invalid neutralServoAngle";
      return false;
    }

    restingValue = static_cast<int>(neutralOverride);
    baseServo.write(restingValue);
    Serial.printf("[Rotate] calibration neutral override=%d\n", restingValue);
    return true;
  }

  void clampCalibrationNeutral() {
    int previousRestingValue = restingValue;
    if (restingValue < CALIBRATION_NEUTRAL_MIN) restingValue = CALIBRATION_NEUTRAL_MIN;
    if (restingValue > CALIBRATION_NEUTRAL_MAX) restingValue = CALIBRATION_NEUTRAL_MAX;
    if (previousRestingValue != restingValue) {
      Serial.printf("[Rotate] calibration neutral clamped from=%d to=%d\n",
                    previousRestingValue, restingValue);
    }
    baseServo.write(restingValue);
    initFixedSpeedProfileAngles();
  }

  long signedCalibrationDiffMs(unsigned long leftMs, unsigned long rightMs) {
    if (leftMs > static_cast<unsigned long>(LONG_MAX)) leftMs = LONG_MAX;
    if (rightMs > static_cast<unsigned long>(LONG_MAX)) rightMs = LONG_MAX;
    return static_cast<long>(leftMs) - static_cast<long>(rightMs);
  }

  bool calibrationIsBalanced(long diffMs) {
    return labs(diffMs) <= static_cast<long>(CALIBRATION_BALANCE_TOLERANCE_MS);
  }

  int calibrationAdjustmentForDiff(long diffMs) {
    unsigned long absDiff = labs(diffMs);
    int adjustment = static_cast<int>((absDiff + 3999) / 4000);
    if (adjustment < 1) adjustment = 1;
    if (adjustment > 3) adjustment = 3;
    return adjustment;
  }

  void adjustCalibrationNeutral(long diffMs) {
    int adjustment = calibrationAdjustmentForDiff(diffMs);
    int previousRestingValue = restingValue;

    if (diffMs > 0) {
      restingValue -= adjustment;
    } else if (diffMs < 0) {
      restingValue += adjustment;
    }

    if (restingValue < CALIBRATION_NEUTRAL_MIN) restingValue = CALIBRATION_NEUTRAL_MIN;
    if (restingValue > CALIBRATION_NEUTRAL_MAX) restingValue = CALIBRATION_NEUTRAL_MAX;

    baseServo.write(restingValue);
    initFixedSpeedProfileAngles();
    Serial.printf("[Rotate] calibration neutral adjust diff=%ld adjustment=%d from=%d to=%d\n",
                  diffMs, adjustment, previousRestingValue, restingValue);
  }

  bool calibrateRotationProfile(long neutralOverride, const char*& error) {
    Serial.printf("[Rotate] profile calibration start raw=%d sign=%d\n", readEncoderRaw(), ENCODER_SIGN);

    RotationProfileBackup backup = makeProfileBackup();
    calibrationPasses = 0;
    calibrationLastDiffMs = 0;
    calibrationBalanced = false;
    calibrationPhase = "starting";
    calibrationLastPulseAccepted = false;
    calibrationLastPulseMs = 0;
    calibrationLastPulseCounts = 0;
    calibrationLastPulseMinCounts = 0;
    calibrationIgnoredPulseCount = 0;

    if (!applyCalibrationNeutralOverride(neutralOverride, error)) {
      restoreProfileBackup(backup);
      return false;
    }
    clampCalibrationNeutral();

    int leftCalibrationAngle = calibrationLeftAngle();
    int rightCalibrationAngle = calibrationRightAngle();
    if (leftCalibrationAngle == restingValue || rightCalibrationAngle == restingValue) {
      error = "neutralServoAngle too close to limit";
      restoreProfileBackup(backup);
      return false;
    }

    baseServo.write(restingValue);
    resetEncoderTracking();
    Serial.printf("[Rotate] profile calibration fixed speed resting=%d offset=%d leftAngle=%d rightAngle=%d\n",
                  restingValue, CALIBRATION_DRIVE_OFFSET, leftCalibrationAngle, rightCalibrationAngle);

    if (!seekTrueNorthRaw(false, rightCalibrationAngle, PROFILE_CALIBRATION_TIMEOUT_MS, error)) {
      restoreProfileBackup(backup);
      return false;
    }

    for (int pass = 1; pass <= CALIBRATION_MAX_PASSES; ++pass) {
      leftCalibrationAngle = calibrationLeftAngle();
      rightCalibrationAngle = calibrationRightAngle();
      if (leftCalibrationAngle == restingValue || rightCalibrationAngle == restingValue) {
        error = "neutralServoAngle too close to limit";
        restoreProfileBackup(backup);
        return false;
      }

      Serial.printf("[Rotate] profile calibration pass=%d resting=%d offset=%d leftAngle=%d rightAngle=%d\n",
                    pass, restingValue, CALIBRATION_DRIVE_OFFSET, leftCalibrationAngle, rightCalibrationAngle);

      unsigned long measuredLeftMs = 0;
      unsigned long measuredRightMs = 0;
      long measuredLeftCpr = 0;
      long measuredRightCpr = 0;

      if (!measureFullRevolution(true, leftCalibrationAngle, "LEFT", measuredLeftMs, measuredLeftCpr, error)) {
        restoreProfileBackup(backup);
        return false;
      }

      if (!measureFullRevolution(false, rightCalibrationAngle, "RIGHT", measuredRightMs, measuredRightCpr, error)) {
        restoreProfileBackup(backup);
        return false;
      }

      leftFullRevMs = measuredLeftMs;
      rightFullRevMs = measuredRightMs;
      leftCountsPerRev = measuredLeftCpr;
      rightCountsPerRev = measuredRightCpr;
      calibrationPasses = pass;
      calibrationLastDiffMs = signedCalibrationDiffMs(leftFullRevMs, rightFullRevMs);
      calibrationBalanced = calibrationIsBalanced(calibrationLastDiffMs);

      Serial.printf("[Rotate] profile calibration pass=%d leftMs=%lu rightMs=%lu diff=%ld balanced=%d leftCpr=%ld rightCpr=%ld\n",
                    pass, leftFullRevMs, rightFullRevMs, calibrationLastDiffMs,
                    calibrationBalanced, leftCountsPerRev, rightCountsPerRev);

      if (pass >= CALIBRATION_MIN_PASSES && calibrationBalanced) {
        break;
      }

      if (pass < CALIBRATION_MAX_PASSES) {
        adjustCalibrationNeutral(calibrationLastDiffMs);
      }
    }

    if (calibrationPasses < CALIBRATION_MIN_PASSES || !calibrationBalanced) {
      error = "profile calibration did not balance";
      calibrationPhase = "failed";
      restoreProfileBackup(backup);
      return false;
    }

    deriveSpeedProfile();

    calibrated = leftCountsPerRev > 0 && rightCountsPerRev > 0;
    if (!calibrated) {
      error = "profile calibration incomplete";
      calibrationPhase = "failed";
      restoreProfileBackup(backup);
      return false;
    }

    profileCalibrated = calibrated;
    basePositionCounts = 0;
    positionTrusted = true;
    saveCalibration();
    saveLastPosition();

    Serial.printf("[Rotate] profile calibration complete resting=%d leftMs=%lu rightMs=%lu leftCpr=%ld rightCpr=%ld\n",
                  restingValue, leftFullRevMs, rightFullRevMs, leftCountsPerRev, rightCountsPerRev);
    calibrationPhase = "complete";
    return true;
  }

  bool calibrateBase(bool rotateLeft, BaseSpeed speed, const char*& error) {
    Serial.printf("[Rotate] calibration start dir=%s raw=%d sign=%d\n",
                  rotateLeft ? "LEFT" : "RIGHT", readEncoderRaw(), ENCODER_SIGN);

    if (!rotateUntilTrueNorth(rotateLeft, speed, HOME_TIMEOUT_MS, error)) {
      return false;
    }

    delay(250);
    basePositionCounts = 0;
    positionTrusted = true;
    resetEncoderTracking();

    long lastObservedCounts = encoderUnwrapped;
    unsigned long startMs = millis();
    unsigned long lastMovementMs = startMs;

    driveServo(rotateLeft, speed);
    while (isTrueNorthPressed()) {
      updateEncoderTracking();
      maintainConnectionDuringWait();

      if (millis() - startMs > CALIBRATION_TIMEOUT_MS) {
        error = "calibration timeout while leaving true north";
        Serial.println("[Rotate] calibration timeout while leaving true north");
        stopServo();
        saveLastPosition();
        return false;
      }

      if (encoderIsStuck(lastObservedCounts, lastMovementMs)) {
        error = "encoder stuck";
        Serial.println("[Rotate] encoder stuck while leaving true north");
        stopServo();
        saveLastPosition();
        return false;
      }

      delay(TRUE_NORTH_POLL_DELAY_MS);
    }

    long startCounts = encoderUnwrapped;
    Serial.printf("[Rotate] calibration off button raw=%d start=%ld\n", encoderRaw, startCounts);

    lastObservedCounts = encoderUnwrapped;
    lastMovementMs = millis();
    uint32_t baselineHits = getTrueNorthHitCount();
    while (!trueNorthHitDetected(baselineHits)) {
      updateEncoderTracking();
      maintainConnectionDuringWait();

      if (millis() - startMs > CALIBRATION_TIMEOUT_MS) {
        error = "calibration timeout";
        Serial.println("[Rotate] calibration timeout");
        stopServo();
        saveLastPosition();
        return false;
      }

      if (encoderIsStuck(lastObservedCounts, lastMovementMs)) {
        error = "encoder stuck";
        Serial.println("[Rotate] encoder stuck during calibration");
        stopServo();
        saveLastPosition();
        return false;
      }

      delay(TRUE_NORTH_POLL_DELAY_MS);
    }

    stopServo();

    long countsPerBaseRev = labs(encoderUnwrapped - startCounts);
    if (countsPerBaseRev <= TARGET_TOLERANCE_COUNTS) {
      error = "calibration count too small";
      Serial.printf("[Rotate] calibration count too small: %ld\n", countsPerBaseRev);
      saveLastPosition();
      return false;
    }

    if (rotateLeft) {
      leftCountsPerRev = countsPerBaseRev;
    } else {
      rightCountsPerRev = countsPerBaseRev;
    }

    calibrated = leftCountsPerRev > 0 || rightCountsPerRev > 0;
    basePositionCounts = 0;
    positionTrusted = true;
    saveCalibration();
    saveLastPosition();

    Serial.printf("[Rotate] calibration complete dir=%s countsPerRev=%ld raw=%d unwrapped=%ld\n",
                  rotateLeft ? "LEFT" : "RIGHT", countsPerBaseRev, encoderRaw, encoderUnwrapped);
    return true;
  }

  bool calibrateBoth(long neutralOverride, const char*& error) {
    return calibrateRotationProfile(neutralOverride, error);
  }

  void buildStatusJson(String& out, const char* error = nullptr) {
    StaticJsonDocument<2048> doc;
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

bool ActionBaseRotate::run(const String& message, String& statusJson, String& detailsKey) {
  initAll();
  statusJson = "";
  detailsKey = "base_rotation";

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
      ok = calibrateBase(rotateLeft, speed, error);
    }
  } else if (strcasecmp(ctl, "CALIBRATE_PROFILE") == 0) {
    long neutralOverride = NO_NEUTRAL_OVERRIDE;
    if (parseNeutralOverride(doc["neutralServoAngle"], neutralOverride, error)) {
      ok = calibrateRotationProfile(neutralOverride, error);
    }
  } else if (strcasecmp(ctl, "CALIBRATE_BOTH") == 0) {
    long neutralOverride = NO_NEUTRAL_OVERRIDE;
    if (parseNeutralOverride(doc["neutralServoAngle"], neutralOverride, error)) {
      ok = calibrateBoth(neutralOverride, error);
    }
  } else if (strcasecmp(ctl, "ENCODER") == 0) {
    bool rotateLeft = false;
    long value = 0;
    usingEstimatedStepCounts = false;
    if (!parseDirection(doc["direction"].as<const char*>(), rotateLeft)) {
      error = "invalid direction";
    } else if (!extractLong(doc["value"], value) || value <= 0) {
      error = "invalid value";
    } else {
      BaseSpeed moveSpeed = stepMovementSpeed(doc["speed"], speed);
      long targetCounts = stepsToCountsWithEstimate(value, rotateLeft, usingEstimatedStepCounts);
      Serial.printf("[Rotate] ENCODER steps=%ld counts=%ld estimated=%d calibrated=%d speed=%d omittedSpeed=%d\n",
                    value, targetCounts, usingEstimatedStepCounts, calibrated, moveSpeed, doc["speed"].isNull());
      ok = moveByCounts(targetCounts, rotateLeft, moveSpeed, error);
    }
  } else if (strcasecmp(ctl, "STEPS") == 0) {
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
      Serial.printf("[Rotate] STEPS steps=%ld counts=%ld estimated=%d calibrated=%d speed=%d omittedSpeed=%d\n",
                    value, targetCounts, usingEstimatedStepCounts, calibrated, moveSpeed, doc["speed"].isNull());
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
  bool ok = calibrateRotationProfile(neutralServoAngle, error);
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
