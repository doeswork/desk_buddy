#include "Heartbeat.h"
#include "ActionOTA.h"
#include <Preferences.h>
#include <ArduinoJson.h>
#include <time.h>

namespace {
  bool heartbeat_enabled = true;
  constexpr unsigned long HEARTBEAT_INTERVAL = 4000;  // 1.6 seconds
  unsigned long lastHeartbeat = 0;

  // Callback functions
  PublishCallback publishCallback = nullptr;
  const char* statusTopic = nullptr;
  const char* heartbeatTopic = nullptr;
  String (*getTimestampCallback)() = nullptr;
}

void Heartbeat::init() {
  lastHeartbeat = 0;
}

void Heartbeat::enable() {
  heartbeat_enabled = true;
}

void Heartbeat::disable() {
  heartbeat_enabled = false;
}

bool Heartbeat::isEnabled() {
  return heartbeat_enabled;
}

bool Heartbeat::shouldSend() {
  if (!heartbeat_enabled) return false;
  unsigned long now = millis();
  return (now - lastHeartbeat >= HEARTBEAT_INTERVAL);
}

void Heartbeat::markSent() {
  lastHeartbeat = millis();
}

void Heartbeat::setPublishCallback(PublishCallback callback) {
  publishCallback = callback;
}

void Heartbeat::setStatusTopic(const char* topic) {
  statusTopic = topic;
}

void Heartbeat::setHeartbeatTopic(const char* topic) {
  heartbeatTopic = topic;
}

void Heartbeat::setTimestampCallback(String (*callback)()) {
  getTimestampCallback = callback;
}

void Heartbeat::send(bool simple) {
  if (!publishCallback || !statusTopic || !heartbeatTopic || !getTimestampCallback) {
    Serial.println("[Heartbeat] Error: Callbacks not set");
    return;
  }

  Preferences prefs;
  prefs.begin("config", true);  // open read-only

  // read raw float angles
  float b = prefs.getFloat("ELBOW_ANGLE", 0.0f);
  float e = prefs.getFloat("WRIST_ANGLE", 0.0f);
  float w = prefs.getFloat("TWIST_ANGLE", 0.0f);
  float g = prefs.getFloat("GRIPPER_ANGLE", 0.0f);

  // read saved hover JSON strings
  String hoverMin = prefs.getString("hover_over_min", "{}");
  String hoverMid = prefs.getString("hover_over_mid", "{}");
  String hoverMax = prefs.getString("hover_over_max", "{}");

  prefs.end();
  ActionOTA::OtaStatus otaStatus = ActionOTA::getStatus();

  Serial.print("[Heartbeat] Running firmware version: ");
  Serial.println(otaStatus.runningVersion);

  String ts = getTimestampCallback();

  // --- Main heartbeat ---
  {
    StaticJsonDocument<512> doc;
    doc["sender"]       = "firmware";
    doc["log"]          = "heartbeat";
    doc["time"]         = ts;
    doc["firmware_version"] = otaStatus.runningVersion;
    doc["desired_version"] = otaStatus.desiredVersion;
    doc["ota_state"] = otaStatus.otaState;
    doc["ota_update_required"] = otaStatus.updateRequired;
    doc["ELBOW_ANGLE"]  = b;
    doc["WRIST_ANGLE"]  = e;
    doc["TWIST_ANGLE"]  = w;
    doc["GRIPPER_ANGLE"] = g;

    String out;
    serializeJson(doc, out);
    if (publishCallback(heartbeatTopic, out)) {
      Serial.print("Heartbeat sent → ");
      Serial.println(out);
    } else {
      Serial.println("Heartbeat publish failed");
    }
  }

  // If simple heartbeat requested, return early
  if (simple) {
    return;
  }

  // --- Hover-over Minimum ---
  {
    StaticJsonDocument<512> doc;
    doc["sender"] = "firmware";
    doc["time"]   = ts;
    JsonObject minObj = doc.createNestedObject("hover_over_min");
    StaticJsonDocument<256> tmp;
    if (deserializeJson(tmp, hoverMin) == DeserializationError::Ok) {
      for (auto kv : tmp.as<JsonObject>()) {
        minObj[kv.key()] = kv.value();
      }
    }
    String out;
    serializeJson(doc, out);
    if (publishCallback(statusTopic, out)) {
      Serial.print("Hover-Over Min sent → ");
      Serial.println(out);
    } else {
      Serial.println("Hover-Over Min publish failed");
    }
  }

  // --- Hover-over Midpoint ---
  {
    StaticJsonDocument<512> doc;
    doc["sender"] = "firmware";
    doc["time"]   = ts;
    JsonObject midObj = doc.createNestedObject("hover_over_mid");
    StaticJsonDocument<256> tmp;
    if (deserializeJson(tmp, hoverMid) == DeserializationError::Ok) {
      for (auto kv : tmp.as<JsonObject>()) {
        midObj[kv.key()] = kv.value();
      }
    }
    String out;
    serializeJson(doc, out);
    if (publishCallback(statusTopic, out)) {
      Serial.print("Hover-Over Mid sent → ");
      Serial.println(out);
    } else {
      Serial.println("Hover-Over Mid publish failed");
    }
  }

  // --- Hover-over Maximum ---
  {
    StaticJsonDocument<512> doc;
    doc["sender"] = "firmware";
    doc["time"]   = ts;
    JsonObject maxObj = doc.createNestedObject("hover_over_max");
    StaticJsonDocument<256> tmp;
    if (deserializeJson(tmp, hoverMax) == DeserializationError::Ok) {
      for (auto kv : tmp.as<JsonObject>()) {
        maxObj[kv.key()] = kv.value();
      }
    }
    String out;
    serializeJson(doc, out);
    if (publishCallback(statusTopic, out)) {
      Serial.print("Hover-Over Max sent → ");
      Serial.println(out);
    } else {
      Serial.println("Hover-Over Max publish failed");
    }
  }
}
