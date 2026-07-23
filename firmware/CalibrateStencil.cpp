#include "CalibrateStencil.h"
#include "ActionBaseRotate.h"
#include "ActionInverseKinematics.h"
#include "ActionGripper.h"
#include "ActionPerch.h"
#include <Arduino.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <math.h>
#include <stdlib.h>
#include <strings.h>

namespace {
  constexpr const char CONFIG_NAMESPACE[] = "config";
  constexpr const char KEY_STENCIL_MAP[] = "st_map";
  constexpr const char KEY_ROTATION_OFFSET_DEGREES[] = "rot_off_deg";
  constexpr const char KEY_IK_OFFSET_MM[] = "ik_off_mm";
  constexpr const char STENCIL_HOME_DIRECTION[] = "RIGHT";
  constexpr const char STENCIL_BASE_SPEED[] = "veryslow";
  constexpr float STENCIL_BASE_TARGET_EPSILON_DEGREES = 0.01f;
  constexpr size_t STENCIL_POINT_COUNT = 15;
  constexpr size_t STENCIL_OFFSET_POINT_COUNT = 9;
  constexpr size_t STENCIL_VALIDATION_POINT_COUNT = STENCIL_POINT_COUNT - STENCIL_OFFSET_POINT_COUNT;

  struct StencilPoint {
    const char* id;
    float distanceMm;
    float angleDegrees;
    float zHeightMm;
    bool offsetContributor;
  };

  struct StencilPointResult {
    bool completed;
    bool grabbed;
    float rotationNudgeDegrees;
    float distanceNudgeMm;
    uint8_t attempts;
  };

  enum class Phase {
    Idle,
    PlacePeg,
    Attempting,
    NeedsAdjustment,
    Complete,
    Failed,
    Canceled,
    Cleared
  };

  const StencilPoint STENCIL_POINTS[STENCIL_POINT_COUNT] = {
    {"z0_left_min",      0.0f, -30.0f,  0.0f, true},
    {"z0_left_mid",     60.0f, -30.0f,  0.0f, true},
    {"z0_left_max",    120.0f, -30.0f,  0.0f, true},
    {"z0_center_min",    0.0f,   0.0f,  0.0f, true},
    {"z0_center_mid",   60.0f,   0.0f,  0.0f, true},
    {"z0_center_max",  120.0f,   0.0f,  0.0f, true},
    {"z0_right_min",     0.0f,  30.0f,  0.0f, true},
    {"z0_right_mid",    60.0f,  30.0f,  0.0f, true},
    {"z0_right_max",   120.0f,  30.0f,  0.0f, true},
    {"z50_center_min",  30.0f,   0.0f, 50.0f, false},
    {"z50_center_mid",  75.0f,   0.0f, 50.0f, false},
    {"z50_center_max", 120.0f,   0.0f, 50.0f, false},
    {"z25_center_min",  15.0f,   0.0f, 25.0f, false},
    {"z25_center_mid",  60.0f,   0.0f, 25.0f, false},
    {"z25_center_max", 120.0f,   0.0f, 25.0f, false}
  };

  bool sessionActive = false;
  String sessionId = "";
  Phase phase = Phase::Idle;
  size_t currentPointIndex = 0;
  StencilPointResult results[STENCIL_POINT_COUNT] = {};
  bool lastGrabbed = false;
  String lastMessage = "No active stencil calibration session.";
  String lastError = "";
  float lastSavedRotationOffsetDegrees = 0.0f;
  float lastSavedIkOffsetMm = 0.0f;
  bool hasLastBaseTargetAngle = false;
  float lastBaseTargetAngleDegrees = 0.0f;
  bool lastBaseMoveSkipped = false;

  void moveArmToPerch(const char* reason) {
    Serial.printf("[Stencil] moving arm to perch before %s\n", reason ? reason : "rotation");
    ActionPerch::run("{}");
  }

  const char* phaseName(Phase p) {
    switch (p) {
      case Phase::Idle: return "idle";
      case Phase::PlacePeg: return "place_peg";
      case Phase::Attempting: return "attempting";
      case Phase::NeedsAdjustment: return "needs_adjustment";
      case Phase::Complete: return "complete";
      case Phase::Failed: return "failed";
      case Phase::Canceled: return "canceled";
      case Phase::Cleared: return "cleared";
    }
    return "unknown";
  }

  bool extractFloat(JsonVariantConst var, float& out) {
    if (var.is<float>()) {
      out = var.as<float>();
      return true;
    }
    if (var.is<int>()) {
      out = static_cast<float>(var.as<int>());
      return true;
    }
    if (var.is<long>()) {
      out = static_cast<float>(var.as<long>());
      return true;
    }
    if (var.is<const char*>()) {
      const char* s = var.as<const char*>();
      if (s && *s) {
        out = static_cast<float>(atof(s));
        return true;
      }
    }
    return false;
  }

  void loadSavedOffsets() {
    Preferences prefs;
    lastSavedRotationOffsetDegrees = 0.0f;
    lastSavedIkOffsetMm = 0.0f;
    if (prefs.begin(CONFIG_NAMESPACE, true)) {
      lastSavedRotationOffsetDegrees = prefs.getFloat(KEY_ROTATION_OFFSET_DEGREES, 0.0f);
      lastSavedIkOffsetMm = prefs.getFloat(KEY_IK_OFFSET_MM, 0.0f);
      prefs.end();
    }
  }

  void clearResults() {
    for (size_t i = 0; i < STENCIL_POINT_COUNT; ++i) {
      results[i].completed = false;
      results[i].grabbed = false;
      results[i].rotationNudgeDegrees = 0.0f;
      results[i].distanceNudgeMm = 0.0f;
      results[i].attempts = 0;
    }
  }

  void resetBaseTargetTracking() {
    hasLastBaseTargetAngle = false;
    lastBaseTargetAngleDegrees = 0.0f;
    lastBaseMoveSkipped = false;
  }

  float normalizeDegrees(float angle) {
    float normalized = fmod(angle, 360.0f);
    if (normalized < 0.0f) normalized += 360.0f;
    return normalized;
  }

  float shortestAngleDifferenceDegrees(float a, float b) {
    float delta = fabs(normalizeDegrees(a) - normalizeDegrees(b));
    return delta > 180.0f ? 360.0f - delta : delta;
  }

  float currentTargetAngleDegrees() {
    if (currentPointIndex >= STENCIL_POINT_COUNT) return 0.0f;
    return STENCIL_POINTS[currentPointIndex].angleDegrees + results[currentPointIndex].rotationNudgeDegrees;
  }

  float currentTargetDistanceMm() {
    if (currentPointIndex >= STENCIL_POINT_COUNT) return 0.0f;
    float target = STENCIL_POINTS[currentPointIndex].distanceMm + results[currentPointIndex].distanceNudgeMm;
    return target < 0.0f ? 0.0f : target;
  }

  float currentTargetZHeightMm() {
    if (currentPointIndex >= STENCIL_POINT_COUNT) return 0.0f;
    return STENCIL_POINTS[currentPointIndex].zHeightMm;
  }

  String placePegMessage() {
    if (currentPointIndex >= STENCIL_POINT_COUNT) return "Stencil calibration complete.";
    const StencilPoint& point = STENCIL_POINTS[currentPointIndex];
    String message = String("Are you ready for peg hole grab attempt at ") +
                     String(currentTargetDistanceMm(), 0) + "mm and " +
                     String(currentTargetAngleDegrees(), 0) + " degrees, z=" +
                     String(point.zHeightMm, 0) + "mm? Place peg in ";
    message += point.id;
    message += (point.offsetContributor ? " offset point" : " validation point");
    message += ", then send RUN_POINT.";
    return message;
  }

  bool saveCompletedOffsets() {
    float rotationSum = 0.0f;
    float distanceSum = 0.0f;
    size_t contributorCount = 0;
    for (size_t i = 0; i < STENCIL_POINT_COUNT; ++i) {
      if (!results[i].completed) return false;
      if (STENCIL_POINTS[i].offsetContributor) {
        rotationSum += results[i].rotationNudgeDegrees;
        distanceSum += results[i].distanceNudgeMm;
        ++contributorCount;
      }
    }
    if (contributorCount == 0) return false;

    lastSavedRotationOffsetDegrees = rotationSum / static_cast<float>(contributorCount);
    lastSavedIkOffsetMm = distanceSum / static_cast<float>(contributorCount);

    DynamicJsonDocument mapDoc(4096);
    JsonArray points = mapDoc.createNestedArray("points");
    for (size_t i = 0; i < STENCIL_POINT_COUNT; ++i) {
      JsonObject point = points.createNestedObject();
      point["id"] = STENCIL_POINTS[i].id;
      point["angle"] = STENCIL_POINTS[i].angleDegrees;
      point["distance"] = STENCIL_POINTS[i].distanceMm;
      point["z"] = STENCIL_POINTS[i].zHeightMm;
      point["offsetContributor"] = STENCIL_POINTS[i].offsetContributor;
      point["r"] = results[i].rotationNudgeDegrees;
      point["d"] = results[i].distanceNudgeMm;
      point["attempts"] = results[i].attempts;
      point["completed"] = results[i].completed;
      point["grabbed"] = results[i].grabbed;
    }
    mapDoc["rot_off_deg"] = lastSavedRotationOffsetDegrees;
    mapDoc["ik_off_mm"] = lastSavedIkOffsetMm;
    mapDoc["point_count"] = STENCIL_POINT_COUNT;
    mapDoc["offset_point_count"] = contributorCount;
    mapDoc["validation_point_count"] = STENCIL_POINT_COUNT - contributorCount;
    mapDoc["home_direction"] = STENCIL_HOME_DIRECTION;
    mapDoc["base_move_speed"] = STENCIL_BASE_SPEED;

    String mapJson;
    serializeJson(mapDoc, mapJson);

    Preferences prefs;
    if (!prefs.begin(CONFIG_NAMESPACE, false)) return false;
    prefs.putString(KEY_STENCIL_MAP, mapJson);
    prefs.putFloat(KEY_ROTATION_OFFSET_DEGREES, lastSavedRotationOffsetDegrees);
    prefs.putFloat(KEY_IK_OFFSET_MM, lastSavedIkOffsetMm);
    prefs.end();
    return true;
  }

  bool clearStoredOffsets() {
    Preferences prefs;
    if (!prefs.begin(CONFIG_NAMESPACE, false)) return false;
    prefs.remove(KEY_STENCIL_MAP);
    prefs.remove(KEY_ROTATION_OFFSET_DEGREES);
    prefs.remove(KEY_IK_OFFSET_MM);
    prefs.end();
    lastSavedRotationOffsetDegrees = 0.0f;
    lastSavedIkOffsetMm = 0.0f;
    return true;
  }

  void buildStatusJson(String& out, const char* error = nullptr) {
    loadSavedOffsets();

    DynamicJsonDocument doc(5120);
    doc["sessionId"] = sessionId;
    doc["phase"] = phaseName(phase);
    doc["active"] = sessionActive;
    doc["pointIndex"] = currentPointIndex;
    doc["totalPointCount"] = STENCIL_POINT_COUNT;
    doc["offsetPointCount"] = STENCIL_OFFSET_POINT_COUNT;
    doc["validationPointCount"] = STENCIL_VALIDATION_POINT_COUNT;
    doc["homeDirection"] = STENCIL_HOME_DIRECTION;
    doc["baseMoveSpeed"] = STENCIL_BASE_SPEED;
    doc["baseMoveSkipped"] = lastBaseMoveSkipped;
    if (hasLastBaseTargetAngle) {
      doc["lastBaseTargetAngleDegrees"] = lastBaseTargetAngleDegrees;
    } else {
      doc["lastBaseTargetAngleDegrees"] = nullptr;
    }

    if (currentPointIndex < STENCIL_POINT_COUNT) {
      const StencilPoint& point = STENCIL_POINTS[currentPointIndex];
      const StencilPointResult& result = results[currentPointIndex];
      doc["pointId"] = point.id;
      doc["baseAngleDegrees"] = point.angleDegrees;
      doc["baseDistanceMm"] = point.distanceMm;
      doc["zHeightMm"] = point.zHeightMm;
      doc["offsetContributor"] = point.offsetContributor;
      doc["targetAngleDegrees"] = currentTargetAngleDegrees();
      doc["targetDistanceMm"] = currentTargetDistanceMm();
      doc["targetZHeightMm"] = currentTargetZHeightMm();
      doc["rotationNudgeDegrees"] = result.rotationNudgeDegrees;
      doc["distanceNudgeMm"] = result.distanceNudgeMm;
      doc["attempts"] = result.attempts;
    } else {
      doc["pointId"] = nullptr;
      doc["baseAngleDegrees"] = nullptr;
      doc["baseDistanceMm"] = nullptr;
      doc["zHeightMm"] = nullptr;
      doc["offsetContributor"] = nullptr;
      doc["targetAngleDegrees"] = nullptr;
      doc["targetDistanceMm"] = nullptr;
      doc["targetZHeightMm"] = nullptr;
      doc["rotationNudgeDegrees"] = nullptr;
      doc["distanceNudgeMm"] = nullptr;
      doc["attempts"] = nullptr;
    }

    doc["grabbed"] = lastGrabbed;
    doc["message"] = lastMessage;
    doc["savedRotationOffsetDegrees"] = lastSavedRotationOffsetDegrees;
    doc["savedIkOffsetMm"] = lastSavedIkOffsetMm;
    if (lastError.length()) doc["error"] = lastError;
    if (error && error[0]) doc["error"] = error;

    JsonArray points = doc.createNestedArray("points");
    for (size_t i = 0; i < STENCIL_POINT_COUNT; ++i) {
      JsonObject point = points.createNestedObject();
      point["id"] = STENCIL_POINTS[i].id;
      point["angleDegrees"] = STENCIL_POINTS[i].angleDegrees;
      point["distanceMm"] = STENCIL_POINTS[i].distanceMm;
      point["zHeightMm"] = STENCIL_POINTS[i].zHeightMm;
      point["offsetContributor"] = STENCIL_POINTS[i].offsetContributor;
      point["completed"] = results[i].completed;
      point["grabbed"] = results[i].grabbed;
      point["rotationNudgeDegrees"] = results[i].rotationNudgeDegrees;
      point["distanceNudgeMm"] = results[i].distanceNudgeMm;
      point["attempts"] = results[i].attempts;
    }

    serializeJson(doc, out);
  }

  bool fail(String& statusJson, const char* error) {
    phase = Phase::Failed;
    sessionActive = false;
    lastGrabbed = false;
    lastError = error ? error : "stencil calibration failed";
    lastMessage = lastError;
    Serial.print("[Stencil] failed: ");
    Serial.println(lastError);
    buildStatusJson(statusJson, lastError.c_str());
    return false;
  }

  bool startSession(String& statusJson) {
    String homeStatus;
    resetBaseTargetTracking();
    moveArmToPerch("true north home");
    if (!ActionBaseRotate::homeToTrueNorth(STENCIL_HOME_DIRECTION, STENCIL_BASE_SPEED, homeStatus)) {
      return fail(statusJson, "failed to home true north");
    }

    String reason;
    if (!ActionBaseRotate::isAbsoluteAngleReady(reason)) {
      return fail(statusJson, reason.c_str());
    }

    sessionActive = true;
    sessionId = String("stencil_") + String(millis());
    currentPointIndex = 0;
    clearResults();
    resetBaseTargetTracking();
    phase = Phase::PlacePeg;
    lastGrabbed = false;
    lastError = "";
    lastMessage = placePegMessage();
    buildStatusJson(statusJson);
    return true;
  }

  bool advanceAfterSuccess(String& statusJson) {
    results[currentPointIndex].completed = true;
    results[currentPointIndex].grabbed = true;
    lastGrabbed = true;
    ActionGripper::drop();

    ++currentPointIndex;
    if (currentPointIndex >= STENCIL_POINT_COUNT) {
      if (!saveCompletedOffsets()) {
        return fail(statusJson, "failed to save stencil offsets");
      }
      phase = Phase::Complete;
      sessionActive = false;
      resetBaseTargetTracking();
      lastMessage = "Stencil calibration complete. Saved averaged z=0 rotation and IK offsets; z=25/z=50 validation diagnostics are in st_map.";
      buildStatusJson(statusJson);
      return true;
    }

    phase = Phase::PlacePeg;
    lastMessage = placePegMessage();
    buildStatusJson(statusJson);
    return true;
  }

  bool moveBaseIfNeeded(float targetAngle, String& rotateStatus) {
    if (hasLastBaseTargetAngle &&
        shortestAngleDifferenceDegrees(lastBaseTargetAngleDegrees, targetAngle) <= STENCIL_BASE_TARGET_EPSILON_DEGREES) {
      lastBaseMoveSkipped = true;
      Serial.printf("[Stencil] base move skipped target=%.2f last=%.2f speed=%s\n",
                    targetAngle, lastBaseTargetAngleDegrees, STENCIL_BASE_SPEED);
      return true;
    }

    lastBaseMoveSkipped = false;
    Serial.printf("[Stencil] base move required target=%.2f last=%s%.2f speed=%s\n",
                  targetAngle,
                  hasLastBaseTargetAngle ? "" : "none/",
                  hasLastBaseTargetAngle ? lastBaseTargetAngleDegrees : 0.0f,
                  STENCIL_BASE_SPEED);
    moveArmToPerch("base lane change");
    if (!ActionBaseRotate::moveToAbsoluteAngle(targetAngle, STENCIL_BASE_SPEED, false, rotateStatus)) {
      return false;
    }

    lastBaseTargetAngleDegrees = targetAngle;
    hasLastBaseTargetAngle = true;
    return true;
  }

  bool attemptCurrentPoint(String& statusJson) {
    if (!sessionActive || currentPointIndex >= STENCIL_POINT_COUNT) {
      return fail(statusJson, "no active stencil calibration session");
    }

    phase = Phase::Attempting;
    lastError = "";
    lastGrabbed = false;
    StencilPointResult& result = results[currentPointIndex];
    ++result.attempts;

    ActionGripper::drop();

    String rotateStatus;
    if (!moveBaseIfNeeded(currentTargetAngleDegrees(), rotateStatus)) {
      return fail(statusJson, "failed to rotate to stencil point");
    }

    if (!ActionInverseKinematics::moveTo(currentTargetDistanceMm(), currentTargetZHeightMm(), false)) {
      return fail(statusJson, "failed to move IK to stencil point");
    }

    bool grabbed = ActionGripper::grab();
    if (grabbed) {
      return advanceAfterSuccess(statusJson);
    }

    result.grabbed = false;
    phase = Phase::NeedsAdjustment;
    lastGrabbed = false;
    lastMessage = "Peg was not grabbed. Send ADJUST with rotationNudgeDegrees and/or distanceNudgeMm.";
    buildStatusJson(statusJson);
    return true;
  }

  void applyAdjustmentToPoint(size_t pointIndex, JsonVariantConst rotationVar, JsonVariantConst distanceVar) {
    float rotationDelta = 0.0f;
    float distanceDelta = 0.0f;
    extractFloat(rotationVar, rotationDelta);
    extractFloat(distanceVar, distanceDelta);

    results[pointIndex].rotationNudgeDegrees += rotationDelta;
    results[pointIndex].distanceNudgeMm += distanceDelta;
    Serial.printf("[Stencil] adjust point=%s rotDelta=%.2f distDelta=%.2f totalRot=%.2f totalDist=%.2f\n",
                  STENCIL_POINTS[pointIndex].id, rotationDelta, distanceDelta,
                  results[pointIndex].rotationNudgeDegrees,
                  results[pointIndex].distanceNudgeMm);
  }

  bool applyAdjustmentAndPrompt(JsonVariantConst rotationVar, JsonVariantConst distanceVar, String& statusJson) {
    if (!sessionActive || currentPointIndex >= STENCIL_POINT_COUNT) {
      return fail(statusJson, "no active stencil calibration session");
    }

    applyAdjustmentToPoint(currentPointIndex, rotationVar, distanceVar);

    phase = Phase::PlacePeg;
    lastGrabbed = false;
    lastError = "";
    lastMessage = placePegMessage();
    buildStatusJson(statusJson);
    return true;
  }

  bool adjustPreviousAndRetry(JsonVariantConst rotationVar, JsonVariantConst distanceVar, String& statusJson) {
    if (!sessionActive) {
      return fail(statusJson, "no active stencil calibration session");
    }
    if (currentPointIndex == 0 || currentPointIndex > STENCIL_POINT_COUNT) {
      return fail(statusJson, "no previous stencil point to retry");
    }

    size_t retryIndex = currentPointIndex - 1;
    if (results[retryIndex].attempts == 0) {
      return fail(statusJson, "previous stencil point has not been run");
    }

    applyAdjustmentToPoint(retryIndex, rotationVar, distanceVar);
    results[retryIndex].completed = false;
    results[retryIndex].grabbed = false;
    currentPointIndex = retryIndex;
    lastGrabbed = false;
    lastError = "";
    phase = Phase::PlacePeg;
    lastMessage = String("Retrying previous stencil point ") + STENCIL_POINTS[retryIndex].id + " with adjustment.";
    Serial.printf("[Stencil] retry previous point=%s index=%u\n",
                  STENCIL_POINTS[retryIndex].id, static_cast<unsigned>(retryIndex));
    return attemptCurrentPoint(statusJson);
  }
}

bool CalibrateStencil::run(const String& message, String& statusJson) {
  statusJson = "";

  StaticJsonDocument<512> doc;
  if (deserializeJson(doc, message) != DeserializationError::Ok) {
    return fail(statusJson, "invalid JSON");
  }

  const char* command = doc["command"].as<const char*>();
  if (!command) {
    return fail(statusJson, "missing command");
  }

  if (strcasecmp(command, "START") == 0) {
    return startSession(statusJson);
  }

  if (strcasecmp(command, "RUN_POINT") == 0) {
    return attemptCurrentPoint(statusJson);
  }

  if (strcasecmp(command, "ADJUST") == 0) {
    return applyAdjustmentAndPrompt(doc["rotationNudgeDegrees"], doc["distanceNudgeMm"], statusJson);
  }

  if (strcasecmp(command, "ADJUST_PREVIOUS") == 0) {
    return adjustPreviousAndRetry(doc["rotationNudgeDegrees"], doc["distanceNudgeMm"], statusJson);
  }

  if (strcasecmp(command, "STATUS") == 0) {
    buildStatusJson(statusJson);
    return true;
  }

  if (strcasecmp(command, "CANCEL") == 0) {
    sessionActive = false;
    resetBaseTargetTracking();
    phase = Phase::Canceled;
    lastGrabbed = false;
    lastError = "";
    lastMessage = "Stencil calibration canceled. No active session changes were saved.";
    buildStatusJson(statusJson);
    return true;
  }

  if (strcasecmp(command, "CLEAR") == 0) {
    sessionActive = false;
    clearResults();
    currentPointIndex = 0;
    resetBaseTargetTracking();
    phase = clearStoredOffsets() ? Phase::Cleared : Phase::Failed;
    lastGrabbed = false;
    lastError = phase == Phase::Failed ? "failed to clear stencil offsets" : "";
    lastMessage = phase == Phase::Failed ? lastError : "Saved stencil offsets cleared.";
    buildStatusJson(statusJson, lastError.length() ? lastError.c_str() : nullptr);
    return phase != Phase::Failed;
  }

  return fail(statusJson, "unknown stencil command");
}
