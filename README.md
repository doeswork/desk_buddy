# Desk Buddy - Affordable Open Source Robot Arm

Welcome to the Desk Buddy project repository! Desk Buddy is an affordable robot arm project that you can build and customize yourself. 

## Project Overview

Desk Buddy is designed to be accessible to everyone. With simple materials and comprehensive guides, you can build your own robot arm that can perform a variety of tasks. It's perfect for your desk, where it can help with simple tasks or just keep you company.

![Desk Buddy](URL_to_desk_buddy_image "Desk Buddy")

## Getting Started

To get started with Desk Buddy, you'll need some basic components and tools. For a full list of materials and step-by-step instructions, check out the [Getting Started Guide](URL_to_getting_started_guide).

## Vision and MQTT Services

The [`vision/`](vision/) project is the MQTT-only companion-computer stack for camera capture, zero-shot detection, depth inference, nine-point calibration, deterministic motion planning, and trained residual corrections. Its coordinator and model workers run in separate Python environments, can be deployed on one or several broker-connected devices, and preserve the ESP32 firmware's existing MQTT command format.

See the [vision setup guide](vision/README.md), [MQTT contract](vision/docs/MQTT_CONTRACT.md), and [migration guide](vision/docs/MIGRATION.md) before connecting it to production robot topics.

## How to Build

We've prepared a series of video tutorials to help you through the building process:
[![Desktop_BlueTooth Tutorial]()](https://www.youtube.com/watch?v=KOnslMjLCzg&ab_channel=HackerTwins)
[![Build Tutorial]()](https://www.youtube.com/watch?v=Q91i7-LMvH8&list=PLEucaNZ-kUSoCu26UAixnopTIWHhtfvJg&index=8&ab_channel=HackerTwins)

## License

Desk Buddy is open source and available under the [MIT License].
