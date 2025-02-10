#ifndef WEBSERVERWRAPPER_H
#define WEBSERVERWRAPPER_H

class WebServerWrapper {
public:
  // Initializes WiFi (using saved credentials if available) and starts the web server.
  // If no saved credentials exist (or connection fails), an AP is started using apSSID and apPassword.
  static void begin(const char* apSSID, const char* apPassword);

  // Processes incoming HTTP client requests (call from loop()).
  static void handleClient();
};

#endif // WEBSERVERWRAPPER_H
