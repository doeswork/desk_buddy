        #pragma once

#include <Arduino.h>

namespace ArmServos {
  enum Joint {
    Elbow = 0,
    Wrist = 1,
    Twist = 2
  };

  void begin();
  int getAngle(Joint joint);
  bool moveTo(Joint joint, int target);
  bool moveToLive(Joint joint, int target);
}
