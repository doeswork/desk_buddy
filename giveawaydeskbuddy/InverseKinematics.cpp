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

// Current angles for tracking servo positions
static int currentElbowAngle = 90;
static int currentWristAngle = 90;

// -------------------------
// Calibration Data for 3-point line interpolation
// -------------------------
static bool hasCal = false;  // True if valid calibration exists

// Calibration points store distance (mm) and servo angles
static float dA = 0.0f;      // Distance for point A (start, 0 mm)
static int elbowA = 90;
static int wristA = 90;

static float dB = 0.0f;      // Distance for point B (middle)
static int elbowB = 90;
static int wristB = 90;

static float dC = 0.0f;      // Distance for point C (max reach)
static int elbowC = 90;
static int wristC = 90;

// -------------------------
// Direct (triangle math) method (fallback)
// -------------------------
static const float pivotHeight = 76.2; // 3.0 inches in mm
static void computeIK_Direct(float targetD, int &elbowAngle, int &wristAngle) {
  float dy = pivotHeight; // Assume target is on table (y=0), pivot at height
  if (fabsf(dy) < 0.0001f) {
    dy = 0.0001f;
  }
  float thetaRad = atanf(targetD / dy);
  float thetaDeg = thetaRad * (180.0f / PI);
  elbowAngle = 90 - (int)thetaDeg;
  wristAngle = 90 + (int)thetaDeg;
}

// -------------------------
// 3-point line interpolation method
// -------------------------
static void computeIK_Interpolated(float td, int &elbowAngle, int &wristAngle) {
  float e, w;
  if (td <= dA) {
    e = elbowA;
    w = wristA;
  } else if (td <= dB) {
    float fraction = (td - dA) / (dB - dA);
    e = elbowA + fraction * (elbowB - elbowA);
    w = wristA + fraction * (wristB - wristA);
  } else if (td <= dC) {
    float fraction = (td - dB) / (dC - dB);
    e = elbowB + fraction * (elbowC - elbowB);
    w = wristB + fraction * (wristC - wristB);
  } else {
    e = elbowC;
    w = wristC;
  }
  // Clamp angles to servo range
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
  if (!server.hasArg("d")) {
    server.send(400, "text/plain", "Missing d parameter.");
    return;
  }
  float td = server.arg("d").toFloat(); // Target distance in mm

  int eAngle = 90, wAngle = 90;
  if (hasCal) {
    computeIK_Interpolated(td, eAngle, wAngle);
  } else {
    computeIK_Direct(td, eAngle, wAngle);
  }
  ikServos[0].write(eAngle);
  ikServos[1].write(wAngle);
  currentElbowAngle = eAngle;
  currentWristAngle = wAngle;

  String response = "IK => (d=" + String(td) + " mm) => Elbow=" + String(eAngle) + ", Wrist=" + String(wAngle);
  Serial.println(response);
  server.send(200, "text/plain", response);
}

// -------------------------
// Endpoint: /calibrateIK
// -------------------------
static void handleCalibrateIK(WebServer &server) {
  server.sendHeader("Access-Control-Allow-Origin", "*");
  if (!server.hasArg("action")) {
    server.send(400, "text/plain", "Missing action parameter.");
    return;
  }
  String action = server.arg("action");
  if (action.equalsIgnoreCase("SAVE")) {
    if (!server.hasArg("point") || !server.hasArg("d")) {
      server.send(400, "text/plain", "Missing point or d parameter.");
      return;
    }
    String pt = server.arg("point");
    float d = server.arg("d").toFloat();
    int e = currentElbowAngle;
    int w = currentWristAngle;
    if (pt.equalsIgnoreCase("A")) {
      dA = d;
      elbowA = e;
      wristA = w;
      server.send(200, "text/plain", "Saved point A (start).");
    } else if (pt.equalsIgnoreCase("B")) {
      dB = d;
      elbowB = e;
      wristB = w;
      server.send(200, "text/plain", "Saved point B (middle).");
    } else if (pt.equalsIgnoreCase("C")) {
      dC = d;
      elbowC = e;
      wristC = w;
      server.send(200, "text/plain", "Saved point C (end).");
    } else {
      server.send(400, "text/plain", "Invalid point (must be A, B, or C).");
    }
  } else if (action.equalsIgnoreCase("FINISH")) {
    // Validate calibration points
    if (dA >= dB || dB >= dC) {
      server.send(400, "text/plain", "Calibration distances must be in order: 0 <= dA < dB < dC.");
      return;
    }
    prefs.putFloat("ik_dA", dA);
    prefs.putInt("ik_elbowA", elbowA);
    prefs.putInt("ik_wristA", wristA);

    prefs.putFloat("ik_dB", dB);
    prefs.putInt("ik_elbowB", elbowB);
    prefs.putInt("ik_wristB", wristB);

    prefs.putFloat("ik_dC", dC);
    prefs.putInt("ik_elbowC", elbowC);
    prefs.putInt("ik_wristC", wristC);

    hasCal = true;
    server.send(200, "text/plain", "Calibration finished and saved.");
  } else {
    server.send(400, "text/plain", "Unknown action (use SAVE or FINISH).");
  }
}

// -------------------------
// Endpoint: /calibrateServo
// -------------------------
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
    currentElbowAngle = angle;
  } else if (which.equalsIgnoreCase("wrist")) {
    ikServos[1].write(angle);
    currentWristAngle = angle;
  } else {
    server.send(400, "text/plain", "Unknown servo parameter.");
    return;
  }
  server.send(200, "text/plain", "Servo calibrated: " + which + " to " + String(angle));
}

// -------------------------
// Namespace Functions
// -------------------------
namespace InverseKinematics {

void begin() {
  // Attach servos and set to neutral position
  for (int i = 0; i < NUM_IK_SERVOS; i++) {
    ikServos[i].attach(ikServoPins[i]);
    ikServos[i].write(90);
  }
  currentElbowAngle = 90;
  currentWristAngle = 90;
  Serial.println("[InverseKinematics] Servos attached at 90°");

  // Load calibration data
  prefs.begin("ikCal", false);

  dA = prefs.getFloat("ik_dA", 0.0f);
  elbowA = prefs.getInt("ik_elbowA", 90);
  wristA = prefs.getInt("ik_wristA", 90);

  dB = prefs.getFloat("ik_dB", 0.0f);
  elbowB = prefs.getInt("ik_elbowB", 90);
  wristB = prefs.getInt("ik_wristB", 90);

  dC = prefs.getFloat("ik_dC", 0.0f);
  elbowC = prefs.getInt("ik_elbowC", 90);
  wristC = prefs.getInt("ik_wristC", 90);

  // Check if calibration exists
  if (dA < dB && dB < dC && (elbowA != 90 || wristA != 90 || elbowB != 90 || wristB != 90 || elbowC != 90 || wristC != 90)) {
    hasCal = true;
    Serial.println("[InverseKinematics] Calibration loaded; using line interpolation.");
  } else {
    hasCal = false;
    Serial.println("[InverseKinematics] No valid calibration; using direct method.");
  }
}

void registerEndpoints(WebServer &server) {
  server.on("/controlIK", HTTP_GET, [&server]() {
    handleControlIK(server);
  });
  server.on("/calibrateIK", HTTP_GET, [&server]() {
    handleCalibrateIK(server);
  });
  server.on("/calibrateServo", HTTP_GET, [&server]() {
    handleCalibrateServo(server);
  });
}

void handleClient() {
  // No per-loop processing needed
}

} // namespace InverseKinematics