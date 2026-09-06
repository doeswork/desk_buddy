// ActionBaseRotate_Internal.h
//
// Shared seam between ActionBaseRotate.cpp (motion + command dispatch) and
// CalibrateRotation.cpp (profiling calibration). Not part of the public API --
// nothing outside those two translation units should include this.
//
// The calibration routines mutate the same module state the motion path reads
// (neutral angle, counts-per-rev, speed profile, position), so that state is
// declared here rather than in an anonymous namespace.
#ifndef ACTION_BASE_ROTATE_INTERNAL_H
#define ACTION_BASE_ROTATE_INTERNAL_H

#include <Arduino.h>

namespace BaseRotateInternal {

  // ---- Hardware / geometry constants -------------------------------------
  constexpr int ENCODER_SIGN = -1;
  constexpr int AS5600_COUNTS_PER_REV = 4096;
  constexpr int DRIVE_GEAR_TEETH = 18;
  constexpr int BASE_GEAR_TEETH = 108;
  constexpr int BASE_STEPS_PER_REV = BASE_GEAR_TEETH * 2;
  // Fallback used for moves before calibration has ever run. Only an estimate:
  // the real per-direction counts come from calibration and are what every
  // calibrated move divides by.
  //
  // Derived from the gear ratio, and measured laps come in around 20000 --
  // roughly 18% under this figure, close enough to be a sane pre-calibration
  // guess. Do not tune this to a measured value; measure instead.
  constexpr long ESTIMATED_COUNTS_PER_BASE_REV =
      static_cast<long>(AS5600_COUNTS_PER_REV) * BASE_GEAR_TEETH / DRIVE_GEAR_TEETH;

  // ---- Timing / tolerance constants shared by both files ------------------
  constexpr unsigned long PROFILE_CALIBRATION_TIMEOUT_MS = 180000;
  constexpr unsigned long CALIBRATION_TIMEOUT_MS = 45000;
  constexpr unsigned long HOME_TIMEOUT_MS = 20000;
  constexpr unsigned long TRUE_NORTH_POLL_DELAY_MS = 1;
  // Contact bounce guard. A revolution is defined by the home switch alone --
  // pressed, released, released continuously for this long, pressed again --
  // so the only thing that has to be filtered is the switch chattering as its
  // contacts make and break. It is deliberately NOT a travel threshold.
  //
  // The previous design decided "is this press a new revolution?" by requiring
  // the base to have covered a minimum number of counts, derived from the
  // counts-per-rev that calibration had not measured yet. On a fresh robot that
  // fell back to a guessed 4096 which sits right on top of the true value, so
  // real revolutions landed on both sides of the bar and were accepted or
  // ignored essentially at random. Gating on the switch removes the circularity.
  constexpr unsigned long TRUE_NORTH_RELEASE_DEBOUNCE_MS = 40;
  constexpr long TARGET_TOLERANCE_COUNTS = 10;
  // Floor for a measured lap. Below this the encoder cannot have gone round at
  // all -- a dead magnet, an unpowered servo, or a home switch stuck closed.
  //
  // Deliberately a floor and not a band. An earlier version bracketed laps
  // against an expected value, which rejected the hardware's real answer twice:
  // 24576 was predicted from the gear ratio, then 4096 from the encoder's
  // native resolution, and the base actually measures about 20000. Calibration
  // exists to discover this number, so it must not presuppose it.
  constexpr long CALIBRATION_MIN_USABLE_COUNTS_PER_REV = 500;
  // Servo offset used to drive the base during calibration. Measured, not
  // guessed: at offset 10 the base reproducibly lost torque mid-revolution and
  // stalled at a different position each run, because 10 degrees sits at the
  // edge of a continuous-rotation servo's deadband. Counts-per-rev is a
  // geometric constant, so measuring it at a faster, torque-adequate speed is
  // equally valid and far more likely to finish.
  constexpr int CALIBRATION_DRIVE_OFFSET = 18;
  // Neutral used when no override is supplied. Calibration never learns or
  // persists a neutral: a hunted value from an aborted run used to be saved
  // and then weakened every later run's drive.
  constexpr int CALIBRATION_DEFAULT_NEUTRAL = 90;
  constexpr int CALIBRATION_NEUTRAL_MIN = 70;
  constexpr int CALIBRATION_NEUTRAL_MAX = 110;
  constexpr long NO_NEUTRAL_OVERRIDE = -1;
  // How often a long-running calibration wait publishes a progress snapshot
  // even when nothing happened. Without it, a hang looks identical to a slow
  // revolution from the wizard's side.
  constexpr unsigned long CALIBRATION_PROGRESS_HEARTBEAT_MS = 3000;
  constexpr size_t SPEED_PROFILE_COUNT = 5;

  extern const char* const SPEED_PROFILE_NAMES[SPEED_PROFILE_COUNT];
  extern const int SPEED_PROFILE_OFFSETS[SPEED_PROFILE_COUNT];

  enum BaseSpeed {
    BASE_VERY_SLOW = 5,
    BASE_SLOW = 10,
    BASE_REGULAR = 20,
    BASE_FAST = 30,
    BASE_SUPERFAST = 40
  };

  // ---- Mutable module state ----------------------------------------------
  // Owned by ActionBaseRotate.cpp; read and written by both files.
  extern int restingValue;
  extern long basePositionCounts;
  extern long encoderUnwrapped;
  extern long leftCountsPerRev;
  extern long rightCountsPerRev;
  extern bool calibrated;
  extern bool profileCalibrated;
  extern bool positionTrusted;
  extern unsigned long leftFullRevMs;
  extern unsigned long rightFullRevMs;
  extern int leftSpeedAngles[SPEED_PROFILE_COUNT];
  extern int rightSpeedAngles[SPEED_PROFILE_COUNT];

  // Calibration progress/telemetry, surfaced by buildStatusJson(). Several of
  // these are retained only because the wizard and MQTT status readers expect
  // the keys; they no longer drive any decision.
  extern int calibrationPasses;
  extern long calibrationLastDiffMs;
  extern bool calibrationBalanced;
  extern const char* calibrationPhase;
  extern bool calibrationLastPulseAccepted;
  extern unsigned long calibrationLastPulseMs;
  extern long calibrationLastPulseCounts;
  extern long calibrationLastPulseMinCounts;
  extern uint32_t calibrationIgnoredPulseCount;
  extern bool verySlowValidationPassed;

  // ---- Servo primitives ---------------------------------------------------
  int clampServoAngle(int angle);
  void writeServo(int angle);
  void driveServo(bool rotateLeft, BaseSpeed speed);
  void driveServoAngle(bool rotateLeft, int driveAngle);
  void stopServo();

  // ---- Encoder / true-north primitives ------------------------------------
  int readEncoderRaw();
  void resetEncoderTracking();
  long updateEncoderTracking();
  bool readTrueNorthPin();
  bool isTrueNorthPressed();
  uint32_t getTrueNorthHitCount();
  bool trueNorthHitDetected(uint32_t baselineCount);
  bool encoderIsStuck(long& lastObservedEncoderCounts, unsigned long& lastMovementMs);

  // ---- Motion primitives ---------------------------------------------------
  bool rotateUntilTrueNorth(bool rotateLeft, BaseSpeed speed,
                            unsigned long timeoutMs, const char*& error);

  // ---- Persistence ---------------------------------------------------------
  void saveLastPosition();
  void saveCalibration();
  void saveNeutralValue();

  // ---- Speed profile helpers ----------------------------------------------
  int speedIndex(BaseSpeed speed);
  void initFixedSpeedProfileAngles();
  int calibrationLeftAngle();
  int calibrationRightAngle();

  // ---- Shared math ---------------------------------------------------------
  long getAverageCountsPerRev();
  long signedCalibrationDiffMs(unsigned long leftMs, unsigned long rightMs);
}

// Entry points implemented in CalibrateRotation.cpp.
namespace CalibrateRotation {
  // Measures how many encoder counts make one full turn of the base, and
  // establishes true north as the zero position.
  //
  // Counts-per-rev is a property of the encoder and the gear train, not of
  // travel direction, so a single measurement serves both directions. The
  // per-direction fields kept in storage are both set from it: the wizard and
  // the MQTT readiness gate still read left and right separately, and any real
  // left/right difference is mechanical backlash, which wants a direction-change
  // offset rather than two different circle sizes.
  bool calibrateRotationProfile(long neutralOverride, const char*& error);

  // Single-direction entry point behind the CALIBRATE controlType. Measures in
  // the requested direction; stores the result for both.
  bool calibrateBase(bool rotateLeft, BaseRotateInternal::BaseSpeed speed,
                     const char*& error);

  // Measures one full base revolution at a fixed drive angle. A revolution runs
  // from one accepted true-north press to the next, where "accepted" means the
  // switch released and stayed released in between.
  bool measureFullRevolution(bool rotateLeft, int driveAngle, const char* phaseName,
                             unsigned long& revMs, long& countsPerRev, const char*& error);

  // Drives at a raw angle until true north is found, then zeroes the position.
  bool seekTrueNorthRaw(bool rotateLeft, int driveAngle,
                        unsigned long timeoutMs, const char*& error);
}

#endif // ACTION_BASE_ROTATE_INTERNAL_H
