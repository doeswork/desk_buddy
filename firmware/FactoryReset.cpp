// FactoryReset.cpp
#include "FactoryReset.h"
#include <Preferences.h>
#include <LED.h>

namespace FactoryReset {

// Factory reset: Hold BOOT button during power-on
constexpr int BOOT_BUTTON = 0;  // GPIO0 on ESP32-S3
constexpr unsigned long HOLD_TIME_MS = 3000;  // 3 seconds

void performReset() {
  Serial.println("\n=== FACTORY RESET ===");
  Serial.println("Clearing all saved settings...");

  Preferences prefs;

  // Clear WiFi credentials
  if (prefs.begin("wifi", false)) {
    prefs.clear();
    prefs.end();
    Serial.println("✓ WiFi cleared");
  }

  // Clear MQTT settings
  if (prefs.begin("mqtt", false)) {
    prefs.clear();
    prefs.end();
    Serial.println("✓ MQTT cleared");
  }

  // Clear config
  if (prefs.begin("config", false)) {
    prefs.clear();
    prefs.end();
    Serial.println("✓ Config cleared");
  }

  // Clear rotation
  if (prefs.begin("rot", false)) {
    prefs.clear();
    prefs.end();
    Serial.println("✓ Rotation cleared");
  }

  Serial.println("=== RESET COMPLETE ===");
  Serial.println("Rebooting into config mode...\n");

  delay(1000);
  ESP.restart();
}

bool checkButtonHeld() {
  // Non-blocking check: returns true if button held for HOLD_TIME_MS
  static unsigned long pressStartTime = 0;
  static bool wasPressed = false;

  pinMode(BOOT_BUTTON, INPUT_PULLUP);

  if (digitalRead(BOOT_BUTTON) == LOW) {
    if (!wasPressed) {
      // Button just pressed
      wasPressed = true;
      pressStartTime = millis();
      Serial.println("\n[BOOT] Button pressed! Hold for 3 seconds to factory reset...");
      LED::Blink(0.1);  // Fast blink
    } else if (millis() - pressStartTime >= HOLD_TIME_MS) {
      // Held long enough
      return true;
    }
  } else {
    if (wasPressed) {
      Serial.println("[BOOT] Released early. Continuing...");
      LED::On();  // Restore LED state
    }
    wasPressed = false;
    pressStartTime = 0;
  }

  return false;
}

void checkAndReset() {
  // Initialize BOOT button
  pinMode(BOOT_BUTTON, INPUT_PULLUP);

  // Check if BOOT button is pressed during power-on
  if (digitalRead(BOOT_BUTTON) == LOW) {
    Serial.println("\n[BOOT] Button detected!");
    Serial.println("[BOOT] Hold for 3 seconds to factory reset...");

    LED::Blink(0.1);  // Fast blink during hold

    unsigned long startTime = millis();
    bool stillPressed = true;

    // Check if button stays pressed for 3 seconds
    while (millis() - startTime < HOLD_TIME_MS) {
      if (digitalRead(BOOT_BUTTON) == HIGH) {
        stillPressed = false;
        break;
      }
      delay(100);
    }

    if (stillPressed) {
      LED::On();
      performReset();  // This will reboot - never returns
    } else {
      Serial.println("[BOOT] Released early. Normal boot...");
      LED::Off();
      delay(500);
    }
  }
}

} // namespace FactoryReset
