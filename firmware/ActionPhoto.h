// ActionPhoto.h

#pragma once

#include <Arduino.h>

class ActionPhoto {
public:
  static void initializeCamera();
  static void run(const String &message, int useModel = -1);
};
