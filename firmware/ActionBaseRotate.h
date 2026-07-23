// ActionBaseRotate.h
#ifndef ACTION_BASE_ROTATE_H
#define ACTION_BASE_ROTATE_H

#include <Arduino.h>

namespace ActionBaseRotate {
// Runs a base rotation command and serializes the current base_rotation
// status object into statusJson. Returns true when the command completed.
bool run(const String& message, String& statusJson);

// Runs the full base rotation self-profiling calibration and serializes the
// current base_rotation status object into statusJson. Pass -1 to use the
// saved/default neutral servo angle.
bool calibrateProfile(String& statusJson, long neutralServoAngle = -1);

// Helpers used by higher-level calibration workflows. targetAngleDegrees may
// be outside 0..360 and will be normalized. Set applyStoredOffset=false when
// calibrating the offset itself.
bool moveToAbsoluteAngle(float targetAngleDegrees,
                         const char* speedLabel,
                         bool applyStoredOffset,
                         String& statusJson);
bool homeToTrueNorth(const char* directionLabel,
                     const char* speedLabel,
                     String& statusJson);
bool isAbsoluteAngleReady(String& reason);
}

#endif  // ACTION_BASE_ROTATE_H
