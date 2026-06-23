#include "ArmServos.h"
#include <ArduinoJson.h>
#include <ESP32Servo.h>
#include <Preferences.h>
#include <math.h>

namespace {
  constexpr uint8_t kServoPins[3] = {47, 39, 19};
  constexpr int kDefaultAngle = 90;

  // Max movement rates in deg/sec.
  constexpr float kMaxDegPerSec[3] = {20.0f, 40.0f, 40.0f};
  // Slow start/end rates in deg/sec.
  constexpr float kMinDegPerSec[3] = {5.0f, 10.0f, 10.0f};

  const char* kPrefKeys[3] = {"ELBOW_ANGLE", "WRIST_ANGLE", "TWIST_ANGLE"};

  struct CalPoint {
    int elbow;
    int wrist;
    float dist;
  };

  constexpr float kTableGuardToleranceDeg = 2.0f;

  Servo servos[3];
  int currentAngles[3] = {kDefaultAngle, kDefaultAngle, kDefaultAngle};
  bool inited = false;
  Preferences prefs;

  int clampAngle(int angle) {
    return constrain(angle, 0, 180);
  }

  float speedForProgress(float progress, int idx) {
    float eased = sinf(progress * PI);
    float minSpeed = kMinDegPerSec[idx];
    float maxSpeed = kMaxDegPerSec[idx];
    return minSpeed + (maxSpeed - minSpeed) * eased;
  }

  int monotonicDir(int a, int b, int c) {
    if (b > a && c > b) return 1;
    if (b < a && c < b) return -1;
    return 0;
  }

  bool parseCalPoint(const char* key, CalPoint& out) {
    String json = prefs.getString(key, "");
    if (!json.length()) return false;

    StaticJsonDocument<128> doc;
    if (deserializeJson(doc, json) != DeserializationError::Ok) return false;
    if (!doc["ELBOW"].is<float>() && !doc["ELBOW"].is<int>()) return false;
    if (!doc["WRIST"].is<float>() && !doc["WRIST"].is<int>()) return false;

    out.elbow = static_cast<int>(doc["ELBOW"].as<float>());
    out.wrist = static_cast<int>(doc["WRIST"].as<float>());
    out.dist = doc["DISTANCE"] | 0.0f;
    return true;
  }

  struct GuardCal {
    CalPoint a;
    CalPoint b;
    CalPoint c;
    int elbowDir;
    int wristDir;
  };

  bool loadGuardCal(GuardCal& cal) {
    if (!parseCalPoint("hover_over_min", cal.a) ||
        !parseCalPoint("hover_over_mid", cal.b) ||
        !parseCalPoint("hover_over_max", cal.c)) {
      return false;
    }
    cal.elbowDir = monotonicDir(cal.a.elbow, cal.b.elbow, cal.c.elbow);
    cal.wristDir = monotonicDir(cal.a.wrist, cal.b.wrist, cal.c.wrist);
    return (cal.elbowDir != 0 || cal.wristDir != 0);
  }

  float signedDistanceToLine(const CalPoint& s, const CalPoint& e, int elbow, int wrist) {
    float dx = static_cast<float>(e.elbow - s.elbow);
    float dy = static_cast<float>(e.wrist - s.wrist);
    float denom = sqrtf(dx * dx + dy * dy);
    if (denom < 1e-4f) return 0.0f;
    float cross = dx * (static_cast<float>(wrist) - s.wrist)
                - dy * (static_cast<float>(elbow) - s.elbow);
    return cross / denom;
  }

  bool selectGuardSegment(const GuardCal& cal, int elbow, int wrist, CalPoint& s, CalPoint& e) {
    if (cal.elbowDir != 0) {
      bool useFirst = (cal.elbowDir > 0) ? (elbow <= cal.b.elbow) : (elbow >= cal.b.elbow);
      s = useFirst ? cal.a : cal.b;
      e = useFirst ? cal.b : cal.c;
      return true;
    }
    if (cal.wristDir != 0) {
      bool useFirst = (cal.wristDir > 0) ? (wrist <= cal.b.wrist) : (wrist >= cal.b.wrist);
      s = useFirst ? cal.a : cal.b;
      e = useFirst ? cal.b : cal.c;
      return true;
    }
    return false;
  }

  bool isPoseTableSafe(int elbow, int wrist, int currentElbow, int currentWrist) {
    GuardCal cal;
    if (!loadGuardCal(cal)) return true;

    CalPoint s, e;
    if (!selectGuardSegment(cal, elbow, wrist, s, e)) return true;

    float distTarget = signedDistanceToLine(s, e, elbow, wrist);
    if (fabsf(distTarget) <= kTableGuardToleranceDeg) return true;

    int perchElbow = static_cast<int>(prefs.getFloat("p_elbow",
                                                     prefs.getFloat("PERCH_ELBOW_ANGLE",
                                                                    currentElbow)));
    int perchWrist = static_cast<int>(prefs.getFloat("p_wrist",
                                                     prefs.getFloat("PERCH_WRIST_ANGLE",
                                                                    currentWrist)));
    float distPerch = signedDistanceToLine(s, e, perchElbow, perchWrist);
    if (fabsf(distPerch) <= kTableGuardToleranceDeg) {
      distPerch = signedDistanceToLine(s, e, currentElbow, currentWrist);
    }
    if (fabsf(distPerch) <= kTableGuardToleranceDeg) return true;

    bool safeSidePositive = distPerch > 0.0f;
    return (distTarget > 0.0f) == safeSidePositive;
  }

  bool moveServoInternal(int idx, int target, bool updatePrefs) {
    target = clampAngle(target);
    int start = currentAngles[idx];
    if (start == target) return true;

    if (idx == 0 || idx == 1) {
      int targetElbow = (idx == 0) ? target : currentAngles[0];
      int targetWrist = (idx == 1) ? target : currentAngles[1];
      if (!isPoseTableSafe(targetElbow, targetWrist, currentAngles[0], currentAngles[1])) {
        Serial.printf("[ArmServos] Table guard blocked move (elbow=%d wrist=%d)\n",
                      targetElbow, targetWrist);
        return false;
      }
    }

    int step = (target > start) ? 1 : -1;
    int steps = abs(target - start);
    for (int i = 1; i <= steps; ++i) {
      int pos = start + (step * i);
      float progress = static_cast<float>(i) / static_cast<float>(steps);
      float speed = speedForProgress(progress, idx);
      int delayMs = static_cast<int>(roundf(1000.0f / speed));
      servos[idx].write(pos);
      delay(delayMs);
    }

    currentAngles[idx] = target;
    if (updatePrefs) {
      prefs.putFloat(kPrefKeys[idx], static_cast<float>(target));
    }
    return true;
  }
} // namespace

void ArmServos::begin() {
  if (inited) return;
  prefs.begin("config", false);

  for (int i = 0; i < 3; ++i) {
    servos[i].attach(kServoPins[i]);
    servos[i].write(kDefaultAngle);
    currentAngles[i] = kDefaultAngle;
  }

  inited = true;

  int elbow = static_cast<int>(prefs.getFloat(kPrefKeys[0], kDefaultAngle));
  int wrist = static_cast<int>(prefs.getFloat(kPrefKeys[1], kDefaultAngle));
  int twist = static_cast<int>(prefs.getFloat(kPrefKeys[2], kDefaultAngle));

  moveServoInternal(0, elbow, false);
  moveServoInternal(1, wrist, false);
  moveServoInternal(2, twist, false);
}

int ArmServos::getAngle(Joint joint) {
  begin();
  return currentAngles[static_cast<int>(joint)];
}

bool ArmServos::moveTo(Joint joint, int target) {
  begin();
  return moveServoInternal(static_cast<int>(joint), target, true);
}

bool ArmServos::moveToLive(Joint joint, int target) {
  begin();
  int idx = static_cast<int>(joint);
  target = clampAngle(target);
  int start = currentAngles[idx];
  if (start == target) return true;

  // Live mode: bypass pose safety checks and use constant slow speed
  int step = (target > start) ? 1 : -1;
  int steps = abs(target - start);
  constexpr int kLiveModeDelayMs = 50; // Slow, constant speed for live control

  for (int i = 1; i <= steps; ++i) {
    int pos = start + (step * i);
    servos[idx].write(pos);
    delay(kLiveModeDelayMs);
  }

  currentAngles[idx] = target;
  prefs.putFloat(kPrefKeys[idx], static_cast<float>(target));
  return true;
}
