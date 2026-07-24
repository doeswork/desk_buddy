#pragma once
#include <Arduino.h>

class ActionGripper {
public:
  // entry point: message is the raw JSON payload
  // returns true on success, false on failure.
  // statusJson/detailsKey are left empty — Gripper never has details to
  // report; RequestFlow sends a plain completed/failed for it.
  static bool run(const String& message, String& statusJson, String& detailsKey);

  // Reusable helpers for higher-level workflows.
  static bool grab();
  static bool drop();
};
