#include <SPI.h>
#include <nRF24L01.h>
#include <RF24.h>
#include <Servo.h>

RF24 radio(9, 10); // CE, CSN
Servo servo1;
Servo servo2;
Servo servo3;
Servo servo4;

int joystick[4]; // to store X and Y values for two joysticks
float servoPos[4] = {90, 90, 90, 90}; // Start at the center for all four servos

void setup() {
  servo1.attach(3);
  servo2.attach(5);
  servo3.attach(6);
  servo4.attach(7);
  radio.begin();
  radio.openReadingPipe(1, 0xF0F0F0F0E1LL); // same unique identifier as before
  radio.setPALevel(RF24_PA_HIGH);
  radio.startListening(); // start listening for data
}

void loop() {
  if (radio.available()) { // if data is available to read
    radio.read(&joystick, sizeof(joystick)); // read the incoming data

    // Adjust servo position based on joystick movement
    for(int i = 0; i < 4; i++) {
      if(joystick[i] > 542) { // 30 units dead zone
        servoPos[i] += (joystick[i] - 542) / 10000.0; // Change the divisor to adjust sensitivity
      } else if(joystick[i] < 482) { // 30 units dead zone
        servoPos[i] -= (482 - joystick[i]) / 10000.0; // Change the divisor to adjust sensitivity
      }

      // Constrain the servo position to 0 - 180 range
      servoPos[i] = constrain(servoPos[i], 0, 180);
    }

    // Write the new position to the servos
    servo1.write((int)servoPos[0]); // Cast back to int for the servo function
    servo2.write((int)servoPos[1]); // Cast back to int for the servo function
    servo3.write((int)servoPos[2]); // Cast back to int for the servo function
    servo4.write((int)servoPos[3]); // Cast back to int for the servo function
  }
}