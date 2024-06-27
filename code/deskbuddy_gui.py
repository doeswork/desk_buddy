import tkinter as tk
from tkinter import Canvas, Scale, HORIZONTAL, Label, Button
# import serial
from math import cos, sin, radians

# Setup the serial connection (this should be the same as your Arduino's port and baud rate)
# arduino = serial.Serial('COM3', 9600, timeout=1)

def send_command(command):
    # arduino.write(command.encode())
    print(command)

def update_servo(servo, value):
    # Send data to Arduino, e.g., '1,90;'
    send_command(f'{servo},{value};')
    draw_servo_positions()

def update_stepper(direction):
    # Send single character command to Arduino, e.g., 'L' or 'R'
    send_command(direction)

def draw_servo_positions():
    canvas.delete("line")  # Clear the previous lines
    bicep_angle = 180 - servo1.get()  # Flip the bicep angle to match the visual perspective
    extension = servo2.get()  # Get the extension value
    wrist_angle = bicep_angle - servo3.get()  # Subtract wrist value to follow the bicep
    gripper_extension = servo4.get()  # Get the gripper extension value

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

def increment_servo(servo, increment):
    new_value = servo.get() + increment
    servo.set(new_value)
    update_servo(servo.id, new_value)

def decrement_servo(servo, decrement):
    new_value = servo.get() - decrement
    servo.set(new_value)
    update_servo(servo.id, new_value)

root = tk.Tk()
root.title("Robot Arm Control")

# Create a larger canvas for drawing servo positions with a white background
canvas_width, canvas_height = 400, 400  # Define a larger size for the canvas
canvas = Canvas(root, width=canvas_width, height=canvas_height, bg='white')
canvas.pack()

# Sliders for the servos
servo1 = Scale(root, from_=0, to=180, orient=HORIZONTAL, label="Bicep Rotation", command=lambda value: update_servo(1, value))
servo1.id = 1
servo1.pack()
servo1_frame = tk.Frame(root)
Button(servo1_frame, text="-10", command=lambda: decrement_servo(servo1, 10)).pack(side=tk.LEFT)
Button(servo1_frame, text="-1", command=lambda: decrement_servo(servo1, 1)).pack(side=tk.LEFT)
servo1.pack(side=tk.LEFT)
Button(servo1_frame, text="+1", command=lambda: increment_servo(servo1, 1)).pack(side=tk.LEFT)
Button(servo1_frame, text="+10", command=lambda: increment_servo(servo1, 10)).pack(side=tk.LEFT)
servo1_frame.pack()

servo2 = Scale(root, from_=0, to=30, orient=HORIZONTAL, label="Bicep Extension (mm)", command=lambda value, s=2: update_servo(s, value))
servo2.pack()

servo3 = Scale(root, from_=0, to=80, orient=HORIZONTAL, label="Wrist Rotation", command=lambda value, s=3: update_servo(s, value))
servo3.pack()

servo4 = Scale(root, from_=0, to=180, orient=HORIZONTAL, label="Gripper Rotation", command=lambda value, s=4: update_servo(s, value))
servo4.pack()

# Create a frame for the stepper control buttons
stepper_control_frame = tk.Frame(root)
stepper_control_frame.pack(side=tk.BOTTOM, pady=10)  # Add some padding for visual appeal

# Place the buttons inside the frame, which will be at the bottom of the root window
Button(stepper_control_frame, text="Left", command=lambda: update_stepper('L')).pack(side=tk.LEFT, padx=5)
Button(stepper_control_frame, text="Right", command=lambda: update_stepper('R')).pack(side=tk.RIGHT, padx=5)

# Run the GUI loop and draw initial servo positions
draw_servo_positions()
root.mainloop()
