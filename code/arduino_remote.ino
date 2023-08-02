#include <SPI.h>
#include <nRF24L01.h>
#include <RF24.h>

RF24 radio(9, 10); // CE, CSN

int joystick1_X, joystick1_Y, joystick2_X, joystick2_Y;
int data[4]; // To store X and Y values for two joysticks

void setup() {
  radio.begin();
  radio.openWritingPipe(0xF0F0F0F0E1LL); // same unique identifier as before
  radio.setPALevel(RF24_PA_HIGH);
}

void loop() {
  joystick1_X = analogRead(A0); // read joystick1 X-axis
  joystick1_Y = analogRead(A1); // read joystick1 Y-axis
  joystick2_X = analogRead(A2); // read joystick2 X-axis
  joystick2_Y = analogRead(A3); // read joystick2 Y-axis

  data[0] = joystick1_X; // save X value of joystick1
  data[1] = joystick1_Y; // save Y value of joystick1
  data[2] = joystick2_X; // save X value of joystick2
  data[3] = joystick2_Y; // save Y value of joystick2

  radio.write(&data, sizeof(data)); // send the data
}
