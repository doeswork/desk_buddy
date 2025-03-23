#ifndef INVERSEKINEMATICS_H
#define INVERSEKINEMATICS_H

#include <Arduino.h>
#include <WebServer.h>

namespace InverseKinematics {
  // Initializes the inverse kinematics module (attaches the elbow and wrist servos).
  void begin();
  
  // Registers the /controlIK endpoint on your global WebServer instance.
  void registerEndpoints(WebServer &server);
  
  // (Optional) Per-loop processing, if needed.
  void handleClient();
}

#endif // INVERSEKINEMATICS_H
