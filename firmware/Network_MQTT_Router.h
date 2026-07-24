// Network_MQTT_Router.h
#ifndef NETWORK_MQTT_ROUTER_H
#define NETWORK_MQTT_ROUTER_H

#include <Arduino.h>
#include <ArduinoJson.h>

// One incoming MQTT action message, decoded once by MQTTRouter::route() and
// handed to a Network_RequestFlow::handleX() function. `phrase` is a view
// into the DynamicJsonDocument owned by route()'s stack frame — it must not
// outlive that call (never stored, never returned up the stack).
struct IncomingRequest {
  String message;           // raw JSON, forwarded to Action*/Calibrate* handlers for their own parsing
  String actionId;
  String actionType;        // computed "type" field value for sendInProgress/sendCompleted
  String calibrationType;   // raw calibration_type field (only meaningful for the Calibrate case)
  JsonVariantConst phrase;
  String useModelJson;
};

namespace MQTTRouter {
  // Entry point called by BuddyMQTT::listen() for every received message.
  // Decodes the envelope, sets/clears workflow context, sends the common
  // pre-dispatch "in_progress" reply, then calls exactly one
  // Network_RequestFlow::handleX() based on the "action" field. Never calls
  // an Action*/Calibrate* handler or any other BuddyMQTT::send* itself.
  void route(const String& message);
}

#endif // NETWORK_MQTT_ROUTER_H
