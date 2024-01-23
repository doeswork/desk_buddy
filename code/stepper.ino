#include <Servo.h>

#define IN1 8
#define IN2 9
#define IN3 10
#define IN4 11
int Steps = 0;
boolean Direction = true; // True: Clockwise, False: Counter-Clockwise
int stepsPerCommand = 512; // Number of steps to take per command 4096 one turn

Servo myservo1;  // Create first servo object
Servo myservo2;  // Create second servo object
Servo myservo3;  // Create third servo object
int pos1 = 0;    // Variable to store the servo1 position
int pos2 = 0;    // Variable to store the servo2 position
int pos3 = 0;    // Variable to store the servo3 position

void setup() {
  Serial.begin(9600);
  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);
  pinMode(IN3, OUTPUT);
  pinMode(IN4, OUTPUT);
  myservo1.attach(6); // Attaches the first servo on pin 6
  myservo2.attach(7); // Attaches the second servo on pin 7
  myservo3.attach(12); // Attaches the third servo on pin 12
}

void loop() {
  if (Serial.available() > 0) {
    char command = Serial.read();
    if (command == 'R') {
      Direction = true;  // Set direction to clockwise
      for(int i=0; i<stepsPerCommand; i++){
        stepper(1);
        delayMicroseconds(800);
      }
    } else if (command == 'L') {
      Direction = false; // Set direction to counter-clockwise
      for(int i=0; i<stepsPerCommand; i++){
        stepper(1);
        delayMicroseconds(800);
      }
    } else if (command == '7') {
      pos1 = max(0, pos1 - 5); // Decrease servo position
      myservo1.write(pos1);
    } else if (command == '8') {
      pos1 = min(180, pos1 + 5); // Increase servo position
      myservo1.write(pos1);
    }
    else if (command == '4') {
      pos2 = max(0, pos2 - 5); // Decrease second servo position
      myservo2.write(pos2);
    } else if (command == '5') {
      pos2 = min(180, pos2 + 5); // Increase second servo position
      myservo2.write(pos2);
    } else if (command == '6') {
      pos3 = max(0, pos3 - 5); // Decrease third servo position
      myservo3.write(pos3);
    } else if (command == '9') {
      pos3 = min(180, pos3 + 5); // Increase third servo position
      myservo3.write(pos3);
    }
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
