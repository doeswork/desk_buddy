#pragma once

#include <Arduino.h>

class ActionPerch {
public:
  // Returns false only when the incoming JSON fails to parse (arm is not
  // moved in that case); true otherwise. statusJson/detailsKey are left
  // empty — Perch never has details to report; RequestFlow sends a plain
  // completed/failed for it.
  static bool run(const String& message, String& statusJson, String& detailsKey);
};
