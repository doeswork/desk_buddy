// ActionPhoto.h

#pragma once

#include <Arduino.h>

class ActionPhoto {
public:
  static void initializeCamera();
  static bool run(const String &message, String& errorDetails, int useModel = -1);
};
