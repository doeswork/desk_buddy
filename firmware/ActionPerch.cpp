#include "ActionPerch.h"
#include "ArmServos.h"
#include <Preferences.h>
#include <ArduinoJson.h>
#include <Arduino.h>

namespace {
  struct PerchSettings {
    int   elbow;
    int   wrist;
    int   twist;
    float minDist;
    float midDist;
    float maxDist;
  };

  PerchSettings loadSettings() {
    Preferences prefs;
    prefs.begin("config", true);
    PerchSettings s;
    // Use short storage keys (<=15 chars), fall back to legacy names if present
    s.elbow   = static_cast<int>(prefs.getFloat("p_elbow", prefs.getFloat("PERCH_ELBOW_ANGLE", 120.0f)));
    s.wrist   = static_cast<int>(prefs.getFloat("p_wrist", prefs.getFloat("PERCH_WRIST_ANGLE",  90.0f)));
    s.twist   = static_cast<int>(prefs.getFloat("p_twist", prefs.getFloat("PERCH_TWIST_ANGLE",  90.0f)));
    s.minDist = prefs.getFloat("p_min",   prefs.getFloat("PERCH_MIN", 0.0f));
    s.midDist = prefs.getFloat("p_mid",   prefs.getFloat("PERCH_MID", 50.0f));
    s.maxDist = prefs.getFloat("p_max",   prefs.getFloat("PERCH_MAX", 100.0f));
    prefs.end();
    return s;
  }
}

void ActionPerch::run(const String& message) {
  ArmServos::begin();

  Serial.println("[Perch] start");

  StaticJsonDocument<128> doc;
  if (deserializeJson(doc, message) != DeserializationError::Ok) {
    Serial.print("[Perch] JSON parse error: ");
    Serial.println(message);
  }

  auto settings = loadSettings();
  Serial.printf("[Perch] Moving to ELBOW=%d WRIST=%d TWIST=%d (MIN=%.2f MID=%.2f MAX=%.2f)\n",
                settings.elbow, settings.wrist, settings.twist,
                settings.minDist, settings.midDist, settings.maxDist);

  ArmServos::moveTo(ArmServos::Wrist, settings.wrist);
  ArmServos::moveTo(ArmServos::Elbow, settings.elbow);
  ArmServos::moveTo(ArmServos::Twist, settings.twist);

  Serial.println("[Perch] done");
}
