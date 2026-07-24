// Network_RequestFlow.cpp
#include "Network_RequestFlow.h"
#include "Network_MQTT.h"
#include <Arduino.h>

void RequestFlow::runAction(const IncomingRequest& req, HandlerFn fn) {
  String statusJson;
  String detailsKey;
  bool ok = fn(req.message, statusJson, detailsKey);

  if (detailsKey.length()) {
    BuddyMQTT::sendCompletedDetails(req.actionId, detailsKey.c_str(), statusJson, req.actionType, ok ? "completed" : "failed", req.phrase);
  } else {
    BuddyMQTT::sendCompleted(req.actionId, req.actionType, ok ? "completed" : "failed", req.phrase);
  }
}
