#ifndef WEBSERVERFORSTARTUP_H
#define WEBSERVERFORSTARTUP_H

#include <Arduino.h>

namespace WebServerForStartup {
  void begin();                    // Initialize the web server
  void maintain();                 // Call in loop to handle requests
  void stop();                     // Stop the web server
  bool isRunning();                // Check if server is active
  bool hasNewCredentials();        // Check if new WiFi credentials were saved
}

#endif // WEBSERVERFORSTARTUP_H
