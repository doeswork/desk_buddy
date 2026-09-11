#pragma once

#include <stddef.h>
#include <stdint.h>

// One deadline budget spans the header and every segment of a publication.
// Never pump MQTT here: another packet would become bytes of this payload.
class MqttStream {
 public:
  explicit MqttStream(uint32_t now) : started(now), progressed(now) {}

  template <typename Client, typename Clock, typename Yield>
  bool write(Client& client, const uint8_t* data, size_t length,
             Clock now, Yield yield) {
    size_t sent = 0;
    while (sent < length) {
      if (!client.connected() || expired(now())) return false;
      const size_t count = length - sent > 4096 ? 4096 : length - sent;
      const size_t wrote = client.write(data + sent, count);
      const uint32_t after = now();
      if (expired(after) || wrote > count || !client.connected()) return false;
      if (wrote) {
        sent += wrote;
        progressed = after;
      }
      yield(wrote ? 1 : 5);
    }
    return !expired(now()) && client.connected();
  }

  bool expired(uint32_t now) const {
    return uint32_t(now - started) >= 15000 ||
           uint32_t(now - progressed) >= 2000;
  }

 private:
  uint32_t started;
  uint32_t progressed;
};
