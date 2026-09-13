# Windows USB access for Studio in WSL

This document lives at the **repository root**, `SERIAL_TUNNEL.md`. Studio can
attach a Windows USB board to WSL2 using **usbipd-win 5 or newer**, then use the
normal Linux serial port for Serial Monitor and Flash Firmware. USB access is
separate from Studio's Windows-host MQTT forwarding.

Windows cannot use a device while it is attached to WSL. First sharing requires
administrator approval; attachment does not. Studio uses an interactive installer
so installation does not silently restart Windows. See Microsoft's
[USB connection guide](https://learn.microsoft.com/en-us/windows/wsl/connect-usb).

## Use the Studio setup flow

1. Plug the board's **UART connector** into Windows with a USB data cable.
2. In Debug Tray, open **Serial Monitor** or **Flash Firmware**, then select
   **Windows USB…**. Both buttons open the same setup dialog and session.
   Opening the dialog only discovers devices; it does not install or attach.
3. If needed, select **Install USB support** and complete the Windows installer
   and its UAC prompt. Studio invokes:

   ```powershell
   winget install --interactive --exact dorssel.usbipd-win --accept-source-agreements
   ```

   Studio checks the installed version again afterward. It also checks
   `C:\Program Files\usbipd-win\usbipd.exe`, so a stale WSL PATH does not require
   restarting Studio. Missing winget, disabled Windows interoperability, WSL1,
   an old kernel, and installation errors are reported in the dialog, with an
   official installation link. No Arduino tools are installed by this action.
4. Select the board. The dialog shows description, COM port when available,
   VID/PID, stable Windows identity, BUSID, sharing state, and Linux path when
   matched. **Show all USB devices** exposes unrecognized boards.
5. Select **Use in Studio**. If necessary, approve the Windows sharing prompt.
   Studio binds just the selected device, lets usbipd select a WSL distribution,
   then waits up to **30 seconds** for a matching Linux serial
   port. An attached USB device without a usable serial port is not ready.
   Multiple matching ports require an explicit Linux-port selection.
6. Once ready, both tabs receive the verified Linux path. Use the existing
   **Connect**, **Set Wi-Fi…**, **Set MQTT…**, and **Flash Firmware** actions.
   Attachment itself neither opens the monitor nor uploads firmware.
7. Select **Release to Windows** when finished. This closes the monitor, stops
   recovery, detaches the device, and removes the released port from the pickers.
   Release is disabled during an upload.

**Cancel setup** stops subsequent setup steps. An installer or UAC operation
already running in Windows may still complete. Refresh before retrying; Studio
also reinspects before every install, attachment, and release. Closing the dialog
cancels an active setup operation; closing it after readiness keeps USB connected.

## Board settings and serial permissions

The existing Studio profile is for **ESP32-S3-CAM N16R8**, using its **CH340 UART
bridge**. The firmware calls `Serial.begin(115200)` and Studio defaults to 115200.
The current build uses `CDCOnBoot=default`, so `Serial` stays on UART0. A native
USB connection can enumerate successfully without carrying this build's serial
heartbeats. Use the UART connector for this workflow.

The usual UART device is `/dev/ttyUSB0`; native USB CDC devices commonly appear
as `/dev/ttyACM0`. Studio checks both families and does not assume the suffix is
always zero. A charge-only cable can power the board without exposing any device.

Studio first checks access on the matched node. If owner permissions are needed,
it invokes `wsl.exe --distribution <current distro> --user root --exec python3`
with the dedicated `wsl_permissions.py` helper. That helper verifies the exact
USB serial node and its VID/PID/serial identity before changing its owner to the
current Studio user and adding owner read/write access. It does not grant broad
access to all serial devices. New nodes are checked again after re-enumeration.
This avoids waiting for a new login or restarting Studio after a group change.

A kernel lacking the relevant USB serial driver can expose the USB device without
creating a tty. Studio reports that condition rather than declaring success.
Update WSL manually or check the connector/driver using the linked installation
guide; Studio never updates or restarts WSL automatically.

## Session lifecycle

- Studio remembers the device **selection** in preferences. Starting Studio again
  always requires an explicit **Use in Studio** action.
- Device identity is independent of BUSID, COM number, and Linux path. Discovery
  uses `usbipd state` JSON and the upstream
  [automation device model](https://github.com/dorssel/usbipd-win/blob/master/Usbipd.Automation/Device.cs).
  Linux matching uses VID/PID and serial when available. Without a serial number,
  Studio uses a unique matching port appearing after attachment. It asks when
  multiple candidates remain; it never substitutes another Windows USB identity.
- While the session is active, Studio checks the verified node approximately once
  a second, including node replacement at an unchanged path. On disappearance it
  refreshes Windows discovery and uses the current BUSID of the selected device.
  Recovery retries use delays of **1, 2, 5, then 10 seconds**, capped at 10 seconds
  between attempts. Host calls and the 30-second enumeration wait add their own
  bounded time. Missing devices remain waiting. Changed or ambiguous identities,
  lost sharing, or a stopped Windows service require user attention. Recovery
  never launches an installer or repeats a UAC request.
- Only a previously open monitor is restored, and only outside an upload. Manual
  **Disconnect** clears that intent. **Cancel monitor reconnect** is available
  while the monitor is waiting. USB attachment recovery can continue independently.
- Flashing closes the monitor and reserves it until Arduino CLI exits, including
  a failure to start. USB recovery can continue during flashing, but Studio never
  repeats an interrupted or failed upload. Check its output, wait for USB readiness,
  and manually flash again if needed.
- Normal application exit stops recovery and detaches attachments created by this
  Studio session. It preserves attachments that already existed when Studio adopted
  the device and leaves persistent Windows sharing in place. Explicit **Release
  to Windows** also releases an adopted attachment. Host operations have timeouts;
  cleanup failures are logged. A killed process or unavailable Windows host can
  require manual detachment.

## Manual diagnosis and flashing

These are optional troubleshooting commands. The GUI performs USB preparation.
Use Windows PowerShell to inspect the installation and device state:

```powershell
usbipd --version
usbipd state
wsl --list --verbose
```

For manual preparation, use the board's current BUSID in place of `2-3`:

```powershell
# Administrator PowerShell: first sharing only
usbipd bind --busid 2-3

# Ordinary PowerShell; usbipd selects a distribution (devices are shared by WSL2)
usbipd attach --wsl --busid 2-3

# After closing serial tools
usbipd detach --busid 2-3
```

The source of truth for builds is
[`studio/services/firmware/commands.py`](studio/services/firmware/commands.py).
Studio now offers to install Arduino CLI and the Espressif ESP32 core when you
click **List USB Devices**, **Verify Build**, or **Flash Firmware** and either is
missing. It checks the core asynchronously, shows installation progress in the
firmware console, and supports **Stop**. Once ready it resumes the requested
action; uploading still has its own confirmation. Declining or cancelling setup
does not upload anything. No downloads are triggered on application startup.

An existing Arduino CLI on PATH is reused. Otherwise Studio downloads the
official Arduino CLI 1.5.1 executable to its user-data `firmware-tools` directory;
no PATH change or restart is required. ESP32 packages use Arduino CLI's normal
user configuration and Espressif's stable package index. Under WSL these are
Linux tools. The download/bootstrap code and Qt setup flow are kept separately in
`studio/services/firmware/install_firmware_flasher.py` and
`studio/ui/components/install_firmware_flasher.py`.

For manual commands, Arduino CLI and the core must be available inside WSL.
From the repository root, this uses the same options and vendored libraries as
Studio (replace the port with the verified path):

```bash
arduino-cli compile \
  --fqbn esp32:esp32:esp32s3 \
  --libraries firmware/vendor \
  --board-options 'USBMode=hwcdc,CDCOnBoot=default,UploadMode=default,CPUFreq=240,FlashMode=qio,FlashSize=16M,PartitionScheme=app3M_fat9M_16MB,PSRAM=opi,UploadSpeed=921600,EraseFlash=none' \
  --upload --port /dev/ttyUSB0 firmware
```

Omit `--upload --port /dev/ttyUSB0` to compile only. Close the monitor before a
manual upload; Studio coordinates exclusivity only for uploads launched in Studio.

Do **not** write an arbitrary application `.bin` at address zero. An application
image and a merged flash image have different placement requirements. Prefer
Arduino CLI's generated upload command; for custom images, use the build's actual
flash arguments and Espressif's
[flashing documentation](https://github.com/espressif/esptool/blob/master/docs/en/esptool/basic-commands.rst).

## Code organization

| Path | Responsibility |
| --- | --- |
| `studio/services/wsl/windows.py` | Shared PowerShell execution, quoting, UAC launcher, result files |
| `studio/services/serial/wsl_usb.py` | Discovery, installation, attachment, matching, session ownership and recovery |
| `studio/services/serial/wsl_permissions.py` | Standalone selected-node WSL-root permission helper |
| `studio/ui/components/wsl_serial.py` | Shared asynchronous USB dialog and monitor/upload coordination |
| `studio/ui/components/debug_tray.py` | Creates the coordinator only under WSL |
| `studio/ui/components/serial_monitor.py` | Existing QSerialPort monitor and provisioning, with small lifecycle hooks |
| `studio/ui/components/firmware_flash.py` | Existing Arduino CLI QProcess, with upload reservation signals |
| `studio/services/network/wsl/windows_host.py` | Existing MQTT host forwarding, reusing the common Windows helper |

Subprocess work runs in one USB worker at a time; Qt polls progress/result events.
Native Windows, Linux, and macOS do not construct the WSL USB integration.
The shared PowerShell runner restores executable extensions within its own
process if the inherited `PATHEXT` lacks `.EXE`; it does not edit Windows settings.
USB commands check their exit code and tolerate informational stderr output.

## Validation

Automated coverage uses mocked host operations and real Linux pseudo-terminals;
it does not install Windows software or touch a physical board:

```bash
venv/bin/python -m studio.services.serial.wsl_usb_tests
QT_QPA_PLATFORM=offscreen venv/bin/python -m studio.ui.components.wsl_serial_tests
QT_QPA_PLATFORM=offscreen venv/bin/python -m studio.ui.components.serial_tests
venv/bin/python -m studio.services.firmware.tests
QT_QPA_PLATFORM=offscreen venv/bin/python -m studio.services.network.tests.setup_tests
QT_QPA_PLATFORM=offscreen venv/bin/python -m studio.services.network.tests.system_tests
QT_QPA_PLATFORM=offscreen venv/bin/python -m studio.services.network.tests.tests
QT_QPA_PLATFORM=offscreen venv/bin/python -m studio.ui.workspaces.network.tests
QT_QPA_PLATFORM=offscreen venv/bin/python -m studio.storage.tests
QT_QPA_PLATFORM=offscreen venv/bin/python -m studio.smoke_test
```

Coverage includes JSON discovery, missing/old tools, installer outcomes, UAC
cancellation, timeouts, permission failures, duplicate devices, changed BUSIDs and
paths, node replacement, reconnect identity, release, and shutdown ownership.
Qt tests cover both pickers, explicit selection, native-platform gating, PTY
heartbeat reception and provisioning, monitor/upload exclusion, and manual retry.

Verified results: **50 USB backend/host tests, 17 WSL Qt/PTY tests**, 20 existing
serial tests, 6 firmware command tests, 49 network setup tests, 44 system broker
tests, 33 broker finder tests, 35 Network UI tests, and 15 storage tests passed.
The Studio smoke test also passed (5 workspaces, 16 pages). A direct Windows probe
confirmed native executable execution and informational stderr handling after the
process-local `PATHEXT` correction; `winget --version` returned `v1.29.290`.

Read-only host inspection on **2026-09-09** found WSL2 kernel
`5.15.167.4-microsoft-standard-WSL2`, working Windows PowerShell interoperability,
and winget. usbipd-win was missing. Unlike the earlier inspection, Windows now
reported **USB Serial Device (COM3), VID:PID `303a:1001`**. No Linux USB serial node
was available. This verifies Windows discovery, not UART heartbeat reception.

**Hardware acceptance remains pending.** Use the GUI with the board's UART
connector to verify installation, first bind/UAC, attachment and permissions,
115200-baud heartbeats, Wi-Fi/MQTT provisioning, flashing, unplug/replug recovery,
manual disconnect, release back to Windows, and owned/pre-existing attachments on
exit. No physical upload, Windows installation, or USB attachment was performed
as part of the automated validation. Packaged WSL distribution testing is also
separate from the source-run checks above.

Firmware behavior and MQTT forwarding policy remain separate from USB access.
On-demand Arduino toolchain installation was added on 2026-09-10; its tests run
with `QT_QPA_PLATFORM=offscreen venv/bin/python -m studio.ui.components.install_firmware_flasher_tests`.
All 23 setup tests passed, along with the existing serial, firmware-command,
WSL Qt, and Studio smoke checks. A separate temporary installation verified the
real Arduino CLI 1.5.1 download, ESP32 core 3.3.11 installation, Studio's Qt USB
scan, and compilation using Studio's board options and vendored libraries.
The build used 1,305,582 bytes of flash and 61,284 bytes of static RAM. No firmware
was uploaded during that check.

### USB/IP reports “Device in error state”

This means the USB handoff failed. A message saying selection of a specific WSL
distribution is unnecessary is informational, not the cause. Studio now omits
that obsolete argument, renders native tool output without PowerShell error-record
formatting, and reinspects device state after a failed command. The focused error
handling lives in `studio/services/serial/wsl_usb_errors.py`.

Unplug and reconnect the cable using the UART/CH340 connector for this firmware,
close Windows serial tools using that board, then Refresh and select the current
device before choosing Use in Studio. A shared entry without a BUSID is only a
saved sharing record; it does not establish that Windows currently sees the board.
If Windows still cannot enumerate the board, check its state in Device Manager.
Studio does not automatically restart Windows, reset USB controllers, or unbind
other devices to work around this error.

Regression command: `venv/bin/python -m studio.services.serial.wsl_usb_error_tests`.
Four targeted tests cover useful error reporting and refreshed device records;
the 50 existing USB backend tests and 17 WSL Qt/PTY tests also pass. A benign real
Windows command emitting stderr and exiting nonzero verified the corrected output
capture. Physical USB recovery still requires reconnecting the board.
