# Desk Buddy Calibration Wizard

Launch from the repository root:

```bash
python3 -m pip install -r calibration_tool/requirements.txt
python3 calibration_tool/app.py
```

The wizard stores local MQTT settings in `calibration_tool/.env` and captures
camera images under `calibration_tool/captures/`.

The interface is organized around the calibration flow:

- **Setup** connects to the robot's MQTT topics.
- **Status** shows saved preferences, firmware defaults, missing required
  calibration, and optional values as a checklist.
- **Base + Perch** keeps the two physical setup steps together.
- **IK** contains the saved reach-plane workflow.
- **Visual Calibration**, immediately after IK, sends `calibrate_depth`, previews
  the fresh firmware photo, and reports the calibration points returned by the
  Visual AI server.
- **Reach and Grab** sends one `detect_object` request for a target description,
  previews the firmware JPEG, and follows matching firmware/Visual AI progress
  through the terminal result. The Vision server owns the generated base, IK,
  gripper, and final-telemetry commands; the GUI does not replay them.
- **Stencil** contains the final physical pickup-offset workflow.
- The robot controller remains on the right side on every page, with live
  servo, gripper, and base state when telemetry is available. Base rotation has
  one shared integer field for absolute-angle, relative-degree, and firmware-step
  commands. Its camera button captures an MQTT photo and scales the complete
  preview to the controller pane's current width without cropping.

Use the `−` and `+` buttons in the header to zoom the interface from 75% to
150%, or use `Ctrl+-`, `Ctrl++`, and `Ctrl+0`. The controller scrolls when the
window is compact. The in-app **Maximize** button also works on WSL window
managers that do not expose Tk's native maximize state.

Reach-and-grab uses the configured client sender and rejects the reserved
`ai_server`, `visual_ai`, and `firmware` sender names. Its timeout is deliberately
non-retrying: if the terminal Visual AI result is lost, the robot may still have
moved. Inspect the shared-topic activity log before manually starting another
request.
