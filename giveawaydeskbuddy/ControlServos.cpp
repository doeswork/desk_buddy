#include "ControlServos.h"
#include <Arduino.h>
#include <ESP32Servo.h>
#include <WebServer.h>

// Number of servos in the robot arm.
#define NUM_SERVOS 5

// Update interval (in milliseconds) for normal servo movement.
#define UPDATE_INTERVAL 20

// Define the pins to which the servos are attached.
// (Adjust these if needed.)
const int servoPins[NUM_SERVOS] = {42, 41, 40, 39, 38};

// Create an array of Servo objects.
static Servo servos[NUM_SERVOS];

// Structure for storing each servo’s state (for normal servos).
struct ServoState {
  int currentPos;
  int targetPos;
  int speed;              // Movement speed (degrees per update tick)
  unsigned long lastUpdate;
};

// One state object per servo.
static ServoState servoStates[NUM_SERVOS];

// For the continuous (360°) servo (servo 4), store a stop time.
static unsigned long servo4StopTime = 0;

// Create a WebServer instance on port 81.
static WebServer servoServer(81);

//
// --- HTTP Endpoint Handlers ---
//

/// /status endpoint: used to confirm connection.
void handleStatus() {
  Serial.println("Status request received");
  servoServer.sendHeader("Access-Control-Allow-Origin", "*");
  servoServer.send(200, "text/plain", "ESP32 Servo Controller: Online");
}

/// /controlServo endpoint: processes both normal and special commands.
void handleControlServo() {
  // Always add the CORS header.
  servoServer.sendHeader("Access-Control-Allow-Origin", "*");
  
  // Check if a special "command" parameter is provided.
  if (servoServer.hasArg("command")) {
    String cmd = servoServer.arg("command");
    if (!servoServer.hasArg("servo")) {
      servoServer.send(400, "text/plain", "Missing servo parameter for command.");
      return;
    }
    int servoIndex = servoServer.arg("servo").toInt();
    
    // --- Gripper (servo 1) special commands ---
    if (servoIndex == 1) {
      if (cmd == "GRAB") {
        // Set up the button pins (pins 19 and 20) as inputs with pull-ups.
        pinMode(19, INPUT_PULLUP);
        pinMode(20, INPUT_PULLUP);
        int pos = 180;
        // Loop from 180 down to 0 (decrementing by 1)
        // and check each time if either button is pressed.
        while (pos > 0) {
          if (digitalRead(19) == LOW || digitalRead(20) == LOW) {
            break;
          }
          servos[1].write(pos);
          delay(20); // wait a bit between steps
          pos--;
        }
        servoStates[1].currentPos = pos;
        servoStates[1].targetPos = pos;
        String response = "Gripper GRAB executed. Final pos: " + String(pos);
        Serial.println(response);
        servoServer.send(200, "text/plain", response);
      } else if (cmd == "DROP") {
        // Set the gripper servo back to 180.
        servos[1].write(180);
        servoStates[1].currentPos = 180;
        servoStates[1].targetPos = 180;
        String response = "Gripper DROP executed. Set pos to 180.";
        Serial.println(response);
        servoServer.send(200, "text/plain", response);
      } else {
        servoServer.send(400, "text/plain", "Unknown command for gripper servo.");
      }
      return;
    }
    // --- Continuous (360°) servo special command (servo 4) ---
    else if (servoIndex == 4) {
      if (cmd == "MOVE") {
        if (!servoServer.hasArg("position") || !servoServer.hasArg("time")) {
          servoServer.send(400, "text/plain", "Missing parameters for MOVE command on continuous servo.");
          return;
        }
        int movePos = servoServer.arg("position").toInt();
        int moveTime = servoServer.arg("time").toInt(); // in ms
        // Attach servo 4 if it isn’t already attached.
        if (!servos[4].attached()) {
          servos[4].attach(servoPins[4]);
        }
        servos[4].write(movePos);
        servo4StopTime = millis() + moveTime;
        String response = "Continuous servo MOVE executed: pos " + String(movePos) +
                          " for " + String(moveTime) + " ms.";
        Serial.println(response);
        servoServer.send(200, "text/plain", response);
      } else {
        servoServer.send(400, "text/plain", "Unknown command for continuous servo.");
      }
      return;
    }
    else {
      servoServer.send(400, "text/plain", "Command not supported for servo index " + String(servoIndex));
      return;
    }
  }
  
  // --- Normal command processing for servos 0, 2, and 3 ---
  if (servoServer.hasArg("servo") && servoServer.hasArg("speed") && servoServer.hasArg("position")) {
    int servoIndex = servoServer.arg("servo").toInt();
    // For servo 1 and servo 4, use command-based control.
    if (servoIndex == 1 || servoIndex == 4) {
      servoServer.send(400, "text/plain", "For servo 1 and 4, please use command-based control.");
      return;
    }
    int speed = servoServer.arg("speed").toInt();
    int targetPos = servoServer.arg("position").toInt();
    if (targetPos < 0 || targetPos > 180) {
      servoServer.send(400, "text/plain", "Invalid position. Must be 0-180.");
      return;
    }
    if (speed < 0) {
      servoServer.send(400, "text/plain", "Invalid speed.");
      return;
    }
    servoStates[servoIndex].targetPos = targetPos;
    servoStates[servoIndex].speed = speed;
    if (speed == 0) {
      // If speed is zero, move instantly.
      servoStates[servoIndex].currentPos = targetPos;
      servos[servoIndex].write(targetPos);
    }
    String response = "Servo " + String(servoIndex) + " moving to " + String(targetPos) +
                      " at speed " + String(speed);
    Serial.println(response);
    servoServer.send(200, "text/plain", response);
  } else {
    servoServer.send(400, "text/plain", "Missing parameters. Use either command or servo, speed, and position.");
  }
}

/// Updates servo positions for normal servos and monitors the continuous servo.
void updateServos() {
  unsigned long currentMillis = millis();
  
  // For the continuous servo (servo 4), check if its MOVE duration has expired.
  if (servos[4].attached() && servo4StopTime != 0 && currentMillis > servo4StopTime) {
    servos[4].detach();
    servo4StopTime = 0;
    Serial.println("Continuous servo (servo 4) detached after MOVE duration expired.");
  }
  
  // Update normal servos (skip servo 4, which is handled separately).
  for (int i = 0; i < NUM_SERVOS; i++) {
    if (i == 4) continue;
    if (servoStates[i].currentPos != servoStates[i].targetPos &&
        (currentMillis - servoStates[i].lastUpdate >= UPDATE_INTERVAL)) {
      int diff = servoStates[i].targetPos - servoStates[i].currentPos;
      int step = servoStates[i].speed;
      if (abs(diff) < step) {
        step = abs(diff);
      }
      if (diff > 0) {
        servoStates[i].currentPos += step;
      } else if (diff < 0) {
        servoStates[i].currentPos -= step;
      }
      servos[i].write(servoStates[i].currentPos);
      servoStates[i].lastUpdate = currentMillis;
    }
  }
}

//
// --- Public Methods ---
//

void ControlServos::begin() {
  // Initialize each servo’s state.
  for (int i = 0; i < NUM_SERVOS; i++) {
    if (i == 4) {
      // For the continuous servo, do not attach at startup.
      servoStates[i].currentPos = 0;
      servoStates[i].targetPos = 0;
      servoStates[i].speed = 0;
      servoStates[i].lastUpdate = millis();
    } else {
      servoStates[i].currentPos = 90;
      servoStates[i].targetPos = 90;
      servoStates[i].speed = 0;
      servoStates[i].lastUpdate = millis();
      servos[i].attach(servoPins[i]);
      servos[i].write(90);
    }
  }
  
  // Set up HTTP endpoints.
  servoServer.on("/status", HTTP_GET, handleStatus);
  servoServer.on("/controlServo", HTTP_GET, handleControlServo);
  
  servoServer.begin();
  Serial.println("ControlServos server started on port 81");
}

void ControlServos::handleClient() {
  servoServer.handleClient();
  updateServos();
}
