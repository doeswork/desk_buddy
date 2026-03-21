// ActionController.cpp
#include "ActionController.h"
#include "ActionCalibrate.h"
#include "ActionPhoto.h"
#include "ActionGripper.h"
#include "ActionBaseRotate.h"
#include "ActionServo.h"
#include "ActionInverseKinematics.h"
#include "ActionPerch.h"
#include "ActionOTA.h"
#include "BuddyMQTT.h"
#include <ArduinoJson.h>
#include <Arduino.h>

enum class ActionType {
    Calibrate,
    CalibrationValues,
    Photo,
    Gripper,
    BaseRotate,
    Servo,
    ControlIK,
    Perch,
    OriginMagnet,
    OTAUpdate,
    Unknown
};

static ActionType parseAction(const char* act) {
    if      (strcmp(act, "gripper")     == 0) return ActionType::Gripper;
    else if (strcmp(act, "baseRotate")  == 0) return ActionType::BaseRotate;
    else if (strcmp(act, "servo")       == 0) return ActionType::Servo;
    else if (strcmp(act, "controlik")   == 0) return ActionType::ControlIK;
    else if (strcmp(act, "perch")       == 0) return ActionType::Perch;
    else if (strcmp(act, "calibrate") == 0) return ActionType::Calibrate;
    else if (strcmp(act, "calibrationvalues") == 0) return ActionType::CalibrationValues;
    else if (strcmp(act, "photo") == 0) return ActionType::Photo;
    else if (strcmp(act, "detect_object") == 0) return ActionType::Photo;
    else if (strcmp(act, "detect_color") == 0) return ActionType::Photo;
    else if (strcmp(act, "calibrate_depth") == 0) return ActionType::Photo;
    else if (strcmp(act, "originmagnet") == 0)  return ActionType::OriginMagnet;
    else if (strcmp(act, "ota_update") == 0) return ActionType::OTAUpdate;
    else                                      return ActionType::Unknown;
}

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
        if (actionId == "originmagnet") {
            BuddyMQTT::sendInProgress(actionId);
            int magnet = ActionBaseRotate::handleOriginMagnet(message);
            StaticJsonDocument<64> response;
            response["origin_magnet"] = magnet;
            String jsonOut;
            serializeJson(response, jsonOut);
            BuddyMQTT::sendCompletedDetails(actionId, "origin_magnet", jsonOut);
        } else {
            Serial.print("[No action field] ");
            Serial.println(message);
        }
        return;
    }
    String actionName = act;
    ActionType type = parseAction(act);
    if (type == ActionType::Unknown && actionId == "originmagnet") {
        type = ActionType::OriginMagnet;
    }
    bool isPhotoAction = (type == ActionType::Photo);
    bool isCalibrateAction = (type == ActionType::Calibrate);
    const char* calibrationTypeField = doc["calibration_type"].as<const char*>();
    String actionType;
    if (isPhotoAction) {
        actionType = actionName;
    } else if (isCalibrateAction && calibrationTypeField && calibrationTypeField[0]) {
        actionType = calibrationTypeField;
    } else if (type == ActionType::CalibrationValues) {
        actionType = actionName;
    } else {
        actionType = "";
    }
    JsonVariantConst phrase = doc["phrase"];

    int magnetPosition = isPhotoAction ? ActionBaseRotate::getOriginMagnet() : -1;

    JsonVariantConst useModelRaw = doc["use_model"];
    if (useModelRaw.isNull()) {
        useModelRaw = doc["useModel"];
    }
    String useModelJson;
    if (!useModelRaw.isNull()) {
        serializeJson(useModelRaw, useModelJson);
    }

    if (!useModelRaw.isNull()) {
        Serial.printf("[ActionController] use_model found in doc, raw value: %s\n", useModelJson.c_str());
    } else {
        Serial.println("[ActionController] use_model NOT found in doc");
    }

    // publish in_progress for known actions
    if (type != ActionType::Unknown && actionId.length()) {
        BuddyMQTT::sendInProgress(actionId, actionType, phrase, nullptr, magnetPosition, -1, useModelJson);
    }

    // dispatch and then send completed
    switch (type) {
      case ActionType::Gripper:
        {
          bool success = ActionGripper::run(message);
          BuddyMQTT::sendCompleted(actionId, "", success ? "completed" : "failed");
        }
        break;

      case ActionType::BaseRotate:
        {
          int magnet = ActionBaseRotate::run(message);
          StaticJsonDocument<48> response;
          response["origin_magnet"] = magnet;
          String jsonOut;
          serializeJson(response, jsonOut);
          BuddyMQTT::sendCompletedDetails(actionId, "origin_magnet", jsonOut);
        }
        break;

      case ActionType::Servo:
        {
          bool ok = ActionServo::run(message);
          BuddyMQTT::sendCompleted(actionId, "", ok ? "completed" : "failed", phrase);
        }
        break;

      case ActionType::ControlIK:
        {
          bool ok = ActionInverseKinematics::run(message);
          BuddyMQTT::sendCompleted(actionId, "", ok ? "completed" : "failed", phrase);
        }
        break;

      case ActionType::Perch:
        ActionPerch::run(message);
        BuddyMQTT::sendCompleted(actionId);
        break;

      case ActionType::Calibrate:
        ActionCalibrate::run(message);
        break;

      case ActionType::CalibrationValues:
        BuddyMQTT::sendCalibrationValues(actionId);
        break;

      case ActionType::Photo:
        ActionPhoto::run(message, -1);
        BuddyMQTT::sendInProgress(actionId, actionType, phrase, "sent", magnetPosition, -1, useModelJson);
        break;

      case ActionType::OriginMagnet: {
        int magnet = ActionBaseRotate::handleOriginMagnet(message);
        if (actionId.length()) {
          StaticJsonDocument<48> response;
          response["origin_magnet"] = magnet;
          String jsonOut;
          serializeJson(response, jsonOut);
          BuddyMQTT::sendCompletedDetails(actionId, "origin_magnet", jsonOut);
        } else {
          BuddyMQTT::sendCompleted(actionId);
        }
        break;
      }

      case ActionType::OTAUpdate:
        {
          bool success = ActionOTA::run(message);
          BuddyMQTT::sendCompleted(actionId, "ota_update", success ? "completed" : "failed");
          // Note: If successful, ESP32 will reboot and won't reach here
        }
        break;

      default:
        Serial.print("[Unknown Action] ");
        Serial.println(message);
        break;
    }
}
