#include "InverseKinematics.h"
#include <ESP32Servo.h>
#include <WebServer.h>
#include <math.h>
#include <Preferences.h>

// -------------------------
// Basic Setup
// -------------------------
#define NUM_IK_SERVOS 2
// Pin assignments: elbow on pin 21, wrist on pin 39.
static const int ikServoPins[NUM_IK_SERVOS] = {21, 39};

static Servo ikServos[NUM_IK_SERVOS];
static Preferences prefs;

// -------------------------
// Direct (triangle math) method
// -------------------------
static const float pivotHeight = 3.0; // Fixed pivot height (inches)
static void computeIK_Direct(float targetX, float targetY, int &elbowAngle, int &wristAngle) {
  float dy = targetY - pivotHeight;
  if (fabsf(dy) < 0.0001f) { 
    dy = 0.0001f;
  }
  float thetaRad = atanf(targetX / dy);
  float thetaDeg = thetaRad * (180.0f / PI);
  elbowAngle = 90 - (int)thetaDeg;
  wristAngle = 90 + (int)thetaDeg;
}

// -------------------------
// Calibration Data for 3-point interpolation
// -------------------------
static bool hasCal = false;  // True if valid calibration exists

// For each calibration point (A, B, C), we store servo angles...
static int elbowA = 90, wristA = 90;
static int elbowB = 90, wristB = 90;
static int elbowC = 90, wristC = 90;

// And their corresponding (x,y) in inches.
static float xA = 0.0f, yA = 3.0f;
static float xB = 0.0f, yB = 3.0f;
static float xC = 0.0f, yC = 3.0f;

// -------------------------
// 3-point (barycentric) interpolation method
// -------------------------
static void computeIK_Interpolated(float tx, float ty, int &elbowAngle, int &wristAngle) {
  float denom = (yB - yC)*(xA - xC) + (xC - xB)*(yA - yC);
  if (fabsf(denom) < 0.0001f) {
    computeIK_Direct(tx, ty, elbowAngle, wristAngle);
    return;
  }
  float alpha = ((yB - yC)*(tx - xC) + (xC - xB)*(ty - yC)) / denom;
  float beta  = ((yC - yA)*(tx - xC) + (xA - xC)*(ty - yC)) / denom;
  float gamma = 1.0f - alpha - beta;
  if (alpha < 0.f || beta < 0.f || gamma < 0.f || alpha > 1.f || beta > 1.f || gamma > 1.f) {
    computeIK_Direct(tx, ty, elbowAngle, wristAngle);
    return;
  }
  float e = alpha * elbowA + beta * elbowB + gamma * elbowC;
  float w = alpha * wristA + beta * wristB + gamma * wristC;
  if (e < 0) e = 0; else if (e > 180) e = 180;
  if (w < 0) w = 0; else if (w > 180) w = 180;
  elbowAngle = (int)e;
  wristAngle = (int)w;
}

// -------------------------
// Endpoint: /controlIK
// -------------------------
static void handleControlIK(WebServer &server) {
  server.sendHeader("Access-Control-Allow-Origin", "*");
  if (!server.hasArg("x") || !server.hasArg("y")) {
    server.send(400, "text/plain", "Missing x and/or y parameter.");
    return;
  }
  float tx = server.arg("x").toFloat();
  float ty = server.arg("y").toFloat();
  
  int eAngle = 90, wAngle = 90;
  if (hasCal) {
    computeIK_Interpolated(tx, ty, eAngle, wAngle);
  } else {
    computeIK_Direct(tx, ty, eAngle, wAngle);
  }
  ikServos[0].write(eAngle);
  ikServos[1].write(wAngle);
  
  String response = "IK => (x=" + String(tx) + ", y=" + String(ty) + 
                    ") => Elbow=" + String(eAngle) + ", Wrist=" + String(wAngle);
  Serial.println(response);
  server.send(200, "text/plain", response);
}

// -------------------------
// Endpoint: /calibrateIK
// -------------------------
// For saving calibration points (action=SAVE, point=A|B|C) and finishing (action=FINISH)
static void handleCalibrateIK(WebServer &server) {
  server.sendHeader("Access-Control-Allow-Origin", "*");
  if (!server.hasArg("action")) {
    server.send(400, "text/plain", "Missing action parameter.");
    return;
  }
  String action = server.arg("action");
  if (action.equalsIgnoreCase("SAVE")) {
    if (!server.hasArg("point") || !server.hasArg("elbow") || 
        !server.hasArg("wrist") || !server.hasArg("X") || !server.hasArg("Y")) {
      server.send(400, "text/plain", "Missing parameters for SAVE.");
      return;
    }
    String pt = server.arg("point");
    int e = server.arg("elbow").toInt();
    int w = server.arg("wrist").toInt();
    float xx = server.arg("X").toFloat();
    float yy = server.arg("Y").toFloat();
    if (pt.equalsIgnoreCase("A")) {
      elbowA = e; wristA = w; xA = xx; yA = yy;
      server.send(200, "text/plain", "Saved point A.");
    } else if (pt.equalsIgnoreCase("B")) {
      elbowB = e; wristB = w; xB = xx; yB = yy;
      server.send(200, "text/plain", "Saved point B.");
    } else if (pt.equalsIgnoreCase("C")) {
      elbowC = e; wristC = w; xC = xx; yC = yy;
      server.send(200, "text/plain", "Saved point C.");
    } else {
      server.send(400, "text/plain", "Invalid point (must be A, B, or C).");
    }
  }
  else if (action.equalsIgnoreCase("FINISH")) {
    prefs.putInt("ik_elbowA", elbowA);
    prefs.putInt("ik_wristA", wristA);
    prefs.putFloat("ik_xA", xA);
    prefs.putFloat("ik_yA", yA);
    
    prefs.putInt("ik_elbowB", elbowB);
    prefs.putInt("ik_wristB", wristB);
    prefs.putFloat("ik_xB", xB);
    prefs.putFloat("ik_yB", yB);
    
    prefs.putInt("ik_elbowC", elbowC);
    prefs.putInt("ik_wristC", wristC);
    prefs.putFloat("ik_xC", xC);
    prefs.putFloat("ik_yC", yC);
    
    hasCal = true;
    server.send(200, "text/plain", "Calibration finished and saved.");
  }
  else {
    server.send(400, "text/plain", "Unknown action (use SAVE or FINISH).");
  }
}

// -------------------------
// Endpoint: /calibrateServo
// -------------------------
// For live moving of the servos via calibration sliders.
static void handleCalibrateServo(WebServer &server) {
  server.sendHeader("Access-Control-Allow-Origin", "*");
  if (!server.hasArg("servo") || !server.hasArg("angle")) {
    server.send(400, "text/plain", "Missing servo or angle parameter.");
    return;
  }
  String which = server.arg("servo");
  int angle = server.arg("angle").toInt();
  if (angle < 0 || angle > 180) {
    server.send(400, "text/plain", "Angle out of range (0-180).");
    return;
  }
  if (which.equalsIgnoreCase("elbow")) {
    ikServos[0].write(angle);
  }
  else if (which.equalsIgnoreCase("wrist")) {
    ikServos[1].write(angle);
  }
  else {
    server.send(400, "text/plain", "Unknown servo parameter.");
    return;
  }
  server.send(200, "text/plain", "Calibrate servo OK");
}

// -------------------------
// registerEndpoints()
// -------------------------
namespace InverseKinematics {

void begin() {
  // Attach servos.
  for (int i = 0; i < NUM_IK_SERVOS; i++) {
    ikServos[i].attach(ikServoPins[i]);
    ikServos[i].write(90);
  }
  Serial.println("[InverseKinematics] Servos attached at 90°");

  // Open Preferences for calibration storage.
  prefs.begin("ikCal", false);

  // Load calibration data.
  elbowA = prefs.getInt("ik_elbowA", 90);
  wristA = prefs.getInt("ik_wristA", 90);
  xA = prefs.getFloat("ik_xA", 0.f);
  yA = prefs.getFloat("ik_yA", 3.f);

  elbowB = prefs.getInt("ik_elbowB", 90);
  wristB = prefs.getInt("ik_wristB", 90);
  xB = prefs.getFloat("ik_xB", 0.f);
  yB = prefs.getFloat("ik_yB", 3.f);

  elbowC = prefs.getInt("ik_elbowC", 90);
  wristC = prefs.getInt("ik_wristC", 90);
  xC = prefs.getFloat("ik_xC", 0.f);
  yC = prefs.getFloat("ik_yC", 3.f);

  // If any calibration value is not the default, we consider calibration valid.
  if ((elbowA != 90) || (wristA != 90) ||
      (elbowB != 90) || (wristB != 90) ||
      (elbowC != 90) || (wristC != 90)) {
    hasCal = true;
    Serial.println("[InverseKinematics] Calibration loaded; using 3-point interpolation.");
  }
  else {
    hasCal = false;
    Serial.println("[InverseKinematics] No calibration found; using direct triangle math.");
  }
}

void registerEndpoints(WebServer &server) {
  // /controlIK endpoint
  server.on("/controlIK", HTTP_GET, [&server](){
    handleControlIK(server);
  });
  // /calibrateIK endpoint for saving calibration points
  server.on("/calibrateIK", HTTP_GET, [&server](){
    handleCalibrateIK(server);
  });
  // /calibrateServo endpoint for live servo control during calibration
  server.on("/calibrateServo", HTTP_GET, [&server](){
    handleCalibrateServo(server);
  });
}

void handleClient() {
  // No per-loop processing needed.
}

} // namespace InverseKinematics
