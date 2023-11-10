import tkinter as tk
from tkinter import Canvas, Button
import serial
from math import cos, sin, radians
from time import sleep

# Setup the serial connection (this should be the same as your Arduino's port and baud rate)
DEVICE = '/dev/rfcomm0'
BAUD_RATE = 9600
# 98:DA:60:07:D9:C7
# sudo rfcomm bind 0 98:DA:60:07:D9:C7

try:
    arduino = serial.Serial(DEVICE, BAUD_RATE)
    print(f"Connected to {DEVICE}")
    sleep(7)

except Exception as e:
    print(f"Failed to connect: {e}")
    exit(1)
    
def send_command(command):
    # Uncomment below line when you have the serial connection ready
    arduino.write(command.encode())
    print(command)

def update_servo(servo, increment):
    global servo1, servo2, servo3, servo4

    # Adjust increments for each servo
    if servo == 1:
        increment = 5 if increment > 0 else -5
        servo1 = max(0, min(180, servo1 + increment))
        send_command('a' if increment > 0 else 'b')
    else:
        increment = 10 if increment > 0 else -10
        if servo == 2:
            servo2 = max(0, min(180, servo2 + increment))
            send_command('c' if increment > 0 else 'd')
        elif servo == 3:
            servo3 = max(0, min(180, servo3 + increment))
            send_command('e' if increment > 0 else 'f')
        elif servo == 4:
            servo4 = max(0, min(180, servo4 + increment))
            send_command('g' if increment > 0 else 'h')

    draw_servo_positions()


def update_stepper(direction):
    # Send single character command to Arduino, e.g., 'L' or 'R'
    send_command(direction)

def create_servo_control_buttons(servo_num, label):
    frame = tk.Frame(root)
    frame.pack()

    Button(frame, text=f"{label} +", command=lambda: update_servo(servo_num, 2)).pack(side=tk.LEFT)
    Button(frame, text=f"{label} -", command=lambda: update_servo(servo_num, -2)).pack(side=tk.RIGHT)

def draw_servo_positions():
    canvas.delete("line")  # Clear the previous lines
    # Use global servo values for angles
    bicep_angle = 180 - servo1  # Flip the bicep angle to match the visual perspective
    extension = servo2  # Get the extension value
    wrist_angle = bicep_angle - servo3  # Subtract wrist value to follow the bicep
    gripper_extension = servo4  # Get the gripper extension value
    # Calculate new bicep line length based on extension
    bicep_length = 50 + extension  # Adjust the base length as necessary for the bicep extension

    # Bicep servo representation (flipped horizontally)
    x1, y1 = 200, 200  # Start from a more centered position
    x2 = x1 + bicep_length * cos(radians(bicep_angle))
    y2 = y1 - bicep_length * sin(radians(bicep_angle))
    canvas.create_line(x1, y1, x2, y2, fill="blue", width=7, tags="line")
    
    # Wrist servo representation (positioning relative to the bicep)
    wrist_length = 30
    x3 = x2 + wrist_length * cos(radians(wrist_angle))
    y3 = y2 - wrist_length * sin(radians(wrist_angle))
    canvas.create_line(x2, y2, x3, y3, fill="green", width=5, tags="line")
    
    # Gripper servo representation (fixed direction relative to the wrist)
    gripper_length = 15  # Length of each gripper line
    # Gap between the two lines changes with servo4 value, starting closer
    gripper_gap = 2 + 0.1 * gripper_extension  # The gap between the two lines, changes with servo4 value

    # Calculate the positions of the two gripper lines so they are always parallel and at the wrist angle
    offset_x = gripper_gap * sin(radians(wrist_angle))
    offset_y = gripper_gap * cos(radians(wrist_angle))
    
    x5a = x3 + offset_x
    y5a = y3 + offset_y
    x5b = x3 - offset_x
    y5b = y3 - offset_y
    
    # Both gripper lines have the same angle as the green line
    x6a = x5a + gripper_length * cos(radians(wrist_angle))
    y6a = y5a - gripper_length * sin(radians(wrist_angle))
    x6b = x5b + gripper_length * cos(radians(wrist_angle))
    y6b = y5b - gripper_length * sin(radians(wrist_angle))
    
    # Draw the gripper lines
    canvas.create_line(x5a, y5a, x6a, y6a, fill="red", width=3, tags="line")
    canvas.create_line(x5b, y5b, x6b, y6b, fill="red", width=3, tags="line")

# Initial servo positions
servo1 = 0  # Bicep Rotation
servo2 = 0  # Bicep Extension
servo3 = 0  # Wrist Rotation
servo4 = 0  # Gripper Rotation

root = tk.Tk()
root.title("Robot Arm Control")

canvas_width, canvas_height = 400, 400
canvas = Canvas(root, width=canvas_width, height=canvas_height, bg='white')
canvas.pack()

# Create control buttons for each servo
create_servo_control_buttons(1, "Bicep Rotation")
create_servo_control_buttons(2, "Bicep Extension")
create_servo_control_buttons(3, "Wrist Rotation")
create_servo_control_buttons(4, "Gripper Rotation")

# Stepper motor control buttons
stepper_control_frame = tk.Frame(root)
stepper_control_frame.pack(side=tk.BOTTOM, pady=10)
Button(stepper_control_frame, text="Left", command=lambda: update_stepper('l')).pack(side=tk.LEFT, padx=5)
Button(stepper_control_frame, text="Right", command=lambda: update_stepper('r')).pack(side=tk.RIGHT, padx=5)

draw_servo_positions()
root.mainloop()
