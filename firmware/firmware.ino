#include <Arduino.h>
#include "BuddyWifi.h"
#include "BuddyMQTT.h"
#include "ActionServo.h"
#include "FactoryReset.h"
#include <LED.h>
#include <esp_system.h>
#include "soc/rtc_cntl_reg.h"

// Get human-readable reset reason
const char* getResetReasonStr(esp_reset_reason_t reason) {
  switch (reason) {
    case ESP_RST_UNKNOWN:    return "Unknown";
    case ESP_RST_POWERON:    return "Power-on";
    case ESP_RST_EXT:        return "External reset";
    case ESP_RST_SW:         return "Software reset (esp_restart)";
    case ESP_RST_PANIC:      return "Exception/panic";
    case ESP_RST_INT_WDT:    return "Interrupt watchdog";
    case ESP_RST_TASK_WDT:   return "Task watchdog";
    case ESP_RST_WDT:        return "Other watchdog";
    case ESP_RST_DEEPSLEEP:  return "Deep sleep wakeup";
    case ESP_RST_BROWNOUT:   return "BROWNOUT - Power issue!";
    case ESP_RST_SDIO:       return "SDIO reset";
    default:                 return "Unknown code";
  }
}

void printResetDiagnostics() {
  esp_reset_reason_t reason = esp_reset_reason();

  Serial.println("\n========== RESET DIAGNOSTICS ==========");
  Serial.printf("Reset reason: %s (code %d)\n", getResetReasonStr(reason), reason);
  Serial.printf("Free heap: %u bytes\n", ESP.getFreeHeap());
  Serial.printf("Min free heap ever: %u bytes\n", ESP.getMinFreeHeap());
  Serial.printf("Heap size: %u bytes\n", ESP.getHeapSize());
  Serial.printf("CPU freq: %u MHz\n", ESP.getCpuFreqMHz());
  Serial.println("========================================\n");

  if (reason == ESP_RST_BROWNOUT) {
    Serial.println("*** BROWNOUT DETECTED! ***");
    Serial.println("This means power dropped below threshold.");
    Serial.println("Possible causes:");
    Serial.println("  - Servo drawing too much current");
    Serial.println("  - Weak USB power supply");
    Serial.println("  - Need separate servo power supply");
    Serial.println("  - Bad/long USB cable");
    Serial.println("***************************\n");
  } else if (reason == ESP_RST_PANIC) {
    Serial.println("*** CRASH/PANIC DETECTED! ***");
    Serial.println("Check for memory issues or bugs.");
    Serial.println("*****************************\n");
  } else if (reason == ESP_RST_TASK_WDT || reason == ESP_RST_INT_WDT || reason == ESP_RST_WDT) {
    Serial.println("*** WATCHDOG RESET! ***");
    Serial.println("Code got stuck somewhere.");
    Serial.println("***********************\n");
  }
}

void setup() {
  Serial.begin(115200);
  delay(1000);  // Wait for USB CDC to initialize on ESP32-S3

  Serial.println("\n\n=== DeskBuddy Starting ===");

  // Print diagnostics FIRST to catch reset reason
  printResetDiagnostics();

  // Store reset info for MQTT ready message
  esp_reset_reason_t reason = esp_reset_reason();
  BuddyMQTT::setResetReason(getResetReasonStr(reason), ESP.getFreeHeap(), ESP.getMinFreeHeap());


  FactoryReset::checkAndReset();  // Hold BOOT button during power-on to factory reset

  ActionServo::begin();  // initialize servos

  Serial.println("End of master setup");
}

void loop() {
  BuddyWifi::maintain();  // keep Wi-Fi alive
  BuddyMQTT::maintain();  // not blocking
  BuddyMQTT::listen();    // blocking until MQTT message arrives

  // no delay to keep mqttClient.loop() responsive inside BuddyMQTT::listen()
}
