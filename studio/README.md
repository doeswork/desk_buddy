# Desk Buddy Studio

Self-host and manage every service that runs Desk Buddy.

## Run

```bash
# from the repo root
python3 -m venv .venv
.venv/bin/pip install -r studio/requirements.txt
.venv/bin/python -m studio
```

A venv is required on distros with an externally-managed Python (Arch, Debian,
Fedora). `python -m studio` and `python studio/app.py` both work.

## Broker setup

Network automatically checks the MQTT connection and prepares the machine's
Mosquitto when needed. Approve the operating system's authorization prompts;
there are no commands to paste in the normal setup flow. A startup preference
also controls whether Studio begins setup when the app opens.

Linux setup has package-manager adapters for Ubuntu/Debian, Arch, Fedora,
openSUSE, and Alpine, and service adapters for systemd, OpenRC, and installed
SysV scripts. Desktop authorization uses polkit. Without a desktop agent,
Studio opens a terminal for `sudo`. WSL runs only the narrow broker helper as
the distribution's root user. Windows robot access is a separate opt-in step,
so local broker setup never requests Windows UAC.

Studio preserves working credentials and existing accounts. New local setups
use an authenticated listener on `0.0.0.0:1883` (or the configured port) and a
generated `studio` account (or an unused suffixed name). Configuration changes are backed up and restored
if starting or verifying the broker fails. Incompatible custom authentication
requires manual review. A custom broker's host and credentials can be entered
under **Advanced / Manual setup**, which starts collapsed and also contains
fallback commands and diagnostics.

WSL setup is complete once the Linux broker is configured and Studio's MQTT
connection is verified. The Broker page then offers **Enable robot access**.
That explicit action requests Windows UAC and creates a subnet-scoped firewall
rule plus either NAT forwarding or a mirrored-network Hyper-V rule. Studio
rechecks its owned rules whenever Network opens, offers repair after an address
or networking-mode change, and can remove them again. The shown robot address
is the Windows LAN address; `0.0.0.0` is only a listener bind address. A ready
card verifies the Windows-facing TCP port, not a connection from a physical
robot.

In NAT mode, a saved forwarding entry can exist without an active Windows
listener. Studio detects this as a repair: **Retry robot access** recreates
its forwarding entry, starts IP Helper if stopped, and checks the port again.

Cancelled or failed setup stays paused until **Retry setup**. Revoking account
management also pauses automatic setup; the system broker continues running.
Native Windows and macOS retain manual installation instructions.

Validation commands (system operations are mocked except for an isolated,
unprivileged temporary MQTT broker when Mosquitto is installed):

```bash
QT_QPA_PLATFORM=offscreen python -m studio.services.network.tests.setup_tests
QT_QPA_PLATFORM=offscreen python -m studio.ui.workspaces.network.tests
QT_QPA_PLATFORM=offscreen python -m studio.services.network.tests.system_tests
QT_QPA_PLATFORM=offscreen python -m studio.services.network.tests.tests
QT_QPA_PLATFORM=offscreen python -m studio.smoke_test
```

The adapters and authorization decisions have automated coverage. Actual
package installation, desktop authorization, and a physical robot connection
still require end-to-end validation on WSL Ubuntu and native Ubuntu/Arch;
automated tests do not establish that platform coverage.

The Flash Firmware tab requires `arduino-cli` and the Espressif ESP32 board
core. Its three sketch libraries are pinned under `../firmware/vendor` and do
not need to be installed separately in the Arduino IDE.

## Package it

Python is not a compiled language, so nothing becomes a `.exe` on its own. A
bundler freezes the interpreter, your code, and the Qt libraries into one file.

```bash
python studio/build.py
```

| Built on | You get |
|---|---|
| Windows | `dist/DeskBuddyStudio.exe` |
| macOS | `dist/DeskBuddyStudio.app` |
| Linux | `dist/DeskBuddyStudio.bin` |

**There is no cross-compiling.** A Windows `.exe` must be built on Windows. You
cannot produce one from this Linux box. That is what
`.github/workflows/build.yml` is for: it runs the same `build.py` on a Windows,
macOS, and Linux runner and uploads all three. Push, then download the artifacts.

Under the hood this is `pyside6-deploy` (Qt's own tool, wraps Nuitka). The spec
lives in `pysidedeploy.spec`. First build is slow — it downloads Nuitka and
compiles. Expect ~50-80MB per binary.

### Not done yet
- **Code signing.** Unsigned, Windows SmartScreen warns "unknown publisher" and
  macOS Gatekeeper refuses to open it. Needs a cert (~$100-400/yr) + Apple
  notarization. See `decide_early` in `../PLAN.md`.
- **Installer.** `build.py` makes a bare executable, not a setup wizard.

## Now

Hello world. A window, a sidebar of the six services from `../PLAN.md`, and a
placeholder page for each. Nothing is wired to a robot yet.

## Next

Phase 1 in `../PLAN.md`: move `mqtt_robot.py` into `studio/robot/`, declare
capabilities as data, build the simulator.

`calibration_tool/` is still the working app. It stays until Phase 3 replaces it.
