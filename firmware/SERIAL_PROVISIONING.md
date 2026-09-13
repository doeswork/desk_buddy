# USB serial provisioning

Desk Buddy Studio can replace the robot's saved Wi-Fi and MQTT credentials
over the same UART USB connection used to flash and monitor the
ESP32-S3-CAM.

Commands are UTF-8 JSON followed by a newline, at most 512 bytes.

## Wi-Fi

```json
{"desk_buddy_command":"set_wifi","ssid":"Network name","password":"secret123"}
```

`ssid` must be 1-32 bytes. `password` may be empty for an open network, an
8-63 byte passphrase, or a 64-digit hexadecimal PSK. After saving, firmware
responds without including the password:

```json
{"serial_provisioning":"wifi","status":"saved","ssid":"Network name"}
```

## MQTT

```json
{"desk_buddy_command":"set_mqtt","server":"192.168.1.50","port":18830,"user":"robot-1","password":"secret123","client_id":"robot-1","tls":false}
```

`server` is required; every other field is optional and only overwrites the
saved value when sent non-empty, so a partial update never blanks out a
credential it did not mean to touch — the same rule the DeskBuddy
access-point web form's MQTT section already follows. `port` defaults to
the firmware's own default when omitted or zero.

`tls` selects the transport and is the exception to the non-empty rule: it
is written whenever the key is present, since `false` is a meaningful value
and "absent" is the only way to mean "leave it alone". It defaults to `true`,
which the cloud broker on port 8883 requires. **Studio's own local broker
serves plaintext** — it has no certificates to offer, by design — so
provisioning a robot to point at it must send `"tls":false`. A TLS client
against a plaintext broker fails the handshake and reports `rc=-2`, which
looks identical to "broker unreachable".

After saving, firmware responds without including the password:

```json
{"serial_provisioning":"mqtt","status":"saved","server":"mqtt.deskbuddy.ai"}
```

## Complete profile

Studio's guided flash flow sends both namespaces together and firmware
restarts only after both writes verify:

```json
{"desk_buddy_command":"set_profile","wifi":{"ssid":"Network name","password":"wifi-password"},"mqtt":{"server":"192.168.1.50","port":1883,"user":"robot-1","password":"mqtt-password","client_id":"robot-1","tls":false}}
```

All fields are required and validated before either namespace changes. A
failure rolls back the other namespace. The acknowledgement contains only
the non-secret result:

```json
{"serial_provisioning":"profile","status":"saved","server":"192.168.1.50"}
```

## Common behavior

The three commands restart the robot on success and connect using the new
settings. Invalid commands receive a response with `status:"error"` and do
not restart. Normal Studio flashing preserves NVS (`EraseFlash=none`); using
the explicit **Erase first** option clears these saved settings, after which
Studio restores the selected profile over USB. The existing DeskBuddy
access-point web form remains available as the no-USB fallback.

Serial provisioning assumes physical access to the robot. Credentials travel
over the USB cable rather than over a network, but the serial link itself is
not encrypted.
