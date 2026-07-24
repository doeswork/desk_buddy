// ActionPhoto.h

#pragma once

#include <Arduino.h>

class ActionPhoto {
public:
  static void initializeCamera();

  // Captures a photo and publishes it as a binary MQTT payload (via
  // BuddyMQTT::publishStatusPhoto — see the comment above that call for why
  // this is the one Action file allowed to touch Network_MQTT.h directly).
  // Returns true once the photo was successfully captured AND published;
  // false on any failure (not a photo/detect action, WiFi down, camera init
  // failed, capture failed, or the MQTT publish itself failed).
  // statusJson/detailsKey are left empty — Photo never has JSON details to
  // report; RequestFlow sends a plain completed/failed for it.
  static bool run(const String &message, String& statusJson, String& detailsKey);
};
