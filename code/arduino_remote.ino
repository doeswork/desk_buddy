#include <SPI.h>
#include <nRF24L01.h>
#include <RF24.h>

RF24 radio(9, 10); // CE, CSN
int joystick[2]; // to store X and Y values

void setup() {
  radio.begin();
  radio.openWritingPipe(0xF0F0F0F0E1LL); // unique identifier for the pipe
  radio.setPALevel(RF24_PA_HIGH);
}

void loop() {
  joystick[0] = analogRead(A0); // reads the value of the X-axis
  joystick[1] = analogRead(A1); // reads the value of the Y-axis
  radio.write(&joystick, sizeof(joystick)); // send the joystick movement
}
