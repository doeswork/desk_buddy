#include "InverseKinematics.h"
#include <ESP32Servo.h>
#include <WebServer.h>
#include <math.h>

#define NUM_IK_SERVOS 2
// Pin assignments: elbow on pin 21, wrist on pin 39.
const int ikServoPins[NUM_IK_SERVOS] = {21, 39};

static Servo ikServos[NUM_IK_SERVOS];

// The elbow pivot is fixed at 3" above the table.
const float pivotHeight = 3.0;

// Computes a right-triangle solution:
// Given horizontal offset (x) and height (y) above table,
// compute angles so that when x=0 both servos are at 90°.
static void computeIK(float targetX, float targetY, int &elbowAngle, int &wristAngle) {
  float dy = targetY - pivotHeight;
  if (dy == 0) { 
    dy = 0.001; // Prevent division by zero.
  }
  float thetaRad = atan(targetX / dy);
  float thetaDeg = thetaRad * (180.0 / PI);
  elbowAngle = 90 - (int)thetaDeg;
  wristAngle = 90 + (int)thetaDeg;
}

namespace InverseKinematics {

void begin() {
  for (int i = 0; i < NUM_IK_SERVOS; i++) {
    ikServos[i].attach(ikServoPins[i]);
    ikServos[i].write(90);  // Start at neutral.
  }
  Serial.println("[InverseKinematics] Initialized (servos attached at 90°)");
}

void registerEndpoints(WebServer &server) {
  server.on("/controlIK", HTTP_GET, [&server]() {
    server.sendHeader("Access-Control-Allow-Origin", "*");
    if (!server.hasArg("x") || !server.hasArg("y")) {
      server.send(400, "text/plain", "Missing x and/or y parameter.");
      return;
    }
    float targetX = server.arg("x").toFloat();
    float targetY = server.arg("y").toFloat();
    int elbowAngle = 90, wristAngle = 90;
    computeIK(targetX, targetY, elbowAngle, wristAngle);
    ikServos[0].write(elbowAngle);
    ikServos[1].write(wristAngle);
    String response = "IK executed: Target (x=" + String(targetX) +
                      ", y=" + String(targetY) +
                      ") => Elbow=" + String(elbowAngle) +
                      ", Wrist=" + String(wristAngle);
    Serial.println(response);
    server.send(200, "text/plain", response);
  });
}

void handleClient() {
  // Nothing extra needed.
}

} // namespace InverseKinematics
