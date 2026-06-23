#ifndef ACTION_SERVO_H
#define ACTION_SERVO_H

#include <Arduino.h>

namespace ActionServo {
  bool run(const String &message);
  void begin();
}

#endif // ACTION_SERVO_H
