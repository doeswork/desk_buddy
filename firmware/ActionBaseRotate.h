// ActionRotate.h
#ifndef ACTION_BASE_ROTATE_H
#define ACTION_BASE_ROTATE_H

#include <Arduino.h>

namespace ActionBaseRotate {
  // message JSON must include:
  //   controlType: "MAGNET" | "ENCODER"
  //   direction:   "LEFT"   | "RIGHT"
  //   speed:       "veryslow"|"slow"|"regular"|"fast"|"superfast"
  //   value:       int  (number of magnets or encoder steps)
  // Returns the current origin magnet (1-12) after processing the command.
  int run(const String& message);

  // Reads/writes the persisted origin magnet (1-12). If the incoming JSON
  // contains "origin_magnet" or "value", the stored magnet is updated.
  int handleOriginMagnet(const String& message);
  int getOriginMagnet();
}

#endif // ACTION_BASE_ROTATE_H
