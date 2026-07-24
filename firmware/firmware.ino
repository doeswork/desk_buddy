#include <Arduino.h>
#include "Network_Wifi.h"
#include "Network_MQTT.h"
#include "ActionServo.h"
#include "Utility_FactoryReset.h"
#include <Utility_LED.h>
#include "Utility_Diagnostics.h"
#include <esp_system.h>
#include "soc/rtc_cntl_reg.h"

void setup() {
  Serial.begin(115200);
  delay(1000);  // Wait for USB CDC to initialize on ESP32-S3

  Serial.println("\n\n=== DeskBuddy Starting ===");

  Diagnostics::printResetDiagnostics();

  // Store reset info for MQTT ready message
  esp_reset_reason_t reason = esp_reset_reason();
  BuddyMQTT::setResetReason(Diagnostics::getResetReasonStr(reason), ESP.getFreeHeap(), ESP.getMinFreeHeap());

  FactoryReset::checkAndReset();  // Hold BOOT button during power-on to factory reset

  ActionServo::begin();  // initialize servos

  Serial.println("End of master setup");
}

void loop() {
  BuddyWifi::maintain();  // keep Wi-Fi alive
  BuddyMQTT::maintain();  // not blocking
  BuddyMQTT::listen();    // blocking until MQTT message arrives
}
