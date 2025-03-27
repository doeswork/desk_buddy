#ifndef INVERSEKINEMATICS_H
#define INVERSEKINEMATICS_H

#include <Arduino.h>
#include <WebServer.h>

namespace InverseKinematics {
  // Initializes the servos and loads calibration data from Preferences.
  void begin();
  
  // Registers the /controlIK, /calibrateIK, and /calibrateServo endpoints on the given server.
  void registerEndpoints(WebServer &server);
  
  // (Optional) Per-loop processing, if needed.
  void handleClient();
}

#endif // INVERSEKINEMATICS_H