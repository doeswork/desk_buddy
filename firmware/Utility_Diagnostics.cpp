// Utility_Diagnostics.cpp
#include "Utility_Diagnostics.h"

const char* Diagnostics::getResetReasonStr(esp_reset_reason_t reason) {
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

void Diagnostics::printResetDiagnostics() {
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
