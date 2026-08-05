// ActionController.cpp
//
// Parses the inbound MQTT envelope and hands the action to ActionRouter, which
// owns the action name -> handler mapping. Reply publishing is driven by each
// route's declared ReplyStyle.
#include "ActionController.h"
#include "ActionRouter.h"
#include "BuddyMQTT.h"
#include <ArduinoJson.h>
#include <Arduino.h>

void ActionController::dispatch(const String& message) {
    DynamicJsonDocument doc(512);
    if (deserializeJson(doc, message) != DeserializationError::Ok) {
        Serial.print("JSON parse error: ");
        Serial.println(message);
        return;
    }

    // ignore our own status messages
    const char* sender = doc["sender"].as<const char*>();
    if (sender && strcmp(sender, "firmware") == 0) return;

    // extract action_id (if present)
    String actionId;
    if      (doc["action_id"].is<const char*>()) actionId = doc["action_id"].as<const char*>();
    else if (doc["action_id"].is<long>())        actionId = String(doc["action_id"].as<long>());

    // set workflow context for all outgoing messages in this dispatch
    if (doc["workflow_id"].is<long>()) {
        BuddyMQTT::setWorkflowContext(
            doc["workflow_id"].as<long>(),
            doc["workflow_event_id"].is<long>() ? doc["workflow_event_id"].as<long>() : -1
        );
    } else {
        BuddyMQTT::clearWorkflowContext();
    }

    // get action field
    const char* act = doc["action"].as<const char*>();
    if (!act) {
        Serial.print("[No action field] ");
        Serial.println(message);
        return;
    }

    const ActionRouter::Route* route = ActionRouter::find(act);
    if (!route) {
        Serial.print("[Unknown Action] ");
        Serial.println(message);
        return;
    }

    String actionName = act;
    const char* calibrationTypeField = doc["calibration_type"].as<const char*>();
    String actionType;
    if (route->typeFromCalibrationField && calibrationTypeField && calibrationTypeField[0]) {
        actionType = calibrationTypeField;
    } else if (route->typeFromActionName) {
        actionType = actionName;
    }

    JsonVariantConst phrase = doc["phrase"];

    JsonVariantConst useModelRaw = doc["use_model"];
    if (useModelRaw.isNull()) {
        useModelRaw = doc["useModel"];
    }
    String useModelJson;
    if (!useModelRaw.isNull()) {
        serializeJson(useModelRaw, useModelJson);
        Serial.printf("[ActionController] use_model found in doc, raw value: %s\n", useModelJson.c_str());
    } else {
        Serial.println("[ActionController] use_model NOT found in doc");
    }

    // publish in_progress for known actions
    if (actionId.length()) {
        BuddyMQTT::sendInProgress(actionId, actionType, phrase, nullptr, -1, useModelJson);
    }

    ActionRouter::Context ctx{message, actionId, actionType, phrase, useModelJson};
    String detailsJson;
    // Long blocking handlers publish their own progress telemetry and need to
    // know which action they are running under.
    BuddyMQTT::setCurrentActionId(actionId);
    bool ok = ActionRouter::run(*route, ctx, detailsJson);
    BuddyMQTT::setCurrentActionId("");
    const char* status = ok ? "completed" : "failed";

    switch (route->reply) {
      case ActionRouter::ReplyStyle::Completed:
        BuddyMQTT::sendCompleted(actionId, "", status, phrase);
        break;

      case ActionRouter::ReplyStyle::CompletedNoPhrase:
        BuddyMQTT::sendCompleted(actionId, "", status);
        break;

      case ActionRouter::ReplyStyle::CompletedNamed:
        BuddyMQTT::sendCompleted(actionId, route->replyLabel, status);
        break;

      case ActionRouter::ReplyStyle::CompletedDetails:
        BuddyMQTT::sendCompletedDetails(actionId, route->replyLabel, detailsJson, "", status, phrase);
        break;

      case ActionRouter::ReplyStyle::PhotoInProgress:
        BuddyMQTT::sendInProgress(actionId, actionType, phrase, "sent", -1, useModelJson);
        break;

      case ActionRouter::ReplyStyle::HandlerOwned:
      case ActionRouter::ReplyStyle::None:
        break;
    }
}
