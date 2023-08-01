#include <SPI.h>
#include <nRF24L01.h>
#include <RF24.h>
#include <Servo.h>

RF24 radio(9, 10); // CE, CSN
Servo servo1;
Servo servo2;
int joystick[2]; // to store X and Y values

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
    int servoValX = map(joystick[0], 0, 1023, 0, 180); // map the incoming value
    int servoValY = map(joystick[1], 0, 1023, 0, 180); // map the incoming value
    servo1.write(servoValX); // move the servos
    servo2.write(servoValY);
  }
}
