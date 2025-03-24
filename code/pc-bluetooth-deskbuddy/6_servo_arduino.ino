#include <Servo.h>

Servo servo1;
Servo servo2;
Servo servo3;
Servo servo4;
Servo servo5;
Servo servo6; // Add the 6th servo for the Big Servo

// Current servo positions
int currentPos1 = 0;
int currentPos2 = 0;
int currentPos3 = 0;
int currentPos4 = 0;
int currentPos6 = 0; // Add a position tracker for the Big Servo

void setup() {
  Serial.begin(9600);

  servo1.attach(6); // Attach servo1 to pin 6
  servo2.attach(7); // Attach servo2 to pin 7
  servo3.attach(12); // Attach servo3 to pin 12
  servo4.attach(13); // Attach servo4 to pin 13
  servo5.attach(10); // Attach Continuous servo5 to pin 10
  servo6.attach(11); // Attach the Big Servo to pin 11

  delay(5000); // Wait for 5 seconds after power-up

  // Wake-up sequence
  wakeUpSequence();
}

void loop() {
  if (Serial.available() > 0) {
    String data = Serial.readStringUntil(';');
    Serial.print("Received command: "); // Debugging print
    Serial.println(data); // Debugging print

    if (data.charAt(0) == 'L' || data.charAt(0) == 'R') {
      // Handle the continuous rotation servo command
      char direction = data.charAt(0);
      int duration = data.substring(1).toInt(); // Adjusted to directly read duration

      handleContinuousServo(servo5, direction, duration);
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
          case 6:
            smoothMove(servo6, &currentPos6, position); // Add the case for the Big Servo
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

void handleContinuousServo(Servo myservo, char direction, int duration) {
  // Set direction and speed to full power
  if (direction == 'R') {
    myservo.write(110); // Clockwise at full speed
  } else if (direction == 'L') {
    myservo.write(70); // Counter-clockwise at full speed
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
    delay(20); // Adjusted delay for smoother and slower movement for the Big Servo
  }
}

void wakeUpSequence() {
  servo1.write(90);
  delay(1500); // 1.5 second delay for the first servo to reach position

  servo2.write(90);
  delay(500);

  servo3.write(90);
  delay(500);

  servo4.write(90);
  delay(500);

  servo5.write(90);
  delay(500);

  servo6.write(90);
  delay(500);
}
