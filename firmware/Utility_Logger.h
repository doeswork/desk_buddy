// Utility_Logger.h
#ifndef UTILITY_LOGGER_H
#define UTILITY_LOGGER_H

#include <Arduino.h>

namespace Logger {
  // Publishes a live diagnostic trace line over MQTT ({"debug":component,
  // "msg":message}). This is the only sanctioned way for anything outside
  // Network_MQTT.cpp/Network_RequestFlow.cpp to put a message on the wire —
  // no Action*/Calibrate* file should call Network_MQTT.h directly, even
  // for debug tracing.
  void debug(const String& component, const String& message);
}

#endif // UTILITY_LOGGER_H
