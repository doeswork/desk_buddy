// CalibrateController.h
#pragma once

#include <Arduino.h>

// Legacy perch/hover calibration operations, triggered by the "calibrate"
// action's calibration_type field. No MQTT knowledge here — run() returns
// bool + out-params and lets Network_RequestFlow's generic runAction()
// translate the result into the wire reply, same shape every other
// Action*/Calibrate* handler uses. The third calibration_type kind,
// "base_rotation_profile", lives in CalibrateBaseRotation.h/.cpp — run()
// classifies internally and delegates to it for that case.
namespace CalibrateController {
  // Classifies+dispatches on calibration_type (read from message itself),
  // covering all three legacy calibration_type kinds (base_rotation_profile,
  // perch_*, hover_*) plus the unknown-type case. detailsKey is set to the
  // matching sendCompletedDetails key on success (e.g. "base_rotation",
  // "PERCH_ELBOW_ANGLE", or the raw hover type string); left empty on
  // failure or an unrecognized calibration_type.
  bool run(const String& message, String& statusJson, String& detailsKey);
}
