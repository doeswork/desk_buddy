// ActionBaseRotate.cpp
#include "ActionBaseRotate.h"
#include <ESP32Servo.h>
#include <ArduinoJson.h>
#include <Arduino.h>
#include <Preferences.h>

namespace {
  // Pin assignments
  constexpr uint8_t SERVO_PIN = 21;
  constexpr uint8_t HALL_PIN  = 14;
  constexpr uint8_t ENC_PIN_A = 2;
  constexpr uint8_t ENC_PIN_B = 1;
  constexpr int     TOTAL_MAGNETS = 12;
  constexpr const char ORIGIN_KEY[] = "origin_magnet";

  static Servo       baseServo;
  static Preferences prefs;
  static bool        inited = false;

  // rotation state
  volatile long magnetCount = 0;
  volatile long encoderPos  = 0;
  volatile long hallEvents  = 0;

  bool   rotateLeft   = false;
  long   magnetsTarget = 0;
  long   encoderTarget = 0;
  long   encoderStart  = 0;
  int    originMagnet  = 1;
  int    persistedOriginMagnet = -1;

  int restingValue = 92;
  enum BaseSpeed {
    BASE_VERY_SLOW  = 5,
    BASE_SLOW       = 10,
    BASE_REGULAR    = 20,
    BASE_FAST       = 30,
    BASE_SUPERFAST  = 40
  };
  BaseSpeed currentSpeed = BASE_REGULAR;

  // ISR: count magnets on falling edge
  void IRAM_ATTR handleHall() {
    static bool last = HIGH;
    bool now = digitalRead(HALL_PIN);
    if (last && !now) {
      magnetCount++;
      hallEvents++;
      Serial.printf("[Rotate] magnetCount=%ld/%ld\n", magnetCount, magnetsTarget);
    }
    last = now;
  }

  // encoder ISRs
  void IRAM_ATTR handleEncA() {
    bool a = digitalRead(ENC_PIN_A), b = digitalRead(ENC_PIN_B);
    encoderPos += (a==b) ? +1 : -1;
  }
  void IRAM_ATTR handleEncB() {
    bool a = digitalRead(ENC_PIN_A), b = digitalRead(ENC_PIN_B);
    encoderPos += (a!=b) ? +1 : -1;
  }

  int normalizeMagnetIndex(int value) {
    int offset = (value - 1) % TOTAL_MAGNETS;
    if (offset < 0) offset += TOTAL_MAGNETS;
    return offset + 1;
  }

  void persistOriginMagnet() {
    if (originMagnet == persistedOriginMagnet) return;  // avoid unnecessary flash writes
    prefs.begin("rot", false);
    prefs.putInt(ORIGIN_KEY, originMagnet);
    prefs.end();
    persistedOriginMagnet = originMagnet;
    Serial.printf("[Rotate] saved %s = %d\n", ORIGIN_KEY, originMagnet);
  }

  void updateOriginFromHall(long& lastHallSeen) {
    long current = hallEvents;
    long delta   = current - lastHallSeen;
    if (delta == 0) return;

    int step = rotateLeft ? -1 : 1;
    for (long i = 0; i < labs(delta); ++i) {
      originMagnet = normalizeMagnetIndex(originMagnet + step);
    }
    lastHallSeen = current;
    Serial.printf("[Rotate] origin_magnet -> %d (delta=%ld)\n", originMagnet, delta);
  }

  int parseMagnetField(JsonVariantConst var) {
    if (var.is<int>()) return var.as<int>();
    if (var.is<long>()) return static_cast<int>(var.as<long>());
    if (var.is<const char*>()) {
      const char* s = var.as<const char*>();
      if (s && *s) return atoi(s);
    }
    return -1;
  }

  void initAll() {
    if (inited) return;
    baseServo.attach(SERVO_PIN);
    prefs.begin("rot", false);
      originMagnet = normalizeMagnetIndex(prefs.getInt(ORIGIN_KEY, 1));
      persistedOriginMagnet = originMagnet;
    prefs.end();
    baseServo.write(restingValue);  // stop position for continuous servo

    pinMode(HALL_PIN, INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(HALL_PIN), handleHall, CHANGE);

    pinMode(ENC_PIN_A, INPUT_PULLUP);
    pinMode(ENC_PIN_B, INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(ENC_PIN_A), handleEncA, CHANGE);
    attachInterrupt(digitalPinToInterrupt(ENC_PIN_B), handleEncB, CHANGE);

    inited = true;
    Serial.printf("[Rotate] initialized @ resting %d\n", restingValue);
  }

  void stopServo() {
    baseServo.write(restingValue);
    Serial.println("[Rotate] stop");
  }

  BaseSpeed parseSpeed(const char* s) {
    if      (strcasecmp(s,"veryslow")==0)  return BASE_VERY_SLOW;
    else if (strcasecmp(s,"slow")   ==0)   return BASE_SLOW;
    else if (strcasecmp(s,"fast")   ==0)   return BASE_FAST;
    else if (strcasecmp(s,"superfast")==0) return BASE_SUPERFAST;
    return BASE_REGULAR;
  }
}

int ActionBaseRotate::handleOriginMagnet(const String& message) {
  initAll();

  StaticJsonDocument<128> doc;
  int requested = -1;
  if (deserializeJson(doc, message) == DeserializationError::Ok) {
    requested = parseMagnetField(doc["origin_magnet"]);
    if (requested < 0) requested = parseMagnetField(doc["value"]);
  }

  if (requested > 0) {
    originMagnet = normalizeMagnetIndex(requested);
    Serial.printf("[Rotate] origin_magnet set to %d\n", originMagnet);
    persistOriginMagnet();
  } else {
    Serial.printf("[Rotate] origin_magnet query -> %d\n", originMagnet);
  }

  return originMagnet;
}

int ActionBaseRotate::getOriginMagnet() {
  initAll();
  return originMagnet;
}

int ActionBaseRotate::run(const String& message) {
  initAll();

  StaticJsonDocument<256> doc;
  if (deserializeJson(doc, message) != DeserializationError::Ok) {
    Serial.print("[Rotate] JSON parse error: ");
    Serial.println(message);
    return originMagnet;
  }

  const char* ctl = doc["controlType"].as<const char*>();
  const char* dir = doc["direction"].  as<const char*>();
  long         val = doc["value"] | -1;

  if (!ctl || !dir || val < 0) {
    Serial.print("[Rotate] missing/invalid fields: ");
    Serial.println(message);
    return originMagnet;
  }

  currentSpeed = BASE_SLOW;
  rotateLeft   = strcasecmp(dir,"LEFT")==0;
  long lastHallSeen = hallEvents;

  if (strcasecmp(ctl,"originmagnet")==0) {
    int targetMagnet = parseMagnetField(doc["value"]);
    if (targetMagnet < 1 || targetMagnet > TOTAL_MAGNETS) {
      Serial.printf("[Rotate] originmagnet invalid target %d\n", targetMagnet);
      return originMagnet;
    }
    targetMagnet = normalizeMagnetIndex(targetMagnet);

    int leftSteps  = (originMagnet - targetMagnet + TOTAL_MAGNETS) % TOTAL_MAGNETS;   // rotateLeft decrements
    int rightSteps = (targetMagnet - originMagnet + TOTAL_MAGNETS) % TOTAL_MAGNETS;   // rotateRight increments

    if (leftSteps == 0 && rightSteps == 0) {
      Serial.printf("[Rotate] originmagnet already at %d\n", originMagnet);
      persistOriginMagnet();
      return originMagnet;
    }

    bool goLeft;
    if (leftSteps < rightSteps) goLeft = true;
    else if (rightSteps < leftSteps) goLeft = false;
    else goLeft = rotateLeft;  // tie-breaker: honor requested direction

    long magnetsToMove = goLeft ? leftSteps : rightSteps;
    rotateLeft = goLeft;

    magnetCount    = 0;
    magnetsTarget  = magnetsToMove;
    Serial.printf("[Rotate] ORIGINMAGNET current=%d target=%d steps=%ld dir=%s speed=%d\n",
                  originMagnet, targetMagnet, magnetsToMove, rotateLeft ? "LEFT" : "RIGHT", currentSpeed);
    lastHallSeen = hallEvents;

    float ratio = float(currentSpeed) / float(BASE_SUPERFAST);
    int   offsetAngle = round(ratio * 90);
    int   driveAngle  = rotateLeft ? (90 - offsetAngle) : (90 + offsetAngle);

    baseServo.write(driveAngle);

    while (magnetCount < magnetsTarget) {
      updateOriginFromHall(lastHallSeen);
      delay(10);
    }
    updateOriginFromHall(lastHallSeen);

  } else if (strcasecmp(ctl,"MAGNET")==0) {
    // reset count & set target
    magnetCount    = 0;
    magnetsTarget  = val;
    Serial.printf("[Rotate] MAGNET dir=%s target=%ld speed=%d\n", dir, val, currentSpeed);
    lastHallSeen = hallEvents;

    // compute continuous-servo angle for speed:
    // offsetAngle in [0..90], proportionate to speed/BASE_SUPERFAST
    float ratio = float(currentSpeed) / float(BASE_SUPERFAST);
    int   offsetAngle = round(ratio * 90);
    int   driveAngle  = rotateLeft ? (90 - offsetAngle) : (90 + offsetAngle);

    baseServo.write(driveAngle);

    while (magnetCount < magnetsTarget) {
      updateOriginFromHall(lastHallSeen);
      delay(10);
    }

  } else if (strcasecmp(ctl,"ENCODER")==0) {
    encoderStart  = encoderPos;
    encoderTarget = val;
    Serial.printf("[Rotate] ENCODER dir=%s target=%ld speed=%d\n", dir, val, currentSpeed);
    lastHallSeen = hallEvents;

    float ratio = float(currentSpeed) / float(BASE_SUPERFAST);
    int   offsetAngle = round(ratio * 90);
    int   driveAngle  = rotateLeft ? (90 - offsetAngle) : (90 + offsetAngle);

    baseServo.write(driveAngle);

    while (labs(encoderPos - encoderStart) < encoderTarget) {
      updateOriginFromHall(lastHallSeen);
      delay(10);
    }

  } else {
    Serial.print("[Rotate] unknown controlType: ");
    Serial.println(ctl);
  }

  updateOriginFromHall(lastHallSeen);
  stopServo();
  persistOriginMagnet();
  return originMagnet;
}
