#include "BuddyWifi.h"
#include "WebServerForStartup.h"
#include "FactoryReset.h"
#include <WiFi.h>
#include <LED.h>
#include <Preferences.h>

namespace {
  const unsigned long TIMEOUT_MS = 10000;
  const unsigned long CONFIG_RETRY_MS = 5000;
  const int MAX_RETRY_ATTEMPTS = 3;

  int failedAttempts = 0;
  bool configModeActive = false;
  unsigned long nextConfigAttemptMs = 0;
  Preferences prefs;

  String savedSSID;
  String savedPassword;

  void startConfigMode(const char* reason) {
    unsigned long now = millis();
    if (nextConfigAttemptMs != 0 &&
        static_cast<long>(now - nextConfigAttemptMs) < 0) {
      return;
    }

    Serial.println(reason);
    WiFi.disconnect(true);
    delay(100);
    savedSSID = "";
    savedPassword = "";
    failedAttempts = 0;
    configModeActive = WebServerForStartup::begin();
    if (configModeActive) {
      nextConfigAttemptMs = 0;
    } else {
      nextConfigAttemptMs = millis() + CONFIG_RETRY_MS;
      Serial.println("Configuration AP startup failed; retrying in 5 seconds");
    }
  }
}

void BuddyWifi::maintain() {
  // This is the single runtime owner of the BOOT gesture. It is deliberately
  // serviced before every WiFi state so config mode cannot hide the button.
  FactoryReset::maintain();

  // If config mode is active, handle web server
  if (configModeActive) {
    WebServerForStartup::maintain();

    // Check if new credentials were saved
    if (WebServerForStartup::hasNewCredentials()) {
      WebServerForStartup::stop();
      configModeActive = false;
      failedAttempts = 0;
      // Credentials will be loaded on next connection attempt
    }
    return;
  }

  // Already connected, nothing to do
  if (WiFi.status() == WL_CONNECTED) {
    return;
  }

  // Not connected, attempt to connect
  LED::Off();

  // Load credentials from Preferences if not already loaded
  if (savedSSID.length() == 0) {
    prefs.begin("wifi", true);
    savedSSID = prefs.getString("ssid", "");
    savedPassword = prefs.getString("password", "");
    prefs.end();
  }

  // If no saved credentials, start config mode
  if (savedSSID.length() == 0) {
    startConfigMode("No WiFi credentials found. Starting configuration mode...");
    return;
  }

  // Try to connect
  Serial.print("Connecting to ");
  Serial.print(savedSSID);
  Serial.print("…");
  WiFi.mode(WIFI_STA);
  WiFi.begin(savedSSID.c_str(), savedPassword.c_str());

  unsigned long start = millis();
  unsigned long lastProgress = start;
  while (WiFi.status() != WL_CONNECTED && millis() - start < TIMEOUT_MS) {
    // Keep the connection-reset gesture responsive while waiting for the station connection.
    FactoryReset::maintain();
    delay(10);

    unsigned long now = millis();
    if (now - lastProgress >= 500) {
      Serial.print('.');
      lastProgress = now;
    }
  }

  if (WiFi.status() == WL_CONNECTED) {
    LED::Blink(0.5);
    Serial.println();
    Serial.print("Connected! IP: ");
    Serial.println(WiFi.localIP());
    failedAttempts = 0;
  } else {
    Serial.println();
    failedAttempts++;
    Serial.print("Connection failed (attempt ");
    Serial.print(failedAttempts);
    Serial.print("/");
    Serial.print(MAX_RETRY_ATTEMPTS);
    Serial.println(")");

    // After max retries, start config mode
    if (failedAttempts >= MAX_RETRY_ATTEMPTS) {
      startConfigMode("Max retry attempts reached. Starting configuration mode...");
    }
  }
}

bool BuddyWifi::isConnected() {
  return WiFi.status() == WL_CONNECTED;
}
