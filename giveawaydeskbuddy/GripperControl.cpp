#include "GripperControl.h"
#include <ESP32Servo.h>

const int GRIPPER_SERVO_PIN = 20;
const int BUTTON_PIN_A      = 46;
const int BUTTON_PIN_B      = 45;

static Servo gripperServo;

// Close until a button is pressed
static void performGrab() {
  pinMode(BUTTON_PIN_A, INPUT_PULLUP);
  pinMode(BUTTON_PIN_B, INPUT_PULLUP);
  int pos = 180;
  while (pos > 0) {
    if (digitalRead(BUTTON_PIN_A) == LOW || digitalRead(BUTTON_PIN_B) == LOW) {
      break;
    }
    gripperServo.write(pos--);
    delay(20);
  }
  Serial.printf("[GripperControl] GRAB complete — final position %d\n", pos);
}

// Open fully
static void performDrop() {
  gripperServo.write(180);
  Serial.println("[GripperControl] DROP executed (position 180)");
}

namespace GripperControl {

void begin() {
  gripperServo.attach(GRIPPER_SERVO_PIN);
  gripperServo.write(180); // start open
  Serial.println("[GripperControl] Initialized");
}

void registerEndpoints(WebServer &server) {
  server.on("/controlGripper", HTTP_GET, [&server]() {
    server.sendHeader("Access-Control-Allow-Origin", "*");
    if (!server.hasArg("command")) {
      server.send(400, "text/plain", "Missing command parameter");
      return;
    }
    String cmd = server.arg("command");
    if (cmd.equalsIgnoreCase("GRAB")) {
      performGrab();
      server.send(200, "text/plain", "Gripper GRAB executed");
    } else if (cmd.equalsIgnoreCase("DROP")) {
      performDrop();
      server.send(200, "text/plain", "Gripper DROP executed");
    } else {
      server.send(400, "text/plain", "Unknown gripper command");
    }
  });
}

void handleClient() {
  // Nothing to do — endpoint handled by global server
}

} // namespace GripperControl
