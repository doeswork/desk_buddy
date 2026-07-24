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
- **IK** and **Stencil** contain the later calibration workflows.
- The robot controller remains on the right side on every page, with live
  servo, gripper, and base state when telemetry is available.

Use the `−` and `+` buttons in the header to zoom the interface from 75% to
150%, or use `Ctrl+-`, `Ctrl++`, and `Ctrl+0`. The controller scrolls when the
window is compact. The in-app **Maximize** button also works on WSL window
managers that do not expose Tk's native maximize state.
