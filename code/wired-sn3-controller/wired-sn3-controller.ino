#include <Servo.h>

// Stepper Motor Pins
#define IN1 8
#define IN2 9
#define IN3 10
#define IN4 11
int Steps = 0;
boolean Direction = true;
int stepsPerCommand = 32;

// Servo objects
Servo myservo1;
Servo myservo2;
Servo myservo3;
Servo myservo4;
int pos1 = 0;
int pos2 = 0;
int pos3 = 0;
int pos4 = 0;

// Joystick Pins
const int joyX1 = A0;
const int joyY1 = A1;
const int joyX2 = A2;
const int joyY2 = A3;
const int button1 = 2;
const int button2 = 3;

void setup() {
  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);
  pinMode(IN3, OUTPUT);
  pinMode(IN4, OUTPUT);
  
  myservo1.attach(6);
  myservo2.attach(7);
  myservo3.attach(12);
  myservo4.attach(13);

  pinMode(joyX1, INPUT);
  pinMode(joyY1, INPUT);
  pinMode(joyX2, INPUT);
  pinMode(joyY2, INPUT);
  pinMode(button1, INPUT_PULLUP);
  pinMode(button2, INPUT_PULLUP);
}

void loop() {
  int x1 = analogRead(joyX1);
  int y1 = analogRead(joyY1);
  int x2 = analogRead(joyX2);
  int y2 = analogRead(joyY2);
  int b1 = digitalRead(button1);
  int b2 = digitalRead(button2);

  // Stepper motor control
  if (x1 > 600) {
    Direction = true;
    for(int i=0; i<stepsPerCommand; i++){
      stepper(1);
      delayMicroseconds(800);
    }
  } else if (x1 < 400) {
    Direction = false;
    for(int i=0; i<stepsPerCommand; i++){
      stepper(1);
      delayMicroseconds(800);
    }
  }

  // Servo1 control
  if (y1 > 600) {
    pos1 = min(180, pos1 + 2);
    myservo1.write(pos1);
    delay(40);
  } else if (y1 < 400) {
    pos1 = max(0, pos1 - 2);
    myservo1.write(pos1);
    delay(40);
  }

  // Servo2 control
  if (x2 > 600) {
    pos2 = min(180, pos2 + 2);
    myservo2.write(pos2);
    delay(10);
  } else if (x2 < 400) {
    pos2 = max(0, pos2 - 2);
    myservo2.write(pos2);
    delay(10);
  }

  // Servo3 control
  if (y2 > 600) {
    pos3 = min(180, pos3 + 2);
    myservo3.write(pos3);
    delay(10);
  } else if (y2 < 400) {
    pos3 = max(0, pos3 - 2);
    myservo3.write(pos3);
    delay(10);
  }

  // Servo4 control
  if (b1 == LOW) {
    pos4 = max(0, pos4 - 2);
    myservo4.write(pos4);
    delay(10);
  } else if (b2 == LOW) {
    pos4 = min(180, pos4 + 2);
    myservo4.write(pos4);
    delay(10);
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
