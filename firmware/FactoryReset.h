// FactoryReset.h
#ifndef FACTORY_RESET_H
#define FACTORY_RESET_H

#include <Arduino.h>

namespace FactoryReset {
  // Check if BOOT button is held during power-on
  // Call this early in setup() before WiFi/MQTT initialize
  void checkAndReset();

  // Check if BOOT button is currently being held (non-blocking)
  // Returns true if held for the required duration
  bool checkButtonHeld();

  // Perform factory reset and reboot
  void performReset();
}

#endif // FACTORY_RESET_H
