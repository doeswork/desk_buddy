#include <Servo.h>

Servo servo1;
Servo servo2;
Servo servo3;
Servo servo4;

// Current servo positions
int currentPos1 = 0;
int currentPos2 = 0;
int currentPos3 = 0;
int currentPos4 = 0;

// Stepper Motor Pins
#define IN1 8
#define IN2 9
#define IN3 10
#define IN4 11
int Steps = 0;
boolean Direction = true;
int stepsPerCommand = 256;

void setup() {
  Serial.begin(9600);
  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);
  pinMode(IN3, OUTPUT);
  pinMode(IN4, OUTPUT);

  servo1.attach(2); // Replace with your actual servo pin
  servo2.attach(3); // Replace with your actual servo pin
  servo3.attach(4); // Replace with your actual servo pin
  servo4.attach(5); // Replace with your actual servo pin
}

void loop() {
  if (Serial.available() > 0) {
    String data = Serial.readStringUntil(';');
    char commandType = data.charAt(0);  // Get the first character of the command

    if (commandType == 'L' || commandType == 'R') {

    if (commandType == 'R') {
      Direction = true;  // Set direction to clockwise
      for(int i=0; i<stepsPerCommand; i++){
        stepper(1);
        delayMicroseconds(800);
      }
    } else if (commandType == 'L') {
      Direction = false; // Set direction to counter-clockwise
      for(int i=0; i<stepsPerCommand; i++){
        stepper(1);
        delayMicroseconds(800);
      }
    }
    } else {
      // Handle servo commands
      int commaIndex = data.indexOf(',');
      int servoID = data.substring(0, commaIndex).toInt();
      int position = data.substring(commaIndex + 1).toInt();

      switch (servoID) {
        case 1:
          smoothMove(servo1, &currentPos1, position);
          break;
        case 2:
          smoothMove(servo2, &currentPos2, position);
          break;
        case 3:
          smoothMove(servo3, &currentPos3, position);
          break;
        case 4:
          smoothMove(servo4, &currentPos4, position);
          break;
      }
    }
  }
}


void smoothMove(Servo myservo, int* currentPosition, int targetPosition) {
  while (*currentPosition != targetPosition) {
    // Move in steps of 1 degree
    if (*currentPosition < targetPosition) {
      *currentPosition += 1;
    } else {
      *currentPosition -= 1;
    }
    myservo.write(*currentPosition);
    delay(15); // Adjust the delay to control the speed of the servo
  }
}

void stepper(int xw) {
  for (int x = 0; x < xw; x++) {
    switch (Steps) {
      case 0:
        digitalWrite(IN1, LOW);
        digitalWrite(IN2, LOW);
        digitalWrite(IN3, LOW);
        digitalWrite(IN4, HIGH);
        break;
      case 1:
        digitalWrite(IN1, LOW);
        digitalWrite(IN2, LOW);
        digitalWrite(IN3, HIGH);
        digitalWrite(IN4, HIGH);
        break;
      case 2:
        digitalWrite(IN1, LOW);
        digitalWrite(IN2, LOW);
        digitalWrite(IN3, HIGH);
        digitalWrite(IN4, LOW);
        break;
      case 3:
        digitalWrite(IN1, LOW);
        digitalWrite(IN2, HIGH);
        digitalWrite(IN3, HIGH);
        digitalWrite(IN4, LOW);
        break;
      case 4:
        digitalWrite(IN1, LOW);
        digitalWrite(IN2, HIGH);
        digitalWrite(IN3, LOW);
        digitalWrite(IN4, LOW);
        break;
      case 5:
        digitalWrite(IN1, HIGH);
        digitalWrite(IN2, HIGH);
        digitalWrite(IN3, LOW);
        digitalWrite(IN4, LOW);
        break;
      case 6:
        digitalWrite(IN1, HIGH);
        digitalWrite(IN2, LOW);
        digitalWrite(IN3, LOW);
        digitalWrite(IN4, LOW);
        break;
      case 7:
        digitalWrite(IN1, HIGH);
        digitalWrite(IN2, LOW);
        digitalWrite(IN3, LOW);
        digitalWrite(IN4, HIGH);
        break;
      default:
        digitalWrite(IN1, LOW);
        digitalWrite(IN2, LOW);
        digitalWrite(IN3, LOW);
        digitalWrite(IN4, LOW);
        break;
    }
    SetDirection();
  }
}

void SetDirection() {
  if (Direction) { // if direction is true (clockwise)
    Steps++;
  } else { // if direction is false (counter-clockwise)
    Steps--;
  }
  if (Steps > 7) {
    Steps = 0;
  }
  if (Steps < 0) {
    Steps = 7;
  }
}
