import serial

# Replace 'COM_PORT' with the COM port the HC-06 is connected to
ser = serial.Serial('COM3', 9600) 

while True:
    data = ser.readline()
    print(data.decode('utf-8'))
