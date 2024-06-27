char dataIn;
bool ledOn = false;

void setup() {
  Serial.begin(9600);

  Serial.println("-----");
  Serial.println("Start");
  Serial.println("-----");
  pinMode(8, OUTPUT);
  // put your setup code here, to run once:

}

void loop() {
  while (Serial.available() > 0 ) {
    dataIn = Serial.read();
    if (dataIn == 'y') {

      if (ledOn == true) {
        digitalWrite(8,LOW);
        ledOn = false;
        Serial.println("Turned off");
      } else
      {
        digitalWrite(8,HIGH);
        ledOn = true;
        Serial.println("Turned on");
      }
    }
  }
}
