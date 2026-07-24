// ActionGripper.cpp
#include "ActionGripper.h"
#include "Utility_Logger.h"
#include <ESP32Servo.h>     // <-- ESP32‐friendly
#include <ArduinoJson.h>
#include <Preferences.h>

namespace {
  // pins
  constexpr uint8_t GRIPPER_SERVO_PIN = 20;
  constexpr uint8_t STOP_PIN_1        = 45;
  constexpr uint8_t STOP_PIN_2        = 46;

  // internal state
  Servo  gripperServo;
  bool   inited = false;
  Preferences prefs;

  void initGripper() {
    if (inited) {
      Logger::debug("gripper", "initGripper: already initialized");
      return;
    }
    Logger::debug("gripper", "initGripper: starting initialization");
    prefs.begin("config", false);  // open NVS namespace "config" read/write
    gripperServo.attach(GRIPPER_SERVO_PIN);
    pinMode(STOP_PIN_1, INPUT_PULLUP);
    pinMode(STOP_PIN_2, INPUT_PULLUP);

    // Restore last position or default to 180
    int lastPos = (int)prefs.getFloat("GRIPPER_ANGLE", 180.0f);
    gripperServo.write(lastPos);
    Serial.print("Gripper initialized at position ");
    Serial.println(lastPos);
    Logger::debug("gripper", "initGripper: initialized at pos " + String(lastPos));
    inited = true;
  }

  bool handlePosition(int position, int speed) {
    Logger::debug("gripper", "handlePosition: pos=" + String(position) + " speed=" + String(speed));
    position = constrain(position, 0, 180);
    int current = gripperServo.read();
    Logger::debug("gripper", "handlePosition: current=" + String(current) + " target=" + String(position));
    if (current == position) {
      Logger::debug("gripper", "handlePosition: already at target");
      return true;
    }
    int step = (position > current) ? 1 : -1;
    Logger::debug("gripper", "handlePosition: moving from " + String(current) + " to " + String(position));
    for (int p = current; p != position; p += step) {
      gripperServo.write(p);
      delay(speed);
    }
    gripperServo.write(position);
    prefs.putFloat("GRIPPER_ANGLE", position);
    Serial.print("Gripper set to position ");
    Serial.println(position);
    Logger::debug("gripper", "handlePosition: complete at " + String(position));
    return true;
  }

  bool handleGrab() {
    Logger::debug("gripper", "handleGrab: starting GRAB");
    int currentPos = gripperServo.read();
    Logger::debug("gripper", "handleGrab: current pos=" + String(currentPos));

    gripperServo.write(180);
    prefs.putFloat("GRIPPER_ANGLE", 180);
    delay(10);
    Logger::debug("gripper", "handleGrab: opened to 180, starting close");

    Serial.println("GRAB command initiated.");
    for (int p = 180; p >= 0; --p) {
      // Debug every 30 degrees
      if (p % 30 == 0) {
        int pin1 = digitalRead(STOP_PIN_1);
        int pin2 = digitalRead(STOP_PIN_2);
        Logger::debug("gripper", "handleGrab: p=" + String(p) + " pin1=" + String(pin1) + " pin2=" + String(pin2));
      }

      if (digitalRead(STOP_PIN_1)==LOW || digitalRead(STOP_PIN_2)==LOW) {
        gripperServo.write(p);
        prefs.putFloat("GRIPPER_ANGLE", p);
        Serial.print("Button detected at ");
        Serial.println(p);
        Logger::debug("gripper", "handleGrab: button detected at " + String(p));
        return true;  // success - object grabbed
      }
      gripperServo.write(p);
      delay(5);
    }
    Serial.println("No button press; reopening gripper.");
    Logger::debug("gripper", "handleGrab: no button detected, reopening");
    gripperServo.write(180);
    prefs.putFloat("GRIPPER_ANGLE", 180);
    return false;  // failure - no object detected
  }

  bool handleDrop() {
    gripperServo.write(180);
    prefs.putFloat("GRIPPER_ANGLE", 180);
    Serial.println("Gripper dropped (opened).");
    return true;
  }

  bool handleSoftHold() {
    int p = gripperServo.read();
    Serial.print("SOFTHOLD starting at ");
    Serial.println(p);
    while ((digitalRead(STOP_PIN_1)==LOW || digitalRead(STOP_PIN_2)==LOW) && p < 180) {
      gripperServo.write(++p);
      delay(50);
    }
    prefs.putFloat("GRIPPER_ANGLE", p);
    Serial.print("SOFTHOLD complete at ");
    Serial.println(p);
    return true;
  }
}

bool ActionGripper::run(const String& message, String& statusJson, String& detailsKey) {
  (void)statusJson;
  (void)detailsKey;
  Logger::debug("gripper", "run: received message: " + message);
  initGripper();

  StaticJsonDocument<256> doc;
  if (deserializeJson(doc, message) != DeserializationError::Ok) {
    Serial.print("JSON parse error: ");
    Serial.println(message);
    Logger::debug("gripper", "run: JSON parse error");
    return false;
  }

  // pull cmd into its own var
  const char* cmd = doc["command"].as<const char*>();
  Logger::debug("gripper", "run: command=" + String(cmd ? cmd : "<null>"));

  if (cmd) {
    if      (strcmp(cmd, "GRAB")     == 0) {
      Logger::debug("gripper", "run: dispatching to handleGrab");
      return handleGrab();
    }
    else if (strcmp(cmd, "DROP")     == 0) {
      Logger::debug("gripper", "run: dispatching to handleDrop");
      return handleDrop();
    }
    else if (strcmp(cmd, "SOFTHOLD") == 0) {
      Logger::debug("gripper", "run: dispatching to handleSoftHold");
      return handleSoftHold();
    }
  }

  // fallback to position
  int pos   = doc["position"] | -1;
  int speed = doc["speed"]    | 10;
  Logger::debug("gripper", "run: checking position fallback pos=" + String(pos) + " speed=" + String(speed));
  if (pos >= 0) {
    Logger::debug("gripper", "run: dispatching to handlePosition");
    return handlePosition(pos, speed);
  } else {
    Serial.print("[Unknown Gripper Command] ");
    Serial.println(cmd ? cmd : "<none>");
    Logger::debug("gripper", "run: unknown command, returning false");
    return false;
  }
}

bool ActionGripper::grab() {
  initGripper();
  return handleGrab();
}

bool ActionGripper::drop() {
  initGripper();
  return handleDrop();
}
