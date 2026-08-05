// ActionRouter.h
//
// Single routing table for every inbound MQTT action. ActionController::dispatch
// parses the envelope (sender, action_id, workflow context) and then hands the
// action name here; the router owns the name -> handler mapping.
//
// To add an action: write a handler and add one ROUTES entry. Nothing else in
// the dispatch path needs to change.
#ifndef ACTION_ROUTER_H
#define ACTION_ROUTER_H

#include <Arduino.h>
#include <ArduinoJson.h>

namespace ActionRouter {

  // How a handler reports back over MQTT once it has run.
  enum class ReplyStyle {
    Completed,          // sendCompleted(actionId, "", status, phrase)
    CompletedNoPhrase,  // sendCompleted(actionId, "", status)
    CompletedNamed,     // sendCompleted(actionId, replyLabel, status)
    CompletedDetails,   // sendCompletedDetails(actionId, replyLabel, json, ...)
    PhotoInProgress,    // photo actions re-publish in_progress with "sent"
    HandlerOwned,       // the handler publishes its own reply
    None                // no reply (unknown action)
  };

  // Everything a handler needs from the parsed envelope. Passing this rather
  // than loose parameters keeps handler signatures uniform.
  struct Context {
    const String& message;
    const String& actionId;
    const String& actionType;
    JsonVariantConst phrase;
    const String& useModelJson;
  };

  // Returns true on success. detailsJson is populated only by handlers whose
  // route uses CompletedDetails.
  using Handler = bool (*)(const Context& ctx, String& detailsJson);

  struct Route {
    const char* action;      // MQTT "action" value
    Handler handler;
    ReplyStyle reply;
    const char* replyLabel;  // used by CompletedNamed / CompletedDetails
    // When set, the in_progress/"type" field uses the payload's
    // calibration_type field instead of the action name.
    bool typeFromCalibrationField;
    // When set, the action name is echoed as the in_progress "type".
    bool typeFromActionName;
  };

  // Looks up a route by action name. Returns nullptr when unknown.
  const Route* find(const char* action);

  // Runs a route's handler, returning its success flag.
  bool run(const Route& route, const Context& ctx, String& detailsJson);
}

#endif // ACTION_ROUTER_H
