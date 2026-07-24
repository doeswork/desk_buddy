#pragma once
#include <Arduino.h>

namespace CalibrateStencil {
  // Always sets detailsKey to "stencil_calibration" (RequestFlow uses it to
  // decide the sendCompletedDetails key).
  bool run(const String& message, String& statusJson, String& detailsKey);
}
