import serial
import tkinter as tk
import time
from tkinter import Canvas, Scale, HORIZONTAL, Label, Button
from math import cos, sin, radians

servo_values = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0}  # Added servo 5 and 6
# Setup the serial connection (this should be the same as your Arduino's port and baud rate)
arduino = serial.Serial('COM3', 9600, timeout=2)
time.sleep(2)

def read_from_port(ser):
    while True:
        if ser.inWaiting() > 0:
            data = ser.read(ser.inWaiting()).decode('utf-8')
            print(data)
        time.sleep(0.1)  # Small delay to prevent CPU overuse

def send_command(command):
    if arduino.is_open:
        arduino.write(command.encode())
        print(f"Sent command: {command}")  # Debugging print
    else:
        print("Serial port is not open")

def update_servo(servo, value):
    servo_values[servo] = value
    draw_servo_positions()

def send_all_servo_commands():
    for servo, value in servo_values.items():
        value_int = int(value)

        if servo == 2:  # Bicep extension
            mapped_value = int((value_int / 30.0) * 180)
        elif servo == 3:  # Wrist rotation
            mapped_value = int((value_int / 90.0) * 180)
        else:
            mapped_value = value_int

        send_command(f'{servo},{mapped_value};')
    print("All commands sent!")

def update_stepper(direction):
    send_command(direction)

def draw_servo_positions():
    canvas.delete("line")
    bicep_angle = 180 - servo1.get()
    extension = servo2.get()
    wrist_angle = bicep_angle - servo3.get()
    gripper_extension = servo4.get()

    bicep_length = 50 + extension

    x1, y1 = 200, 200
    x2 = x1 + bicep_length * cos(radians(bicep_angle))
    y2 = y1 - bicep_length * sin(radians(bicep_angle))
    canvas.create_line(x1, y1, x2, y2, fill="blue", width=7, tags="line")
    
    wrist_length = 30
    x3 = x2 + wrist_length * cos(radians(wrist_angle))
    y3 = y2 - wrist_length * sin(radians(wrist_angle))
    canvas.create_line(x2, y2, x3, y3, fill="green", width=5, tags="line")
    
    gripper_length = 15
    gripper_gap = 2 + 0.1 * gripper_extension

    offset_x = gripper_gap * sin(radians(wrist_angle))
    offset_y = gripper_gap * cos(radians(wrist_angle))
    
    x5a = x3 + offset_x
    y5a = y3 + offset_y
    x5b = x3 - offset_x
    y5b = y3 - offset_y
    
    x6a = x5a + gripper_length * cos(radians(wrist_angle))
    y6a = y5a - gripper_length * sin(radians(wrist_angle))
    x6b = x5b + gripper_length * cos(radians(wrist_angle))
    y6b = y5b - gripper_length * sin(radians(wrist_angle))
    
    canvas.create_line(x5a, y5a, x6a, y6a, fill="red", width=3, tags="line")
    canvas.create_line(x5b, y5b, x6b, y6b, fill="red", width=3, tags="line")

def increment_servo(servo, increment):
    new_value = servo.get() + increment
    servo.set(new_value)
    update_servo(servo.id, new_value)

def decrement_servo(servo, decrement):
    new_value = servo.get() - decrement
    servo.set(new_value)
    update_servo(servo.id, new_value)

def send_continuous_servo_command():
    direction = direction_var.get()
    duration = duration_entry.get()

    if not duration.isdigit():
        print("Please enter a valid duration in milliseconds")
        return

    command = f'{direction}{duration};'
    send_command(command)
    print(f"Sent command: {command}")

root = tk.Tk()
root.title("Robot Arm Control")

canvas_width, canvas_height = 400, 400
canvas = Canvas(root, width=canvas_width, height=canvas_height, bg='white')
canvas.pack()

send_button = Button(root, text="Send Commands", command=send_all_servo_commands)
send_button.pack()

# Servo 1 (Bicep Rotation)
servo1_frame = tk.Frame(root)
Button(servo1_frame, text="-10", command=lambda: decrement_servo(servo1, 10)).pack(side=tk.LEFT)
Button(servo1_frame, text="-1", command=lambda: decrement_servo(servo1, 1)).pack(side=tk.LEFT)
servo1 = Scale(servo1_frame, from_=0, to=180, orient=HORIZONTAL, label="Bicep Rotation", command=lambda value: update_servo(1, value))
servo1.id = 1
servo1.pack(side=tk.LEFT)
Button(servo1_frame, text="+1", command=lambda: increment_servo(servo1, 1)).pack(side=tk.LEFT)
Button(servo1_frame, text="+10", command=lambda: increment_servo(servo1, 10)).pack(side=tk.LEFT)
servo1_frame.pack()

# Servo 2 (Bicep Extension)
servo2_frame = tk.Frame(root)
Button(servo2_frame, text="-10", command=lambda: decrement_servo(servo2, 10)).pack(side=tk.LEFT)
Button(servo2_frame, text="-1", command=lambda: decrement_servo(servo2, 1)).pack(side=tk.LEFT)
servo2 = Scale(servo2_frame, from_=0, to=30, orient=tk.HORIZONTAL, label="Bicep Extension (mm)", command=lambda value: update_servo(2, value))
servo2.id = 2
servo2.pack(side=tk.LEFT)
Button(servo2_frame, text="+1", command=lambda: increment_servo(servo2, 1)).pack(side=tk.LEFT)
Button(servo2_frame, text="+10", command=lambda: increment_servo(servo2, 10)).pack(side=tk.LEFT)
servo2_frame.pack()

# Servo 3 (Wrist Rotation)
servo3_frame = tk.Frame(root)
Button(servo3_frame, text="-10", command=lambda: decrement_servo(servo3, 10)).pack(side=tk.LEFT)
Button(servo3_frame, text="-1", command=lambda: decrement_servo(servo3, 1)).pack(side=tk.LEFT)
servo3 = Scale(servo3_frame, from_=0, to=90, orient=tk.HORIZONTAL, label="Wrist Rotation", command=lambda value: update_servo(3, value))
servo3.id = 3
servo3.pack(side=tk.LEFT)
Button(servo3_frame, text="+1", command=lambda: increment_servo(servo3, 1)).pack(side=tk.LEFT)
Button(servo3_frame, text="+10", command=lambda: increment_servo(servo3, 10)).pack(side=tk.LEFT)
servo3_frame.pack()

# Servo 4 (Gripper Rotation)
servo4_frame = tk.Frame(root)
Button(servo4_frame, text="-10", command=lambda: decrement_servo(servo4, 10)).pack(side=tk.LEFT)
Button(servo4_frame, text="-1", command=lambda: decrement_servo(servo4, 1)).pack(side=tk.LEFT)
servo4 = Scale(servo4_frame, from_=0, to=180, orient=tk.HORIZONTAL, label="Gripper Rotation", command=lambda value: update_servo(4, value))
servo4.id = 4
servo4.pack(side=tk.LEFT)
Button(servo4_frame, text="+1", command=lambda: increment_servo(servo4, 1)).pack(side=tk.LEFT)
Button(servo4_frame, text="+10", command=lambda: increment_servo(servo4, 10)).pack(side=tk.LEFT)
servo4_frame.pack()

# Servo 6 (Gripper Turn)
servo6_frame = tk.Frame(root)
Button(servo6_frame, text="-10", command=lambda: decrement_servo(servo6, 10)).pack(side=tk.LEFT)
Button(servo6_frame, text="-1", command=lambda: decrement_servo(servo6, 1)).pack(side=tk.LEFT)
servo6 = Scale(servo6_frame, from_=0, to=180, orient=tk.HORIZONTAL, label="Gripper Turn", command=lambda value: update_servo(6, value))
servo6.id = 6
servo6.pack(side=tk.LEFT)
Button(servo6_frame, text="+1", command=lambda: increment_servo(servo6, 1)).pack(side=tk.LEFT)
Button(servo6_frame, text="+10", command=lambda: increment_servo(servo6, 10)).pack(side=tk.LEFT)
servo6_frame.pack()

# Continuous rotation servo (servo 5)
continuous_servo_frame = tk.Frame(root)
continuous_servo_frame.pack(pady=10)

# Direction control (left or right)
direction_var = tk.StringVar(value="R")
Label(continuous_servo_frame, text="Direction:").pack(side=tk.LEFT)
Button(continuous_servo_frame, text="Left", command=lambda: direction_var.set("L")).pack(side=tk.LEFT)
Button(continuous_servo_frame, text="Right", command=lambda: direction_var.set("R")).pack(side=tk.LEFT)

# Speed control (slow or fast)
speed_var = tk.StringVar(value="S")
Label(continuous_servo_frame, text="Speed:").pack(side=tk.LEFT)
Button(continuous_servo_frame, text="Slow", command=lambda: speed_var.set("S")).pack(side=tk.LEFT)
Button(continuous_servo_frame, text="Fast", command=lambda: speed_var.set("F")).pack(side=tk.LEFT)

# Duration input (in milliseconds)
Label(continuous_servo_frame, text="Duration (ms):").pack(side=tk.LEFT)
duration_entry = tk.Entry(continuous_servo_frame, width=10)
duration_entry.pack(side=tk.LEFT)

# Send command button
send_continuous_servo_button = Button(continuous_servo_frame, text="Send Continuous Servo Command", command=send_continuous_servo_command)
send_continuous_servo_button.pack(side=tk.LEFT)

# Run the GUI loop and draw initial servo positions
draw_servo_positions()
root.mainloop()
