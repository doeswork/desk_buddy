#ifndef HEARTBEAT_H
#define HEARTBEAT_H

#include <Arduino.h>

// Callback function type for publishing MQTT messages
typedef bool (*PublishCallback)(const char* topic, const String& payload);

namespace Heartbeat {
  void init();
  void enable();
  void disable();
  bool isEnabled();
  bool shouldSend();  // Check if it's time to send heartbeat (non-blocking)
  void send(bool simple = true);
  void markSent();  // Update the last sent timestamp

  // Set the callback functions for publishing
  void setPublishCallback(PublishCallback callback);
  void setStatusTopic(const char* topic);
  void setHeartbeatTopic(const char* topic);
  void setTimestampCallback(String (*callback)());
}

#endif
