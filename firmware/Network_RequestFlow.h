// Network_RequestFlow.h
#ifndef NETWORK_REQUEST_FLOW_H
#define NETWORK_REQUEST_FLOW_H

#include <Arduino.h>
#include "Network_MQTT_Router.h"

// The only code (besides Network_MQTT.cpp itself and Utility_Logger) allowed
// to call BuddyMQTT::send*.
namespace RequestFlow {
  // Every Action*/Calibrate* handler shares this exact shape: parse the
  // message, do the thing, report what happened. statusJson and detailsKey
  // are out-params the handler fills in itself — statusJson is the JSON
  // blob to report (may be left empty), detailsKey is the
  // sendCompletedDetails key to report it under (empty means "no details,
  // just a plain completed/failed").
  using HandlerFn = bool (*)(const String& message, String& statusJson, String& detailsKey);

  // Calls fn(req.message, ...), then sends completed/failed — with
  // sendCompletedDetails if the handler set a non-empty detailsKey,
  // otherwise a plain sendCompleted. type and phrase are always forwarded
  // from req, uniformly, for every action — no per-action special-casing.
  void runAction(const IncomingRequest& req, HandlerFn fn);
}

#endif // NETWORK_REQUEST_FLOW_H
