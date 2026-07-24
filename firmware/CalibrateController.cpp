// CalibrateController.cpp
#include "CalibrateController.h"
#include "CalibrateBaseRotation.h"
#include <Preferences.h>
#include <ArduinoJson.h>
#include <cstring>  // for strcmp
#include <cstdlib>

namespace {
  enum CalibrationType {
    HOVER_OVER_MIN,
    HOVER_OVER_MID,
    HOVER_OVER_MAX,
    HOVER_MIN_120,
    HOVER_MID_120,
    HOVER_MAX_120,
    UNKNOWN_TYPE
  };

  struct PerchKeyMap {
    const char* type;       // incoming calibration_type
    const char* storageKey; // short key for NVS (<=15 chars)
    const char* reportKey;  // key reported back to server
  };

  const PerchKeyMap kPerchKeys[] = {
    {"perch_elbow_angle", "p_elbow", "PERCH_ELBOW_ANGLE"},
    {"perch_wrist_angle", "p_wrist", "PERCH_WRIST_ANGLE"},
    {"perch_twist_angle", "p_twist", "PERCH_TWIST_ANGLE"},
    {"perch_min",         "p_min",   "PERCH_MIN"},
    {"perch_mid",         "p_mid",   "PERCH_MID"},
    {"perch_max",         "p_max",   "PERCH_MAX"},
  };

  const PerchKeyMap* findPerchKey(const char* type) {
    if (!type) return nullptr;
    for (const auto& k : kPerchKeys) {
      if (strcmp(k.type, type) == 0) return &k;
    }
    return nullptr;
  }

  CalibrationType getCalibrationType(const char* str) {
    if (strcmp(str, "hover_over_min") == 0) {
      return HOVER_OVER_MIN;
    } else if (strcmp(str, "hover_over_mid") == 0) {
      return HOVER_OVER_MID;
    } else if (strcmp(str, "hover_over_max") == 0) {
      return HOVER_OVER_MAX;
    } else if (strcmp(str, "hover_min_120") == 0) {
      return HOVER_MIN_120;
    } else if (strcmp(str, "hover_mid_120") == 0) {
      return HOVER_MID_120;
    } else if (strcmp(str, "hover_max_120") == 0) {
      return HOVER_MAX_120;
    } else {
      return UNKNOWN_TYPE;
    }
  }

  bool extractFloat(JsonVariantConst var, float &out) {
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

  bool runPerchValue(const char* typeCStr, JsonVariantConst inDoc, String& statusJson, String& detailsKey) {
    const PerchKeyMap* perchKey = findPerchKey(typeCStr);
    if (!perchKey) return false;

    Preferences prefs;
    if (!prefs.begin("config", false)) {
      Serial.println("[Calibrate] Failed to open prefs namespace 'config'");
      return false;
    }

    float perchValue;
    if (!extractFloat(inDoc["value"], perchValue) &&
        !extractFloat(inDoc["distance"], perchValue)) {
      Serial.print("[Calibrate] Missing perch value for ");
      Serial.println(perchKey->reportKey);
      prefs.end();
      return false;
    }

    size_t wrote = prefs.putFloat(perchKey->storageKey, perchValue);
    if (wrote != sizeof(float)) {
      Serial.printf("[Calibrate] FAILED to save %s (bytes=%u)\n", perchKey->storageKey, (unsigned)wrote);
      prefs.end();
      return false;
    }

    StaticJsonDocument<64> perchDoc;
    perchDoc["TYPE"]  = perchKey->reportKey;
    perchDoc["VALUE"] = perchValue;
    serializeJson(perchDoc, statusJson);
    detailsKey = perchKey->reportKey;

    Serial.printf("[Calibrate] saved %s = %.2f (report %s)\n", perchKey->storageKey, perchValue, perchKey->reportKey);
    prefs.end();
    return true;
  }

  bool runHoverPoint(const char* typeCStr, JsonVariantConst inDoc, String& statusJson, String& detailsKey) {
    CalibrationType calibType = getCalibrationType(typeCStr);
    if (calibType == UNKNOWN_TYPE) {
      Serial.print("[Calibrate] Unknown calibration type: ");
      Serial.println(typeCStr);
      return false;
    }

    Preferences prefs;
    if (!prefs.begin("config", false)) {
      Serial.println("[Calibrate] Failed to open prefs namespace 'config'");
      return false;
    }

    float distance = -1.0f;
    if (!extractFloat(inDoc["distance"], distance) || distance < 0.0f) {
      Serial.println("[Calibrate] Missing or invalid distance");
      prefs.end();
      return false;
    }

    float b = prefs.getFloat("ELBOW_ANGLE", 90.0f);
    float e = prefs.getFloat("WRIST_ANGLE",  90.0f);
    float w = prefs.getFloat("TWIST_ANGLE",  90.0f);
    float tmp;
    if (extractFloat(inDoc["ELBOW"], tmp)) b = tmp;
    if (extractFloat(inDoc["WRIST"], tmp)) e = tmp;
    if (extractFloat(inDoc["TWIST"], tmp)) w = tmp;

    prefs.putFloat("ELBOW_ANGLE", b);
    prefs.putFloat("WRIST_ANGLE", e);
    prefs.putFloat("TWIST_ANGLE", w);

    StaticJsonDocument<256> outDoc;
    outDoc["ELBOW"]    = b;
    outDoc["WRIST"]    = e;
    outDoc["TWIST"]    = w;
    outDoc["DISTANCE"] = distance;
    serializeJson(outDoc, statusJson);

    switch (calibType) {
      case HOVER_OVER_MIN: detailsKey = "hover_over_min"; break;
      case HOVER_OVER_MID: detailsKey = "hover_over_mid"; break;
      case HOVER_OVER_MAX: detailsKey = "hover_over_max"; break;
      case HOVER_MIN_120:  detailsKey = "hover_min_120";  break;
      case HOVER_MID_120:  detailsKey = "hover_mid_120";  break;
      case HOVER_MAX_120:  detailsKey = "hover_max_120";  break;
      default: detailsKey = "hover_over_min"; break;
    }

    prefs.putString(detailsKey.c_str(), statusJson);
    prefs.end();

    Serial.print("[Calibrate] saved ");
    Serial.print(detailsKey);
    Serial.print(" = ");
    Serial.println(statusJson);

    return true;
  }
}

bool CalibrateController::run(const String& message, String& statusJson, String& detailsKey) {
  statusJson = "";
  detailsKey = "";

  StaticJsonDocument<384> inDoc;
  if (deserializeJson(inDoc, message) != DeserializationError::Ok) {
    Serial.print("[Calibrate] JSON parse error: ");
    Serial.println(message);
    return false;
  }

  const char* typeCStr = inDoc["calibration_type"].as<const char*>();
  if (!typeCStr) typeCStr = "hover_over_min";

  if (strcmp(typeCStr, "base_rotation_profile") == 0) {
    bool ok = CalibrateBaseRotation::calibrateProfile(message, statusJson);
    if (ok) detailsKey = "base_rotation";
    return ok;
  }

  if (findPerchKey(typeCStr)) {
    return runPerchValue(typeCStr, inDoc, statusJson, detailsKey);
  }

  if (getCalibrationType(typeCStr) != UNKNOWN_TYPE) {
    return runHoverPoint(typeCStr, inDoc, statusJson, detailsKey);
  }

  Serial.print("[Calibrate] Unknown calibration type: ");
  Serial.println(typeCStr);
  return false;
}
