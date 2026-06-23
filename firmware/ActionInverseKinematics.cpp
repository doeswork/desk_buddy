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
  static constexpr float kZCalHeightMm = 50.0f;
  static constexpr const char* KEY_IK_OFFSET_MM = "ik_off_mm";

  struct CalPoint {
    int elbow;
    int wrist;
    float dist;
  };

  static CalPoint calA, calB, calC;           // z=0mm (table hover)
  static CalPoint calA120, calB120, calC120;  // legacy keys for upper z plane (z=50mm)
  static float ikDistanceOffsetMm = 0.0f;

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

  float lerpFloat(float a, float b, float t) {
    return a + (b - a) * t;
  }

  int lerpInt(int a, int b, float t) {
    return static_cast<int>(lroundf(lerpFloat(static_cast<float>(a), static_cast<float>(b), t)));
  }

  bool isWithin(float value, float minValue, float maxValue, float epsilon = 0.01f) {
    return value >= (minValue - epsilon) && value <= (maxValue + epsilon);
  }

  float computeNormalizedDistance(float minValue, float maxValue, float dist) {
    float span = maxValue - minValue;
    if (fabsf(span) < 1e-4f) return 0.0f;
    return (dist - minValue) / span;
  }

  void resetCalDefaults() {
    // z=0 (existing behavior)
    calA = {126,   0,   0.0f};
    calB = {132,  46,  60.0f};
    calC = {165, 160, 115.0f};
    Serial.println("[IK] Default calA: elbow=126, wrist=0, dist=0.00");
    Serial.println("[IK] Default calB: elbow=132, wrist=46, dist=60.00");
    Serial.println("[IK] Default calC: elbow=165, wrist=160, dist=115.00");

    // Upper z plane, currently z=50mm. Preference keys keep their legacy *_120 names.
    calA120 = {90, 90, 0.0f};
    calB120 = {90, 90, 0.0f};
    calC120 = {90, 90, 0.0f};
    Serial.println("[IK] Default upper z-plane cal: uncalibrated (z_height > 0 blocked until saved)");
  }

  void loadIKCalibrations() {
    resetCalDefaults();

    prefs.begin("config", true);
    Serial.println("[IK] Beginning prefs load for 'config'");

    loadCal("hover_over_min", calA);
    loadCal("hover_over_mid", calB);
    loadCal("hover_over_max", calC);
    loadCal("hover_min_120", calA120);
    loadCal("hover_mid_120", calB120);
    loadCal("hover_max_120", calC120);
    ikDistanceOffsetMm = prefs.getFloat(KEY_IK_OFFSET_MM, 0.0f);
    Serial.printf("[IK] Stencil distance offset: %.2fmm\n", ikDistanceOffsetMm);

    prefs.end();
    Serial.println("[IK] Ended prefs");

    bool hasCal0 = hasValidCal(calA, calB, calC);
    bool hasCal120 = hasValidCal(calA120, calB120, calC120);
    Serial.printf("[IK] %scalibration loaded for z=0 (A < B < C = %s)\n",
                  hasCal0 ? "" : "no valid ",
                  hasCal0 ? "true" : "false");
    Serial.printf("[IK] %scalibration loaded for upper z plane / z=50 (A < B < C = %s)\n",
                  hasCal120 ? "" : "no valid ",
                  hasCal120 ? "true" : "false");
  }

  void initIK() {
    if (inited) return;

    ArmServos::begin();
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

  bool computeIK_Trapezoid(float dist, float z, int& e, int& w) {
    if (z > kZCalHeightMm) {
      Serial.printf("[IK] Refusing z_height above calibrated trapezoid (z=%.2f max=%.2f)\n", z, kZCalHeightMm);
      return false;
    }

    if (!hasValidCal(calA, calB, calC)) {
      Serial.println("[IK] z_height > 0 requires valid z=0 hover_over_min/mid/max calibration");
      return false;
    }

    if (!hasValidCal(calA120, calB120, calC120)) {
      Serial.println("[IK] z_height > 0 requires valid z=50 hover_min_120/mid/max calibration");
      return false;
    }

    float fz = z / kZCalHeightMm;
    float minAtZ = lerpFloat(calA.dist, calA120.dist, fz);
    float maxAtZ = lerpFloat(calC.dist, calC120.dist, fz);
    if (maxAtZ <= minAtZ) {
      Serial.printf("[IK] Invalid trapezoid slice at z=%.2f min=%.2f max=%.2f\n", z, minAtZ, maxAtZ);
      return false;
    }

    if (!isWithin(dist, minAtZ, maxAtZ)) {
      Serial.printf("[IK] Refusing point outside trapezoid: d=%.2f z=%.2f allowed=[%.2f, %.2f]\n",
                    dist, z, minAtZ, maxAtZ);
      return false;
    }

    float u = constrain(computeNormalizedDistance(minAtZ, maxAtZ, dist), 0.0f, 1.0f);
    float bottomDist = lerpFloat(calA.dist, calC.dist, u);
    float topDist = lerpFloat(calA120.dist, calC120.dist, u);

    int e0, w0;
    int e50, w50;
    computeIK_ForCalSet(calA, calB, calC, bottomDist, e0, w0);
    computeIK_ForCalSet(calA120, calB120, calC120, topDist, e50, w50);

    e = constrain(lerpInt(e0, e50, fz), 0, 180);
    w = constrain(lerpInt(w0, w50, fz), 0, 180);

    Serial.printf("[IK] Trapezoid d=%.2f z=%.2f fz=%.3f u=%.3f slice=[%.2f, %.2f] bottomD=%.2f topD=%.2f -> E=%d W=%d\n",
                  dist, z, fz, u, minAtZ, maxAtZ, bottomDist, topDist, e, w);
    return true;
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

bool ActionInverseKinematics::moveTo(float dist, float z, bool applyStoredOffset) {
  initIK();
  loadIKCalibrations();

  if (dist < 0.0f) {
    Serial.printf("[IK] Refusing distance < 0 (d=%.2f)\n", dist);
    return false;
  }

  if (z < 0.0f) {
    Serial.printf("[IK] Refusing z_height < 0 (z=%.2f)\n", z);
    return false;
  }

  float effectiveOffset = applyStoredOffset ? ikDistanceOffsetMm : 0.0f;
  float adjustedDist = dist + effectiveOffset;
  if (adjustedDist < 0.0f) adjustedDist = 0.0f;

  int e, w;
  if (z <= 0.0f) {
    computeIK_ForCalSet(calA, calB, calC, adjustedDist, e, w);
  } else if (!computeIK_Trapezoid(adjustedDist, z, e, w)) {
    Serial.printf("[IK] Blocked requested_d=%.2f offset=%.2f adjusted_d=%.2f z=%.2f\n",
                  dist, effectiveOffset, adjustedDist, z);
    return false;
  }

  bool ok = moveServosSmoothly(e, w);
  Serial.printf("[IK] %s requested_d=%.2f offset=%.2f adjusted_d=%.2f z=%.2f -> E=%d W=%d\n",
                ok ? "Moved" : "Blocked", dist, effectiveOffset, adjustedDist, z, e, w);
  return ok;
}

bool ActionInverseKinematics::run(const String& message) {
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

  return ActionInverseKinematics::moveTo(dist, z);
}
