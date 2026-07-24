// CalibrateValues.cpp
#include "CalibrateValues.h"
#include <Preferences.h>
#include <ArduinoJson.h>

bool CalibrateValues::run(const String& message, String& statusJson, String& detailsKey) {
  (void)message;
  statusJson = "";
  detailsKey = "";

  Preferences prefs;
  if (!prefs.begin("config", true)) {
    Serial.println("[Calibrate] Failed to open prefs namespace 'config' for calibrationvalues");
    return false;
  }

  DynamicJsonDocument doc(6144);
  auto hasAnyKey = [&](const char* shortKey, const char* legacyKey) {
    return prefs.isKey(shortKey) || prefs.isKey(legacyKey);
  };
  auto addFloatOrNull = [&](const char* storageKey, const char* reportKey, float defaultValue) {
    if (prefs.isKey(storageKey)) {
      doc[reportKey] = prefs.getFloat(storageKey, defaultValue);
    } else if (prefs.isKey(reportKey)) { // legacy long key fallback
      doc[reportKey] = prefs.getFloat(reportKey, defaultValue);
    } else {
      doc[reportKey] = nullptr;
    }
  };
  struct HoverPoint {
    bool present;
    bool valid;
    float dist;
  };
  auto readHoverPoint = [&](const char* key) {
    HoverPoint point = {false, false, 0.0f};
    if (!prefs.isKey(key)) return point;

    point.present = true;
    String raw = prefs.getString(key, "{}");
    StaticJsonDocument<256> tmp;
    if (deserializeJson(tmp, raw) != DeserializationError::Ok) return point;
    if (!tmp["DISTANCE"].is<float>() &&
        !tmp["DISTANCE"].is<int>() &&
        !tmp["DISTANCE"].is<long>()) {
      return point;
    }

    point.dist = tmp["DISTANCE"].as<float>();
    point.valid = point.dist >= 0.0f;
    return point;
  };
  auto validHoverSet = [&](const char* minKey, const char* midKey, const char* maxKey) {
    HoverPoint minPoint = readHoverPoint(minKey);
    HoverPoint midPoint = readHoverPoint(midKey);
    HoverPoint maxPoint = readHoverPoint(maxKey);
    return minPoint.present && midPoint.present && maxPoint.present &&
           minPoint.valid && midPoint.valid && maxPoint.valid &&
           minPoint.dist < midPoint.dist && midPoint.dist < maxPoint.dist;
  };

  addFloatOrNull("ELBOW_ANGLE",       "ELBOW_ANGLE",       0.0f);
  addFloatOrNull("WRIST_ANGLE",       "WRIST_ANGLE",       0.0f);
  addFloatOrNull("TWIST_ANGLE",       "TWIST_ANGLE",       0.0f);
  addFloatOrNull("GRIPPER_ANGLE",     "GRIPPER_ANGLE",     0.0f);
  addFloatOrNull("p_elbow",           "PERCH_ELBOW_ANGLE", 120.0f);
  addFloatOrNull("p_wrist",           "PERCH_WRIST_ANGLE", 90.0f);
  addFloatOrNull("p_twist",           "PERCH_TWIST_ANGLE", 90.0f);
  addFloatOrNull("p_min",             "PERCH_MIN",         0.0f);
  addFloatOrNull("p_mid",             "PERCH_MID",         50.0f);
  addFloatOrNull("p_max",             "PERCH_MAX",         100.0f);

  bool perchConfigured = hasAnyKey("p_elbow", "PERCH_ELBOW_ANGLE") &&
                         hasAnyKey("p_wrist", "PERCH_WRIST_ANGLE") &&
                         hasAnyKey("p_twist", "PERCH_TWIST_ANGLE");
  bool perchDistanceConfigured = hasAnyKey("p_min", "PERCH_MIN") &&
                                 hasAnyKey("p_mid", "PERCH_MID") &&
                                 hasAnyKey("p_max", "PERCH_MAX");
  doc["perch_configured"] = perchConfigured;
  doc["perch_distance_configured"] = perchDistanceConfigured;
  doc["perch_defaults_applied"] = !perchConfigured;
  JsonObject perchEffective = doc.createNestedObject("perch_effective");
  perchEffective["ELBOW"] = prefs.getFloat("p_elbow", prefs.getFloat("PERCH_ELBOW_ANGLE", 120.0f));
  perchEffective["WRIST"] = prefs.getFloat("p_wrist", prefs.getFloat("PERCH_WRIST_ANGLE", 90.0f));
  perchEffective["TWIST"] = prefs.getFloat("p_twist", prefs.getFloat("PERCH_TWIST_ANGLE", 90.0f));
  perchEffective["MIN"] = prefs.getFloat("p_min", prefs.getFloat("PERCH_MIN", 0.0f));
  perchEffective["MID"] = prefs.getFloat("p_mid", prefs.getFloat("PERCH_MID", 50.0f));
  perchEffective["MAX"] = prefs.getFloat("p_max", prefs.getFloat("PERCH_MAX", 100.0f));
  perchEffective["source"] = perchConfigured ? "saved" : "firmware_default";

  auto addHoverObject = [&](const char* key) {
    if (!prefs.isKey(key)) {
      doc[key] = nullptr;
      return;
    }
    String raw = prefs.getString(key, "{}");
    StaticJsonDocument<256> tmp;
    if (deserializeJson(tmp, raw) == DeserializationError::Ok) {
      JsonObject obj = doc.createNestedObject(key);
      for (auto kv : tmp.as<JsonObject>()) obj[kv.key()] = kv.value();
    } else {
      doc[key] = raw;
    }
  };
  addHoverObject("hover_over_min");
  addHoverObject("hover_over_mid");
  addHoverObject("hover_over_max");
  addHoverObject("hover_min_120");
  addHoverObject("hover_mid_120");
  addHoverObject("hover_max_120");

  bool ikHoverCalibrated = validHoverSet("hover_over_min", "hover_over_mid", "hover_over_max");
  bool ikZ120Calibrated = validHoverSet("hover_min_120", "hover_mid_120", "hover_max_120");
  doc["ik_hover_calibrated"] = ikHoverCalibrated;
  doc["ik_z120_calibrated"] = ikZ120Calibrated;
  doc["ik_z50_calibrated"] = ikZ120Calibrated;
  doc["ik_hover_source"] = ikHoverCalibrated ? "saved" : "firmware_default";
  doc["ik_z120_source"] = ikZ120Calibrated ? "saved" : "optional_not_saved";
  doc["ik_z50_source"] = ikZ120Calibrated ? "saved_legacy_keys" : "optional_not_saved";

  addFloatOrNull("rot_off_deg", "rot_off_deg", 0.0f);
  addFloatOrNull("ik_off_mm", "ik_off_mm", 0.0f);
  bool hasStencilMap = prefs.isKey("st_map");
  bool hasRotationOffset = prefs.isKey("rot_off_deg");
  bool hasIkOffset = prefs.isKey("ik_off_mm");
  if (prefs.isKey("st_map")) {
    doc["st_map"] = prefs.getString("st_map", "{}");
  } else {
    doc["st_map"] = nullptr;
  }
  bool stencilCalibrated = hasStencilMap && hasRotationOffset && hasIkOffset;
  doc["stencil_calibrated"] = stencilCalibrated;
  doc["stencil_runtime_mode"] = "average_offsets";

  prefs.end();

  Preferences rotPrefs;
  bool baseRotationReady = false;
  if (rotPrefs.begin("rot", true)) {
    bool baseCalibrated = rotPrefs.getBool("calibrated", false);
    bool baseProfileCalibrated = rotPrefs.getBool("prof_cal", false);
    long leftCountsPerRev = rotPrefs.getLong("left_cpr", 0);
    long rightCountsPerRev = rotPrefs.getLong("right_cpr", 0);
    bool lastValid = rotPrefs.getBool("last_valid", false);

    doc["base_rotation_calibrated"] = baseCalibrated;
    doc["base_rotation_profileCalibrated"] = baseProfileCalibrated;
    doc["base_rotation_leftCountsPerRev"] = leftCountsPerRev;
    doc["base_rotation_rightCountsPerRev"] = rightCountsPerRev;
    doc["base_rotation_lastCounts"] = rotPrefs.getLong("last_counts", 0);
    doc["base_rotation_lastValid"] = lastValid;
    baseRotationReady = baseCalibrated && baseProfileCalibrated &&
                        leftCountsPerRev > 0 && rightCountsPerRev > 0 &&
                        lastValid;
    doc["base_rotation_ready"] = baseRotationReady;
    rotPrefs.end();
  } else {
    doc["base_rotation_calibrated"] = nullptr;
    doc["base_rotation_profileCalibrated"] = nullptr;
    doc["base_rotation_leftCountsPerRev"] = nullptr;
    doc["base_rotation_rightCountsPerRev"] = nullptr;
    doc["base_rotation_lastCounts"] = nullptr;
    doc["base_rotation_lastValid"] = nullptr;
    doc["base_rotation_ready"] = false;
  }

  doc["motion_calibration_ready"] = baseRotationReady && ikHoverCalibrated;
  doc["initial_calibration_ready"] = baseRotationReady && ikHoverCalibrated && stencilCalibrated;

  serializeJson(doc, statusJson);
  detailsKey = "calibrationvalues";
  return true;
}
