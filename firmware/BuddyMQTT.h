// BuddyMQTT.h
#ifndef BUDDY_MQTT_H
#define BUDDY_MQTT_H

#include <Arduino.h>
#include <ArduinoJson.h>

namespace BuddyMQTT {
  void maintain();
  void listen();
  void sendInProgress(const String& actionId,
                      const String& type = "",
                      JsonVariantConst phrase = JsonVariantConst(),
                      const char* logMessage = nullptr,
                      int useModel = -1,
                      const String& useModelJson = String());

  // one-argument version (used elsewhere)
  void sendCompleted(const String& actionId, const String& type = "", const char* status = "completed", JsonVariantConst phrase = JsonVariantConst());

  // new multi-argument version for detailed results
  void sendCompletedDetails(const String& actionId, const char* key, const String& jsonOut, const String& type = "", const char* status = "completed", JsonVariantConst phrase = JsonVariantConst());

  void sendCalibrationValues(const String& actionId);

  bool publishBinary(const String& topic, const uint8_t* data, size_t length);
  bool publishStatusPhoto(const String& actionId, const String& requester, const uint8_t* data, size_t length, JsonVariantConst phrase = JsonVariantConst(), int useModel = -1, const String& useModelJson = String());

  // Workflow context — set once per incoming action; automatically included
  // in every outgoing message while set.  Pass -1 to leave a field unset.
  void setWorkflowContext(int workflowId, int workflowEventId);
  void clearWorkflowContext();

  // Debug message publishing
  void sendDebug(const String& component, const String& message);

  // Progress telemetry for long blocking actions (base rotation calibration).
  // A blocking action starves listen()/maintain(), so this also pumps the MQTT
  // client to keep the session alive across a multi-minute run.
  void sendProgress(const String& actionId, const String& type, const String& detailsJson);

  // Action id of the command currently being executed, or "" when idle. Set by
  // ActionController::dispatch so deep code can attribute its own telemetry.
  const String& currentActionId();
  void setCurrentActionId(const String& actionId);

  // Reset diagnostics - call from setup() before maintain()
  void setResetReason(const char* reason, uint32_t freeHeap, uint32_t minFreeHeap);
}

#endif // BUDDY_MQTT_H
