// ActionOTA.h
#ifndef ACTION_OTA_H
#define ACTION_OTA_H

#include <Arduino.h>

#define FIRMWARE_VERSION "not_set"

namespace ActionOTA {
  struct OtaStatus {
    String runningVersion;
    String desiredVersion;
    String desiredUrl;
    String desiredSha;
    String otaState;
    String lastAttemptedVersion;
    String lastError;
    bool updateRequired;
  };

  // Initiates HTTP OTA update from a URL
  // Returns true if update started successfully, false otherwise
  bool run(const String& message);

  // Persist current running firmware at boot and update OTA state.
  void markBooted();

  // Read persistent OTA status and desired/actual versions.
  OtaStatus getStatus();

  // If desired version differs from running version, retry OTA using stored URL/SHA.
  // Returns true if an OTA attempt was started.
  bool enforceDesiredVersion();
}

#endif // ACTION_OTA_H
