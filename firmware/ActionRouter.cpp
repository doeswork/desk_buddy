// ActionRouter.cpp
//
// The routing table and its handlers. Each handler adapts one Action* module to
// the uniform Handler signature; the table below is the only place an action
// name is bound to behavior.
#include "ActionRouter.h"
#include "ActionCalibrate.h"
#include "ActionPhoto.h"
#include "ActionGripper.h"
#include "ActionBaseRotate.h"
#include "ActionServo.h"
#include "ActionInverseKinematics.h"
#include "ActionStencilCalibrate.h"
#include "ActionPerch.h"
#include "ActionOTA.h"
#include "CalibrateRotation.h"
#include "BuddyMQTT.h"
#include <string.h>

namespace {

  bool handleGripper(const ActionRouter::Context& ctx, String&) {
    return ActionGripper::run(ctx.message);
  }

  bool handleBaseRotate(const ActionRouter::Context& ctx, String& detailsJson) {
    return ActionBaseRotate::run(ctx.message, detailsJson);
  }

  // Dedicated base rotation profiling. The action itself is the intent, so no
  // controlType is required in the payload.
  bool handleCalibrateBaseRotation(const ActionRouter::Context& ctx, String& detailsJson) {
    StaticJsonDocument<256> doc;
    long neutral = -1;
    if (deserializeJson(doc, ctx.message) == DeserializationError::Ok) {
      JsonVariantConst neutralValue = doc["neutralServoAngle"];
      if (neutralValue.is<long>() || neutralValue.is<int>()) {
        neutral = neutralValue.as<long>();
      }
    }
    return ActionBaseRotate::calibrateProfile(detailsJson, neutral);
  }

  bool handleServo(const ActionRouter::Context& ctx, String&) {
    return ActionServo::run(ctx.message);
  }

  bool handleControlIK(const ActionRouter::Context& ctx, String&) {
    return ActionInverseKinematics::run(ctx.message);
  }

  bool handleStencilCalibrate(const ActionRouter::Context& ctx, String& detailsJson) {
    return ActionStencilCalibrate::run(ctx.message, detailsJson);
  }

  bool handlePerch(const ActionRouter::Context& ctx, String&) {
    ActionPerch::run(ctx.message);
    return true;
  }

  // ActionCalibrate publishes its own MQTT reply.
  bool handleCalibrate(const ActionRouter::Context& ctx, String&) {
    ActionCalibrate::run(ctx.message);
    return true;
  }

  bool handleCalibrationValues(const ActionRouter::Context& ctx, String&) {
    BuddyMQTT::sendCalibrationValues(ctx.actionId);
    return true;
  }

  bool handlePhoto(const ActionRouter::Context& ctx, String&) {
    ActionPhoto::run(ctx.message, -1);
    return true;
  }

  bool handleOtaUpdate(const ActionRouter::Context& ctx, String&) {
    // On success the ESP32 reboots and never returns here.
    return ActionOTA::run(ctx.message);
  }

  using ActionRouter::ReplyStyle;

  constexpr ActionRouter::Route ROUTES[] = {
    // gripper's reply intentionally carries no phrase, matching prior behavior.
    {"gripper",           handleGripper,             ReplyStyle::CompletedNoPhrase, "",                   false, false},
    {"baseRotate",        handleBaseRotate,          ReplyStyle::CompletedDetails,  "base_rotation",      false, false},
    {"calibrate_base_rotation", handleCalibrateBaseRotation,
                                                     ReplyStyle::CompletedDetails,  "base_rotation",      false, true},
    {"servo",             handleServo,               ReplyStyle::Completed,         "",                   false, false},
    {"controlik",         handleControlIK,           ReplyStyle::Completed,         "",                   false, false},
    {"stencilCalibrate",  handleStencilCalibrate,    ReplyStyle::CompletedDetails,  "stencil_calibration",false, false},
    {"perch",             handlePerch,               ReplyStyle::CompletedNoPhrase, "",                   false, false},
    {"calibrate",         handleCalibrate,           ReplyStyle::HandlerOwned,      "",                   true,  false},
    {"calibrationvalues", handleCalibrationValues,   ReplyStyle::HandlerOwned,      "",                   false, true},
    {"photo",             handlePhoto,               ReplyStyle::PhotoInProgress,   "",                   false, true},
    {"detect_object",     handlePhoto,               ReplyStyle::PhotoInProgress,   "",                   false, true},
    {"detect_color",      handlePhoto,               ReplyStyle::PhotoInProgress,   "",                   false, true},
    {"calibrate_depth",   handlePhoto,               ReplyStyle::PhotoInProgress,   "",                   false, true},
    {"ota_update",        handleOtaUpdate,           ReplyStyle::CompletedNamed,    "ota_update",         false, false},
  };

  constexpr size_t ROUTE_COUNT = sizeof(ROUTES) / sizeof(ROUTES[0]);
}

const ActionRouter::Route* ActionRouter::find(const char* action) {
  if (!action) return nullptr;
  for (size_t i = 0; i < ROUTE_COUNT; ++i) {
    if (strcmp(action, ROUTES[i].action) == 0) {
      return &ROUTES[i];
    }
  }
  return nullptr;
}

bool ActionRouter::run(const Route& route, const Context& ctx, String& detailsJson) {
  if (!route.handler) return false;
  return route.handler(ctx, detailsJson);
}
