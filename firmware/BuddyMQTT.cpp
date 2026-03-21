#include "BuddyMQTT.h"
#include "BuddyWifi.h"
#include "ActionController.h"
#include "ActionOTA.h"
#include "Heartbeat.h"
#include "FactoryReset.h"
#include <WiFi.h>
#include <WiFiClientSecure.h>   // secure TCP
#include <PubSubClient.h>
#include <Preferences.h>
#include <Arduino.h>
#include <time.h>
#include <LED.h>
#include <ArduinoJson.h>

namespace {
  // —— Default broker settings (TLS) - can be overridden via web config ————
  constexpr char     DEFAULT_SERVER[]       = "mqtt.deskbuddy.ai";
  constexpr uint16_t DEFAULT_PORT           = 8883;
  constexpr char     DEFAULT_USER[]         = "";  // Must be configured via web UI
  constexpr char     DEFAULT_PASS[]         = "";  // Must be configured via web UI
  constexpr char     DEFAULT_CLIENT_ID[]    = "";  // Must be configured via web UI

  // Reset diagnostics - stored from setup(), reported in ready message
  String lastResetReason = "unknown";
  uint32_t bootFreeHeap = 0;
  uint32_t bootMinFreeHeap = 0;

  // Runtime settings loaded from Preferences
  String SERVER;
  uint16_t PORT;
  String USER;
  String PASS;
  String CLIENT_ID;
  String STATUS_TOPIC;       // Built from USER/test
  String HEARTBEAT_TOPIC;    // Built from USER/HEARTBEAT


  WiFiClientSecure netClient;     // TLS client
  PubSubClient     mqttClient(netClient);

  bool   inited = false;
  String receivedMessage;

  // Workflow context — persists until overwritten or cleared
  int currentWorkflowId      = -1;
  int currentWorkflowEventId = -1;

  void injectWorkflow(JsonDocument& doc) {
    if (currentWorkflowId      >= 0) doc["workflow_id"]       = currentWorkflowId;
    if (currentWorkflowEventId >= 0) doc["workflow_event_id"] = currentWorkflowEventId;
  }

  constexpr unsigned long PUBLISH_INTERVAL = 3000;
  unsigned long lastPublish = 0;
  int publishCount = 0;
  constexpr unsigned long OTA_ENFORCE_INTERVAL_MS = 60000;

  // Forward declarations
  String getTimestamp();
  bool publishInternal(const char* topic, const String& payload);

  String getTimestamp() {
    time_t now = time(nullptr);
    struct tm* t = localtime(&now);
    char buf[32];
    strftime(buf, sizeof(buf), "%Y-%m-%d %H:%M:%S", t);
    return String(buf);
  }

  bool publishInternal(const char* topic, const String& payload) {
    return mqttClient.connected() && mqttClient.publish(topic, payload.c_str());
  }

  void copyphrase(JsonVariantConst phrase, JsonDocument& doc) {
    if (phrase.isNull()) return;
    if (phrase.is<JsonArrayConst>()) {
      JsonArray dest = doc.createNestedArray("phrase");
      for (JsonVariantConst p : phrase.as<JsonArrayConst>()) dest.add(p);
    } else {
      doc["phrase"] = phrase;
    }
  }

  void copyUseModel(const String& useModelJson, int useModel, JsonDocument& doc) {
    if (useModelJson.length()) {
      doc["use_model"] = serialized(useModelJson);
      return;
    }
    if (useModel >= 0) {
      doc["use_model"] = (useModel == 1);
    }
  }

  void ensureInited() {
    if (inited) return;

    // Load MQTT settings from Preferences (or use defaults)
    Preferences prefs;
    if (prefs.begin("mqtt", true)) {
      SERVER = prefs.getString("server", DEFAULT_SERVER);
      PORT = prefs.getInt("port", DEFAULT_PORT);
      USER = prefs.getString("user", DEFAULT_USER);
      PASS = prefs.getString("password", DEFAULT_PASS);
      CLIENT_ID = prefs.getString("client_id", DEFAULT_CLIENT_ID);
      prefs.end();
    } else {
      // Use defaults if Preferences not available
      SERVER = DEFAULT_SERVER;
      PORT = DEFAULT_PORT;
      USER = DEFAULT_USER;
      PASS = DEFAULT_PASS;
      CLIENT_ID = DEFAULT_CLIENT_ID;
    }

    // Build topics from username
    STATUS_TOPIC = USER + "/test";
    HEARTBEAT_TOPIC = USER + "/HEARTBEAT";

    Serial.println("MQTT Settings:");
    Serial.println("  Server: " + SERVER);
    Serial.println("  Port: " + String(PORT));
    Serial.println("  User: " + USER);
    Serial.println("  Client ID: " + CLIENT_ID);
    Serial.println("  Status Topic: " + STATUS_TOPIC);
    Serial.println("  Heartbeat Topic: " + HEARTBEAT_TOPIC);

    // TODO: load cert from Preferences/web UI for proper TLS verification
    netClient.setInsecure();
    netClient.setTimeout(15);
    mqttClient.setKeepAlive(30);

    // MQTT setup
    mqttClient.setServer(SERVER.c_str(), PORT);
    mqttClient.setSocketTimeout(15);
    // Increase buffer so larger JSON payloads (like calibrationvalues) can publish
    mqttClient.setBufferSize(2048);

    // NTP (for timestamps)
    configTime(0, 0, "pool.ntp.org", "time.nist.gov");

    // Heartbeat hooks
    Heartbeat::setPublishCallback(publishInternal);
    Heartbeat::setHeartbeatTopic(HEARTBEAT_TOPIC.c_str());
    Heartbeat::setStatusTopic(STATUS_TOPIC.c_str());
    Heartbeat::setTimestampCallback(getTimestamp);

    inited = true;
  }

  void messageCallback(char* topic, byte* payload, unsigned int length) {
    String msg;
    msg.reserve(length);
    for (unsigned int i = 0; i < length; i++) msg += (char)payload[i];

    if (msg.startsWith("{\"count\":")) return;

    StaticJsonDocument<128> doc;
    if (deserializeJson(doc, msg) == DeserializationError::Ok) {
      const char* s = doc["sender"] | nullptr;
      if (s && strcmp(s, "firmware") == 0) return;
    }
    receivedMessage = msg;
  }
} // namespace

void BuddyMQTT::maintain() {
  ensureInited();
  if (!BuddyWifi::isConnected()) return;

  static bool sentReadyMessage = false;
  static bool otaBootMarked = false;
  static unsigned long lastOtaEnforceAttemptMs = 0;

  if (!mqttClient.connected()) {
    sentReadyMessage = false;  // Reset flag on disconnect
    Serial.print("Connecting MQTT (TLS)… ");
    LED::Blink(0.5);

    mqttClient.setCallback(messageCallback);

    if (mqttClient.connect(CLIENT_ID.c_str(), USER.c_str(), PASS.c_str())) {
      LED::On();
      Serial.println("connected");
      mqttClient.subscribe(STATUS_TOPIC.c_str());
      Heartbeat::send(true);
    } else {
      Serial.print("failed, rc=");
      Serial.print(mqttClient.state());   // -4 timeout, 5 not authorized, etc.
      Serial.println("; retrying next loop (hold BOOT 3s to factory reset)");

      // Check if user is holding BOOT button to trigger factory reset
      if (FactoryReset::checkButtonHeld()) {
        FactoryReset::performReset();  // Never returns - reboots device
      }

      return;
    }
  }

  mqttClient.loop();

  if (mqttClient.connected() && !otaBootMarked) {
    ActionOTA::markBooted();
    otaBootMarked = true;
  }

  // Auto-enforcement disabled - OTA only via explicit ota_update command
  // if (mqttClient.connected()) {
  //   ActionOTA::OtaStatus otaStatus = ActionOTA::getStatus();
  //   unsigned long nowMs = millis();
  //   if (otaStatus.updateRequired &&
  //       (lastOtaEnforceAttemptMs == 0 || nowMs - lastOtaEnforceAttemptMs >= OTA_ENFORCE_INTERVAL_MS)) {
  //     lastOtaEnforceAttemptMs = nowMs;
  //     if (ActionOTA::enforceDesiredVersion()) {
  //       return;
  //     }
  //   }
  // }

  if (Heartbeat::isEnabled()) {
    unsigned long now = millis();
    if (now - lastPublish >= PUBLISH_INTERVAL) {
      lastPublish = now;
      ++publishCount;
      String payload = String("{\"count\":") + publishCount +
                       ",\"time\":\"" + getTimestamp() + "\"}";
      if (publishInternal(STATUS_TOPIC.c_str(), payload)) {
        Serial.println("Published → " + payload);
      } else {
        Serial.println("Publish failed");
      }
    }
  }

  // Send "ready to play" message once per connection session
  if (mqttClient.connected() && !sentReadyMessage) {
    ActionOTA::OtaStatus otaStatus = ActionOTA::getStatus();

    StaticJsonDocument<1024> doc;
    doc["sender"] = "firmware";
    doc["status"] = "ready";
    doc["message"] = otaStatus.updateRequired
      ? "Connected - OTA required (running behind desired)"
      : "Connected - you can now play game on";
    doc["firmware_version"] = otaStatus.runningVersion;
    doc["compiled_firmware_version"] = FIRMWARE_VERSION;
    doc["running_version"] = otaStatus.runningVersion;
    doc["desired_version"] = otaStatus.desiredVersion;
    doc["ota_state"] = otaStatus.otaState;
    doc["ota_update_required"] = otaStatus.updateRequired;
    doc["last_attempted_version"] = otaStatus.lastAttemptedVersion;
    if (otaStatus.lastError.length()) doc["last_error"] = otaStatus.lastError;
    if (otaStatus.desiredUrl.length()) doc["desired_url"] = otaStatus.desiredUrl;
    doc["ready_message_revision"] = 20;

    // Reset diagnostics - helps track brownouts and crashes
    doc["last_reset_reason"] = lastResetReason;
    doc["boot_free_heap"] = bootFreeHeap;
    doc["boot_min_free_heap"] = bootMinFreeHeap;
    doc["current_free_heap"] = ESP.getFreeHeap();

    String readyPayload;
    serializeJson(doc, readyPayload);

    if (publishInternal(STATUS_TOPIC.c_str(), readyPayload)) {
      Serial.println("Published ready message → " + readyPayload);
      sentReadyMessage = true;
    }
  }
}

void BuddyMQTT::listen() {
  ensureInited();
  if (!mqttClient.connected()) return;

  if (Heartbeat::isEnabled()) Serial.println("Start of Listen Loop");

  receivedMessage.clear();

  if (Heartbeat::isEnabled()) {
    while (mqttClient.connected() && receivedMessage == "") {
      mqttClient.loop();
      if (Heartbeat::shouldSend()) { Heartbeat::send(true); Heartbeat::markSent(); }
    }
  } else {
    while (mqttClient.connected() && receivedMessage == "") {
      mqttClient.loop();
      delay(10);
    }
  }

  if (receivedMessage.length()) {
    Serial.println("Received MQTT message:");
    Serial.println(receivedMessage);
    ActionController::dispatch(receivedMessage);
  }
  if (Heartbeat::isEnabled()) Serial.println("End of Listen Loop");
}

void BuddyMQTT::sendInProgress(const String& actionId, const String& type, JsonVariantConst phrase, const char* logMessage, int magnetPosition, int useModel, const String& useModelJson) {
  ensureInited();
  Serial.printf("[sendInProgress] useModel param = %d\n", useModel);
  StaticJsonDocument<512> doc;
  doc["sender"]    = "firmware";
  doc["action_id"] = actionId;
  doc["status"]    = "in_progress";
  if (type.length()) doc["type"] = type;
  if (logMessage && logMessage[0]) doc["log"] = logMessage;
  if (magnetPosition >= 0) doc["MagnetPosition"] = magnetPosition;
  copyUseModel(useModelJson, useModel, doc);
  if (doc["use_model"].isNull()) {
    Serial.println("[sendInProgress] useModel < 0, NOT adding to doc");
  } else {
    String useModelOut;
    serializeJson(doc["use_model"], useModelOut);
    Serial.printf("[sendInProgress] Added use_model = %s\n", useModelOut.c_str());
  }
  copyphrase(phrase, doc);
  injectWorkflow(doc);
  String out;
  serializeJson(doc, out);
  if (publishInternal(STATUS_TOPIC.c_str(), out)) {
    Serial.print("Sent in_progress → ");
    Serial.println(out);
  } else {
    Serial.println("Failed to send in_progress");
  }
}

void BuddyMQTT::sendCompleted(const String& actionId, const String& type, const char* status, JsonVariantConst phrase) {
  ensureInited();
  StaticJsonDocument<512> doc;
  doc["sender"]    = "firmware";
  doc["action_id"] = actionId;
  doc["status"]    = status ? status : "completed";
  if (type.length()) doc["type"] = type;
  copyphrase(phrase, doc);
  injectWorkflow(doc);
  String out;
  serializeJson(doc, out);
  if (publishInternal(STATUS_TOPIC.c_str(), out)) {
    Serial.print("Sent completed → ");
    Serial.println(out);
  } else {
    Serial.println("Failed to send completed");
  }
}

void BuddyMQTT::sendCompletedDetails(const String& actionId, const char* key, const String& jsonOut, const String& type, const char* status, JsonVariantConst phrase) {
  ensureInited();
  StaticJsonDocument<1024> doc;
  doc["sender"]    = "firmware";
  doc["action_id"] = actionId;
  doc["status"]    = status ? status : "completed";
  if (type.length()) doc["type"] = type;
  copyphrase(phrase, doc);
  injectWorkflow(doc);

  StaticJsonDocument<768> nested;
  if (deserializeJson(nested, jsonOut) == DeserializationError::Ok) {
    JsonObject src = nested.as<JsonObject>();
    JsonObject dst = doc.createNestedObject(key);
    for (auto kv : src) dst[kv.key()] = kv.value();
  } else {
    doc[key] = jsonOut;  // fallback
  }

  String out;
  serializeJson(doc, out);
  if (publishInternal(STATUS_TOPIC.c_str(), out)) {
    Serial.print("Sent completed with data → ");
    Serial.println(out);
  } else {
    Serial.println("Failed to send completed with data");
    if (actionId.length()) {
      sendCompleted(actionId, type, "failed", phrase);
    }
  }
}

void BuddyMQTT::sendCalibrationValues(const String& actionId) {
  ensureInited();

  Preferences prefs;
  if (!prefs.begin("config", true)) {
    Serial.println("[MQTT] Failed to open prefs namespace 'config' for calibrationvalues");
    sendCompleted(actionId, "calibrationvalues", "failed");
    return;
  }

  StaticJsonDocument<1024> doc;
  auto addFloatOrNull = [&](const char* storageKey, const char* reportKey, float defaultValue) {
    if (prefs.isKey(storageKey)) {
      doc[reportKey] = prefs.getFloat(storageKey, defaultValue);
    } else if (prefs.isKey(reportKey)) { // legacy long key fallback
      doc[reportKey] = prefs.getFloat(reportKey, defaultValue);
    } else {
      doc[reportKey] = nullptr;
    }
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

  prefs.end();

  Preferences rotPrefs;
  if (rotPrefs.begin("rot", true)) {
    if (rotPrefs.isKey("origin_magnet")) {
      doc["origin_magnet"] = rotPrefs.getInt("origin_magnet", -1);
    } else {
      doc["origin_magnet"] = nullptr;
    }
    rotPrefs.end();
  } else {
    doc["origin_magnet"] = nullptr;
  }

  String jsonOut;
  serializeJson(doc, jsonOut);
  sendCompletedDetails(actionId, "calibrationvalues", jsonOut, "calibrationvalues");
}

// Write all bytes with small sub-chunks and retry if partial writes happen.
// Returns true when exactly 'len' bytes have been pushed.
bool writeAll(PubSubClient& client, const uint8_t* data, size_t len) {
  const size_t SUB_CHUNK = 20224;          // conservative for WiFiClientSecure
  const unsigned long PER_WRITE_TIMEOUT_MS = 2000;

  size_t sent = 0;
  while (sent < len) {
    size_t toSend = len - sent;
    if (toSend > SUB_CHUNK) toSend = SUB_CHUNK;

    unsigned long start = millis();
    size_t wrote = client.write(data + sent, toSend);

    if (wrote == 0) {
      // Give the stack a moment, then time out if it keeps failing
      delay(5);
      if (millis() - start > PER_WRITE_TIMEOUT_MS) {
        return false;
      }
      continue; // retry this sub-chunk
    }

    sent += wrote;
    // Yield so WiFi/TLS can progress
    delay(1);
  }
  return true;
}


bool BuddyMQTT::publishBinary(const String& topic, const uint8_t* data, size_t length) {
  ensureInited();
  if (!topic.length() || !data || length == 0) {
    Serial.println("publishBinary: invalid arguments");
    return false;
  }
  if (!mqttClient.connected()) {
    Serial.println("publishBinary: MQTT not connected");
    return false;
  }
  if (!mqttClient.beginPublish(topic.c_str(), length, false)) {
    Serial.println("publishBinary: beginPublish failed");
    return false;
  }

  bool ok = writeAll(mqttClient, data, length);
  if (!ok) {
    Serial.println("publishBinary: writeAll failed");
    mqttClient.endPublish(); // end regardless to flush/reset state
    return false;
  }

  ok = mqttClient.endPublish();
  if (!ok) Serial.println("publishBinary: endPublish failed");
  return ok;
}

bool BuddyMQTT::publishStatusPhoto(const String& actionId, const String& requester, const uint8_t* data, size_t length, JsonVariantConst phrase, int magnetPosition, int useModel, const String& useModelJson) {
  ensureInited();
  if (!data || length == 0) {
    Serial.println("publishStatusPhoto: no data provided");
    return false;
  }
  if (!mqttClient.connected()) {
    Serial.println("publishStatusPhoto: MQTT not connected");
    return false;
  }

  StaticJsonDocument<512> doc;
  doc["sender"] = "firmware";
  if (actionId.length())   doc["action_id"]   = actionId;
  doc["photo"] = "sending_photo";
  if (requester.length())  doc["requested_by"] = requester;
  if (magnetPosition >= 0) doc["MagnetPosition"] = magnetPosition;
  copyUseModel(useModelJson, useModel, doc);
  copyphrase(phrase, doc);
  injectWorkflow(doc);

  String prefix;
  serializeJson(doc, prefix);
  if (!prefix.length() || prefix[prefix.length() - 1] != '}') {
    Serial.println("publishStatusPhoto: failed to build prefix");
    return false;
  }
  prefix.remove(prefix.length() - 1); // strip closing brace
  prefix += ",\"payload\":";          // now we'll stream raw JPEG, then close with '}'.

  const char* topic  = STATUS_TOPIC.c_str();
  const char  suffix[] = "}";

  size_t totalLen = prefix.length() + length + (sizeof(suffix) - 1);
  if (!mqttClient.beginPublish(topic, totalLen, false)) {
    Serial.println("publishStatusPhoto: beginPublish failed");
    return false;
  }

  // prefix
  if (!writeAll(mqttClient, reinterpret_cast<const uint8_t*>(prefix.c_str()), prefix.length())) {
    Serial.println("publishStatusPhoto: prefix writeAll failed");
    mqttClient.endPublish();
    return false;
  }

  // binary JPEG
  if (!writeAll(mqttClient, data, length)) {
    Serial.println("publishStatusPhoto: binary writeAll failed");
    mqttClient.endPublish();
    return false;
  }

  // suffix
  if (!writeAll(mqttClient, reinterpret_cast<const uint8_t*>(suffix), sizeof(suffix) - 1)) {
    Serial.println("publishStatusPhoto: suffix writeAll failed");
    mqttClient.endPublish();
    return false;
  }

  bool ok = mqttClient.endPublish();
  if (!ok) Serial.println("publishStatusPhoto: endPublish failed");
  return ok;
}

void BuddyMQTT::setWorkflowContext(int workflowId, int workflowEventId) {
  currentWorkflowId      = workflowId;
  currentWorkflowEventId = workflowEventId;
}

void BuddyMQTT::clearWorkflowContext() {
  currentWorkflowId      = -1;
  currentWorkflowEventId = -1;
}

void BuddyMQTT::sendDebug(const String& component, const String& message) {
  ensureInited();
  if (!mqttClient.connected()) return;

  StaticJsonDocument<256> doc;
  doc["debug"] = component;
  doc["msg"] = message;

  String payload;
  serializeJson(doc, payload);

  publishInternal(STATUS_TOPIC.c_str(), payload);
}

void BuddyMQTT::setResetReason(const char* reason, uint32_t freeHeap, uint32_t minFreeHeap) {
  lastResetReason = reason;
  bootFreeHeap = freeHeap;
  bootMinFreeHeap = minFreeHeap;
}
