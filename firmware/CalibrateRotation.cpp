// CalibrateRotation.cpp
//
// Base rotation calibration. Motion primitives, hardware access and shared
// state come from ActionBaseRotate via ActionBaseRotate_Internal.h; this file
// contains only the calibration logic built on top of them.
//
// What calibration does, in full:
//
//   1. Find the home button.
//   2. Drive RIGHT one full lap, back to home. Count the encoder. That is RS.
//   3. Drive LEFT one full lap, back to home. Count the encoder. That is LS.
//
// RS and LS are the number of encoder counts in one complete turn of the base,
// per direction. Every move divides by them: "right 180" travels RS/2 counts,
// "left 90" travels LS/4. That is the entire contract, and it is what makes a
// left move and a right move of the same angle cover the same ground.
//
// A lap is delimited by the home switch alone -- pressed, released, released
// continuously for a debounce window, pressed again. No encoder threshold takes
// part in deciding whether a press ends a lap. An earlier design gated presses
// on having travelled a minimum number of counts, derived from the counts-per-
// revolution that calibration had not measured yet; on an uncalibrated robot it
// substituted a guess, and when that guess landed near the true value real laps
// were accepted or rejected essentially at random.
//
// Nothing here assumes what the count *should* be. Successive guesses at the
// expected value (24576 from the gear ratio, then 4096 from the encoder's
// native resolution) were both wrong -- the hardware measures about 20000 --
// so calibration reports whatever it finds and rejects only the physically
// impossible.
#include "CalibrateRotation.h"
#include "BuddyMQTT.h"
#include <Arduino.h>
#include <ArduinoJson.h>
#include <math.h>
#include <stdlib.h>

using namespace BaseRotateInternal;

namespace {

  // ---- Progress telemetry ---------------------------------------------------
  //
  // Calibration blocks for a minute or more with no other MQTT traffic, so a
  // hang is indistinguishable from slow progress from the wizard's side. Each
  // lap boundary and a periodic heartbeat publish a snapshot, which is enough
  // to tell "still turning" from "servo not moving at all".
  void publishRotationProgress(const char* event, const char* phaseName, int driveAngle) {
    StaticJsonDocument<512> doc;
    doc["event"] = event;
    if (phaseName) doc["measure_phase"] = phaseName;
    doc["phase"] = calibrationPhase ? calibrationPhase : "";
    if (driveAngle >= 0) doc["drive_angle"] = driveAngle;
    doc["resting"] = restingValue;
    doc["encoder_unwrapped"] = encoderUnwrapped;
    doc["encoder_raw"] = readEncoderRaw();
    doc["base_counts"] = basePositionCounts;
    doc["true_north_pressed"] = isTrueNorthPressed();
    doc["true_north_hits"] = getTrueNorthHitCount();
    doc["pulse_accepted"] = calibrationLastPulseAccepted;
    doc["pulse_ms"] = calibrationLastPulseMs;
    doc["pulse_counts"] = calibrationLastPulseCounts;
    doc["pulse_min_counts"] = calibrationLastPulseMinCounts;
    doc["ignored_pulses"] = calibrationIgnoredPulseCount;
    doc["pass"] = calibrationPasses;
    doc["left_cpr"] = leftCountsPerRev;
    doc["right_cpr"] = rightCountsPerRev;
    doc["left_ms"] = leftFullRevMs;
    doc["right_ms"] = rightFullRevMs;

    String details;
    serializeJson(doc, details);
    BuddyMQTT::sendProgress(BuddyMQTT::currentActionId(), "calibrate_base_rotation", details);
  }

  void publishRotationProgress(const char* event) {
    publishRotationProgress(event, nullptr, -1);
  }

  // ---- Neutral angle handling ----------------------------------------------

  // Applies the neutral the operator asked for, and saves it.
  //
  // A supplied neutralServoAngle always wins and becomes the new stored value.
  // When none is supplied, calibration starts from CALIBRATION_DEFAULT_NEUTRAL
  // rather than the stored one: an older design hunted a neutral and persisted
  // it, so an aborted run could leave a badly off-centre value saved (76 was
  // seen in the field) that weakened the drive on every later run. Starting
  // from a known centre makes each run independent of how the last one ended.
  bool applyCalibrationNeutralOverride(long neutralOverride, const char*& error) {
    if (neutralOverride != NO_NEUTRAL_OVERRIDE &&
        (neutralOverride < CALIBRATION_NEUTRAL_MIN || neutralOverride > CALIBRATION_NEUTRAL_MAX)) {
      error = "invalid neutralServoAngle";
      return false;
    }

    int previousRestingValue = restingValue;
    restingValue = neutralOverride == NO_NEUTRAL_OVERRIDE
        ? CALIBRATION_DEFAULT_NEUTRAL
        : static_cast<int>(neutralOverride);
    writeServo(restingValue);
    initFixedSpeedProfileAngles();
    saveNeutralValue();
    Serial.printf("[Rotate] calibration neutral from=%d to=%d (override=%s)\n",
                  previousRestingValue, restingValue,
                  neutralOverride == NO_NEUTRAL_OVERRIDE ? "default" : "requested");
    return true;
  }

  // A lap count this small means the encoder never really moved: a dead magnet,
  // an unpowered servo, or a home switch stuck closed. Anything above it is
  // reported as measured -- this is a floor, not an expected-value band.
  bool lapCountIsUsable(long counts) {
    return counts >= CALIBRATION_MIN_USABLE_COUNTS_PER_REV;
  }

  // ---- True-stop finding ------------------------------------------------------
  //
  // On a continuous-rotation servo, write(restingValue) is not guaranteed to be
  // the angle at which the base actually stops turning -- that point drifts per
  // unit and is not knowable from the datasheet value alone. Base profile
  // calibration used to trust the operator-supplied neutral outright, so a base
  // whose true stop sat a couple of degrees off the entered value would creep
  // indefinitely afterward, which is exactly what a continuous-rotation base
  // does when driven at a not-quite-neutral angle instead of being stopped.
  //
  // This sweeps outward from the operator's angle in whichever direction the
  // very first sample shows less drift, one degree at a time, until a window
  // comes back under the drift threshold. It only ever writes restingValue (and
  // persists it) once a clean window is found; any failure to converge leaves
  // the previously saved neutral untouched and stops the servo. That mirrors the
  // existing rule against persisting a hunted-but-unproven value (see
  // applyCalibrationNeutralOverride above): a partial or failed hunt must never
  // weaken the drive angles a later run inherits.
  constexpr unsigned long NEUTRAL_SAMPLE_WINDOW_MS = 400;
  constexpr long NEUTRAL_DRIFT_TOLERANCE_COUNTS = 40;
  constexpr int NEUTRAL_SEARCH_MAX_STEPS = CALIBRATION_NEUTRAL_MAX - CALIBRATION_NEUTRAL_MIN;

  // Drives at `angle` and reports signed drift over one sample window. Positive
  // means the encoder counted up (as ENCODER_SIGN and the wiring define "up").
  long sampleNeutralDrift(int angle) {
    writeServo(clampServoAngle(angle));
    resetEncoderTracking();
    unsigned long windowStart = millis();
    while (millis() - windowStart < NEUTRAL_SAMPLE_WINDOW_MS) {
      updateEncoderTracking();
      delay(TRUE_NORTH_POLL_DELAY_MS);
    }
    return encoderUnwrapped;
  }

  // Finds the servo angle at which the base actually stops turning, starting
  // from `startAngle`. On success, writes and persists the found angle as
  // restingValue and returns true. On failure -- no direction reduces drift, or
  // the search runs past the neutral bounds without converging -- leaves
  // restingValue exactly as it was on entry, stops the servo, and returns false.
  bool findTrueNeutral(int startAngle, int& foundAngle, const char*& error) {
    calibrationPhase = "finding_neutral";
    publishRotationProgress("finding_neutral_start");

    long baselineDrift = sampleNeutralDrift(startAngle);
    if (labs(baselineDrift) <= NEUTRAL_DRIFT_TOLERANCE_COUNTS) {
      stopServo();
      foundAngle = startAngle;
      Serial.printf("[Rotate] neutral already true at %d (drift=%ld)\n", startAngle, baselineDrift);
      return true;
    }

    // A continuous-rotation servo drives "forward" above its true stop and
    // "backward" below it (or vice versa depending on wiring), so the sign of
    // the very first drift sample tells us which way to step to reduce it.
    int step = baselineDrift > 0 ? -1 : 1;
    int angle = startAngle;
    long bestDrift = baselineDrift;

    for (int i = 0; i < NEUTRAL_SEARCH_MAX_STEPS; ++i) {
      angle += step;
      if (angle < CALIBRATION_NEUTRAL_MIN || angle > CALIBRATION_NEUTRAL_MAX) {
        stopServo();
        error = "true neutral not found within bounds";
        Serial.printf("[Rotate] neutral search left bounds at %d (best drift=%ld)\n", angle, bestDrift);
        publishRotationProgress("finding_neutral_out_of_bounds");
        return false;
      }

      long drift = sampleNeutralDrift(angle);
      Serial.printf("[Rotate] neutral probe angle=%d drift=%ld\n", angle, drift);
      publishRotationProgress("finding_neutral_probe");

      if (labs(drift) <= NEUTRAL_DRIFT_TOLERANCE_COUNTS) {
        stopServo();
        foundAngle = angle;
        Serial.printf("[Rotate] neutral found at %d (drift=%ld)\n", angle, drift);
        return true;
      }

      // Overshot the stop point: drift flipped sign and grew again rather than
      // continuing to shrink. Give up rather than oscillate past the bounds.
      if (labs(drift) > labs(bestDrift) && (drift > 0) != (baselineDrift > 0)) {
        stopServo();
        error = "true neutral search overshot";
        Serial.printf("[Rotate] neutral search overshot at %d (drift=%ld, best=%ld)\n",
                      angle, drift, bestDrift);
        publishRotationProgress("finding_neutral_overshot");
        return false;
      }
      bestDrift = drift;
    }

    stopServo();
    error = "true neutral search exhausted steps";
    publishRotationProgress("finding_neutral_exhausted");
    return false;
  }
}

// ---- True north acquisition ------------------------------------------------

bool CalibrateRotation::seekTrueNorthRaw(bool rotateLeft, int driveAngle,
                                         unsigned long timeoutMs, const char*& error) {
  long lastObservedCounts = encoderUnwrapped;
  unsigned long startMs = millis();
  unsigned long lastMovementMs = startMs;
  uint32_t startHits = getTrueNorthHitCount();

  calibrationPhase = "seeking_home";
  calibrationLastPulseAccepted = false;
  Serial.println("[Rotate] seeking home");
  publishRotationProgress("seek_true_north_start", "SEEK", driveAngle);
  driveServoAngle(rotateLeft, driveAngle);
  unsigned long lastHeartbeatMs = startMs;
  while (!trueNorthHitDetected(startHits)) {
    updateEncoderTracking();

    if (millis() - startMs > timeoutMs) {
      error = "true north timeout";
      Serial.println("[Rotate] true north timeout");
      stopServo();
      publishRotationProgress("seek_true_north_timeout", "SEEK", driveAngle);
      saveLastPosition();
      return false;
    }

    if (encoderIsStuck(lastObservedCounts, lastMovementMs)) {
      error = "encoder stuck";
      Serial.println("[Rotate] encoder stuck while seeking home");
      stopServo();
      publishRotationProgress("seek_true_north_encoder_stuck", "SEEK", driveAngle);
      saveLastPosition();
      return false;
    }

    if (millis() - lastHeartbeatMs >= CALIBRATION_PROGRESS_HEARTBEAT_MS) {
      lastHeartbeatMs = millis();
      publishRotationProgress("seek_true_north_waiting", "SEEK", driveAngle);
    }

    delay(TRUE_NORTH_POLL_DELAY_MS);
  }

  basePositionCounts = 0;
  positionTrusted = true;
  calibrationLastPulseAccepted = true;
  calibrationLastPulseMs = millis() - startMs;
  calibrationLastPulseCounts = labs(encoderUnwrapped);
  calibrationLastPulseMinCounts = 0;
  Serial.println("[Rotate] home found");
  publishRotationProgress("true_north_hit_initial", "SEEK", driveAngle);
  return true;
}

// Drives one full lap: from the home button, all the way around, back to the
// home button, counting encoder ticks the whole way. That count is the number
// of encoder counts in a complete turn of the base in this direction.
//
// The lap boundary is the switch and nothing else. Leaving the current press is
// step one; confirming the switch genuinely opened is step two; the next press
// after that confirmed release closes the lap.
bool CalibrateRotation::measureFullRevolution(bool rotateLeft, int driveAngle, const char* phaseName,
                                              unsigned long& revMs, long& countsPerRev,
                                              const char*& error) {
  if (!isTrueNorthPressed()) {
    Serial.printf("[Rotate] %s re-acquiring home before lap\n", phaseName);
    if (!CalibrateRotation::seekTrueNorthRaw(rotateLeft, driveAngle,
                                             PROFILE_CALIBRATION_TIMEOUT_MS, error)) {
      return false;
    }
  }

  basePositionCounts = 0;
  positionTrusted = true;
  calibrationPhase = rotateLeft ? "measuring_left_lap" : "measuring_right_lap";
  calibrationLastPulseAccepted = false;

  long lastObservedCounts = encoderUnwrapped;
  unsigned long startMs = millis();
  unsigned long lastMovementMs = startMs;

  Serial.printf("[Rotate] measuring %s lap\n", phaseName);
  publishRotationProgress("revolution_start", phaseName, driveAngle);
  driveServoAngle(rotateLeft, driveAngle);
  unsigned long lastHeartbeatMs = startMs;

  // Roll off the switch first. Counting starts once the base is clear of it, so
  // that the lap measured is home-edge to home-edge.
  while (isTrueNorthPressed()) {
    updateEncoderTracking();

    if (millis() - startMs > PROFILE_CALIBRATION_TIMEOUT_MS) {
      error = "timeout leaving home";
      Serial.println("[Rotate] timeout leaving home");
      stopServo();
      publishRotationProgress("leave_true_north_timeout", phaseName, driveAngle);
      saveLastPosition();
      return false;
    }

    if (encoderIsStuck(lastObservedCounts, lastMovementMs)) {
      error = "encoder stuck";
      Serial.println("[Rotate] encoder stuck while leaving home");
      stopServo();
      publishRotationProgress("leave_true_north_encoder_stuck", phaseName, driveAngle);
      saveLastPosition();
      return false;
    }

    if (millis() - lastHeartbeatMs >= CALIBRATION_PROGRESS_HEARTBEAT_MS) {
      lastHeartbeatMs = millis();
      publishRotationProgress("leaving_true_north", phaseName, driveAngle);
    }

    delay(TRUE_NORTH_POLL_DELAY_MS);
  }

  resetEncoderTracking();
  long startCounts = encoderUnwrapped;
  unsigned long timingStartMs = millis();
  lastObservedCounts = encoderUnwrapped;
  lastMovementMs = timingStartMs;
  // Rebase the timeout budget: leaving home already consumed part of it, and
  // the lap itself needs the full window.
  startMs = timingStartMs;
  lastHeartbeatMs = timingStartMs;
  calibrationLastPulseMinCounts = 0;
  publishRotationProgress("left_true_north", phaseName, driveAngle);

  // The switch has stopped reading pressed, but contacts chatter as they open.
  // Require a continuous quiet stretch before trusting the release, so bounce
  // on the way off the button is never mistaken for a completed lap.
  unsigned long releasedSinceMs = millis();
  bool releaseConfirmed = false;

  while (true) {
    updateEncoderTracking();

    if (millis() - startMs > PROFILE_CALIBRATION_TIMEOUT_MS) {
      error = "lap timeout";
      Serial.println("[Rotate] lap timeout");
      stopServo();
      publishRotationProgress("revolution_timeout", phaseName, driveAngle);
      saveLastPosition();
      return false;
    }

    if (encoderIsStuck(lastObservedCounts, lastMovementMs)) {
      error = "encoder stuck";
      Serial.println("[Rotate] encoder stuck during lap");
      stopServo();
      publishRotationProgress("revolution_encoder_stuck", phaseName, driveAngle);
      saveLastPosition();
      return false;
    }

    if (millis() - lastHeartbeatMs >= CALIBRATION_PROGRESS_HEARTBEAT_MS) {
      lastHeartbeatMs = millis();
      publishRotationProgress(releaseConfirmed ? "rotating" : "confirming_release",
                              phaseName, driveAngle);
    }

    bool pressed = readTrueNorthPin();

    if (!releaseConfirmed) {
      // Still proving the switch let go. Any press restarts the quiet window.
      if (pressed) {
        releasedSinceMs = millis();
      } else if (millis() - releasedSinceMs >= TRUE_NORTH_RELEASE_DEBOUNCE_MS) {
        releaseConfirmed = true;
        // Only now does a press mean "came back round". Clear any hits the
        // interrupt recorded during the release so bounce cannot close the lap.
        (void)getTrueNorthHitCount();
        Serial.printf("[Rotate] %s left home, watching for return\n", phaseName);
        publishRotationProgress("release_confirmed", phaseName, driveAngle);
      }
      delay(TRUE_NORTH_POLL_DELAY_MS);
      continue;
    }

    if (!pressed) {
      delay(TRUE_NORTH_POLL_DELAY_MS);
      continue;
    }

    // Back at home after a confirmed release: one full lap.
    stopServo();
    revMs = millis() - timingStartMs;
    countsPerRev = labs(encoderUnwrapped - startCounts);

    calibrationLastPulseAccepted = true;
    calibrationLastPulseMs = revMs;
    calibrationLastPulseCounts = countsPerRev;

    if (!lapCountIsUsable(countsPerRev)) {
      error = "lap count too small";
      Serial.printf("[Rotate] %s lap unusable ms=%lu counts=%ld (minimum %ld)\n",
                    phaseName, revMs, countsPerRev, CALIBRATION_MIN_USABLE_COUNTS_PER_REV);
      calibrationLastPulseAccepted = false;
      stopServo();
      publishRotationProgress("revolution_too_small", phaseName, driveAngle);
      saveLastPosition();
      return false;
    }

    basePositionCounts = 0;
    positionTrusted = true;
    saveLastPosition();
    Serial.printf("[Rotate] %s lap complete counts=%ld ms=%lu angle=%d\n",
                  phaseName, countsPerRev, revMs, driveAngle);
    publishRotationProgress("true_north_hit_accepted", phaseName, driveAngle);
    return true;
  }
}

// ---- Full calibration -------------------------------------------------------

bool CalibrateRotation::calibrateRotationProfile(long neutralOverride, const char*& error) {
  Serial.printf("[Rotate] calibration start raw=%d sign=%d\n", readEncoderRaw(), ENCODER_SIGN);

  // Apply and persist the operator's neutral before anything else. It is an
  // input from the form, not a result of this run, so it must take effect even
  // if a later step fails.
  if (!applyCalibrationNeutralOverride(neutralOverride, error)) {
    return false;
  }

  calibrationPasses = 0;
  calibrationLastDiffMs = 0;
  calibrationBalanced = false;
  calibrationPhase = "starting";
  calibrationLastPulseAccepted = false;
  calibrationLastPulseMs = 0;
  calibrationLastPulseCounts = 0;
  calibrationLastPulseMinCounts = 0;
  calibrationIgnoredPulseCount = 0;

  // Calibration is about to overwrite the stored counts. Until it finishes,
  // nothing downstream should act on the old ones or on a position measured
  // against them.
  calibrated = false;
  profileCalibrated = false;
  verySlowValidationPassed = false;
  positionTrusted = false;

  int leftCalibrationAngle = calibrationLeftAngle();
  int rightCalibrationAngle = calibrationRightAngle();
  if (leftCalibrationAngle == restingValue || rightCalibrationAngle == restingValue) {
    error = "neutralServoAngle too close to limit";
    return false;
  }

  writeServo(restingValue);
  resetEncoderTracking();
  Serial.printf("[Rotate] calibration resting=%d offset=%d leftAngle=%d rightAngle=%d\n",
                restingValue, CALIBRATION_DRIVE_OFFSET, leftCalibrationAngle, rightCalibrationAngle);
  publishRotationProgress("calibration_start");

  // Step 1: find home.
  if (!CalibrateRotation::seekTrueNorthRaw(false, rightCalibrationAngle,
                                           PROFILE_CALIBRATION_TIMEOUT_MS, error)) {
    return false;
  }

  // Step 2: one lap RIGHT, home back to home. That is RS.
  long rightSteps = 0;
  unsigned long rightMs = 0;
  if (!CalibrateRotation::measureFullRevolution(false, rightCalibrationAngle, "RIGHT",
                                                rightMs, rightSteps, error)) {
    calibrationPhase = "failed";
    publishRotationProgress("calibration_failed");
    return false;
  }
  calibrationPasses = 1;
  publishRotationProgress("revolution_measured", "RIGHT", rightCalibrationAngle);

  // Step 3: one lap LEFT from where the right lap left us -- at home. That is LS.
  long leftSteps = 0;
  unsigned long leftMs = 0;
  if (!CalibrateRotation::measureFullRevolution(true, leftCalibrationAngle, "LEFT",
                                                leftMs, leftSteps, error)) {
    calibrationPhase = "failed";
    publishRotationProgress("calibration_failed");
    return false;
  }
  calibrationPasses = 2;
  publishRotationProgress("revolution_measured", "LEFT", leftCalibrationAngle);

  leftCountsPerRev = leftSteps;
  rightCountsPerRev = rightSteps;
  leftFullRevMs = leftMs;
  rightFullRevMs = rightMs;
  calibrationLastDiffMs = signedCalibrationDiffMs(leftFullRevMs, rightFullRevMs);

  // Speed angles are fixed offsets from neutral, never learned, so nothing here
  // can persist a weakened drive angle for a later run to inherit.
  initFixedSpeedProfileAngles();

  // The two laps measure the same circle from opposite directions, so they
  // should agree closely. A large gap is worth seeing in the log -- it points at
  // mechanical backlash or a home switch that triggers at a different point
  // depending on approach direction -- but it does not invalidate either count,
  // since each direction's moves divide by its own.
  long spread = labs(leftSteps - rightSteps);
  long larger = leftSteps > rightSteps ? leftSteps : rightSteps;
  Serial.printf("[Rotate] LS=%ld RS=%ld spread=%ld (%ld%% of larger)\n",
                leftSteps, rightSteps, spread, larger > 0 ? (spread * 100L) / larger : 0L);

  calibrated = leftCountsPerRev > 0 && rightCountsPerRev > 0;
  if (!calibrated) {
    error = "calibration produced no usable counts";
    calibrationPhase = "failed";
    publishRotationProgress("calibration_failed");
    return false;
  }

  verySlowValidationPassed = calibrated;
  profileCalibrated = calibrated;
  basePositionCounts = 0;
  positionTrusted = true;
  saveCalibration();
  saveLastPosition();

  // The laps above prove the encoder and drive work; they say nothing about
  // whether restingValue is where the base actually stops. Find that now, with
  // a known-good encoder, rather than leaving the base creeping at whatever the
  // operator entered. A failed search is not fatal to the calibration itself --
  // counts-per-rev are still valid -- but it must not silently leave the base
  // turning, so the servo is always stopped by findTrueNeutral before we return.
  int foundNeutral = restingValue;
  const char* neutralError = nullptr;
  if (findTrueNeutral(restingValue, foundNeutral, neutralError)) {
    if (foundNeutral != restingValue) {
      Serial.printf("[Rotate] true neutral %d differs from entered %d, adopting it\n",
                    foundNeutral, restingValue);
    }
    restingValue = foundNeutral;
    initFixedSpeedProfileAngles();
    saveNeutralValue();
  } else {
    Serial.printf("[Rotate] true neutral search failed (%s); keeping entered neutral %d, base stopped\n",
                  neutralError ? neutralError : "unknown", restingValue);
  }
  writeServo(restingValue);

  Serial.printf("[Rotate] calibration complete resting=%d LS=%ld RS=%ld leftMs=%lu rightMs=%lu\n",
                restingValue, leftCountsPerRev, rightCountsPerRev, leftFullRevMs, rightFullRevMs);
  calibrationPhase = "complete";
  publishRotationProgress("calibration_complete");
  return true;
}

// ---- Single direction calibration ------------------------------------------

// Measures one lap in the requested direction only, storing that direction's
// count. Shares measureFullRevolution with the full flow so there is a single
// definition of what a lap is.
bool CalibrateRotation::calibrateBase(bool rotateLeft, BaseSpeed speed, const char*& error) {
  Serial.printf("[Rotate] single direction calibration dir=%s raw=%d sign=%d\n",
                rotateLeft ? "LEFT" : "RIGHT", readEncoderRaw(), ENCODER_SIGN);

  if (!rotateUntilTrueNorth(rotateLeft, speed, HOME_TIMEOUT_MS, error)) {
    return false;
  }

  delay(250);
  basePositionCounts = 0;
  positionTrusted = true;
  resetEncoderTracking();

  const char* phaseName = rotateLeft ? "LEFT" : "RIGHT";
  int driveAngle = rotateLeft ? calibrationLeftAngle() : calibrationRightAngle();
  unsigned long revMs = 0;
  long lapCounts = 0;

  if (!CalibrateRotation::measureFullRevolution(rotateLeft, driveAngle, phaseName,
                                                revMs, lapCounts, error)) {
    return false;
  }

  if (rotateLeft) {
    leftCountsPerRev = lapCounts;
    leftFullRevMs = revMs;
  } else {
    rightCountsPerRev = lapCounts;
    rightFullRevMs = revMs;
  }

  calibrated = leftCountsPerRev > 0 || rightCountsPerRev > 0;
  basePositionCounts = 0;
  positionTrusted = true;
  saveCalibration();
  saveLastPosition();

  Serial.printf("[Rotate] single direction calibration complete dir=%s counts=%ld ms=%lu\n",
                phaseName, lapCounts, revMs);
  return true;
}
