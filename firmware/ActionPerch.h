#pragma once

#include <Arduino.h>

class ActionPerch {
public:
  // Returns false when the request is malformed or any guarded servo move is
  // refused. Callers must not report a completed perch after a partial pose.
  static bool run(const String& message);
};
