#pragma once
#include <Arduino.h>

class ActionGripper {
public:
  // entry point: message is the raw JSON payload
  // returns true on success, false on failure
  static bool run(const String& message);
};
