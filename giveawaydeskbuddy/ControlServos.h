#ifndef CONTROLSERVOS_H
#define CONTROLSERVOS_H

class ControlServos {
public:
  // Initializes the servo control module (attaches servos and starts the HTTP server).
  static void begin();

  // Should be called in loop() to handle incoming HTTP requests and update servo positions.
  static void handleClient();
};




























#endif // CONTROLSERVOS_H
