#include "RotationBase.h"
#include <ESP32Servo.h>

// =============================
// Pin Assignments
// =============================
const int RotationBase::SERVO_PIN   = 47;
const int RotationBase::HALL_PIN    = 14;
const int RotationBase::ENC_PIN_A   = 2;
const int RotationBase::ENC_PIN_B   = 1;

// =============================
// Static Variables
// =============================
Servo RotationBase::baseServo;
volatile bool RotationBase::hallState       = false;
volatile bool RotationBase::lastHallState   = false;
volatile int  RotationBase::magnetCount     = 0;
volatile long RotationBase::encoderPos      = 0;

bool RotationBase::isRotating     = false;
bool RotationBase::useMagnets     = false;
bool RotationBase::useEncoder     = false;
int  RotationBase::magnetsTarget  = 0;
int  RotationBase::magnetsPassed  = 0;
long RotationBase::encoderStartPos= 0;
long RotationBase::encoderTarget  = 0;
bool RotationBase::rotateLeft     = false;
int  RotationBase::currentAngle   = RotationBase::BASE_STOP_ANGLE;
RotationBase::BaseSpeed RotationBase::currentSpeed = RotationBase::BASE_STOP;

// =============================
// begin()
// =============================
void RotationBase::begin() {
    // Attach base servo and set to neutral.
    baseServo.attach(SERVO_PIN);
    baseServo.write(BASE_STOP_ANGLE);
    Serial.printf("[RotationBase] Base servo on pin %d set to %d (neutral)\n", SERVO_PIN, BASE_STOP_ANGLE);

    // Set up hall sensor.
    pinMode(HALL_PIN, INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(HALL_PIN), handleHallSensor, CHANGE);
    hallState     = digitalRead(HALL_PIN);
    lastHallState = hallState;
    Serial.printf("[RotationBase] Hall sensor on pin %d initialized with state %d\n", HALL_PIN, hallState);

    // Set up encoder pins.
    pinMode(ENC_PIN_A, INPUT_PULLUP);
    pinMode(ENC_PIN_B, INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(ENC_PIN_A), handleEncoderA, CHANGE);
    attachInterrupt(digitalPinToInterrupt(ENC_PIN_B), handleEncoderB, CHANGE);
    Serial.printf("[RotationBase] Encoder pins A=%d, B=%d initialized\n", ENC_PIN_A, ENC_PIN_B);

    // Reset counters and flags.
    magnetCount   = 0;
    isRotating    = false;
    useMagnets    = false;
    useEncoder    = false;
    magnetsTarget = 0;
    magnetsPassed = 0;
    encoderPos    = 0;

    Serial.println("[RotationBase] Initialization complete");
}

// =============================
// startRotationByMagnet()
// =============================
void RotationBase::startRotationByMagnet(String direction, BaseSpeed speed, int magnetsToPass) {
    magnetCount   = 0;
    magnetsPassed = 0;
    isRotating    = true;
    useMagnets    = true;
    useEncoder    = false;
    magnetsTarget = magnetsToPass;

    // Determine rotation direction.
    rotateLeft = direction.equalsIgnoreCase("LEFT");

    // Use the provided speed as an offset.
    currentSpeed = speed;
    int offset = (int)speed;
    if (rotateLeft) offset = -offset;

    currentAngle = BASE_STOP_ANGLE + offset;
    baseServo.write(currentAngle);
    Serial.printf("[RotationBase] Magnet rotation: dir=%s, speed=%d, magnets=%d; servo set to %d\n",
                  direction.c_str(), (int)speed, magnetsToPass, currentAngle);
}

// =============================
// startRotationByEncoder()
// =============================
void RotationBase::startRotationByEncoder(String direction, BaseSpeed speed, int steps) {
    encoderStartPos = encoderPos;
    isRotating      = true;
    useMagnets      = false;
    useEncoder      = true;

    rotateLeft = direction.equalsIgnoreCase("LEFT");

    currentSpeed = speed;
    int offset = (int)speed;
    if (rotateLeft) offset = -offset;

    currentAngle = BASE_STOP_ANGLE + offset;
    baseServo.write(currentAngle);

    encoderTarget = steps; // e.g. 100 steps from start.
    Serial.printf("[RotationBase] Encoder rotation: dir=%s, speed=%d, steps=%d; servo set to %d\n",
                  direction.c_str(), (int)speed, steps, currentAngle);
}

// =============================
// loop()
// =============================
void RotationBase::loop() {
    if (!isRotating) return;

    if (useMagnets) {
        if (magnetCount >= magnetsTarget) {
            Serial.printf("[RotationBase] Magnet count (%d) reached target; stopping.\n", magnetCount);
            stop();
            return;
        }
    }

    if (useEncoder) {
        long delta = encoderPos - encoderStartPos;
        if (abs(delta) >= encoderTarget) {
            Serial.printf("[RotationBase] Encoder delta (%ld) reached target (%ld); stopping.\n", delta, encoderTarget);
            stop();
            return;
        }
    }
    // Otherwise, continue rotating.
}

// =============================
// stop()
// =============================
void RotationBase::stop() {
    Serial.println("[RotationBase] Stopping; setting servo to neutral (90°).");
    baseServo.write(BASE_STOP_ANGLE);
    isRotating   = false;
    useMagnets   = false;
    useEncoder   = false;
    magnetsTarget = 0;
    magnetsPassed = 0;
    encoderTarget = 0;
    currentSpeed  = BASE_STOP;
}

// =============================
// Interrupt Handlers
// =============================
void IRAM_ATTR RotationBase::handleHallSensor() {
    bool newState = digitalRead(HALL_PIN);
    if (newState != lastHallState) {
        if (!newState) {  // Count on falling edge.
            magnetCount++;
        }
    }
    lastHallState = newState;
}

void IRAM_ATTR RotationBase::handleEncoderA() {
    bool a = digitalRead(ENC_PIN_A);
    bool b = digitalRead(ENC_PIN_B);
    if (a == b) {
        encoderPos++;
    } else {
        encoderPos--;
    }
}

void IRAM_ATTR RotationBase::handleEncoderB() {
    bool a = digitalRead(ENC_PIN_A);
    bool b = digitalRead(ENC_PIN_B);
    if (a != b) {
        encoderPos++;
    } else {
        encoderPos--;
    }
}

// =============================
// registerEndpoints()
// =============================
#include <WebServer.h>  // Ensure WebServer is included

void RotationBase::registerEndpoints(WebServer &server) {
    server.on("/controlRotationBase", HTTP_GET, [&server]() {
        server.sendHeader("Access-Control-Allow-Origin", "*");
        if (!server.hasArg("mode") || !server.hasArg("direction") || !server.hasArg("count")) {
            server.send(400, "text/plain", "Missing parameters. Required: mode, direction, count");
            return;
        }
        String mode = server.arg("mode");
        String direction = server.arg("direction");
        int count = server.arg("count").toInt();

        // Use default speed BASE_MEDIUM.
        BaseSpeed speed = BASE_MEDIUM;
        
        if (mode.equalsIgnoreCase("encoder")) {
            RotationBase::startRotationByEncoder(direction, speed, count);
        } else if (mode.equalsIgnoreCase("magnet")) {
            RotationBase::startRotationByMagnet(direction, speed, count);
        } else {
            server.send(400, "text/plain", "Invalid mode. Use 'encoder' or 'magnet'.");
            return;
        }
        server.send(200, "text/plain", "Rotation command accepted");
    });
}
