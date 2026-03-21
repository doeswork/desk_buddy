// LED.cpp
#include "LED.h"
#include <Ticker.h>

namespace {
  constexpr uint8_t PIN = 41;
  static Ticker ticker;
  static bool   initialized = false;

  void lazyInit() {
    if (!initialized) {
      pinMode(PIN, OUTPUT);
      digitalWrite(PIN, LOW);
      ticker.detach();
      initialized = true;
    }
  }

  void toggleLED() {
    digitalWrite(PIN, !digitalRead(PIN));
  }
}

void LED::Blink(float intervalSec) {
  lazyInit();
  ticker.detach();
  ticker.attach(intervalSec, toggleLED);
}

void LED::On() {
  lazyInit();
  ticker.detach();
  digitalWrite(PIN, HIGH);
}

void LED::Off() {
  lazyInit();
  ticker.detach();
  digitalWrite(PIN, LOW);
}
