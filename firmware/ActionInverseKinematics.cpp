// ActionInverseKinematics.cpp
#include "ActionInverseKinematics.h"
#include "ArmServos.h"
#include <Arduino.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <math.h>

namespace {
  static Preferences prefs;
  static bool inited = false;

  static constexpr float pivotHeight = 76.2f;
  static constexpr float kZCalHeightMm = 120.0f;

  struct CalPoint {
    int elbow;
    int wrist;
    float dist;
  };

  static CalPoint calA, calB, calC;           // z=0mm (table hover)
  static CalPoint calA120, calB120, calC120;  // z=120mm above table

  void loadCal(const char* key, CalPoint& c) {
    String json = prefs.getString(key, "{}");
    Serial.printf("[IK] Pref key '%s' raw JSON: %s\n", key, json.c_str());
    StaticJsonDocument<128> doc;
    if (deserializeJson(doc, json) == DeserializationError::Ok) {
      c.elbow = doc["ELBOW"] | c.elbow;
      c.wrist = doc["WRIST"] | c.wrist;
      c.dist  = doc["DISTANCE"] | c.dist;
    } else {
      Serial.printf("[IK] JSON parse failed for key '%s'\n", key);
    }
    Serial.printf("[IK] Cal '%s' => elbow:%d, wrist:%d, dist:%.2f\n",
                  key, c.elbow, c.wrist, c.dist);
  }

  bool hasValidCal(const CalPoint& a, const CalPoint& b, const CalPoint& c) {
    return (a.dist < b.dist && b.dist < c.dist);
  }

  void initIK() {
    if (inited) return;

    ArmServos::begin();

    prefs.begin("config", true);
    Serial.println("[IK] Beginning prefs load for 'config'");

    // z=0 (existing behavior)
    calA = {126,   0,   0.0f};
    calB = {132,  46,  60.0f};
    calC = {165, 160, 115.0f};
    Serial.println("[IK] Default calA: elbow=126, wrist=0, dist=0.00");
    Serial.println("[IK] Default calB: elbow=132, wrist=46, dist=60.00");
    Serial.println("[IK] Default calC: elbow=165, wrist=160, dist=115.00");

    // z=120mm (ignored until user calibrates these)
    calA120 = {90, 90, 0.0f};
    calB120 = {90, 90, 0.0f};
    calC120 = {90, 90, 0.0f};
    Serial.println("[IK] Default calA120/B120/C120: uncalibrated (z_height ignored until saved)");

    loadCal("hover_over_min", calA);
    loadCal("hover_over_mid", calB);
    loadCal("hover_over_max", calC);
    loadCal("hover_min_120", calA120);
    loadCal("hover_mid_120", calB120);
    loadCal("hover_max_120", calC120);

    prefs.end();
    Serial.println("[IK] Ended prefs");

    bool hasCal0 = hasValidCal(calA, calB, calC);
    bool hasCal120 = hasValidCal(calA120, calB120, calC120);
    Serial.printf("[IK] %scalibration loaded for z=0 (A < B < C = %s)\n",
                  hasCal0 ? "" : "no valid ",
                  hasCal0 ? "true" : "false");
    Serial.printf("[IK] %scalibration loaded for z=120 (A < B < C = %s)\n",
                  hasCal120 ? "" : "no valid ",
                  hasCal120 ? "true" : "false");

    inited = true;
  }

  void computeIK_Direct(float td, int& e, int& w) {
    float dy = fabsf(pivotHeight) < 1e-4f ? 1e-4f : pivotHeight;
    float theta = atanf(td / dy) * 180.0f / static_cast<float>(M_PI);
    e = 90 - static_cast<int>(theta);
    w = 90 + static_cast<int>(theta);
  }

  void computeIK_InterpolatedFor(const CalPoint& A, const CalPoint& B, const CalPoint& C, float td, int& e, int& w) {
    float f = 0.0f;
    if (td <= A.dist) {
      e = A.elbow;
      w = A.wrist;
      Serial.printf("[IK] Using CalA (td=%.2f <= %.2f)\n", td, A.dist);
    } else if (td <= B.dist) {
      f = (td - A.dist) / (B.dist - A.dist);
      e = A.elbow + static_cast<int>(f * (B.elbow - A.elbow));
      w = A.wrist + static_cast<int>(f * (B.wrist - A.wrist));
      Serial.printf("[IK] Interpolating A->B: f=%.3f, td=%.2f in [%.2f, %.2f]\n", f, td, A.dist, B.dist);
    } else if (td <= C.dist) {
      f = (td - B.dist) / (C.dist - B.dist);
      e = B.elbow + static_cast<int>(f * (C.elbow - B.elbow));
      w = B.wrist + static_cast<int>(f * (C.wrist - B.wrist));
      Serial.printf("[IK] Interpolating B->C: f=%.3f, td=%.2f in [%.2f, %.2f]\n", f, td, B.dist, C.dist);
    } else {
      e = C.elbow;
      w = C.wrist;
      Serial.printf("[IK] Using CalC (td=%.2f > %.2f)\n", td, C.dist);
    }

    Serial.printf("[IK] CalA: dist=%.2f, elbow=%d, wrist=%d\n", A.dist, A.elbow, A.wrist);
    Serial.printf("[IK] CalB: dist=%.2f, elbow=%d, wrist=%d\n", B.dist, B.elbow, B.wrist);
    Serial.printf("[IK] CalC: dist=%.2f, elbow=%d, wrist=%d\n", C.dist, C.elbow, C.wrist);
    Serial.printf("[IK] Result: e=%d, w=%d\n", e, w);

    e = constrain(e, 0, 180);
    w = constrain(w, 0, 180);
  }

  void computeIK_ForCalSet(const CalPoint& A, const CalPoint& B, const CalPoint& C, float dist, int& e, int& w) {
    if (hasValidCal(A, B, C)) {
      computeIK_InterpolatedFor(A, B, C, dist, e, w);
    } else {
      computeIK_Direct(dist, e, w);
    }
  }

  bool moveServosSmoothly(int targetElbow, int targetWrist) {
    int currentElbow = ArmServos::getAngle(ArmServos::Elbow);
    int lift = max(currentElbow - 40, 0);
    if (!ArmServos::moveTo(ArmServos::Elbow, lift)) return false;
    delay(100);

    if (targetElbow < currentElbow) {
      if (!ArmServos::moveTo(ArmServos::Elbow, targetElbow)) return false;
      if (!ArmServos::moveTo(ArmServos::Wrist, targetWrist)) return false;
    } else {
      if (!ArmServos::moveTo(ArmServos::Wrist, targetWrist)) return false;
      if (!ArmServos::moveTo(ArmServos::Elbow, targetElbow)) return false;
    }
    return true;
  }
}

bool ActionInverseKinematics::run(const String& message) {
  initIK();

  StaticJsonDocument<256> doc;
  if (deserializeJson(doc, message) != DeserializationError::Ok) {
    Serial.print("[IK] JSON parse error: ");
    Serial.println(message);
    return false;
  }

  float dist = doc["distance"] | -1.0f;
  if (dist < 0.0f) {
    Serial.print("[IK] Missing distance: ");
    Serial.println(message);
    return false;
  }

  float z = doc["z_height"] | 0.0f;
  if (z < 0.0f) {
    Serial.printf("[IK] Refusing z_height < 0 (z=%.2f)\n", z);
    return false;
  }

  int e0, w0;
  computeIK_ForCalSet(calA, calB, calC, dist, e0, w0);

  int e = e0;
  int w = w0;

  if (z > 0.0f) {
    if (hasValidCal(calA120, calB120, calC120)) {
      int e120, w120;
      computeIK_ForCalSet(calA120, calB120, calC120, dist, e120, w120);

      float fz = z / kZCalHeightMm; // allow >1.0 (robot can go above calibrated height)
      e = static_cast<int>(lroundf(static_cast<float>(e0) + fz * static_cast<float>(e120 - e0)));
      w = static_cast<int>(lroundf(static_cast<float>(w0) + fz * static_cast<float>(w120 - w0)));
      e = constrain(e, 0, 180);
      w = constrain(w, 0, 180);
    } else {
      Serial.println("[IK] z_height provided but hover_*_120 calibrations are missing; using z=0 solution");
    }
  }

  bool ok = moveServosSmoothly(e, w);
  Serial.printf("[IK] %s d=%.2f z=%.2f -> E=%d W=%d\n", ok ? "Moved" : "Blocked", dist, z, e, w);
  return ok;
}

