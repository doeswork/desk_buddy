#ifndef ACTION_SERVO_H
#define ACTION_SERVO_H

#include <Arduino.h>

namespace ActionServo {
  // statusJson/detailsKey are left empty — Servo never has details to
  // report; RequestFlow sends a plain completed/failed for it.
  bool run(const String &message, String& statusJson, String& detailsKey);
  void begin();
}

#endif // ACTION_SERVO_H
