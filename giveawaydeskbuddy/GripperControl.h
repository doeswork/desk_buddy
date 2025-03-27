#ifndef GRIPPERCONTROL_H
#define GRIPPERCONTROL_H

#include <Arduino.h>
#include <WebServer.h>

namespace GripperControl {
  // Attach the servo and configure pins.
  void begin();
  // (No-op) required by module interface.
  void handleClient();
  // Register the /controlGripper endpoint on the global WebServer.
  void registerEndpoints(WebServer &server);
}

#endif // GRIPPERCONTROL_H
