// CalibrateValues.h
#pragma once

#include <Arduino.h>

// "calibrationvalues" action — reports every saved calibration value (arm
// angles, perch settings, IK hover points, base rotation profile, stencil
// offsets) read from Preferences. Doesn't need anything from the incoming
// message itself.
namespace CalibrateValues {
  // Always sets detailsKey to "calibrationvalues" on success (RequestFlow
  // uses it to decide the sendCompletedDetails key); left empty on failure.
  bool run(const String& message, String& statusJson, String& detailsKey);
}
