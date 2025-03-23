#ifndef WRISTTWIST_H
#define WRISTTWIST_H

#include <Arduino.h>
#include <WebServer.h>

namespace WristTwist {
  // Attach servo and set neutral position
  void begin();
  // Register the /controlWristTwist endpoint on the global WebServer
  void registerEndpoints(WebServer &server);
  // No per‑loop processing needed
  void handleClient();
}

#endif // WRISTTWIST_H
