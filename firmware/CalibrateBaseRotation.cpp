// CalibrateBaseRotation.cpp
#include "CalibrateBaseRotation.h"
#include "ActionBaseRotate.h"
#include <ArduinoJson.h>
#include <cstdlib>

namespace {
  bool extractLong(JsonVariantConst var, long &out) {
    if (var.is<int>()) {
      out = var.as<int>();
      return true;
    }
    if (var.is<long>()) {
      out = var.as<long>();
      return true;
    }
    if (var.is<const char*>()) {
      const char* s = var.as<const char*>();
      if (s && *s) {
        char* end = nullptr;
        long value = strtol(s, &end, 10);
        if (end && *end == '\0') {
          out = value;
          return true;
        }
      }
    }
    return false;
  }
}

bool CalibrateBaseRotation::calibrateProfile(const String& message, String& statusJson) {
  StaticJsonDocument<384> inDoc;
  if (deserializeJson(inDoc, message) != DeserializationError::Ok) {
    Serial.print("[Calibrate] JSON parse error: ");
    Serial.println(message);
    return false;
  }

  long neutralServoAngle = -1;
  if (!inDoc["neutralServoAngle"].isNull() &&
      (!extractLong(inDoc["neutralServoAngle"], neutralServoAngle) ||
       neutralServoAngle < 0 || neutralServoAngle > 180)) {
    Serial.println("[Calibrate] Invalid neutralServoAngle for base_rotation_profile");
    return false;
  }

  return ActionBaseRotate::calibrateProfile(statusJson, neutralServoAngle);
}
