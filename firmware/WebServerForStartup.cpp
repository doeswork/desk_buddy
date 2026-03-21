#include "WebServerForStartup.h"
#include "ConfigPageHTML.h"
#include "SavedPageHTML.h"
#include <WiFi.h>
#include <WebServer.h>
#include <DNSServer.h>
#include <Preferences.h>
#include <LED.h>

namespace {
  WebServer server(80);
  DNSServer dnsServer;
  Preferences prefs;

  bool serverRunning = false;
  bool newCredentialsSaved = false;
  const byte DNS_PORT = 53;
  const char* AP_SSID = "DeskBuddy";
  const char* AP_PASS = "arobotoneverydesk";

  void handleRoot() {
    server.send(200, "text/html", CONFIG_PAGE_HTML);
  }

  void handleSave() {
    if (server.method() != HTTP_POST) {
      server.send(405, "text/plain", "Method Not Allowed");
      return;
    }

    // Save WiFi credentials
    String ssid = server.arg("ssid");
    String password = server.arg("password");

    if (ssid.length() > 0) {
      prefs.begin("wifi", false);
      prefs.putString("ssid", ssid);
      prefs.putString("password", password);
      prefs.end();

      Serial.println("WiFi credentials saved:");
      Serial.println("  SSID: " + ssid);
      newCredentialsSaved = true;
    }

    // Save MQTT settings if provided
    String mqtt_server = server.arg("mqtt_server");
    if (mqtt_server.length() > 0) {
      prefs.begin("mqtt", false);
      prefs.putString("server", mqtt_server);
      prefs.putInt("port", server.arg("mqtt_port").toInt());
      prefs.putString("user", server.arg("mqtt_user"));
      prefs.putString("password", server.arg("mqtt_pass"));
      prefs.putString("client_id", server.arg("mqtt_client_id"));
      prefs.end();

      Serial.println("MQTT settings saved");
    }

    server.send(200, "text/html", SAVED_PAGE_HTML);

    // Schedule restart in 3 seconds
    delay(3000);
    ESP.restart();
  }

  void handleNotFound() {
    // Redirect all unknown requests to root (captive portal)
    server.sendHeader("Location", "/", true);
    server.send(302, "text/plain", "");
  }
}

void WebServerForStartup::begin() {
  if (serverRunning) return;

  Serial.println("\n=== Starting Configuration Web Server ===");

  // Start Access Point
  WiFi.mode(WIFI_AP);
  WiFi.softAP(AP_SSID, AP_PASS);

  IPAddress IP = WiFi.softAPIP();
  Serial.print("AP IP address: ");
  Serial.println(IP);
  Serial.println("AP SSID: " + String(AP_SSID));
  Serial.println("AP Password: " + String(AP_PASS));

  // Start DNS server for captive portal
  dnsServer.start(DNS_PORT, "*", IP);

  // Setup web server routes
  server.on("/", handleRoot);
  server.on("/save", handleSave);
  server.onNotFound(handleNotFound);

  server.begin();
  serverRunning = true;
  newCredentialsSaved = false;

  Serial.println("Connect to WiFi '" + String(AP_SSID) + "' to configure");
  Serial.println("Web server ready at http://" + IP.toString());

  LED::Blink(2.0);  // Slow blink to indicate config mode
}

void WebServerForStartup::maintain() {
  if (!serverRunning) return;

  dnsServer.processNextRequest();
  server.handleClient();
}

void WebServerForStartup::stop() {
  if (!serverRunning) return;

  Serial.println("Stopping configuration web server");
  server.stop();
  dnsServer.stop();
  WiFi.softAPdisconnect(true);
  serverRunning = false;
}

bool WebServerForStartup::isRunning() {
  return serverRunning;
}

bool WebServerForStartup::hasNewCredentials() {
  return newCredentialsSaved;
}
