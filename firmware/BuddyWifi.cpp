#include "BuddyWifi.h"
#include "WebServerForStartup.h"
#include <WiFi.h>
#include <LED.h>
#include <Preferences.h>

namespace {
  const unsigned long TIMEOUT_MS = 10000;
  const int MAX_RETRY_ATTEMPTS = 3;

  int failedAttempts = 0;
  bool configModeActive = false;
  Preferences prefs;

  String savedSSID;
  String savedPassword;
}

void BuddyWifi::maintain() {
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
    Serial.println("No WiFi credentials found. Starting configuration mode...");
    configModeActive = true;
    WebServerForStartup::begin();
    return;
  }

  // Try to connect
  Serial.print("Connecting to ");
  Serial.print(savedSSID);
  Serial.print("…");
  WiFi.mode(WIFI_STA);
  WiFi.begin(savedSSID.c_str(), savedPassword.c_str());

  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < TIMEOUT_MS) {
    delay(500);
    Serial.print('.');
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
      Serial.println("Max retry attempts reached. Starting configuration mode...");
      configModeActive = true;
      failedAttempts = 0;
      WebServerForStartup::begin();
    }
  }
}

bool BuddyWifi::isConnected() {
  return WiFi.status() == WL_CONNECTED;
}
