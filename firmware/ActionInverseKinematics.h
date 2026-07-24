// ActionInverseKinematics.h
#pragma once
#include <Arduino.h>

namespace ActionInverseKinematics {
  // Parses movement commands ("controlik" action):
  // JSON field:
  //   "distance": float
  //   "z_height": float (optional, mm; default 0)
  // statusJson/detailsKey are left empty — ControlIK never has details to
  // report; RequestFlow sends a plain completed/failed for it.
  bool run(const String& message, String& statusJson, String& detailsKey);

  // Reusable helper for higher-level workflows. Set applyStoredOffset=false
  // when calibrating the stencil offset itself.
  bool moveTo(float distance, float zHeight = 0.0f, bool applyStoredOffset = true);
}
