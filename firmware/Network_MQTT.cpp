#include "Network_MQTT.h"
#include "Network_Wifi.h"
#include "Network_MQTT_Router.h"
#include "Utility_Heartbeat.h"
#include "Utility_FactoryReset.h"
#include <WiFi.h>
#include <WiFiClientSecure.h>   // secure TCP
#include <PubSubClient.h>
#include <Preferences.h>
#include <Arduino.h>
#include <time.h>
#include <Utility_LED.h>
#include <ArduinoJson.h>

#define FIRMWARE_VERSION "not_set"

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
    mqttClient.setBufferSize(6144);

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

  // Send "ready to play" message once per connection session
  if (mqttClient.connected() && !sentReadyMessage) {
    StaticJsonDocument<1024> doc;
    doc["sender"] = "firmware";
    doc["status"] = "ready";
    doc["message"] = "Connected - you can now play game on";
    doc["firmware_version"] = FIRMWARE_VERSION;
    doc["compiled_firmware_version"] = FIRMWARE_VERSION;
    doc["running_version"] = FIRMWARE_VERSION;
    doc["desired_version"] = "";
    doc["ota_state"] = "disabled";
    doc["ota_update_required"] = false;
    doc["last_attempted_version"] = "";
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
      BuddyWifi::maintain();
      mqttClient.loop();
      if (Heartbeat::shouldSend()) { Heartbeat::send(true); Heartbeat::markSent(); }
    }
  } else {
    while (mqttClient.connected() && receivedMessage == "") {
      BuddyWifi::maintain();
      mqttClient.loop();
      delay(10);
    }
  }

  if (receivedMessage.length()) {
    Serial.println("Received MQTT message:");
    Serial.println(receivedMessage);
    MQTTRouter::route(receivedMessage);
  }
  if (Heartbeat::isEnabled()) Serial.println("End of Listen Loop");
}

void BuddyMQTT::sendInProgress(const String& actionId, const String& type, JsonVariantConst phrase, const char* logMessage, int useModel, const String& useModelJson) {
  ensureInited();
  Serial.printf("[sendInProgress] useModel param = %d\n", useModel);
  StaticJsonDocument<512> doc;
  doc["sender"]    = "firmware";
  doc["action_id"] = actionId;
  doc["status"]    = "in_progress";
  if (type.length()) doc["type"] = type;
  if (logMessage && logMessage[0]) doc["log"] = logMessage;
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
  DynamicJsonDocument doc(6144);
  doc["sender"]    = "firmware";
  doc["action_id"] = actionId;
  doc["status"]    = status ? status : "completed";
  if (type.length()) doc["type"] = type;
  copyphrase(phrase, doc);
  injectWorkflow(doc);

  DynamicJsonDocument nested(6144);
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


bool BuddyMQTT::publishStatusPhoto(const String& actionId, const String& requester, const uint8_t* data, size_t length, JsonVariantConst phrase, int useModel, const String& useModelJson) {
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
