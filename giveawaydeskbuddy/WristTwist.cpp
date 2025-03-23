#include "WristTwist.h"
#include <ESP32Servo.h>

#define WRIST_TWIST_SERVO_PIN 33

static Servo twistServo;

namespace WristTwist {

void begin() {
  twistServo.attach(WRIST_TWIST_SERVO_PIN);
  twistServo.write(90);  // Neutral starting angle
  Serial.println("[WristTwist] Initialized");
}

void registerEndpoints(WebServer &server) {
  server.on("/controlWristTwist", HTTP_GET, [&server]() {
    server.sendHeader("Access-Control-Allow-Origin", "*");
    if (!server.hasArg("angle")) {
      server.send(400, "text/plain", "Missing angle parameter.");
      return;
    }
    int angle = server.arg("angle").toInt();
    if (angle < 0 || angle > 180) {
      server.send(400, "text/plain", "Angle out of range (0–180).");
      return;
    }
    twistServo.write(angle);
    String response = "Wrist twist set to angle " + String(angle);
    Serial.println("[WristTwist] " + response);
    server.send(200, "text/plain", response);
  });
}

void handleClient() {
  // No continuous logic required
}

} // namespace WristTwist
