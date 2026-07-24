// FactoryReset.h
#ifndef FACTORY_RESET_H
#define FACTORY_RESET_H

#include <Arduino.h>

namespace FactoryReset {
  // Check if BOOT button is held during power-on
  // Call this early in setup() before WiFi/MQTT initialize
  void checkAndReset();

  // Service runtime BOOT-button events. A dedicated monitor latches a
  // completed hold even while a network call temporarily blocks loop().
  void maintain();

  // Perform factory reset and reboot
  void performReset();
}

#endif // FACTORY_RESET_H
