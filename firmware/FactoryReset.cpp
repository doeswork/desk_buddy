// FactoryReset.cpp
#include "FactoryReset.h"
#include <Preferences.h>
#include <LED.h>

namespace FactoryReset {

// Connection reset: Hold BOOT for three seconds during startup or normal runtime.
// This intentionally preserves robot calibration stored in the config and rot
// namespaces so changing networks does not require recalibrating the robot.
constexpr int BOOT_BUTTON = 0;  // GPIO0 on ESP32-S3
constexpr unsigned long HOLD_TIME_MS = 3000;  // 3 seconds
constexpr unsigned long BUTTON_POLL_MS = 10;

namespace {
  TaskHandle_t buttonTaskHandle = nullptr;
  portMUX_TYPE buttonStateMux = portMUX_INITIALIZER_UNLOCKED;
  bool pressStarted = false;
  bool releasedEarly = false;
  bool resetRequested = false;

  void pollButton(bool& wasPressed, unsigned long& pressStartTime) {
    const bool isPressed = digitalRead(BOOT_BUTTON) == LOW;
    const unsigned long now = millis();

    if (isPressed) {
      if (!wasPressed) {
        wasPressed = true;
        pressStartTime = now;
        portENTER_CRITICAL(&buttonStateMux);
        pressStarted = true;
        portEXIT_CRITICAL(&buttonStateMux);
      } else if (now - pressStartTime >= HOLD_TIME_MS) {
        // Latch the request. Releasing BOOT before loop() runs again must not
        // lose a valid three-second hold.
        portENTER_CRITICAL(&buttonStateMux);
        resetRequested = true;
        portEXIT_CRITICAL(&buttonStateMux);
      }
    } else if (wasPressed) {
      portENTER_CRITICAL(&buttonStateMux);
      if (!resetRequested) releasedEarly = true;
      portEXIT_CRITICAL(&buttonStateMux);
      wasPressed = false;
      pressStartTime = 0;
    }
  }

  void buttonMonitorTask(void*) {
    bool wasPressed = false;
    unsigned long pressStartTime = 0;

    for (;;) {
      pollButton(wasPressed, pressStartTime);
      vTaskDelay(pdMS_TO_TICKS(BUTTON_POLL_MS));
    }
  }

  void startButtonMonitor() {
    if (buttonTaskHandle != nullptr) return;

    BaseType_t created = xTaskCreate(
      buttonMonitorTask,
      "boot_button",
      2048,
      nullptr,
      1,
      &buttonTaskHandle
    );

    if (created != pdPASS) {
      buttonTaskHandle = nullptr;
      Serial.println("[BOOT] ERROR: Could not start button monitor");
    }
  }
}

void performReset() {
  Serial.println("\n=== CONNECTION RESET ===");
  Serial.println("Clearing saved WiFi and MQTT settings...");

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

  Serial.println("✓ Robot calibration preserved");
  Serial.println("=== CONNECTION RESET COMPLETE ===");
  Serial.println("Rebooting into config mode...\n");

  delay(1000);
  ESP.restart();
}

void maintain() {
  // Fall back to loop polling if the monitor task could not be allocated.
  if (buttonTaskHandle == nullptr) {
    static bool wasPressed = false;
    static unsigned long pressStartTime = 0;
    pollButton(wasPressed, pressStartTime);
  }

  // Copy and clear notification flags atomically before printing. The
  // background task continues monitoring while Serial or networking runs.
  portENTER_CRITICAL(&buttonStateMux);
  const bool shouldAnnouncePress = pressStarted;
  const bool shouldAnnounceRelease = releasedEarly;
  const bool shouldReset = resetRequested;
  pressStarted = false;
  releasedEarly = false;
  portEXIT_CRITICAL(&buttonStateMux);

  if (shouldAnnouncePress) {
    Serial.println("\n[BOOT] Button pressed! Hold for 3 seconds to reset WiFi/MQTT...");
    LED::Blink(0.1);
  }

  if (shouldAnnounceRelease) {
    Serial.println("[BOOT] Released early. Continuing...");
    LED::On();
  }

  if (shouldReset) {
    performReset();  // Never returns
  }
}

void checkAndReset() {
  // Initialize BOOT button
  pinMode(BOOT_BUTTON, INPUT_PULLUP);

  // Check if BOOT button is pressed during power-on
  if (digitalRead(BOOT_BUTTON) == LOW) {
    Serial.println("\n[BOOT] Button detected!");
    Serial.println("[BOOT] Hold for 3 seconds to reset WiFi/MQTT...");

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

  // From this point on a background monitor records the entire hold, even if
  // WiFi or TLS temporarily prevents loop() from polling the button.
  startButtonMonitor();
}

} // namespace FactoryReset
