// ActionInverseKinematics.h
#pragma once
#include <Arduino.h>

namespace ActionInverseKinematics {
  // Parses movement commands ("controlik" action):
  // JSON field:
  //   "distance": float
  //   "z_height": float (optional, mm; default 0)
  bool run(const String& message);
}
