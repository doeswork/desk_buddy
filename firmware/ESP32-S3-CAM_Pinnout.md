# Desk Buddy ESP32-S3-CAM pinout and board-use reference

Last audited against the repository on 2026-09-19.

This document describes the ESP32-S3 camera board as Desk Buddy actually uses
it: firmware GPIO assignments, camera bus, controller sockets, carrier-board
connectors, power path, Arduino settings, onboard-resource conflicts, startup
behavior, and the facts that still need physical verification. The requested
filename intentionally uses `Pinnout`.

The evidence levels used below are:

- **Confirmed in code:** a current firmware or Studio source file establishes it.
- **Confirmed in PCB data:** the reconstructed schematic/netlist establishes the
  carrier-board connection, but that does not by itself prove circuit intent.
- **Expected hardware:** the BOM or Freenove documentation identifies it; verify
  the marking on an actual board before replacing hardware or changing power.
- **Unknown:** the repository does not establish it, so this document does not
  silently fill it in.

## Critical rules before connecting or flashing

1. Use the board's **USB-to-UART/programming port**, not its native USB/OTG
   port. Desk Buddy drives servos on GPIO19 and GPIO20, which are also the
   ESP32-S3's fixed native USB D- and D+ pins.
2. Do not use the onboard microSD slot. GPIO39 is both the board's SD clock and
   Desk Buddy's wrist-servo output. Leave the slot empty while this firmware is
   running.
3. GPIO45 and GPIO46 are ESP32-S3 strapping pins as well as the two active-low
   gripper stops. Their reset defaults are weak pull-downs, and the carrier
   switches only connect them to ground, so that wiring preserves the default
   LOW strap state. Do not add pull-ups or a device that drives either pin HIGH
   during reset without reviewing the ESP32-S3 strap table.
4. GPIO0 is the chip's BOOT strap and the firmware's connection-reset button.
   For the firmware's three-second Wi-Fi/MQTT reset, press it **after the
   application has booted**. Holding it low while resetting can enter the ROM
   download bootloader instead of running the application.
5. Servo power and ESP32 power must share ground. Do not power servos from a
   GPIO or the 3.3 V output. Size the servo supply for motor startup/stall
   current; the firmware explicitly diagnoses servo-induced brownouts.
6. The carrier connector names do not all match the current firmware roles.
   Use the plug map below, not only the silkscreen.

## Board identity and supported build

The repository targets an ESP32-S3 camera development board with two 20-pin
headers and dual USB connectors. The BOM calls it an `ESP32-S3 CAM Module`,
`N16R8`, dual Type-C, with a 24-pin camera connector, and names
Freenove/Amazon as the source. The GPIO layout and camera bus match Freenove's
ESP32-S3 WROOM camera-board pinout.

Expected physical board features are:

- ESP32-S3-WROOM module and PCB antenna for 2.4 GHz Wi-Fi;
- two 1x20 header rows used by the Desk Buddy carrier sockets;
- one USB-C port through the UART bridge and one native USB-C/OTG port;
- 24-contact camera FPC connector;
- onboard microSD socket using GPIO38/GPIO39/GPIO40;
- EN/RST and BOOT/GPIO0 pushbuttons;
- power and UART-activity indicators, a GPIO2 LED, and a GPIO48 WS2812.

Only the functions explicitly described later are used by Desk Buddy. Keep the
module antenna end clear of metal, wiring bundles, and copper where practical.

Desk Buddy Studio is more specific than the BOM and is the build source of
truth:

| Property | Project setting |
| --- | --- |
| Studio board name | `ESP32-S3-CAM (N16R8)` |
| Arduino FQBN | `esp32:esp32:esp32s3` (`ESP32S3 Dev Module`) |
| CPU | 240 MHz |
| Flash mode/frequency | QIO, 80 MHz |
| Flash size | 16 MB |
| Partition scheme | 16 MB flash: 3 MB application / 9.9 MB FATFS |
| PSRAM | 8 MB OPI PSRAM expected (`PSRAM=opi`) |
| USB mode menu | Hardware CDC and JTAG |
| USB CDC on boot | Disabled |
| Upload mode | UART0 / Hardware CDC |
| Upload speed | 921600 baud |
| Normal erase setting | Disabled; Studio's explicit **Erase first** enables it |
| Runtime `Serial` baud | 115200 |

The most recently documented full compile used Espressif Arduino core 3.3.11,
but the setup code currently installs `esp32:esp32` without pinning a version.
The three sketch libraries are vendored in this repository:

- ArduinoJson 7.2.1
- ESP32Servo 3.0.6
- PubSubClient 2.8

The BOM row is internally inconsistent: it says `N16R8` but also says `8MB
Flash | 8MB PSRAM`. `N16R8` and the Studio profile mean 16 MB flash and 8 MB
PSRAM. Read the module marking or query the actual chip before using this image
on a replacement board; an 8 MB flash part does not match the configured 16 MB
partition table.

## Complete project GPIO inventory

Directions are from the ESP32's point of view. `INPUT_PULLUP` means the normal
unasserted level is HIGH and the external contact should assert by connecting
the signal to ground.

| GPIO | Desk Buddy use | Direction / configuration | Important overlap |
| ---: | --- | --- | --- |
| 0 | BOOT button and connection reset | `INPUT_PULLUP`, active LOW; hold 3 s | Boot-mode strapping pin; weak pull-up at reset |
| 1 | AS5600 analog angle output | ADC1 input, 12-bit `analogRead()` | Carrier net is misleadingly named `CLK` |
| 2 | Not used by Desk Buddy | No application configuration | Vendor pinout marks an onboard LED |
| 3 | Not used by Desk Buddy | No application configuration | JTAG-source strapping pin; floating default |
| 4 | Camera SCCB `SIOD` | Bidirectional camera-control data | Camera owns it |
| 5 | Camera SCCB `SIOC` | Camera-control clock output | Camera owns it |
| 6 | Camera `VSYNC` | Camera input | Camera owns it |
| 7 | Camera `HREF` | Camera input | Camera owns it |
| 8 | Camera D2 / sensor `Y4` | Camera parallel-data input | Camera owns it |
| 9 | Camera D1 / sensor `Y3` | Camera parallel-data input | Camera owns it |
| 10 | Camera D3 / sensor `Y5` | Camera parallel-data input | Camera owns it |
| 11 | Camera D0 / sensor `Y2` | Camera parallel-data input | Camera owns it |
| 12 | Camera D4 / sensor `Y6` | Camera parallel-data input | Camera owns it |
| 13 | Camera `PCLK` | Camera pixel-clock input | Camera owns it |
| 14 | Base true-north switch | `INPUT_PULLUP`, active LOW, FALLING interrupt | Also sampled by polling |
| 15 | Camera `XCLK` | 20 MHz clock output | Camera owns it |
| 16 | Camera D7 / sensor `Y9` | Camera parallel-data input | Camera owns it |
| 17 | Camera D6 / sensor `Y8` | Camera parallel-data input | Camera owns it |
| 18 | Camera D5 / sensor `Y7` | Camera parallel-data input | Camera owns it |
| 19 | Arm twist servo | 50 Hz servo PWM output | Native USB D-; also UART1 RTS alternate function |
| 20 | Gripper servo | 50 Hz servo PWM output | Native USB D+; also UART1 CTS alternate function |
| 21 | Base continuous-rotation servo | 50 Hz servo PWM output | Carrier connector is labeled `BICEP` |
| 22-25 | Do not exist on ESP32-S3 | N/A | There is a numbering gap in the chip GPIOs |
| 26-32 | SPI0/1 flash/PSRAM memory interface | Reserved inside/on the module | Do not assign |
| 33-34 | Upper OPI memory data lines; not exposed by this board | Reserved by OPI PSRAM | Do not assign |
| 35-37 | OPI PSRAM interface | Reserved by the N16R8 build | Do not assign |
| 38 | Not used by Desk Buddy | No application configuration | Onboard SD `CMD` |
| 39 | Arm wrist servo | 50 Hz servo PWM output | Onboard SD `CLK`; JTAG `MTCK` |
| 40 | Not used by Desk Buddy | No application configuration | Onboard SD `DATA`; JTAG `MTDO` |
| 41 | Desk Buddy status LED | Digital output; HIGH = on | JTAG `MTDI`; carrier LED1 |
| 42 | Carrier LED2 is not driven | No application configuration | JTAG `MTMS` |
| 43 | UART0 transmit / serial log and provisioning | Configured indirectly by `Serial.begin(115200)` | Connected to the USB-UART bridge |
| 44 | UART0 receive / serial provisioning | Configured indirectly by `Serial.begin(115200)` | Connected to the USB-UART bridge |
| 45 | Gripper stop 1 | `INPUT_PULLUP`, active LOW | VDD_SPI strapping pin, weak pull-down at reset; carrier net `B1` |
| 46 | Gripper stop 2 | `INPUT_PULLUP`, active LOW | Boot/ROM-log strapping pin, weak pull-down at reset; carrier net `B2` |
| 47 | Arm elbow servo | 50 Hz servo PWM output | Carrier connector is labeled `BASE` |
| 48 | Not used by Desk Buddy | No application configuration | Onboard WS2812 data |

GPIO1 is on ADC1, so the encoder does not depend on ADC2 while Wi-Fi is active.
No Desk Buddy GPIO is 5 V tolerant; external signals must remain within the
ESP32-S3's 3.3 V logic/ADC limits.

## Camera wiring: complete DVP/SCCB map

`camera_pins.h` defines the following 8-bit parallel camera interface. The
sensor-side `Y2` through `Y9` names are the names historically used by the
Espressif camera examples; firmware maps them to MCU D0 through D7 as shown.

| Camera signal | MCU bus bit | GPIO | ESP32 direction | Purpose |
| --- | ---: | ---: | --- | --- |
| `SIOD` | — | 4 | Bidirectional | SCCB register/configuration data |
| `SIOC` | — | 5 | Output | SCCB register/configuration clock |
| `VSYNC` | — | 6 | Input | Frame synchronization |
| `HREF` | — | 7 | Input | Active-pixel/line qualification |
| `Y4` | D2 | 8 | Input | Parallel pixel data bit 2 |
| `Y3` | D1 | 9 | Input | Parallel pixel data bit 1 |
| `Y5` | D3 | 10 | Input | Parallel pixel data bit 3 |
| `Y2` | D0 | 11 | Input | Parallel pixel data bit 0 |
| `Y6` | D4 | 12 | Input | Parallel pixel data bit 4 |
| `PCLK` | — | 13 | Input | Pixel clock from sensor |
| `XCLK` | — | 15 | Output | 20 MHz master clock to sensor |
| `Y9` | D7 | 16 | Input | Parallel pixel data bit 7 |
| `Y8` | D6 | 17 | Input | Parallel pixel data bit 6 |
| `Y7` | D5 | 18 | Input | Parallel pixel data bit 5 |
| `PWDN` | — | `-1` | Not connected to an MCU GPIO | No firmware power-down control |
| `RESET` | — | `-1` | Not connected to an MCU GPIO | No firmware hardware-reset control |

SCCB is the camera sensor's control bus. Although electrically I2C-like, these
pins belong to the camera driver and are not initialized as the sketch's
general-purpose `Wire` bus.

The firmware sets `LEDC_TIMER_0` and `LEDC_CHANNEL_0` fields in the portable
camera configuration. On ESP32-S3, Espressif's camera driver generates XCLK
through the LCD_CAM peripheral, so XCLK does not consume one of the eight LEDC
channels used by ESP32Servo.

### Camera runtime configuration

- Camera initialization is lazy: the first `photo`, `detect_object`,
  `detect_color`, or `calibrate_depth` action initializes it. It stays
  initialized afterward.
- The firmware tries JPEG SVGA (800x600), then VGA (640x480), then QVGA
  (320x240), stopping at the first successful initialization.
- JPEG quality is 20. For Espressif's OV-family camera driver, a lower number
  means higher quality and generally a larger frame.
- It uses one framebuffer and `CAMERA_GRAB_WHEN_EMPTY`.
- It uses PSRAM for the framebuffer when `psramFound()` succeeds and otherwise
  falls back to internal DRAM.
- Every request discards one old frame, waits 10 ms, and captures a fresh one.
- A failed capture gets one recovery attempt: deinitialize, wait 50 ms,
  initialize again, and recapture.
- Frames are raw JPEG bytes wrapped in the project's binary MQTT photo frame.
  The ESP32 does not perform object detection, color detection, or depth ML;
  those action names capture an image for the external Vision service.
- There is no camera streaming server, SD-card recording, or camera flash-light
  control in this firmware.

The BOM expects an OV2640 with a 200 mm, 24-pin, approximately 66-degree camera
cable/module. The firmware does not hard-code a sensor model; `esp_camera_init`
probes the attached supported sensor. `CAMERA_MODEL_ESP32S3_EYE` in
`camera_pins.h` is only a pin-map label here and is not proof that the physical
board is an Espressif ESP32-S3-EYE. The repository does **not** establish the
24-pin FPC contact order, so do not derive ribbon-pad numbering from the GPIO
table.

## Servo and motion wiring

The vendored ESP32Servo library defaults used here are 50 Hz and 544-2400 us
for 0-180 degrees. The ESP32-S3 implementation has eight LEDC channels. Desk
Buddy attaches three arm servos during `setup()` and lazily attaches the base
and gripper, for at most five servo channels.

| Firmware role | GPIO | Carrier connector label | Initialization | BOM's likely motor role |
| --- | ---: | --- | --- | --- |
| Arm elbow | 47 | `BASE` / signal `S1` | At boot | One of three SG90 servos |
| Arm wrist | 39 | `ELBOW` / signal `S3` | At boot | One of three SG90 servos |
| Arm twist | 19 | `TWIST` / signal `S4` | At boot | One of three SG90 servos |
| Base rotation | 21 | `BICEP` / signal `S2` | On first base command | MG90S 360-degree continuous servo |
| Gripper | 20 | `GRIPPER` / signal `S5` | On first gripper command | MG996R high-torque servo |

The motor-model column is the sensible mapping of the BOM's quantities, not a
runtime check or an explicit connector declaration in the BOM.

The first three connector-label mismatches are real in the current files:

- Plug the firmware's **elbow** servo into the carrier connector labeled
  **BASE** (GPIO47).
- Plug the **base continuous-rotation** servo into **BICEP** (GPIO21).
- Plug the firmware's **wrist** servo into **ELBOW** (GPIO39).
- `TWIST` and `GRIPPER` match their firmware roles.

Each five-servo connector is `pin 1 = signal`, `pin 2 = +SV`, and
`pin 3 = GND` in the reconstructed PCB netlist. Confirm the physical connector
orientation before inserting a servo plug.

At boot the arm code attaches elbow, wrist, and twist in that order, commands
90 degrees, then reads saved angles (default 90) from NVS and moves to them.
That can produce a startup movement. Base and gripper outputs do not attach
until their first command. Base servo values are speed/direction commands
around a nominal neutral of 90, not physical angular positions.

### Base feedback and home switch

| Function | GPIO | Carrier endpoint | Behavior |
| --- | ---: | --- | --- |
| AS5600 analog output | 1 | `BICEP1` pin 1, carrier net `CLK` | 12-bit sample, raw 0-4095 |
| Sensor 3.3 V | — | `BICEP1` pin 2, carrier net `3V` | Comes from the module's 3V3 header |
| Sensor ground | — | `BICEP1` pin 3 | Common ground |
| True north | 14 | `U1` pin 1, carrier net `M1` | Active-low contact/sensor to ground |
| True-north ground | — | `U1` pin 2 | Common ground |

The PCB labels `CLK`, `BICEP1`, and `M1` are historical and do not describe the
current firmware semantics well. Current code treats GPIO1 strictly as the
AS5600 **analog OUT** signal and GPIO14 as the true-north switch.

There is a bill-of-materials discrepancy: `hardware/BOM.csv` lists a KY-003
digital Hall module but does not list an AS5600. The current firmware and
calibration walkthrough explicitly require an AS5600 analog angle signal on
GPIO1 in addition to the GPIO14 true-north input. Verify the installed sensors;
a KY-003 alone cannot provide the 0-4095 rotational feedback this code expects.

The encoder is unwrapped across its 4095/0 rollover and multiplied by an
`ENCODER_SIGN` of -1. Gear constants are 18 teeth on the drive gear and 108 on
the base, with 216 firmware steps per base revolution. Before calibration, the
fallback is `4096 * 108 / 18 = 24576` encoder counts per base revolution. The
source comments say measured hardware is closer to 20,000, which is why real
calibration is preferred. Movement times out after 15 seconds and declares a
stall when it sees less than 100 counts of progress in one second.

### Gripper stops

| Firmware input | GPIO | PCB connection | Asserted state |
| --- | ---: | --- | --- |
| Stop 1 | 45 | `BTNFING2` pin 1 / net `B1` | LOW, switch to GND |
| Stop 2 | 46 | `BTNFING1` pin 1 / net `B2` | LOW, switch to GND |

Both button connectors use pin 2 as ground. A `GRAB` succeeds when either input
goes LOW. GPIO45 and GPIO46 both have weak pull-down reset defaults, so a closed
switch to ground does not invert their default strap state. Keep these as
passive contacts to ground; do not add a reset-time pull-up or active HIGH
driver without reviewing the chip's boot configuration.

## Controller sockets and carrier-board net map

This table combines the Freenove 40-pin board layout with the reconstructed
Desk Buddy PCB netlist. It is the most useful continuity/probing reference for
the two 1x20 sockets. `Open` means the carrier PCB adds no connection; an
onboard camera, memory, LED, USB bridge, or SD socket can still use that GPIO.

U3 is the board side that begins with 3V3 and ends with 5V:

| U3 pin | Module pin | Carrier connection | Desk Buddy meaning |
| ---: | --- | --- | --- |
| 1 | 3V3 | `3V`, `BICEP1.2` | Sensor 3.3 V |
| 2 | RST/EN | Open | Module reset only |
| 3 | GPIO4 | Open | Camera SIOD onboard |
| 4 | GPIO5 | Open | Camera SIOC onboard |
| 5 | GPIO6 | Open | Camera VSYNC onboard |
| 6 | GPIO7 | Open | Camera HREF onboard |
| 7 | GPIO15 | Open | Camera XCLK onboard |
| 8 | GPIO16 | Open | Camera D7/Y9 onboard |
| 9 | GPIO17 | Open | Camera D6/Y8 onboard |
| 10 | GPIO18 | Open | Camera D5/Y7 onboard |
| 11 | GPIO8 | Open | Camera D2/Y4 onboard |
| 12 | GPIO3 | Open | Unused strapping pin |
| 13 | GPIO46 | `B2`, `BTNFING1.1` | Gripper stop 2 |
| 14 | GPIO9 | Open | Camera D1/Y3 onboard |
| 15 | GPIO10 | Open | Camera D3/Y5 onboard |
| 16 | GPIO11 | Open | Camera D0/Y2 onboard |
| 17 | GPIO12 | Open | Camera D4/Y6 onboard |
| 18 | GPIO13 | Open | Camera PCLK onboard |
| 19 | GPIO14 | `M1`, `U1.1` | True-north input |
| 20 | 5V | `+5V` | Carrier feed to module 5V pin |

U4 is the side that begins with UART0 TX and ends with ground:

| U4 pin | Module pin | Carrier connection | Desk Buddy meaning |
| ---: | --- | --- | --- |
| 1 | GPIO43 / UART0 TX | Open | Used onboard by USB-UART serial |
| 2 | GPIO44 / UART0 RX | Open | Used onboard by USB-UART serial |
| 3 | GPIO1 | `CLK`, `BICEP1.1` | AS5600 analog output |
| 4 | GPIO2 | Open | Firmware does not use onboard LED |
| 5 | GPIO42 | `42`, LED2 anode | Second carrier LED, not driven |
| 6 | GPIO41 | `41`, LED1 anode | Firmware status LED |
| 7 | GPIO40 | Open | SD DATA onboard; unused by firmware |
| 8 | GPIO39 | `S3`, `ELBOW.1` | Firmware wrist servo |
| 9 | GPIO38 | Open | SD CMD onboard; unused by firmware |
| 10 | GPIO37 | Open | OPI PSRAM, reserved |
| 11 | GPIO36 | Open | OPI PSRAM, reserved |
| 12 | GPIO35 | Open | OPI PSRAM, reserved |
| 13 | GPIO0 | Open | Onboard BOOT button / connection reset |
| 14 | GPIO45 | `B1`, `BTNFING2.1` | Gripper stop 1 |
| 15 | GPIO48 | Open | Onboard WS2812, unused |
| 16 | GPIO47 | `S1`, `BASE.1` | Firmware elbow servo |
| 17 | GPIO21 | `S2`, `BICEP.1` | Firmware base servo |
| 18 | GPIO20 | `S5`, `GRIPPER.1` | Gripper servo; native USB D+ conflict |
| 19 | GPIO19 | `S4`, `TWIST.1` | Twist servo; native USB D- conflict |
| 20 | GND | Ground plane | Common logic/servo ground |

The pin numbering/orientation above is derived from the PCB netlist plus the
matching Freenove header layout. The reconstructed schematic retained the PCB's
connections and passed its graph validation, but no physical continuity test
was performed during this audit. Probe pin 1/orientation before using this as a
manufacturing test fixture.

## Carrier power path

The reconstructed PCB establishes this topology:

```text
PWR pin 1 (+V)
  +-- FH1 fuse holder --> +SV --> all five servo connector pin-2 rails
  +-- SS34 diode --> ESP32_+V --> ferrite bead --> net +5V --> U3 pin 20

PWR pin 2 (GND) ---------------------------------------> common ground
ESP32 U3 pin 1 (3V3) ----------------------------------> BICEP1 pin 2
```

The servo rail has shared decoupling capacitors. The ESP32 feed has its own
filtering. There is **no voltage regulator on the Desk Buddy carrier PCB**; any
regulation from the module's 5 V input to 3.3 V is on the ESP32 development
board itself.

The BOM lists a 6 V, 2 A adapter, while the PCB net after the diode/ferrite is
named `+5V`. Net labels do not guarantee an actual voltage, the installed fuse
rating is not documented, and the accepted carrier input-voltage range has not
been electrically validated. Verify the adapter polarity, module input limit,
fuse, and worst-case servo current on hardware. Five servos can exceed 2 A
during simultaneous startup or stall even if ordinary motion appears stable.

## USB, serial, programming, and reset behavior

The supported build disables USB CDC on boot, so the sketch's
`Serial.begin(115200)` uses UART0 on GPIO43/GPIO44 through the board's USB-UART
bridge. Studio's source calls that bridge CH340; Freenove's support repository
ships a CH343 driver. The exact bridge variant should be read from the physical
board, but either description means the same project rule: use the UART-labeled
programming connector.

The `firmware.ino` comment saying it waits one second for "USB CDC" is stale for
the Studio build profile; the one-second delay still gives the serial monitor
time to open. Serial is used for reset diagnostics, logs, a periodic alive
heartbeat, and newline-delimited JSON provisioning commands up to 512 bytes.

Native USB Serial/JTAG and USB-OTG share GPIO19/GPIO20. Those pins are actively
driven at servo PWM frequencies after the corresponding servos attach. Do not
expect native USB, native USB flashing, or USB JTAG to coexist with the current
twist/gripper wiring. GPIO39 and GPIO41 are also occupied members of the
traditional JTAG pin group, so external four-wire JTAG is not available without
remapping/disconnecting project hardware.

The Espressif chip datasheet is authoritative about polarity: GPIO19 is USB D-
and GPIO20 is USB D+. A Freenove pinout artwork version labels that pair in the
opposite order; do not copy the artwork's USB labels into a new circuit.

Holding GPIO0 LOW during reset selects ROM download behavior. Once normal
firmware is running, holding the BOOT button for three seconds clears only the
`wifi` and `mqtt` NVS namespaces, preserves calibration, and restarts. A
background FreeRTOS task samples the button every 10 ms so network blocking does
not hide the gesture.

## Status LED behavior

The firmware controls the carrier LED on GPIO41, not the vendor board's GPIO2
LED or GPIO48 WS2812. GPIO41 HIGH turns LED1 on; LOW turns it off.

| State | GPIO41 behavior |
| --- | --- |
| Wi-Fi disconnected/connection attempt | Off, then 0.5 s blink after Wi-Fi connects |
| MQTT connecting | 0.5 s blink |
| MQTT connected | On |
| Captive configuration AP | 2.0 s blink |
| BOOT button being held | 0.1 s blink |
| OTA download/write | 0.1 s blink |
| OTA success before reboot | On |
| OTA failure | Off |

Carrier LED2 is wired from GPIO42 through its own resistor to ground but no
current firmware code configures or drives it.

## Onboard features intentionally unavailable or unused

- **Native USB/OTG and USB Serial/JTAG:** unavailable because GPIO19/20 are
  servo outputs. Use the USB-UART port.
- **microSD:** not initialized, and GPIO39/SD_CLK is the wrist-servo signal.
  GPIO38/SD_CMD and GPIO40/SD_DATA remain unused.
- **WS2812 on GPIO48:** not initialized.
- **Onboard GPIO2 LED:** not initialized.
- **EN/RST, power, and UART-activity hardware:** EN/RST still resets the module;
  the power/TX/RX indicators are board hardware rather than Desk Buddy status
  outputs.
- **Bluetooth/BLE:** the ESP32-S3 supports it, but Desk Buddy uses Wi-Fi only.
- **General I2C/SPI buses:** the application does not call `Wire.begin()` or
  `SPI.begin()`. Camera SCCB is private to the camera driver.
- **Camera flash/illumination:** not controlled.
- **Deep sleep/battery monitoring:** not implemented.
- **FATFS space in the selected partition:** reserved by the partition scheme,
  but the current firmware does not mount or use it.

## Startup and lazy-initialization sequence

1. Start UART0 `Serial` at 115200 and wait one second.
2. Print reset reason, CPU frequency, heap, and brownout/watchdog diagnostics.
3. Check GPIO0 for the three-second connection reset and start its monitor task.
4. Attach the three arm servos and restore their saved positions.
5. Enter the loop: serial heartbeat/provisioning, Wi-Fi maintenance, MQTT
   maintenance, then MQTT message processing.
6. Initialize the gripper servo and GPIO45/46 only on the first gripper action.
7. Initialize the base servo, GPIO14 interrupt, and GPIO1 ADC only on the first
   base-rotation action.
8. Initialize the camera only on the first photo-family action.

This means GPIO45/46 do not receive their runtime internal pull-ups until the
first gripper action, and GPIO14 does not receive its pull-up until the first
base action. Boot strapping is sampled before any Arduino `pinMode()` call in
all cases, so external hardware must provide safe reset-time levels by design.

## Persistent state and network use

The ESP32's NVS is used under these namespaces:

| Namespace | Contents | Cleared by 3 s BOOT gesture? |
| --- | --- | --- |
| `wifi` | SSID and password | Yes |
| `mqtt` | Server, port, user, password, client ID, TLS flag | Yes |
| `config` | Arm/gripper positions and arm/stencil/perch calibration | No |
| `rot` | Base calibration, position, neutral, profile | No |
| `ota` | OTA state/version metadata | No |

With no saved Wi-Fi credentials, or after three failed station connections,
the firmware creates the password-protected `DeskBuddy` configuration access
point and captive portal. Studio can instead provision Wi-Fi and MQTT over the
UART USB connection. MQTT and HTTPS OTA can use TLS, but the current clients
call `setInsecure()` and do not verify server certificates.

See [SERIAL_PROVISIONING.md](SERIAL_PROVISIONING.md) for the serial messages and
[MQTT_SPEC.md](MQTT_SPEC.md) for the network contract.

## Known uncertainties and required physical checks

- Confirm the exact development-board vendor and revision. The repository says
  Freenove/Amazon and matches the Freenove pinout, but clone boards exist.
- Confirm the module marking is N16R8 before using the 16 MB build profile.
- Confirm the actual camera sensor marking, ribbon orientation, and 24-pin FPC
  contact order. The BOM says OV2640; firmware pin names alone do not prove it.
- Confirm U3/U4 pin-1 orientation by silkscreen or continuity before probing.
- Confirm the five three-pin servo headers' physical pin orientation.
- Confirm PWR polarity, input range, installed fuse rating, diode drop, and
  available current. PCB net names are not electrical ratings.
- Reconcile the BOM's KY-003 Hall module with the firmware's required AS5600
  analog encoder, and confirm which physical device drives GPIO14 true north.
- Confirm any replacement GPIO45/46 stop-switch circuitry remains passive to
  ground and does not drive the strapping pins HIGH during reset. The current
  PCB shows contacts to ground and no added pull resistors.
- Confirm the native USB port is not being used or externally driving
  GPIO19/20 while servos are connected.
- Run a physical camera/servo concurrency test. The code compiles and the
  ESP32-S3 camera clock does not consume LEDC, but this audit did not attach a
  board or scope PWM/XCLK signals.

## Sources used for this audit

Repository sources:

- [camera_pins.h](camera_pins.h) and [ActionPhoto.cpp](ActionPhoto.cpp)
- [firmware.ino](firmware.ino), [FactoryReset.cpp](FactoryReset.cpp), and
  [LED.cpp](LED.cpp)
- [ArmServos.cpp](ArmServos.cpp), [ActionGripper.cpp](ActionGripper.cpp), and
  [ActionBaseRotate.cpp](ActionBaseRotate.cpp)
- [Studio firmware command construction](../studio/services/firmware/commands.py)
- [Hardware BOM](../hardware/BOM.csv)
- [Reconstructed schematic notes](../hardware/PCB/DeskBuddy_Schematic_Import_Notes.md)
  and [validated net map](../hardware/PCB/DeskBuddy_Schematic_Validation.json)

External primary references:

- [Espressif ESP32-S3 datasheet](https://documentation.espressif.com/esp32_s3_datasheet_en.pdf)
- [Espressif ESP32-S3 hardware design checklist](https://docs.espressif.com/projects/esp-hardware-design-guidelines/en/latest/esp32s3/schematic-checklist.html)
- [Espressif camera driver](https://github.com/espressif/esp32-camera)
- [Freenove ESP32-S3 WROOM board pinout](https://github.com/Freenove/Freenove_ESP32_S3_WROOM_Board/blob/main/ESP32S3_Pinout.png)
