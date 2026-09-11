# Vendored Arduino libraries

These are the external libraries used by the Desk Buddy firmware. They are
kept here so Studio builds the same source and versions without depending on
libraries installed in a user's Arduino sketchbook.

| Library | Version | License | Upstream |
| --- | --- | --- | --- |
| ArduinoJson | 7.2.1 | MIT | https://github.com/bblanchon/ArduinoJson/tree/v7.2.1 |
| ESP32Servo | 3.0.6 | LGPL-2.1-or-later | https://github.com/madhephaestus/ESP32Servo/tree/3.0.6 |
| PubSubClient | 2.8 | MIT | https://github.com/knolleary/pubsubclient/tree/2.8 |

Each directory contains its Arduino metadata, source, and license. The
ESP32Servo release states its license in `README.md` but did not ship a
standalone license file, so the corresponding LGPL text is included as
`LICENSE.txt`.

The Espressif ESP32 Arduino core is still installed and managed as a board
platform; it is not copied here. Studio passes this directory to Arduino CLI
with `--libraries`, giving these copies precedence over sketchbook libraries.

## Local PubSubClient changes

The 2.8 copy includes a 32-bit MQTT Remaining Length encoder for streamed JPEGs,
preflight topic/buffer/protocol bounds, and `abortPublish()` to close an incomplete
packet without sending MQTT DISCONNECT. Short streamed headers also abort the
socket. `endPublish()` retains upstream behavior and is not a delivery receipt.
Run `python3 firmware/tests/run_host_tests.py` before replacing or updating this
copy; the tests compile this exact source, including payloads above 64 KiB.
