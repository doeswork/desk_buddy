#ifndef ROTATIONBASE_H
#define ROTATIONBASE_H

#include <Arduino.h>
#include <ESP32Servo.h>
#include <WebServer.h>    // For WebServer type

class RotationBase {
public:
  // Predefined speed values.
  enum BaseSpeed {
    BASE_STOP = 0,
    BASE_SLOW = 20,
    BASE_MEDIUM = 40,
    BASE_FAST = 60
  };

  static const int BASE_STOP_ANGLE = 90;

  // Pin assignments.
  static const int SERVO_PIN;
  static const int HALL_PIN;
  static const int ENC_PIN_A;
  static const int ENC_PIN_B;

  // Public methods.
  static void begin();
  static void startRotationByMagnet(String direction, BaseSpeed speed, int magnetsToPass);
  static void startRotationByEncoder(String direction, BaseSpeed speed, int steps);
  static void loop();
  static void stop();

  // Register the /controlRotationBase endpoint on a given WebServer.
  static void registerEndpoints(WebServer &server);

  // Interrupt handlers.
  static void IRAM_ATTR handleHallSensor();
  static void IRAM_ATTR handleEncoderA();
  static void IRAM_ATTR handleEncoderB();

  static int currentAngle;

private:
  static Servo baseServo;
  static volatile bool hallState;
  static volatile bool lastHallState;
  static volatile int magnetCount;
  static volatile long encoderPos;
  
  static bool isRotating;
  static bool useMagnets;
  static bool useEncoder;
  static int magnetsTarget;
  static int magnetsPassed;
  static long encoderStartPos;
  static long encoderTarget;
  static bool rotateLeft;
  static BaseSpeed currentSpeed;
};

#endif // ROTATIONBASE_H
