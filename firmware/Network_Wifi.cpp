#include "Network_Wifi.h"
#include "Network_WebServerForStartup.h"
#include <WiFi.h>
#include <LED.h>
#include <Preferences.h>

namespace {
  const unsigned long TIMEOUT_MS = 10000;
  const int MAX_RETRY_ATTEMPTS = 3;
  const int BOOT_BUTTON = 0;
  const unsigned long WIFI_CONFIG_HOLD_MS = 3000;

  int failedAttempts = 0;
  bool configModeActive = false;
  Preferences prefs;

  String savedSSID;
  String savedPassword;

  void startConfigMode(const char* reason) {
    Serial.println(reason);
    WiFi.disconnect(true);
    delay(100);
    savedSSID = "";
    savedPassword = "";
    failedAttempts = 0;
    configModeActive = true;
    WebServerForStartup::begin();
  }

  bool wifiConfigButtonHeld() {
    static unsigned long pressStartTime = 0;
    static bool wasPressed = false;

    pinMode(BOOT_BUTTON, INPUT_PULLUP);

    if (digitalRead(BOOT_BUTTON) == LOW) {
      if (!wasPressed) {
        wasPressed = true;
        pressStartTime = millis();
        Serial.println("\n[BOOT] Button pressed. Hold for 3 seconds to change WiFi...");
        LED::Blink(0.1);
      } else if (millis() - pressStartTime >= WIFI_CONFIG_HOLD_MS) {
        wasPressed = false;
        pressStartTime = 0;
        return true;
      }
    } else {
      if (wasPressed) {
        Serial.println("[BOOT] Released early. Continuing normal WiFi operation...");
        if (WiFi.status() == WL_CONNECTED) {
          LED::On();
        } else {
          LED::Off();
        }
      }
      wasPressed = false;
      pressStartTime = 0;
    }

    return false;
  }
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

  if (wifiConfigButtonHeld()) {
    startConfigMode("\n[BOOT] WiFi change requested. Starting configuration mode...");
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
      startConfigMode("Max retry attempts reached. Starting configuration mode...");
    }
  }
}

bool BuddyWifi::isConnected() {
  return WiFi.status() == WL_CONNECTED;
}
