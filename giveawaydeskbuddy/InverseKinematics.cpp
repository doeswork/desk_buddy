#include "InverseKinematics.h"
#include <ESP32Servo.h>
#include <WebServer.h>
#include <math.h>
#include <Preferences.h>

// -------------------------
// Basic Setup
// -------------------------
#define NUM_IK_SERVOS 3  // Increased from 2 to 3 to include twist servo
// Pin assignments: elbow on 21, wrist on 39, twist on 19
static const int ikServoPins[NUM_IK_SERVOS] = {21, 39, 19};

static Servo ikServos[NUM_IK_SERVOS];
static Preferences prefs;

// Current angles for tracking servo positions
static int currentElbowAngle = 90;
static int currentWristAngle = 90;
static int currentTwistAngle = 90;  // Added for twist servo

// Motion control parameters
static const int MOVE_STEPS = 20;     // Number of steps for smooth movement
static const int STEP_DELAY_MS = 10;  // Delay between steps (ms)

// -------------------------
// Calibration Data for 3-point line interpolation
// -------------------------
static bool hasCal = false;  // True if valid calibration exists

static float dA = 0.0f;      // Distance for point A (start, 0 mm)
static int elbowA = 90;
static int wristA = 90;
static int twistA = 90;      // Added twist angle for point A

static float dB = 0.0f;      // Distance for point B (middle)
static int elbowB = 90;
static int wristB = 90;
static int twistB = 90;      // Added twist angle for point B

static float dC = 0.0f;      // Distance for point C (max reach)
static int elbowC = 90;
static int wristC = 90;
static int twistC = 90;      // Added twist angle for point C

// -------------------------
// Direct (triangle math) method (fallback)
// -------------------------
static const float pivotHeight = 76.2; // 3.0 inches in mm
static void computeIK_Direct(float targetD, int &elbowAngle, int &wristAngle, int &twistAngle) {
  float dy = pivotHeight;
  if (fabsf(dy) < 0.0001f) {
    dy = 0.0001f;
  }
  float thetaRad = atanf(targetD / dy);
  float thetaDeg = thetaRad * (180.0f / PI);
  elbowAngle = 90 - (int)thetaDeg;
  wristAngle = 90 + (int)thetaDeg;
  twistAngle = 90;  // Default twist angle when no calibration exists
}

// -------------------------
// 3-point line interpolation method
// -------------------------
static void computeIK_Interpolated(float td, int &elbowAngle, int &wristAngle, int &twistAngle) {
  float e, w, t;
  if (td <= dA) {
    e = elbowA;
    w = wristA;
    t = twistA;
  } else if (td <= dB) {
    float fraction = (td - dA) / (dB - dA);
    e = elbowA + fraction * (elbowB - elbowA);
    w = wristA + fraction * (wristB - wristA);
    t = twistA + fraction * (twistB - twistA);
  } else if (td <= dC) {
    float fraction = (td - dB) / (dC - dB);
    e = elbowB + fraction * (elbowC - elbowB);
    w = wristB + fraction * (wristC - wristB);
    t = twistB + fraction * (twistC - twistB);
  } else {
    e = elbowC;
    w = wristC;
    t = twistC;
  }
  // Clamp angles to servo range (0-180 degrees)
  if (e < 0) e = 0; else if (e > 180) e = 180;
  if (w < 0) w = 0; else if (w > 180) w = 180;
  if (t < 0) t = 0; else if (t > 180) t = 180;
  elbowAngle = (int)e;
  wristAngle = (int)w;
  twistAngle = (int)t;
}

// -------------------------
// Function to move servos simultaneously with initial elbow lift
// -------------------------
static void moveServosSmoothly(int targetElbow, int targetWrist, int targetTwist) {
  // Step 1: Quickly lift the elbow by -5 degrees
  int liftElbow = currentElbowAngle - 40;
  if (liftElbow < 0) liftElbow = 0; // Clamp to minimum servo limit
  ikServos[0].write(liftElbow);     // Move elbow to lifted position
  delay(100);                       // Small delay (e.g., 100ms) to let it settle

  // Step 2: Set starting positions for smooth movement
  // Elbow starts from the lifted position, wrist and twist from current positions
  int startElbow = liftElbow;
  int startWrist = currentWristAngle;
  int startTwist = currentTwistAngle;

  // Calculate step sizes for interpolation
  float elbowStep = (float)(targetElbow - startElbow) / MOVE_STEPS;
  float wristStep = (float)(targetWrist - startWrist) / MOVE_STEPS;
  float twistStep = (float)(targetTwist - startTwist) / MOVE_STEPS;

  // Perform smooth movement
  for (int i = 0; i <= MOVE_STEPS; i++) {
    int elbowPos = startElbow + (int)(elbowStep * i);
    int wristPos = startWrist + (int)(wristStep * i);
    int twistPos = startTwist + (int)(twistStep * i);
    ikServos[0].write(elbowPos); // Elbow
    ikServos[1].write(wristPos); // Wrist
    ikServos[2].write(twistPos); // Twist
    delay(STEP_DELAY_MS);
  }

  // Set final positions exactly
  ikServos[0].write(targetElbow);
  ikServos[1].write(targetWrist);
  ikServos[2].write(targetTwist);

  // Update current angles
  currentElbowAngle = targetElbow;
  currentWristAngle = targetWrist;
  currentTwistAngle = targetTwist;
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

  int eAngle = 90, wAngle = 90, tAngle = 90;
  if (hasCal) {
    computeIK_Interpolated(td, eAngle, wAngle, tAngle);
  } else {
    computeIK_Direct(td, eAngle, wAngle, tAngle);
  }
  
  moveServosSmoothly(eAngle, wAngle, tAngle);

  String response = "IK => (d=" + String(td) + " mm) => Elbow=" + String(eAngle) + ", Wrist=" + String(wAngle) + ", Twist=" + String(tAngle);
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
    int t = currentTwistAngle;  // Capture twist angle
    if (pt.equalsIgnoreCase("A")) {
      dA = d;
      elbowA = e;
      wristA = w;
      twistA = t;
      server.send(200, "text/plain", "Saved point A (start).");
    } else if (pt.equalsIgnoreCase("B")) {
      dB = d;
      elbowB = e;
      wristB = w;
      twistB = t;
      server.send(200, "text/plain", "Saved point B (middle).");
    } else if (pt.equalsIgnoreCase("C")) {
      dC = d;
      elbowC = e;
      wristC = w;
      twistC = t;
      server.send(200, "text/plain", "Saved point C (end).");
    } else {
      server.send(400, "text/plain", "Invalid point (must be A, B, or C).");
    }
  } else if (action.equalsIgnoreCase("FINISH")) {
    if (dA >= dB || dB >= dC) {
      server.send(400, "text/plain", "Calibration distances must be in order: 0 <= dA < dB < dC.");
      return;
    }
    prefs.putFloat("ik_dA", dA);
    prefs.putInt("ik_elbowA", elbowA);
    prefs.putInt("ik_wristA", wristA);
    prefs.putInt("ik_twistA", twistA);  // Store twist angle

    prefs.putFloat("ik_dB", dB);
    prefs.putInt("ik_elbowB", elbowB);
    prefs.putInt("ik_wristB", wristB);
    prefs.putInt("ik_twistB", twistB);  // Store twist angle

    prefs.putFloat("ik_dC", dC);
    prefs.putInt("ik_elbowC", elbowC);
    prefs.putInt("ik_wristC", wristC);
    prefs.putInt("ik_twistC", twistC);  // Store twist angle

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
  } else if (which.equalsIgnoreCase("twist")) {
    ikServos[2].write(angle);
    currentTwistAngle = angle;
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
  for (int i = 0; i < NUM_IK_SERVOS; i++) {
    ikServos[i].attach(ikServoPins[i]);
    ikServos[i].write(90);
  }
  currentElbowAngle = 90;
  currentWristAngle = 90;
  currentTwistAngle = 90;  // Initialize twist angle
  Serial.println("[InverseKinematics] Servos attached at 90°");

  prefs.begin("ikCal", false);

  dA = prefs.getFloat("ik_dA", 0.0f);
  elbowA = prefs.getInt("ik_elbowA", 90);
  wristA = prefs.getInt("ik_wristA", 90);
  twistA = prefs.getInt("ik_twistA", 90);  // Load twist angle

  dB = prefs.getFloat("ik_dB", 0.0f);
  elbowB = prefs.getInt("ik_elbowB", 90);
  wristB = prefs.getInt("ik_wristB", 90);
  twistB = prefs.getInt("ik_twistB", 90);  // Load twist angle

  dC = prefs.getFloat("ik_dC", 0.0f);
  elbowC = prefs.getInt("ik_elbowC", 90);
  wristC = prefs.getInt("ik_wristC", 90);
  twistC = prefs.getInt("ik_twistC", 90);  // Load twist angle

  if (dA < dB && dB < dC && (elbowA != 90 || wristA != 90 || twistA != 90 ||
                             elbowB != 90 || wristB != 90 || twistB != 90 ||
                             elbowC != 90 || wristC != 90 || twistC != 90)) {
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