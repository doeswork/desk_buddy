#include <Servo.h>

Servo servo1;
Servo servo2;
Servo servo3;
Servo servo4;
Servo servo5; // Add the 5th servo

// Current servo positions
int currentPos1 = 0;
int currentPos2 = 0;
int currentPos3 = 0;
int currentPos4 = 0;

void setup() {
  Serial.begin(9600);

  servo1.attach(6); // Replace with your actual servo pin
  servo2.attach(7); // Replace with your actual servo pin
  servo3.attach(12); // Replace with your actual servo pin
  servo4.attach(13); // Replace with your actual servo pin
  servo5.attach(8); // Attach the 5th servo to pin 8 (replace with your actual pin)
}

void loop() {
  if (Serial.available() > 0) {
    String data = Serial.readStringUntil(';');
    Serial.print("Received command: "); // Debugging print
    Serial.println(data); // Debugging print

    if (data.charAt(0) == 'L' || data.charAt(0) == 'R') {
      // Handle the continuous rotation servo command
      char direction = data.charAt(0);
      char speed = data.charAt(1);
      int duration = data.substring(2).toInt();

      handleContinuousServo(servo5, direction, speed, duration);
    } else {
      // Handle standard servo commands
      int commaIndex = data.indexOf(',');
      if (commaIndex != -1) {
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
          default:
            Serial.println("Invalid servo ID"); // Debugging print
        }
      } else {
        Serial.println("Invalid command format"); // Debugging print
      }
    }
  }
}

void handleContinuousServo(Servo myservo, char direction, char speed, int duration) {
  int speedValue;

  // Determine speed value
  if (speed == 'S') {
    speedValue = 70;  // Example value for slow speed (adjust as needed)
  } else if (speed == 'F') {
    speedValue = 110; // Example value for fast speed (adjust as needed)
  }

  // Set direction and speed
  if (direction == 'R') {
    myservo.write(speedValue); // Clockwise
  } else if (direction == 'L') {
    myservo.write(180 - speedValue); // Counter-clockwise (180 - speedValue to reverse direction)
  }

  // Run the servo for the specified duration
  delay(duration);
  myservo.write(90); // Stop the servo (90 is neutral position for most continuous rotation servos)
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