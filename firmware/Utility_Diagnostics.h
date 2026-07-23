// Utility_Diagnostics.h
#ifndef UTILITY_DIAGNOSTICS_H
#define UTILITY_DIAGNOSTICS_H

#include <Arduino.h>
#include <esp_system.h>

namespace Diagnostics {
  // Human-readable label for an ESP32 reset reason code.
  const char* getResetReasonStr(esp_reset_reason_t reason);

  // Prints reset reason, heap stats, and CPU freq to Serial. Call early in
  // setup() before WiFi/MQTT initialize so a brownout/panic/watchdog reset
  // is visible even if the device fails to reconnect afterward.
  void printResetDiagnostics();
}

#endif // UTILITY_DIAGNOSTICS_H
