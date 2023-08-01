#include <SPI.h>
#include <nRF24L01.h>
#include <RF24.h>
#include <Servo.h>

RF24 radio(9, 10); // CE, CSN
Servo servo1;
Servo servo2;

int joystick[2]; // to store X and Y values
float servoPos[2] = {90, 90}; // Start at the center, now as a float

void setup() {
  servo1.attach(3);
  servo2.attach(5);
  radio.begin();
  radio.openReadingPipe(1, 0xF0F0F0F0E1LL); // same unique identifier as before
  radio.setPALevel(RF24_PA_HIGH);
  radio.startListening(); // start listening for data
}

void loop() {
  if (radio.available()) { // if data is available to read
    radio.read(&joystick, sizeof(joystick)); // read the incoming data

    // Adjust servo position based on joystick movement
    if(joystick[0] > 542) { // 30 units dead zone
      servoPos[0] += (joystick[0] - 542) / 1000.0; // Change the divisor to adjust sensitivity
    } else if(joystick[0] < 482) { // 30 units dead zone
      servoPos[0] -= (482 - joystick[0]) / 1000.0; // Change the divisor to adjust sensitivity
    }
    if(joystick[1] > 542) { // 30 units dead zone
      servoPos[1] += (joystick[1] - 542) / 1000.0; // Change the divisor to adjust sensitivity
    } else if(joystick[1] < 482) { // 30 units dead zone
      servoPos[1] -= (482 - joystick[1]) / 1000.0; // Change the divisor to adjust sensitivity
    }

    // Constrain the servo position to 0 - 180 range
    servoPos[0] = constrain(servoPos[0], 0, 180);
    servoPos[1] = constrain(servoPos[1], 0, 180);

    // Write the new position to the servos
    servo1.write((int)servoPos[0]); // Cast back to int for the servo function
    servo2.write((int)servoPos[1]); // Cast back to int for the servo function
  }
}
