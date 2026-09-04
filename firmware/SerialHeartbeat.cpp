#include "SerialHeartbeat.h"

#include <Arduino.h>
#include <ArduinoJson.h>

#include "BuddyWifi.h"

namespace SerialHeartbeat {
namespace {
  // Loose on purpose: this exists to be glanced at, not polled tightly. A
  // faster interval would just be more lines to scroll past for no more
  // information — "still alive" does not change meaning between one second
  // and five.
  constexpr unsigned long INTERVAL_MS = 2000;
  unsigned long lastSent = 0;
}

void maintain() {
  unsigned long now = millis();
  if (lastSent != 0 && now - lastSent < INTERVAL_MS) return;
  lastSent = now;

  // One JSON line, matching SerialProvisioning's wire shape: a discriminator
  // key Studio's Serial Monitor can filter on, uptime so a silent gap in the
  // log is visible even if the exact timestamps are not, and Wi-Fi state
  // since "is the board alive" and "has it reached the network yet" are the
  // two different questions someone plugging in a fresh board is actually
  // asking.
  StaticJsonDocument<192> doc;
  doc["serial_heartbeat"] = true;
  doc["uptime_ms"] = now;
  doc["wifi_connected"] = BuddyWifi::isConnected();
  doc["free_heap"] = ESP.getFreeHeap();
  serializeJson(doc, Serial);
  Serial.println();
}
}
