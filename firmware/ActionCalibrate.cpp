// ActionCalibrate.cpp
#include "ActionCalibrate.h"
#include "BuddyMQTT.h"
#include <Preferences.h>
#include <ArduinoJson.h>
#include <cstring>  // for strcmp
#include <cstdlib>

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

static const PerchKeyMap kPerchKeys[] = {
  {"perch_elbow_angle", "p_elbow", "PERCH_ELBOW_ANGLE"},
  {"perch_wrist_angle", "p_wrist", "PERCH_WRIST_ANGLE"},
  {"perch_twist_angle", "p_twist", "PERCH_TWIST_ANGLE"},
  {"perch_min",         "p_min",   "PERCH_MIN"},
  {"perch_mid",         "p_mid",   "PERCH_MID"},
  {"perch_max",         "p_max",   "PERCH_MAX"},
};

static const PerchKeyMap* findPerchKey(const char* type) {
  if (!type) return nullptr;
  for (const auto& k : kPerchKeys) {
    if (strcmp(k.type, type) == 0) return &k;
  }
  return nullptr;
}

static CalibrationType getCalibrationType(const char* str) {
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

static bool extractFloat(JsonVariantConst var, float &out) {
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

void ActionCalibrate::run(const String &message) {
  // parse incoming JSON
  StaticJsonDocument<200> inDoc;
  if (deserializeJson(inDoc, message) != DeserializationError::Ok) {
    Serial.print("[Calibrate] JSON parse error: ");
    Serial.println(message);
    return;
  }

  // --- DEBUG: print the parsed doc as JSON string ---
  String inDebug;
  serializeJson(inDoc, inDebug);
  Serial.print("[Calibrate] parsed message → ");
  Serial.println(inDebug);
  // ----------------------------------------------------

  const char* typeCStr = inDoc["calibration_type"].as<const char*>();
  if (!typeCStr) typeCStr = "hover_over_min";

  String actionId = inDoc["action_id"].as<String>();
  if (actionId == "") actionId = "unknown_action";

  Preferences prefs;
  if (!prefs.begin("config", false)) {
    Serial.println("[Calibrate] Failed to open prefs namespace 'config'");
    BuddyMQTT::sendCompleted(actionId, typeCStr, "failed");
    return;
  }

  if (const PerchKeyMap* perchKey = findPerchKey(typeCStr)) {
    float perchValue;
    if (!extractFloat(inDoc["value"], perchValue) &&
        !extractFloat(inDoc["distance"], perchValue)) {
      Serial.print("[Calibrate] Missing perch value for ");
      Serial.println(perchKey->reportKey);
      prefs.end();
      BuddyMQTT::sendCompleted(actionId, typeCStr, "failed");
      return;
    }

    size_t wrote = prefs.putFloat(perchKey->storageKey, perchValue);
    StaticJsonDocument<64> perchDoc;
    perchDoc["TYPE"]  = perchKey->reportKey;
    perchDoc["VALUE"] = perchValue;
    String perchJson;
    serializeJson(perchDoc, perchJson);
    if (wrote != sizeof(float)) {
      Serial.printf("[Calibrate] FAILED to save %s (bytes=%u)\n", perchKey->storageKey, (unsigned)wrote);
      prefs.end();
      BuddyMQTT::sendCompleted(actionId, typeCStr, "failed");
      return;
    }
    Serial.printf("[Calibrate] saved %s = %.2f (report %s)\n", perchKey->storageKey, perchValue, perchKey->reportKey);
    prefs.end();
    BuddyMQTT::sendCompletedDetails(actionId, perchKey->reportKey, perchJson);
    return;
  }

  CalibrationType calibType = getCalibrationType(typeCStr);

  // extract distance
  JsonVariant dv = inDoc["distance"];
  int distance = -1;
  if      (dv.is<int>())         distance = dv.as<int>();
  else if (dv.is<const char*>()) distance = atoi(dv.as<const char*>());
  if (distance < 0) {
    Serial.println("[Calibrate] Missing or invalid distance");
    prefs.end();
    BuddyMQTT::sendCompleted(actionId, typeCStr, "failed");
    return;
  }

  // read current servo angles (override with provided values if present)
  float b = prefs.getFloat("ELBOW_ANGLE", 90.0f);
  float e = prefs.getFloat("WRIST_ANGLE",  90.0f);
  float w = prefs.getFloat("TWIST_ANGLE",  90.0f);
  float tmp;
  if (extractFloat(inDoc["ELBOW"], tmp)) b = tmp;
  if (extractFloat(inDoc["WRIST"], tmp)) e = tmp;
  if (extractFloat(inDoc["TWIST"], tmp)) w = tmp;

  // persist overrides for future sessions
  prefs.putFloat("ELBOW_ANGLE", b);
  prefs.putFloat("WRIST_ANGLE", e);
  prefs.putFloat("TWIST_ANGLE", w);

  // build JSON with angles + distance
  StaticJsonDocument<256> outDoc;
  outDoc["ELBOW"]    = b;
  outDoc["WRIST"]    = e;
  outDoc["TWIST"]    = w;
  outDoc["DISTANCE"] = distance;

  String jsonOut;
  serializeJson(outDoc, jsonOut);

  // decide key based on type
  const char* key;
  switch (calibType) {
    case HOVER_OVER_MIN: key = "hover_over_min"; break;
    case HOVER_OVER_MID: key = "hover_over_mid"; break;
    case HOVER_OVER_MAX: key = "hover_over_max"; break;
    case HOVER_MIN_120:  key = "hover_min_120";  break;
    case HOVER_MID_120:  key = "hover_mid_120";  break;
    case HOVER_MAX_120:  key = "hover_max_120";  break;
    default:
      Serial.print("[Calibrate] Unknown calibration type: ");
      Serial.println(typeCStr);
      prefs.end();
      return;
  }

  // store in NVS
  prefs.putString(key, jsonOut);
  prefs.end();

  Serial.print("[Calibrate] saved ");
  Serial.print(key);
  Serial.print(" = ");
  Serial.println(jsonOut);

  BuddyMQTT::sendCompletedDetails(actionId, key, jsonOut);
}
