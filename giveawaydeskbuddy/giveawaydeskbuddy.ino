#include <Arduino.h>
#include "WebServerWrapper.h"
#include <WiFi.h>
#include "InverseKinematics.h"
#include "GripperControl.h"
#include "RotationBase.h"
#include "WristTwist.h"
#include <WebServer.h>

// Global control server for all robot endpoints on port 81.
static WebServer controlServer(81);

void setup() {
  Serial.begin(115200);
  delay(1000);
  
  // Start WiFi configuration server (on port 80) using WebServerWrapper.
  WebServerWrapper::begin("DeskBuddy", "12345678");

  // Global status endpoint.
  controlServer.on("/status", HTTP_GET, []() {
    controlServer.sendHeader("Access-Control-Allow-Origin", "*");
    controlServer.send(200, "text/plain", "ESP32 Robot Controller: Online");
  });

  // Initialize each module.
  InverseKinematics::begin();
  GripperControl::begin();
  RotationBase::begin();
  WristTwist::begin();

  // Register endpoints for each module on the global control server.
  InverseKinematics::registerEndpoints(controlServer);
  GripperControl::registerEndpoints(controlServer);
  RotationBase::registerEndpoints(controlServer);
  WristTwist::registerEndpoints(controlServer);

  // Start the control server.
  controlServer.begin();
  Serial.println("Control server started on port 81");
}

void loop() {
  // Process WiFi configuration server (port 80).
  WebServerWrapper::handleClient();
  
  // Process control endpoints.
  controlServer.handleClient();
  
  // Process any continuous tasks (e.g. RotationBase monitoring).
  RotationBase::loop();
  
  delay(100);
}
