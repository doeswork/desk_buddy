#include "SerialProvisioning.h"

#include <Arduino.h>
#include <ArduinoJson.h>
#include <Preferences.h>

namespace SerialProvisioning {
namespace {
  constexpr size_t MAX_COMMAND_BYTES = 512;
  String pending;
  bool discardingOversizedCommand = false;

  void reply(const char* kind, const char* status, const char* error = nullptr,
             const char* detail = nullptr, const char* detailKey = "ssid") {
    StaticJsonDocument<256> response;
    response["serial_provisioning"] = kind;
    response["status"] = status;
    if (error != nullptr) response["error"] = error;
    if (detail != nullptr) response[detailKey] = detail;
    serializeJson(response, Serial);
    Serial.println();
  }

  bool validPassword(const String& password) {
    // Empty selects an open network. WPA/WPA2 passphrases are 8-63 chars;
    // a 64-character hexadecimal PSK is also accepted by the ESP32 stack.
    if (password.length() == 0) return true;
    if (password.length() >= 8 && password.length() <= 63) return true;
    if (password.length() != 64) return false;
    for (size_t index = 0; index < password.length(); ++index) {
      if (!isxdigit(static_cast<unsigned char>(password[index]))) return false;
    }
    return true;
  }

  bool requiredText(const char* value, size_t maximum) {
    return value != nullptr && strlen(value) > 0 && strlen(value) <= maximum;
  }

  struct SavedText {
    bool present = false;
    String value;
  };

  SavedText savedText(Preferences& preferences, const char* key) {
    SavedText saved;
    saved.present = preferences.isKey(key);
    saved.value = preferences.getString(key, "");
    return saved;
  }

  void restoreText(Preferences& preferences, const char* key, const SavedText& saved) {
    if (saved.present) {
      preferences.putString(key, saved.value);
    } else {
      preferences.remove(key);
    }
  }

  void restoreBool(Preferences& preferences, const char* key, bool present, bool value) {
    if (present) {
      preferences.putBool(key, value);
    } else {
      preferences.remove(key);
    }
  }

  void restoreInt(Preferences& preferences, const char* key, bool present, int value) {
    if (present) {
      preferences.putInt(key, value);
    } else {
      preferences.remove(key);
    }
  }

  void handleProfile(JsonVariantConst command) {
    JsonObjectConst wifi = command["wifi"].as<JsonObjectConst>();
    JsonObjectConst mqtt = command["mqtt"].as<JsonObjectConst>();
    if (wifi.isNull() || mqtt.isNull()) {
      reply("profile", "error", "wifi and mqtt objects are required");
      return;
    }

    const char* ssidValue = wifi["ssid"] | "";
    const char* wifiPasswordValue = wifi["password"] | "";
    String ssid(ssidValue);
    String wifiPassword(wifiPasswordValue);
    if (ssid.length() == 0 || ssid.length() > 32) {
      reply("profile", "error", "SSID must be 1-32 bytes");
      return;
    }
    if (!validPassword(wifiPassword)) {
      reply("profile", "error", "WiFi password must be empty, 8-63 characters, or a 64-digit hex PSK");
      return;
    }

    const char* serverValue = mqtt["server"] | "";
    const char* userValue = mqtt["user"] | "";
    const char* passwordValue = mqtt["password"] | "";
    const char* clientIdValue = mqtt["client_id"] | "";
    long port = mqtt["port"] | 0;
    if (!requiredText(serverValue, 128)) {
      reply("profile", "error", "MQTT server must be 1-128 bytes");
      return;
    }
    if (port < 1 || port > 65535) {
      reply("profile", "error", "MQTT port must be between 1 and 65535");
      return;
    }
    if (!requiredText(userValue, 64) || !requiredText(passwordValue, 128) ||
        !requiredText(clientIdValue, 64)) {
      reply("profile", "error", "MQTT user, password, and client_id are required");
      return;
    }
    if (!mqtt["tls"].is<bool>()) {
      reply("profile", "error", "MQTT tls must be true or false");
      return;
    }
    const bool tls = mqtt["tls"].as<bool>();

    // Snapshot both namespaces before changing either one. NVS namespaces
    // are separate commits, so a failed second write must put the first one
    // back exactly as it was. No credential is ever put in the response.
    Preferences oldWifi;
    Preferences oldMqtt;
    if (!oldWifi.begin("wifi", false) || !oldMqtt.begin("mqtt", false)) {
      oldWifi.end();
      oldMqtt.end();
      reply("profile", "error", "Could not open WiFi and MQTT preferences");
      return;
    }
    const SavedText oldSsid = savedText(oldWifi, "ssid");
    const SavedText oldWifiPassword = savedText(oldWifi, "password");
    const SavedText oldServer = savedText(oldMqtt, "server");
    const SavedText oldUser = savedText(oldMqtt, "user");
    const SavedText oldMqttPassword = savedText(oldMqtt, "password");
    const SavedText oldClientId = savedText(oldMqtt, "client_id");
    const bool oldPortPresent = oldMqtt.isKey("port");
    const int oldPort = oldMqtt.getInt("port", 0);
    const bool oldTlsPresent = oldMqtt.isKey("tls");
    const bool oldTls = oldMqtt.getBool("tls", false);
    oldWifi.end();
    oldMqtt.end();

    Preferences wifiPreferences;
    if (!wifiPreferences.begin("wifi", false)) {
      reply("profile", "error", "Could not open WiFi preferences");
      return;
    }
    wifiPreferences.putString("ssid", ssid);
    wifiPreferences.putString("password", wifiPassword);
    const bool wifiSaved = wifiPreferences.getString("ssid", "") == ssid &&
                           wifiPreferences.getString("password", "") == wifiPassword;
    wifiPreferences.end();
    if (!wifiSaved) {
      if (wifiPreferences.begin("wifi", false)) {
        restoreText(wifiPreferences, "ssid", oldSsid);
        restoreText(wifiPreferences, "password", oldWifiPassword);
        wifiPreferences.end();
      }
      reply("profile", "error", "Could not save WiFi preferences");
      return;
    }

    Preferences mqttPreferences;
    if (!mqttPreferences.begin("mqtt", false)) {
      if (wifiPreferences.begin("wifi", false)) {
        restoreText(wifiPreferences, "ssid", oldSsid);
        restoreText(wifiPreferences, "password", oldWifiPassword);
        wifiPreferences.end();
      }
      reply("profile", "error", "Could not open MQTT preferences; WiFi was rolled back");
      return;
    }
    mqttPreferences.putString("server", serverValue);
    mqttPreferences.putInt("port", static_cast<int>(port));
    mqttPreferences.putString("user", userValue);
    mqttPreferences.putString("password", passwordValue);
    mqttPreferences.putString("client_id", clientIdValue);
    mqttPreferences.putBool("tls", tls);
    const bool mqttSaved = mqttPreferences.getString("server", "") == serverValue &&
                           mqttPreferences.getInt("port", 0) == port &&
                           mqttPreferences.getString("user", "") == userValue &&
                           mqttPreferences.getString("password", "") == passwordValue &&
                           mqttPreferences.getString("client_id", "") == clientIdValue &&
                           mqttPreferences.getBool("tls", !tls) == tls;
    mqttPreferences.end();
    if (!mqttSaved) {
      if (wifiPreferences.begin("wifi", false)) {
        restoreText(wifiPreferences, "ssid", oldSsid);
        restoreText(wifiPreferences, "password", oldWifiPassword);
        wifiPreferences.end();
      }
      if (mqttPreferences.begin("mqtt", false)) {
        restoreText(mqttPreferences, "server", oldServer);
        restoreInt(mqttPreferences, "port", oldPortPresent, oldPort);
        restoreText(mqttPreferences, "user", oldUser);
        restoreText(mqttPreferences, "password", oldMqttPassword);
        restoreText(mqttPreferences, "client_id", oldClientId);
        restoreBool(mqttPreferences, "tls", oldTlsPresent, oldTls);
        mqttPreferences.end();
      }
      reply("profile", "error", "Could not save MQTT preferences; changes were rolled back");
      return;
    }

    // One acknowledgement and one restart after both namespaces verify.
    reply("profile", "saved", nullptr, serverValue, "server");
    Serial.flush();
    delay(350);
    ESP.restart();
  }

  void handleWifi(JsonVariantConst command) {
    const char* ssidValue = command["ssid"] | "";
    const char* passwordValue = command["password"] | "";
    String ssid(ssidValue);
    String password(passwordValue);
    if (ssid.length() == 0 || ssid.length() > 32) {
      reply("wifi", "error", "SSID must be 1-32 bytes");
      return;
    }
    if (!validPassword(password)) {
      reply("wifi", "error", "Password must be empty, 8-63 characters, or a 64-digit hex PSK");
      return;
    }

    Preferences preferences;
    if (!preferences.begin("wifi", false)) {
      reply("wifi", "error", "Could not open WiFi preferences");
      return;
    }
    const String previousSsid = preferences.getString("ssid", "");
    const String previousPassword = preferences.getString("password", "");
    preferences.putString("ssid", ssid);
    preferences.putString("password", password);
    const bool saved = preferences.getString("ssid", "") == ssid &&
                       preferences.getString("password", "") == password;
    if (!saved) {
      // Avoid leaving a new SSID paired with an old password after a partial
      // NVS failure. Best-effort restoration keeps the prior setup usable.
      preferences.putString("ssid", previousSsid);
      preferences.putString("password", previousPassword);
      preferences.end();
      reply("wifi", "error", "Could not save WiFi preferences");
      return;
    }
    preferences.end();

    // Never echo the password. Flush the acknowledgement before restarting so
    // Studio can distinguish a successful save from a cable disconnect.
    reply("wifi", "saved", nullptr, ssid.c_str());
    Serial.flush();
    delay(350);
    ESP.restart();
  }

  // Mirrors WebServerForStartup's own MQTT semantics: every field is
  // optional and applied only if non-empty, so setting the broker over
  // serial can never blank out a credential the caller did not mean to
  // touch. `server` is the one field worth requiring outright — an MQTT
  // setup with everything but a broker address is not a usable one.
  void handleMqtt(JsonVariantConst command) {
    const char* serverValue = command["server"] | "";
    String server(serverValue);
    if (server.length() == 0) {
      reply("mqtt", "error", "server is required");
      return;
    }

    long port = command["port"] | 0;
    if (port < 0 || port > 65535) {
      reply("mqtt", "error", "port must be between 1 and 65535");
      return;
    }

    Preferences preferences;
    if (!preferences.begin("mqtt", false)) {
      reply("mqtt", "error", "Could not open MQTT preferences");
      return;
    }
    preferences.putString("server", server);
    if (port > 0) preferences.putInt("port", static_cast<int>(port));

    const char* userValue = command["user"] | "";
    if (strlen(userValue) > 0) preferences.putString("user", String(userValue));
    const char* passwordValue = command["password"] | "";
    if (strlen(passwordValue) > 0) preferences.putString("password", String(passwordValue));
    const char* clientIdValue = command["client_id"] | "";
    if (strlen(clientIdValue) > 0) preferences.putString("client_id", String(clientIdValue));
    // Unlike the string fields, `tls` is written whenever it is present at
    // all: false is a meaningful value, so "absent" is the only way to mean
    // "leave it alone" — an empty-string test would make `false` unsendable.
    if (command["tls"].is<bool>()) preferences.putBool("tls", command["tls"].as<bool>());
    preferences.end();

    // Never echo the password. Flush before restarting so Studio can tell a
    // successful save from a cable disconnect, same as set_wifi.
    reply("mqtt", "saved", nullptr, server.c_str(), "server");
    Serial.flush();
    delay(350);
    ESP.restart();
  }

  void handle(const String& line) {
    // The wire protocol is capped at 512 bytes, but ArduinoJson needs room
    // for the object tree in addition to the encoded input bytes.
    StaticJsonDocument<768> command;
    DeserializationError parseError = deserializeJson(command, line);
    if (parseError) {
      reply("error", "error", "Command must be valid JSON");
      return;
    }

    const char* name = command["desk_buddy_command"] | "";
    if (strcmp(name, "set_wifi") == 0) {
      handleWifi(command.as<JsonVariantConst>());
    } else if (strcmp(name, "set_mqtt") == 0) {
      handleMqtt(command.as<JsonVariantConst>());
    } else if (strcmp(name, "set_profile") == 0) {
      handleProfile(command.as<JsonVariantConst>());
    } else {
      reply("error", "error", "Unknown serial provisioning command");
    }
  }
}

void maintain() {
  while (Serial.available() > 0) {
    const char next = static_cast<char>(Serial.read());
    if (next == '\r') continue;
    if (next == '\n') {
      if (discardingOversizedCommand) {
        reply("error", "error", "Serial command was too large");
      } else if (pending.length() > 0) {
        handle(pending);
      }
      pending = "";
      discardingOversizedCommand = false;
      continue;
    }
    if (discardingOversizedCommand) continue;
    if (pending.length() >= MAX_COMMAND_BYTES) {
      pending = "";
      discardingOversizedCommand = true;
      continue;
    }
    pending += next;
  }
}
}
