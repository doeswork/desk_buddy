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
Servo myservo1, myservo2, myservo3, myservo4;
int pos1 = 0, pos2 = 0, pos3 = 0, pos4 = 0;

void setup() {
  Serial.begin(9600); // Start the serial communication
  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);
  pinMode(IN3, OUTPUT);
  pinMode(IN4, OUTPUT);
  
  myservo1.attach(6);
  myservo2.attach(7);
  myservo3.attach(12);
  myservo4.attach(13);
}

void loop() {
  if (Serial.available() > 0) {
    char command = Serial.read();

    switch (command) {
      // Servo 1 commands
      case 'a': // Increase position
        pos1 = min(180, pos1 + 5);
        myservo1.write(pos1);
        break;
      case 'b': // Decrease position
        pos1 = max(0, pos1 - 5);
        myservo1.write(pos1);
        break;

      // Servo 2 commands
      case 'c': // Increase position
        pos2 = min(180, pos2 + 10);
        myservo2.write(pos2);
        break;
      case 'd': // Decrease position
        pos2 = max(0, pos2 - 10);
        myservo2.write(pos2);
        break;

      // Servo 3 commands
      case 'e': // Increase position
        pos3 = min(180, pos3 + 10);
        myservo3.write(pos3);
        break;
      case 'f': // Decrease position
        pos3 = max(0, pos3 - 10);
        myservo3.write(pos3);
        break;

      // Servo 4 commands
      case 'g': // Increase position
        pos4 = min(180, pos4 + 10);
        myservo4.write(pos4);
        break;
      case 'h': // Decrease position
        pos4 = max(0, pos4 - 10);
        myservo4.write(pos4);
        break;

      // Stepper motor commands
      case 'l': // Turn left
        Direction = false;
        stepper(stepsPerCommand);
        break;
      case 'r': // Turn right
        Direction = true;
        stepper(stepsPerCommand);
        break;
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
