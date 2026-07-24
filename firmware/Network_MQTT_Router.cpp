// Network_MQTT_Router.cpp
#include "Network_MQTT_Router.h"
#include "Network_RequestFlow.h"
#include "Network_MQTT.h"
#include "ActionGripper.h"
#include "ActionBaseRotate.h"
#include "ActionServo.h"
#include "ActionInverseKinematics.h"
#include "CalibrateStencil.h"
#include "ActionPerch.h"
#include "CalibrateController.h"
#include "CalibrateValues.h"
#include "ActionPhoto.h"
#include <ArduinoJson.h>
#include <Arduino.h>

namespace {
  enum class ActionType {
      Calibrate,
      CalibrationValues,
      Photo,
      Gripper,
      BaseRotate,
      Servo,
      ControlIK,
      StencilCalibrate,
      Perch,
      Unknown
  };

  ActionType parseAction(const char* act) {
      if      (strcmp(act, "gripper")     == 0) return ActionType::Gripper;
      else if (strcmp(act, "baseRotate")  == 0) return ActionType::BaseRotate;
      else if (strcmp(act, "servo")       == 0) return ActionType::Servo;
      else if (strcmp(act, "controlik")   == 0) return ActionType::ControlIK;
      else if (strcmp(act, "stencilCalibrate") == 0) return ActionType::StencilCalibrate;
      else if (strcmp(act, "perch")       == 0) return ActionType::Perch;
      else if (strcmp(act, "calibrate") == 0) return ActionType::Calibrate;
      else if (strcmp(act, "calibrationvalues") == 0) return ActionType::CalibrationValues;
      else if (strcmp(act, "photo") == 0) return ActionType::Photo;
      else if (strcmp(act, "detect_object") == 0) return ActionType::Photo;
      else if (strcmp(act, "detect_color") == 0) return ActionType::Photo;
      else if (strcmp(act, "calibrate_depth") == 0) return ActionType::Photo;
      else                                      return ActionType::Unknown;
  }
}

void MQTTRouter::route(const String& message) {
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
    String actionName = act;
    ActionType type = parseAction(act);
    bool isPhotoAction = (type == ActionType::Photo);
    bool isCalibrateAction = (type == ActionType::Calibrate);
    const char* calibrationTypeField = doc["calibration_type"].as<const char*>();
    String calibrationType;
    String actionType;
    if (isPhotoAction) {
        actionType = actionName;
    } else if (isCalibrateAction) {
        // CalibrateController defaults an absent/empty calibration_type to
        // "hover_over_min" internally; apply the same default here so the
        // in_progress message's "type" field agrees with the eventual
        // completed/failed message's "type" field instead of diverging
        // (previously: in_progress got "", completed got "hover_over_min").
        calibrationType = (calibrationTypeField && calibrationTypeField[0]) ? calibrationTypeField : "hover_over_min";
        actionType = calibrationType;
    } else if (type == ActionType::CalibrationValues) {
        actionType = actionName;
    } else {
        actionType = "";
    }
    JsonVariantConst phrase = doc["phrase"];

    JsonVariantConst useModelRaw = doc["use_model"];
    if (useModelRaw.isNull()) {
        useModelRaw = doc["useModel"];
    }
    String useModelJson;
    if (!useModelRaw.isNull()) {
        serializeJson(useModelRaw, useModelJson);
    }

    if (!useModelRaw.isNull()) {
        Serial.printf("[MQTTRouter] use_model found in doc, raw value: %s\n", useModelJson.c_str());
    } else {
        Serial.println("[MQTTRouter] use_model NOT found in doc");
    }

    // publish in_progress for known actions
    if (type != ActionType::Unknown && actionId.length()) {
        BuddyMQTT::sendInProgress(actionId, actionType, phrase, nullptr, -1, useModelJson);
    }

    IncomingRequest req;
    req.message = message;
    req.actionId = actionId;
    req.actionType = actionType;
    req.calibrationType = calibrationType;
    req.phrase = phrase;
    req.useModelJson = useModelJson;

    // Every action shares one shape — see RequestFlow::runAction/HandlerFn.
    switch (type) {
      case ActionType::Gripper:           RequestFlow::runAction(req, &ActionGripper::run); break;
      case ActionType::BaseRotate:        RequestFlow::runAction(req, &ActionBaseRotate::run); break;
      case ActionType::Servo:             RequestFlow::runAction(req, &ActionServo::run); break;
      case ActionType::ControlIK:         RequestFlow::runAction(req, &ActionInverseKinematics::run); break;
      case ActionType::StencilCalibrate:  RequestFlow::runAction(req, &CalibrateStencil::run); break;
      case ActionType::Perch:             RequestFlow::runAction(req, &ActionPerch::run); break;
      case ActionType::Calibrate:         RequestFlow::runAction(req, &CalibrateController::run); break;
      case ActionType::CalibrationValues: RequestFlow::runAction(req, &CalibrateValues::run); break;
      case ActionType::Photo:             RequestFlow::runAction(req, &ActionPhoto::run); break;

      default:
        Serial.print("[Unknown Action] ");
        Serial.println(message);
        break;
    }
}
