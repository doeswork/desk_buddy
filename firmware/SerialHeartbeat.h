#ifndef SERIAL_HEARTBEAT_H
#define SERIAL_HEARTBEAT_H

namespace SerialHeartbeat {
  // Call every loop(). Non-blocking: prints one line on its own interval and
  // returns immediately otherwise. Independent of Wi-Fi and MQTT state, so
  // it is the one signal that says "the board is alive and looping" the
  // moment it boots over USB — before either of those connect, and while
  // they are down or retrying.
  void maintain();
}

#endif // SERIAL_HEARTBEAT_H
