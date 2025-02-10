#include <Arduino.h>
#include "WebServerWrapper.h"
#include <WiFi.h>
#include "ControlServos.h" 

bool tookPicture = false;

static void syncTimeIfSTA();

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("[desk_buddy_firmware] Setup start");

  // Start the web server for WiFi setup.
  WebServerWrapper::begin("DeskBuddy", "12345678");
  // Initialize the control servos web server.
  ControlServos::begin();
}

void loop() {
  WebServerWrapper::handleClient();
  ControlServos::handleClient();

  delay(100);
}