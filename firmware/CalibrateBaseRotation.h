// CalibrateBaseRotation.h
#pragma once

#include <Arduino.h>

// calibration_type == "base_rotation_profile" handler, split out of
// CalibrateController.cpp since it's structurally unrelated to the
// perch/hover legacy calibration types (it just validates one optional
// field and delegates straight to ActionBaseRotate::calibrateProfile).
namespace CalibrateBaseRotation {
  // Delegates to ActionBaseRotate::calibrateProfile. statusJson is
  // populated with the base_rotation report on success; returns false
  // (statusJson may be empty) if neutralServoAngle is present but invalid,
  // or if the underlying calibration itself fails.
  bool calibrateProfile(const String& message, String& statusJson);
}
