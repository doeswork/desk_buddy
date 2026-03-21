// ActionOTA.cpp
#include "ActionOTA.h"
#include "BuddyMQTT.h"
#include <ArduinoJson.h>

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <Update.h>
#include <LED.h>
#include <Preferences.h>
#include "mbedtls/sha256.h"

namespace ActionOTA {
namespace {
  constexpr const char* OTA_NS = "ota";
  constexpr const char* CONFIG_NS = "config";

  constexpr const char* KEY_FW_VER = "fw_ver";
  constexpr const char* KEY_RUNNING_VER = "running_ver";
  constexpr const char* KEY_DESIRED_VER = "desired_ver";
  constexpr const char* KEY_DESIRED_URL = "desired_url";
  constexpr const char* KEY_DESIRED_SHA = "desired_sha";
  constexpr const char* KEY_LAST_ATTEMPT_VER = "last_attempt";
  constexpr const char* KEY_LAST_ERROR = "last_error";
  constexpr const char* KEY_OTA_STATE = "ota_state";

  constexpr const char* STATE_IDLE = "idle";
  constexpr const char* STATE_OUTDATED = "outdated";
  constexpr const char* STATE_DOWNLOADING = "downloading";
  constexpr const char* STATE_FLASHING = "flashing";
  constexpr const char* STATE_REBOOTING = "rebooting";
  constexpr const char* STATE_BOOTED = "booted";
  constexpr const char* STATE_FAILED = "failed";

  // Extract version from GitHub release URL.
  // e.g. https://github.com/user/repo/releases/download/v0.0.3/firmware.bin -> v0.0.3
  String extractVersionFromUrl(const char* url) {
    String urlStr(url ? url : "");
    int downloadIdx = urlStr.indexOf("/download/");
    if (downloadIdx == -1) return "";

    int versionStart = downloadIdx + 10; // "/download/"
    int versionEnd = urlStr.indexOf("/", versionStart);
    if (versionEnd == -1) return "";

    return urlStr.substring(versionStart, versionEnd);
  }

  String prefsReadString(const char* ns, const char* key, const String& fallback = "") {
    Preferences prefs;
    if (!prefs.begin(ns, true)) return fallback;
    String out = prefs.getString(key, fallback);
    prefs.end();
    return out;
  }

  bool prefsWriteString(const char* ns, const char* key, const String& value) {
    Preferences prefs;
    if (!prefs.begin(ns, false)) return false;
    bool ok = prefs.putString(key, value) > 0;
    prefs.end();
    return ok;
  }

  bool setOtaState(const String& state, const String& lastError = "") {
    bool okState = prefsWriteString(OTA_NS, KEY_OTA_STATE, state);
    bool okErr = prefsWriteString(OTA_NS, KEY_LAST_ERROR, lastError);
    return okState && okErr;
  }

  String normalizeSha256(const char* in) {
    if (!in) return "";
    String s(in);
    s.trim();

    if (s.startsWith("sha256:")) s = s.substring(7);
    s.trim();
    s.toLowerCase();

    // must be 64 hex chars
    if (s.length() != 64) return "";

    for (size_t i = 0; i < s.length(); i++) {
      char c = s[i];
      bool hex = (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f');
      if (!hex) return "";
    }

    return s;
  }

  String sha256ToHex(const uint8_t hash[32]) {
    static const char* hex = "0123456789abcdef";
    char out[65];
    for (int i = 0; i < 32; i++) {
      out[i * 2] = hex[(hash[i] >> 4) & 0xF];
      out[i * 2 + 1] = hex[hash[i] & 0xF];
    }
    out[64] = '\0';
    return String(out);
  }

  String readJsonString(const JsonVariantConst& v) {
    if (v.isNull()) return "";
    if (v.is<const char*>()) {
      const char* s = v.as<const char*>();
      return s ? String(s) : String();
    }
    if (v.is<String>()) return v.as<String>();
    if (v.is<long>()) return String(v.as<long>());
    if (v.is<int>()) return String(v.as<int>());
    if (v.is<float>()) return String(v.as<float>(), 6);
    if (v.is<double>()) return String(v.as<double>(), 6);
    return "";
  }

  bool persistDesiredTarget(const String& desiredVersion, const String& url, const String& sha256) {
    bool ok = true;
    ok = prefsWriteString(OTA_NS, KEY_DESIRED_VER, desiredVersion) && ok;
    ok = prefsWriteString(OTA_NS, KEY_DESIRED_URL, url) && ok;
    ok = prefsWriteString(OTA_NS, KEY_DESIRED_SHA, sha256) && ok;
    ok = prefsWriteString(OTA_NS, KEY_LAST_ATTEMPT_VER, desiredVersion) && ok;
    return ok;
  }

  OtaStatus readStatusInternal() {
    OtaStatus out;
    out.runningVersion = prefsReadString(OTA_NS, KEY_RUNNING_VER, FIRMWARE_VERSION);
    if (out.runningVersion.length() == 0) out.runningVersion = FIRMWARE_VERSION;
    out.desiredVersion = prefsReadString(OTA_NS, KEY_DESIRED_VER, "");
    out.desiredUrl = prefsReadString(OTA_NS, KEY_DESIRED_URL, "");
    out.desiredSha = prefsReadString(OTA_NS, KEY_DESIRED_SHA, "");
    out.otaState = prefsReadString(OTA_NS, KEY_OTA_STATE, STATE_IDLE);
    out.lastAttemptedVersion = prefsReadString(OTA_NS, KEY_LAST_ATTEMPT_VER, "");
    out.lastError = prefsReadString(OTA_NS, KEY_LAST_ERROR, "");
    out.updateRequired = out.desiredVersion.length() > 0 && out.desiredVersion != out.runningVersion;
    return out;
  }

  bool runFromTarget(const String& url, const String& desiredVersionIn, const String& expectedSha, const char* sourceTag) {
    if (url.length() == 0) {
      setOtaState(STATE_FAILED, "No URL provided");
      return false;
    }

    String desiredVersion = desiredVersionIn;
    if (desiredVersion.length() == 0) {
      desiredVersion = extractVersionFromUrl(url.c_str());
    }

    if (desiredVersion.length() == 0) {
      setOtaState(STATE_FAILED, "Could not determine desired version");
      BuddyMQTT::sendDebug("OTA", "ERROR: desired version missing (set 'version' field or use releases/download URL)");
      return false;
    }

    persistDesiredTarget(desiredVersion, url, expectedSha);
    setOtaState(STATE_DOWNLOADING, "");
    BuddyMQTT::sendDebug("OTA", String("Starting OTA from ") + sourceTag + ": desired=" + desiredVersion + " url=" + url);

    LED::Blink(0.1);

    WiFiClientSecure client;
    client.setInsecure();

    HTTPClient http;

    auto fail = [&](const String& reason, bool abortUpdate) -> bool {
      BuddyMQTT::sendDebug("OTA", "ERROR: " + reason);
      if (abortUpdate) Update.abort();
      http.end();
      LED::Off();
      setOtaState(STATE_FAILED, reason);
      return false;
    };

    if (!http.begin(client, url)) {
      return fail("HTTP begin failed", false);
    }

    http.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);

    int code = http.GET();
    if (code != HTTP_CODE_OK) {
      return fail("HTTP GET failed: " + String(code), false);
    }

    int total = http.getSize(); // may be -1 if unknown
    BuddyMQTT::sendDebug("OTA", "HTTP size: " + String(total));

    if (!Update.begin((total > 0) ? (size_t)total : UPDATE_SIZE_UNKNOWN, U_FLASH)) {
      return fail("Update.begin failed", false);
    }

    setOtaState(STATE_FLASHING, "");

    // SHA256 streaming
    mbedtls_sha256_context ctx;
    mbedtls_sha256_init(&ctx);
    mbedtls_sha256_starts(&ctx, 0 /* 0 = SHA256 */);

    WiFiClient* stream = http.getStreamPtr();

    uint8_t buf[2048];
    size_t writtenTotal = 0;
    unsigned long lastLog = millis();

    while (http.connected()) {
      size_t avail = stream->available();
      if (avail == 0) {
        delay(5);
        continue;
      }

      int toRead = (avail > sizeof(buf)) ? sizeof(buf) : (int)avail;
      int readBytes = stream->readBytes(buf, toRead);
      if (readBytes <= 0) break;

      mbedtls_sha256_update(&ctx, buf, readBytes);

      size_t w = Update.write(buf, readBytes);
      if (w != (size_t)readBytes) {
        mbedtls_sha256_free(&ctx);
        return fail("Update.write failed (short write)", true);
      }

      writtenTotal += w;

      // Progress log ~every 1s
      if (millis() - lastLog > 1000) {
        lastLog = millis();
        if (total > 0) {
          int pct = (int)((writtenTotal * 100ULL) / (unsigned long)total);
          Serial.printf("[OTA] %d%% (%u/%d)\n", pct, (unsigned)writtenTotal, total);
        } else {
          Serial.printf("[OTA] %u bytes\n", (unsigned)writtenTotal);
        }
      }

      if (total > 0 && (int)writtenTotal >= total) break;
    }

    uint8_t hash[32];
    mbedtls_sha256_finish(&ctx, hash);
    mbedtls_sha256_free(&ctx);

    String actualSha = sha256ToHex(hash);
    BuddyMQTT::sendDebug("OTA", "Actual SHA256: " + actualSha);

    if (expectedSha.length() > 0 && actualSha != expectedSha) {
      return fail("SHA256 mismatch", true);
    }

    if (!Update.end(true)) {
      return fail(String("Update.end failed: ") + Update.errorString(), false);
    }

    if (!Update.isFinished()) {
      return fail("Update not finished", false);
    }

    http.end();

    setOtaState(STATE_REBOOTING, "");
    BuddyMQTT::sendDebug("OTA", "Update successful, rebooting");
    LED::On();
    delay(500);
    ESP.restart();
    return true;
  }
} // namespace

bool run(const String& message) {
  Serial.println("[OTA] Starting HTTPS OTA update");

  DynamicJsonDocument doc(1024);
  if (deserializeJson(doc, message) != DeserializationError::Ok) {
    setOtaState(STATE_FAILED, "Failed to parse OTA JSON");
    Serial.println("[OTA] Failed to parse JSON");
    return false;
  }

  const char* urlRaw = doc["url"].as<const char*>();
  if (!urlRaw || strlen(urlRaw) == 0) {
    setOtaState(STATE_FAILED, "No URL provided");
    Serial.println("[OTA] Error: No URL provided");
    return false;
  }
  String url(urlRaw);

  const char* sha256Raw = doc["sha256"].as<const char*>();
  String expectedSha = normalizeSha256(sha256Raw);

  if (sha256Raw && strlen(sha256Raw) > 0) {
    Serial.print("[OTA] Provided SHA256: ");
    Serial.println(sha256Raw);
    if (expectedSha.length() == 0) {
      BuddyMQTT::sendDebug("OTA", "WARNING: sha256 provided but invalid format (need 64 hex, optional 'sha256:' prefix)");
    } else {
      BuddyMQTT::sendDebug("OTA", "Expected SHA256 (normalized): " + expectedSha);
    }
  }

  String desiredVersion = readJsonString(doc["version"]);
  if (desiredVersion.length() == 0) {
    desiredVersion = extractVersionFromUrl(url.c_str());
  }

  return runFromTarget(url, desiredVersion, expectedSha, "command");
}

void markBooted() {
  // Running binary is ground truth for flashed firmware.
  prefsWriteString(OTA_NS, KEY_RUNNING_VER, FIRMWARE_VERSION);

  // Backward compatibility for existing telemetry that still reads fw_ver.
  prefsWriteString(OTA_NS, KEY_FW_VER, FIRMWARE_VERSION);
  prefsWriteString(CONFIG_NS, KEY_FW_VER, FIRMWARE_VERSION);

  OtaStatus status = readStatusInternal();
  if (status.desiredVersion.length() == 0) {
    setOtaState(STATE_IDLE, "");
  } else if (status.desiredVersion == status.runningVersion) {
    setOtaState(STATE_BOOTED, "");
  } else {
    prefsWriteString(OTA_NS, KEY_OTA_STATE, STATE_OUTDATED);
  }
}

OtaStatus getStatus() {
  return readStatusInternal();
}

bool enforceDesiredVersion() {
  OtaStatus status = readStatusInternal();
  if (!status.updateRequired) {
    return false;
  }

  if (status.desiredUrl.length() == 0) {
    setOtaState(STATE_FAILED, "desired_version exists but desired_url missing");
    BuddyMQTT::sendDebug("OTA", "ERROR: desired_version exists but desired_url missing");
    return false;
  }

  BuddyMQTT::sendDebug("OTA", "Outdated firmware detected. running=" + status.runningVersion + " desired=" + status.desiredVersion);
  return runFromTarget(status.desiredUrl, status.desiredVersion, status.desiredSha, "enforce");
}

} // namespace ActionOTA
