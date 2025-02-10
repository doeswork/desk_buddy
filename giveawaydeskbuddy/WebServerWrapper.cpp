#include "WebServerWrapper.h"
#include <WiFi.h>
#include <WebServer.h>
#include <Preferences.h>

// Create a global Preferences object and variables to hold saved WiFi credentials.
static Preferences preferences;
static String networkName;
static String networkPassword;

// Create a web server instance on port 80.
WebServer server(80);

// HTML for the index page served at "/"
// This page lets you enter your local WiFi network credentials.
const char INDEX_HTML[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html>
<head>
  <title>WiFi Setup</title>
</head>
<body>
  <h1>Configure WiFi</h1>
  <form action="/save" method="POST">
    <label for="ssid">SSID:</label>
    <input type="text" id="ssid" name="ssid" required><br>
    <label for="password">Password:</label>
    <input type="password" id="password" name="password" required><br>
    <input type="submit" value="Save">
  </form>
</body>
</html>
)rawliteral";

// Handler for the root ("/") endpoint.  
// Simply serves the HTML form for WiFi configuration.
void handleRoot() {
  server.send(200, "text/html", INDEX_HTML);
}

// Handler for the "/save" endpoint.
// Reads the submitted credentials, saves them in Preferences, and then attempts to connect.
void handleSave() {
  if (server.method() == HTTP_POST) {
    // Get the new WiFi credentials from the POST request.
    String newSSID = server.arg("ssid");
    String newPassword = server.arg("password");

    // Save the new credentials in non-volatile storage.
    preferences.putString("networkName", newSSID);
    preferences.putString("networkPassword", newPassword);
    networkName = newSSID;
    networkPassword = newPassword;

    // Switch to dual mode: AP (for configuration) + STA (for connecting to your network).
    WiFi.mode(WIFI_AP_STA);
    WiFi.begin(newSSID.c_str(), newPassword.c_str());
    unsigned long startAttemptTime = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - startAttemptTime < 15000) {
      delay(500);
    }
    if (WiFi.status() == WL_CONNECTED) {
      String ipStr = WiFi.localIP().toString();
      String response = "<html><body>";
      response += "<script>alert('Connected to WiFi. IP: " + ipStr + "');</script>";
      response += "<h1>Connected!</h1><p>Your ESP32 IP address is: " + ipStr + "</p>";
      response += "</body></html>";
      server.send(200, "text/html", response);
    } else {
      String response = "<html><body><h1>Connection Failed</h1><p>Please check your credentials and try again.</p></body></html>";
      server.send(200, "text/html", response);
    }
  }
}

void WebServerWrapper::begin(const char* apSSID, const char* apPassword) {
  // Initialize the Preferences library (using the "buddy" namespace).
  preferences.begin("buddy", false);
  
  // Attempt to load saved WiFi credentials.
  networkName = preferences.getString("networkName", "");
  networkPassword = preferences.getString("networkPassword", "");

  if (networkName.length() > 0) {
    // If saved credentials exist, try connecting in STA mode.
    Serial.print("Attempting to connect to saved WiFi: ");
    Serial.println(networkName);
    WiFi.mode(WIFI_STA);
    WiFi.begin(networkName.c_str(), networkPassword.c_str());
    unsigned long startAttemptTime = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - startAttemptTime < 15000) {
      delay(500);
    }
    if (WiFi.status() == WL_CONNECTED) {
      Serial.print("Connected to saved WiFi. IP: ");
      Serial.println(WiFi.localIP());
    } else {
      Serial.println("Failed to connect to saved WiFi. Starting AP mode for configuration.");
      // If connection fails, start AP mode so the user can update credentials.
      WiFi.mode(WIFI_AP_STA);
      WiFi.softAP(apSSID, apPassword);
    }
  } else {
    // No saved credentials—start AP mode to let the user configure WiFi.
    WiFi.mode(WIFI_AP_STA);
    WiFi.softAP(apSSID, apPassword);
  }

  // Print the AP IP address so you can connect to it.
  Serial.print("AP IP address: ");
  Serial.println(WiFi.softAPIP());

  // Define the HTTP endpoints.
  server.on("/", HTTP_GET, handleRoot);
  server.on("/save", HTTP_POST, handleSave);

  // Start the web server.
  server.begin();
  Serial.println("Web server started");
}

void WebServerWrapper::handleClient() {
  server.handleClient();
}
