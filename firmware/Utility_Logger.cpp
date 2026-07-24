// Utility_Logger.cpp
#include "Utility_Logger.h"
#include "Network_MQTT.h"

void Logger::debug(const String& component, const String& message) {
  BuddyMQTT::sendDebug(component, message);
}
