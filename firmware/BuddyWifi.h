#ifndef BUDDYWIFI_H
#define BUDDYWIFI_H

#include <Arduino.h>

namespace BuddyWifi {
  void maintain();      // call every loop to (re)connect, handle OTA, or run config server
  bool isConnected();   // check Wi-Fi status
}

#endif // BUDDYWIFI_H
