// ActionServo.cpp
#include "ActionServo.h"
#include "ArmServos.h"
#include <ArduinoJson.h>
#include <Arduino.h>

void ActionServo::begin() {
  ArmServos::begin();
}

bool ActionServo::run(const String &message) {
  ArmServos::begin();

  StaticJsonDocument<256> doc;
  if (deserializeJson(doc, message) != DeserializationError::Ok) {
    Serial.print("[Servo] JSON error: ");
    Serial.println(message);
    return false;
  }

  const char *name = doc["servoName"].as<const char*>();
  if (!name) {
    Serial.println("[Servo] Missing servoName");
    return false;
  }

  int position = doc["position"] | -1;
  if (position < 0 || position > 180) {
    Serial.print("[Servo] Invalid position: ");
    Serial.println(position);
    return false;
  }

  ArmServos::Joint joint;
  if      (strcasecmp(name, "ELBOW") == 0) joint = ArmServos::Elbow;
  else if (strcasecmp(name, "WRIST") == 0) joint = ArmServos::Wrist;
  else if (strcasecmp(name, "TWIST") == 0) joint = ArmServos::Twist;
  else {
    Serial.print("[Servo] Unknown servoName: ");
    Serial.println(name);
    return false;
  }

  // Check if this is a "live" action - bypass safety guards
  String actionId;
  if (doc["action_id"].is<const char*>()) {
    actionId = doc["action_id"].as<const char*>();
  } else if (doc["action_id"].is<long>()) {
    actionId = String(doc["action_id"].as<long>());
  }

  bool isLive = (actionId == "live");

  if (isLive) {
    if (!ArmServos::moveToLive(joint, position)) {
      Serial.print("[Servo] Live move failed to ");
      Serial.println(position);
      return false;
    }
    Serial.print("[Servo] live moved to ");
    Serial.println(position);
  } else {
    if (!ArmServos::moveTo(joint, position)) {
      Serial.print("[Servo] Move blocked to ");
      Serial.println(position);
      return false;
    }
    Serial.print("[Servo] moved to ");
    Serial.println(position);
  }
  return true;
}
