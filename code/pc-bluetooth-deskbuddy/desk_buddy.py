import serial
import tkinter as tk
import time
from tkinter import Canvas, Scale, HORIZONTAL, Label, Button
from math import cos, sin, radians

servo_values = {1: 0, 2: 0, 3: 0, 4: 0}

def read_from_port(ser):
    while True:
        if ser.inWaiting() > 0:
            data = ser.read(ser.inWaiting()).decode('utf-8')
            print(data)
        time.sleep(0.1)  # Small delay to prevent CPU overuse

def send_A():
    if ser.is_open:
        ser.write(b"A")
    else:
        print("Serial port is not open")

# Set up the serial connection (Replace 'COM_PORT' with your actual COM port)
# ser = serial.Serial('COM3', 9600, timeout=1)

# # Start a thread for reading from the serial port
# thread = Thread(target=read_from_port, args=(ser,))
# thread.daemon = True
# thread.start()

# Setup the serial connection (this should be the same as your Arduino's port and baud rate)
arduino = serial.Serial('COM10', 9600, timeout=1)
time.sleep(5)

def send_command(command):
    if arduino.is_open:
        arduino.write(command.encode())
        print(f"Sent command: {command}")  # Debugging print
    else:
        print("Serial port is not open")

def update_servo(servo, value):
    # Store the value instead of sending it immediately
    servo_values[servo] = value
    draw_servo_positions()

def send_all_servo_commands():
    for servo, value in servo_values.items():
        # Ensure the value is treated as an integer
        value_int = int(value)

        if servo == 2:  # Bicep extension
            # Map from 0-30 mm to 0-180 degrees
            mapped_value = int((value_int / 30.0) * 180)
        elif servo == 3:  # Wrist rotation
            # Map from 0-90 degrees to 0-180 degrees
            mapped_value = int((value_int / 90.0) * 180)
        else:
            mapped_value = value_int  # No mapping needed

        send_command(f'{servo},{mapped_value};')
    print("All commands sent!")

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

def send_continuous_servo_command():
    direction = direction_var.get()
    speed = speed_var.get()
    duration = duration_entry.get()

    if not duration.isdigit():
        print("Please enter a valid duration in milliseconds")
        return

    command = f'{direction}{speed}{duration};'
    send_command(command)
    print(f"Sent command: {command}")

root = tk.Tk()
root.title("Robot Arm Control")

# Create a larger canvas for drawing servo positions with a white background
canvas_width, canvas_height = 400, 400  # Define a larger size for the canvas
canvas = Canvas(root, width=canvas_width, height=canvas_height, bg='white')
canvas.pack()

send_button = Button(root, text="Send Commands", command=send_all_servo_commands)
send_button.pack()

# Sliders for the servos with corresponding buttons
servo1_frame = tk.Frame(root)
Button(servo1_frame, text="-10", command=lambda: decrement_servo(servo1, 10)).pack(side=tk.LEFT)
Button(servo1_frame, text="-1", command=lambda: decrement_servo(servo1, 1)).pack(side=tk.LEFT)
servo1 = Scale(servo1_frame, from_=0, to=180, orient=HORIZONTAL, label="Bicep Rotation", command=lambda value: update_servo(1, value))
servo1.id = 1
servo1.pack(side=tk.LEFT)
Button(servo1_frame, text="+1", command=lambda: increment_servo(servo1, 1)).pack(side=tk.LEFT)
Button(servo1_frame, text="+10", command=lambda: increment_servo(servo1, 10)).pack(side=tk.LEFT)
servo1_frame.pack()

# Servo 2
servo2_frame = tk.Frame(root)
Button(servo2_frame, text="-10", command=lambda: decrement_servo(servo2, 10)).pack(side=tk.LEFT)
Button(servo2_frame, text="-1", command=lambda: decrement_servo(servo2, 1)).pack(side=tk.LEFT)
servo2 = Scale(servo2_frame, from_=0, to=30, orient=tk.HORIZONTAL, label="Bicep Extension (mm)", command=lambda value: update_servo(2, value))
servo2.id = 2
servo2.pack(side=tk.LEFT)
Button(servo2_frame, text="+1", command=lambda: increment_servo(servo2, 1)).pack(side=tk.LEFT)
Button(servo2_frame, text="+10", command=lambda: increment_servo(servo2, 10)).pack(side=tk.LEFT)
servo2_frame.pack()

# Servo 3
servo3_frame = tk.Frame(root)
Button(servo3_frame, text="-10", command=lambda: decrement_servo(servo3, 10)).pack(side=tk.LEFT)
Button(servo3_frame, text="-1", command=lambda: decrement_servo(servo3, 1)).pack(side=tk.LEFT)
servo3 = Scale(servo3_frame, from_=0, to=90, orient=tk.HORIZONTAL, label="Wrist Rotation", command=lambda value: update_servo(3, value))
servo3.id = 3
servo3.pack(side=tk.LEFT)
Button(servo3_frame, text="+1", command=lambda: increment_servo(servo3, 1)).pack(side=tk.LEFT)
Button(servo3_frame, text="+10", command=lambda: increment_servo(servo3, 10)).pack(side=tk.LEFT)
servo3_frame.pack()

# Servo 4
servo4_frame = tk.Frame(root)
Button(servo4_frame, text="-10", command=lambda: decrement_servo(servo4, 10)).pack(side=tk.LEFT)
Button(servo4_frame, text="-1", command=lambda: decrement_servo(servo4, 1)).pack(side=tk.LEFT)
servo4 = Scale(servo4_frame, from_=0, to=180, orient=tk.HORIZONTAL, label="Gripper Rotation", command=lambda value: update_servo(4, value))
servo4.id = 4
servo4.pack(side=tk.LEFT)
Button(servo4_frame, text="+1", command=lambda: increment_servo(servo4, 1)).pack(side=tk.LEFT)
Button(servo4_frame, text="+10", command=lambda: increment_servo(servo4, 10)).pack(side=tk.LEFT)
servo4_frame.pack()

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

# Create a frame for the stepper control buttons
stepper_control_frame = tk.Frame(root)
stepper_control_frame.pack(side=tk.BOTTOM, pady=10)  # Add some padding for visual appeal

# Place the buttons inside the frame, which will be at the bottom of the root window
Button(stepper_control_frame, text="Left", command=lambda: update_stepper('L')).pack(side=tk.LEFT, padx=5)
Button(stepper_control_frame, text="Right", command=lambda: update_stepper('R')).pack(side=tk.RIGHT, padx=5)
Button(stepper_control_frame, text="Large Left", command=lambda: update_stepper('K')).pack(side=tk.LEFT, padx=5)
Button(stepper_control_frame, text="Large Right", command=lambda: update_stepper('T')).pack(side=tk.RIGHT, padx=5)

# Run the GUI loop and draw initial servo positions
draw_servo_positions()
root.mainloop()
