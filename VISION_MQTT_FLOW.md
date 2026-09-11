# Desk Buddy MQTT Vision Flow

This document explains the MQTT-only photo path between Desk Buddy Studio, the
ESP32 robot firmware, and the Python Vision worker. It is both a current-state
reference and a checklist for the next round of protocol work.

The firmware source is authoritative if this document and the implementation
disagree. The main firmware contract is [firmware/MQTT_SPEC.md](firmware/MQTT_SPEC.md).

> Local reliability implementation: firmware transport and Studio/Vision request
> handling are updated. Sections 9–13 record the current limits, automated
> validation, and remaining physical ESP32 checks. Hardware speed has not yet
> been measured; passing host tests is not a hardware benchmark.

## 1. Short answer

The current photo path is:

```text
Studio Detections page
  -> {robot}/commands       JSON detect_object request
  -> ESP32 firmware         captures a JPEG
  -> {robot}/photos         raw binary JPEG frame
  -> Vision worker          validates and runs object detection
  -> {robot}/vision         JSON detection result
  -> Studio                 displays the original JPEG with local box overlays
```

The broker fans out the photo publication to both Studio and Vision. The Vision
worker does not currently send an annotated or otherwise updated photo back to
Studio. Studio receives the original photo directly and paints the detection
boxes locally.

This is already an MQTT-only design for the photo path. There is no HTTP hop
between the robot, Vision worker, and Studio in the current implementation.

## 2. Actors and deployment

### Studio

Studio is the Linux desktop/controller application. Its Vision workspace owns
the `VisionServiceManager`, which:

- launches the standalone Python detector worker as a local child process;
- connects the worker and Studio to the same MQTT broker;
- subscribes to the worker's status and result topics;
- subscribes to the selected robot's photo, event, and Vision topics while a
  preview is active;
- publishes the `detect_object` command to the robot;
- forwards MQTT-thread callbacks into the Qt GUI thread; and
- holds the current JPEG and detection result in memory for the preview UI.

The relevant implementation is [studio/services/vision/manager.py](studio/services/vision/manager.py),
with presentation in [studio/ui/workspaces/vision/detections.py](studio/ui/workspaces/vision/detections.py).

### Vision worker

The worker is a separate Python process started by Studio. It loads one pinned
zero-shot object detector and uses Paho MQTT directly. It is not a web server;
it is an MQTT service process.

The worker subscribes to:

- `vision/detector/request` for local-photo tests;
- each configured robot's `{robot}/commands` to observe detection requests;
- each configured robot's `{robot}/events` to observe correlated firmware
  failures; and
- each configured robot's `{robot}/photos` to consume raw JPEG frames.

It publishes:

- retained service health on `vision/detector/status`;
- local-photo results on `vision/detector/result`; and
- robot results on `{robot}/vision`.

The worker implementation is [studio/services/vision/worker_runtime/desk_buddy_vision_worker/service.py](studio/services/vision/worker_runtime/desk_buddy_vision_worker/service.py).

### ESP32 firmware

The ESP32 connects to the configured broker using its MQTT username as the
robot topic root. For a robot username of `black`, the firmware derives:

```text
black/commands
black/events
black/photos
black/vision
black/heartbeat
```

The firmware subscribes only to `black/commands`. It does not subscribe to
`black/events`, `black/photos`, or `black/vision`, so its own output and Vision
results cannot be mistaken for incoming robot commands.

### Broker

The broker is the message fan-out point. Studio, the Vision worker, and the
ESP32 each maintain their own MQTT connection. The broker does not invoke the
detector or transform the JPEG.

Studio and the Vision worker can connect to the broker using the local machine's
endpoint. The ESP32 must use a LAN-reachable or explicitly forwarded address;
`127.0.0.1` would point back to the ESP32 itself. WSL requires special care:
the WSL private NAT address is not automatically a usable robot endpoint.

## 3. Current end-to-end sequence

```mermaid
sequenceDiagram
    participant S as Studio
    participant B as MQTT broker
    participant F as ESP32 firmware
    participant V as Vision worker

    S->>B: Subscribe to robot photos/events/vision
    S->>B: Publish detect_object JSON to {robot}/commands
    B->>F: Deliver command to firmware subscription
    B->>V: Deliver command to Vision command route

    F->>B: in_progress JSON on {robot}/events
    B-->>S: in_progress
    B-->>V: in_progress (ignored except failures)

    F->>F: Capture fresh JPEG
    F->>B: Binary photo frame on {robot}/photos
    B-->>S: Same raw JPEG frame
    B-->>V: Same raw JPEG frame

    V->>V: Decode, validate, and run detector
    F->>B: completed JSON on {robot}/events
    B-->>S: completed event; Studio continues waiting for Vision result

    V->>B: Detection JSON on {robot}/vision
    B-->>S: Detection JSON
    S->>S: Paint boxes over the original JPEG
```

The photo and Vision messages are correlated by the same `action_id` created by
Studio. Consumers correlate by topic, sender, status, and `action_id`. Studio tolerates
both photo-before-result and result-before-photo delivery and keeps the request
open until both a successful result and its original photo are present.

## 4. Topic ownership and payload types

`{robot}` is the robot's MQTT topic root, normally the same value as the
firmware MQTT username and Studio's configured robot account name.

| Topic | Publisher | Current subscribers | Payload | Current transport |
| --- | --- | --- | --- | --- |
| `{robot}/commands` | Studio or another authorized controller | ESP32; Vision observes `detect_object` requests | JSON command | Studio publishes QoS 1; firmware subscribes QoS 0; non-retained |
| `{robot}/events` | ESP32 firmware | Studio, Vision, authorized controllers | JSON lifecycle/diagnostic event | Firmware QoS 0; non-retained |
| `{robot}/photos` | ESP32 firmware | Studio and Vision | `desk_buddy.photo.v1` binary JPEG frame | Firmware QoS 0; non-retained |
| `{robot}/vision` | Vision service | Studio and authorized controllers | Correlated JSON inference result | Vision QoS 1; non-retained |
| `{robot}/heartbeat` | ESP32 firmware | Studio and monitoring services | JSON telemetry | Firmware QoS 0; non-retained |
| `vision/detector/status` | Vision worker | Studio Vision manager | JSON service status | QoS 1; retained; Last Will publishes offline |
| `vision/detector/request` | Studio | Vision worker | `desk_buddy.vision.detect-request.v1` binary JPEG frame | QoS 1; non-retained |
| `vision/detector/result` | Vision worker | Studio Vision manager | JSON local-photo inference result | QoS 1; non-retained |

The robot's `vision` topic is output-only from the firmware's point of view.
The firmware neither publishes nor subscribes to it. The detailed firmware
topic contract is in [firmware/MQTT_SPEC.md](firmware/MQTT_SPEC.md#1-connection-and-topics).

## 5. Message lifecycle

### 5.1 Studio request

For a robot preview, Studio creates a UUID-like `action_id`, waits for subscription acknowledgements before
publishing, and sends a compact JSON command similar to:

```json
{
  "sender": "studio",
  "action_id": "f0b3113be68a49c3b14123edf7aff570",
  "action": "detect_object",
  "phrase": "glasses",
  "use_model": false,
  "model_name": "google/owlv2-base-patch16"
}
```

The command is published to `{robot}/commands`. `phrase` is the object prompt.
`use_model:false` deliberately selects the current detection-only milestone;
automatic reach-and-grab is not enabled by the basic Studio Vision worker.

The firmware command parser requires a nonempty `sender`, `action_id`, and
recognized `action`. Malformed commands, allocation failures, and commands
missing those fields may produce only serial diagnostics and no MQTT terminal result. Vendored ArduinoJson
7.2.1 dynamically allocates its documents: the legacy `DynamicJsonDocument(512)`
argument is not a 512-byte parsing limit. Studio checks the actual encoded robot
command against the 6,144-byte MQTT receive buffer, including topic and maximum
header overhead. There is no arbitrary character-count cap; Unicode escaping
counts toward the actual encoded size.

### 5.2 Firmware lifecycle event

For a recognized photo action, firmware first sends an event similar to:

```json
{
  "sender": "firmware",
  "action_id": "f0b3113be68a49c3b14123edf7aff570",
  "status": "in_progress",
  "type": "detect_object"
}
```

On a successful capture and publication, firmware later sends:

```json
{
  "sender": "firmware",
  "action_id": "f0b3113be68a49c3b14123edf7aff570",
  "status": "completed",
  "type": "detect_object"
}
```

Camera, Wi-Fi, capture, or MQTT publication failures use `status:"failed"`
and a structured error object when the connection remains usable. An aborted
stream closes the socket, so that failure may only be logged on serial. No photo is published if capture fails.

### 5.3 Binary photo frame

The photo is deliberately not Base64 and is not valid JSON as a whole. Its
wire shape is:

```text
<JSON metadata object with its final `}` removed>,"payload":<raw JPEG bytes>}
```

The metadata uses the `desk_buddy.photo.v1` schema and contains fields such as:

```json
{
  "schema": "desk_buddy.photo.v1",
  "sender": "firmware",
  "action_id": "f0b3113be68a49c3b14123edf7aff570",
  "type": "detect_object",
  "photo": "sending_photo",
  "content_type": "image/jpeg",
  "width": 800,
  "height": 600,
  "size": 117057
}
```

The actual MQTT payload continues after the metadata with the raw JPEG bytes
and ends with the envelope's final `}`. JPEG bytes may themselves contain a
`}`; decoders must use the final envelope byte and the exact marker consisting
of `,` followed by `"payload":`, rather than searching for a JSON closing brace
inside the image.

The decoder must:

1. Reject an oversized or non-byte payload.
2. Locate the exact marker consisting of `,` followed by `"payload":`.
3. Restore the metadata's closing `}` and parse only that prefix as JSON.
4. Treat the bytes between the marker and final envelope `}` as the JPEG.
5. Validate JPEG SOI `FF D8` and EOI `FF D9`.
6. Validate the photo schema, sender, media type, dimensions, action type, and
   metadata `size` against the extracted JPEG length.

The shared implementation is re-exported by [studio/services/vision/frames.py](studio/services/vision/frames.py)
and implemented in the worker runtime's `frames.py`.

The ESP32 camera currently tries SVGA, VGA, then QVGA at JPEG quality 20, uses
one framebuffer, prefers PSRAM, flushes one frame, and retries capture once
after camera reinitialization. Firmware streams the frame in chunks rather than
building a Base64 string in memory.

### 5.4 Vision result

For a successful robot detection, the worker publishes JSON on
`{robot}/vision` similar to:

```json
{
  "sender": "visual_ai",
  "action_id": "f0b3113be68a49c3b14123edf7aff570",
  "status": "completed",
  "type": "detect_object",
  "stage": "detection_only",
  "detection_batch": {
    "schema": "detections.v1",
    "image_width": 825,
    "image_height": 1243,
    "prompt": "glasses",
    "detections": [
      {
        "label": "glasses",
        "score": 0.70,
        "box_px": [137.3, 452.2, 612.9, 601.9],
        "box_normalized": [0.17, 0.36, 0.74, 0.48],
        "center_px": [375.1, 527.0],
        "center_normalized": [0.45, 0.42],
        "area_normalized": 0.07,
        "model_id": "google/owlv2-base-patch16",
        "revision": "2a1560802f8cf3c408fec9b809d705f56a2f7146"
      }
    ]
  },
  "selected_detection": {
    "label": "glasses",
    "score": 0.70,
    "box_px": [137.3, 452.2, 612.9, 601.9],
    "box_normalized": [0.17, 0.36, 0.74, 0.48],
    "center_px": [375.1, 527.0],
    "center_normalized": [0.45, 0.42],
    "area_normalized": 0.07,
    "model_id": "google/owlv2-base-patch16",
    "revision": "2a1560802f8cf3c408fec9b809d705f56a2f7146"
  },
  "model_id": "google/owlv2-base-patch16",
  "catalog_id": "owlv2-base",
  "revision": "2a1560802f8cf3c408fec9b809d705f56a2f7146"
}
```

The real `selected_detection` repeats the highest-confidence detection. The
worker sorts detections by descending score and normalizes/clamps boxes before
publishing them. (The `image_width`/`image_height` values above are
illustrative — real frames carry the captured SVGA/VGA/QVGA dimensions from
§5.3, not fixed constants.)

Failures are also correlated JSON results, for example:

```json
{
  "sender": "visual_ai",
  "action_id": "f0b3113be68a49c3b14123edf7aff570",
  "status": "failed",
  "type": "detect_object",
  "stage": "detection_only",
  "error": {
    "code": "no_detection",
    "message": "No 'glasses' detection met the confidence threshold."
  },
  "model_id": "google/owlv2-base-patch16",
  "revision": "2a1560802f8cf3c408fec9b809d705f56a2f7146"
}
```

The worker includes the exact model source and immutable revision so a result
can be reproduced and audited.

### 5.5 Studio display

Studio's Vision manager accepts a photo only when:

- the message arrived on the pending robot's photo topic;
- the binary frame validates; and
- the frame `action_id` matches the pending request.

It stores the JPEG and emits `photo_received`. A matching successful `visual_ai`
result is held until the photo is present, then emitted through `detection_result`.
A matching failure is immediately terminal. Starting a robot request clears the
previous preview image so old pixels cannot appear under new result boxes.

The UI then paints the original JPEG and maps `box_px` coordinates into the
letterboxed preview widget. This is a local display overlay, not a new image
publication.

## 6. Correlation and timing rules

`action_id` is the transaction key for the complete photo operation:

```text
Studio command action_id
  = firmware in_progress action_id
  = photo metadata action_id
  = firmware terminal event action_id
  = Vision terminal result action_id
```

Studio registers callbacks, reconciles its MQTT connection, then waits
asynchronously for successful SUBACKs on the photo, event, and result topics.
Local-photo requests wait for the result subscription. Preparation fails visibly
after 10 seconds. Worker readiness also requires all of its subscription
acknowledgements plus the loaded model; reconnects repeat that check.

| Situation | Current behavior |
| --- | --- |
| Matching photo | Validated and queued once; Studio displays it immediately |
| Matching firmware failure | Atomically terminates the worker request, including queued/running work; Studio shows failure |
| Invalid frame with recoverable matching ID | `invalid_image` for that request |
| Uncorrelatable malformed frame or unmatched ID | Ignored; never assigned to another request |
| Repeated command or photo | Ignored for an outstanding or recently terminal ID |
| Request/source limit or image queue full | Correlated `detector_busy` |
| Waiting for photo past 120 seconds | `photo_timeout` |
| Queued/running past 120 seconds | `detection_timeout`; late inference output suppressed |
| Successful result reaches Studio before photo | Wait for photo; outer timeout reports incomplete preview |
| MQTT connection loss detected during request | Operation fails; reconnect does not trigger an application capture retry |
| Explicit user retry | New action ID, using the existing preview action |
| No detections / detector exception | `no_detection` / `inference_failed` |

Worker records are keyed by `(result_topic, action_id)`, which isolates robot
roots and local-photo results. One lock protects admission and completion across
the network, inference, and expiry threads. There is at most one outstanding
request per robot, one local-photo request, and eight requests total. One
inference thread processes images, with one additional queued image. Expired
inference may still finish computationally, but its result is suppressed. The
terminal-ID cache holds at most 256 entries for up to 120 seconds; it is not a
durable exactly-once guarantee across process restarts or cache eviction.

Studio permits one outstanding preview. It displays preparation, waiting for
camera, capturing, photo received/waiting for detection, or result received/waiting
for photo. At 30 seconds it adds a slow-operation hint; the outer 120-second
budget starts with preparation. Worker expiry starts at request receipt.

The firmware still has one receive slot and synchronous actions. Stateful robot
commands must not overlap. Worker deduplication does not add firmware command
deduplication. MQTT QoS 1 may redeliver a command/result at the protocol layer;
application code never automatically republishes a timed-out capture.

## 7. Local-photo path

Studio also supports testing the detector without a robot:

```text
Studio local JPEG
  -> vision/detector/request   binary detect-request.v1 frame
  -> Vision worker             local decode and inference
  -> vision/detector/result    JSON result
  -> Studio                    original local JPEG + local overlay
```

This path does not use robot topics, firmware, or `{robot}/vision`. It is useful
for testing model behavior independently from camera and broker connectivity.

## 8. Legacy calibration and external Vision behavior

The older `calibration_tool` is a separate client and should not be confused
with the current basic Studio Vision worker.

The legacy tool subscribes to robot events, photos, Vision results, and
heartbeat topics. It expects an external `visual_ai` service to handle
`calibrate_depth` and automatic reach-and-grab workflows. It saves the raw
photo locally and waits for the corresponding Vision result.

The current firmware still accepts `calibrate_depth` as a camera action, but
the ESP32 does not run a depth model. The current
`studio/services/vision` worker only accepts `detect_object` robot requests.
Therefore:

- `calibrate_depth` currently means “capture a photo for a downstream consumer”;
- no depth inference or depth result contract is implemented in the basic
  Vision worker; and
- the legacy external-service path remains future or separate functionality.

## 9. Implemented local reliability changes

- Fixed the vendored PubSubClient MQTT Remaining Length encoder to use 32 bits.
  Before the fix, 117,071 was encoded as 51,535; Remaining Length 65,536 became
  zero. Header-size and MQTT maximum-length checks run before sending bytes.
- Preserved streamed raw JPEGs, QoS 0 photos, topic separation, SVGA → VGA →
  QVGA fallback, JPEG quality 20, and one framebuffer. Camera configuration is
  zero-initialized and framebuffer ownership is scoped through publication.
- Stream writes use 4 KiB chunks and one shared budget for header, JSON prefix,
  JPEG, and suffix: 2 seconds without progress and 15 seconds overall. Plaintext
  streamed socket writes are nonblocking so the core's internal retry loop cannot
  bypass the budget. TLS uses the configured socket timeout; the application
  checks deadlines between socket calls, not by preempting a blocked call.
- An incomplete header or payload closes the underlying transport immediately.
  No DISCONNECT packet, heartbeat, or failure event is appended to a partial
  packet. Existing MQTT maintenance reconnects afterward. A failed stream can
  therefore have only serial diagnostics; Studio/Vision handle the missing photo
  via connection state or timeout.
- `endPublish()` remains an upstream no-op, not an acknowledgement or stream
  repair mechanism. Success means bytes were written locally, not delivery.
- Serial `[photo]` diagnostics report action ID, capture/publication durations,
  JPEG bytes, internal free heap, PSRAM, elapsed time, and capture/publication
  stage. Internal memory is measured with `MALLOC_CAP_INTERNAL`, separately from
  PSRAM.
- Studio skips JSON decoding when a topic has only raw subscribers. Mixed
  raw/JSON subscribers still both receive their appropriate callbacks.
- Subscription readiness, bounded lifecycle state, deduplication, deadlines,
  and order-independent preview pairing now follow section 6.

## 10. Current limits

| Layer | Setting / behavior |
| --- | --- |
| Firmware normal MQTT buffer | 6,144 bytes; streamed photos do not require a frame-sized MQTT buffer |
| Studio robot request preflight | JSON bytes + UTF-8 topic bytes + 7 bytes reserved overhead ≤ 6,144 |
| Photo envelope | At most 8 MiB, enforced in firmware and Python |
| Stream chunk | 4 KiB |
| Stream retry budgets | 2 s no progress; 15 s total, checked between calls |
| Configured transport timeout | 2,000 ms |
| MQTT socket read timeout | 2 s |
| MQTT keepalive | 30 s |
| Reconnect scheduling | Firmware existing 5 s interval |
| Subscription preparation | Studio 10 s; worker startup subscription wait 10 s |
| Preview outer timeout / hint | 120 s / 30 s |
| Worker capacity | 8 outstanding total, 1 per robot, 1 local, 1 running + 1 queued image |
| Recent terminal IDs | 256 entries, 120 s TTL, memory only |
| Integration-test broker `max_packet_size` | 8,389,632 bytes (8 MiB + 1 KiB MQTT overhead allowance) |
| Actual deployed broker limit | Not measured or changed; existing configuration is preserved |

The ESP32 is a LAN client of the broker, not a client of `127.0.0.1`. Existing
local plaintext/TLS selection and WSL addressing behavior are unchanged.

The raw JPEG still fans out to both Studio and Vision. The separate wildcard
traffic recorder also receives and stores binaries; its storage policy is deferred.

## 11. Deferred work

TLS certificate verification, directional broker ACLs, depth inference,
automatic reach-and-grab orchestration, derived image artifacts, concurrent
inference, and debug-history storage changes are outside this local milestone.
Existing sender strings are correlation/loopback fields, not authentication.
Keep the detection-only `use_model:false` boundary and avoid overlapping motion
commands. No new security gate was added to local preview.

Replacing PubSubClient or introducing chunk reassembly is unnecessary for this
milestone. Subscriber QoS cannot upgrade the firmware's QoS 0 publication.
Manual capture retry remains available; automatic application retry is disabled.

## 12. Physical ESP32 acceptance run (still required)

1. Build/flash using Studio's existing ESP32-S3 N16R8 board options and the
   vendored libraries. Confirm the board uses the LAN-reachable local broker.
2. Start serial logging. Run 30 sequential capture-only previews, waiting for
   each operation to finish before issuing the next. Keep resolution/quality
   unchanged and include a detailed scene producing a JPEG larger than 64 KiB.
3. Record `[photo]` diagnostics for every action ID. Compare median/p95 capture
   and publication time, JPEG size, failures, internal heap, and PSRAM. Report
   inference duration separately; do not attribute model time to the ESP32.
4. Interrupt the broker or Wi-Fi during publication. Confirm the request fails
   visibly, no automatic capture occurs, and a new manual request succeeds after
   reconnect. Check that framebuffer/heap usage recovers across repetitions.

No physical board was attached during implementation. These checks and any
claim of actual capture-speed improvement remain unverified.

## 13. Executable validation

Run from the repository root with Studio's Python dependencies installed:

```bash
python3 firmware/tests/run_host_tests.py
QT_QPA_PLATFORM=offscreen venv/bin/python -m studio.services.vision.tests
QT_QPA_PLATFORM=offscreen venv/bin/python -m studio.services.vision.reliability_tests
QT_QPA_PLATFORM=offscreen venv/bin/python -m studio.ui.workspaces.vision.tests
QT_QPA_PLATFORM=offscreen venv/bin/python -m studio.services.network.tests.tests
```

The host test compiles the actual vendored MQTT client with address/undefined
behavior sanitizers and a deterministic socket. It covers the 64 KiB boundary,
117 KB and larger payloads, header bounds/partial headers, partial/zero writes,
total timeout, clock rollover, connection loss, framebuffer return on successful/failed
publication, and subsequent publication.

The reliability suite uses fake inference and an isolated, unprivileged Mosquitto
broker with temporary configuration. It verifies subscription gating, byte-exact
photo fan-out, correlated completion, broker restart and explicit retry, plus
unit coverage for bounded state, stale/invalid frames, deduplication, and
completion/timeout races. It never downloads model weights.

The old service fixtures used `black/test` with incomplete photo metadata; they
now use the current command/event/photo/Vision routes. The existing environment
has PySide6, so the earlier missing-GUI-dependency limitation no longer applies.

Implementation validation results:

- Firmware host transport tests: passed with ASan/UBSan, including framebuffer
  ownership on success and failed publication.
- Vision service: 34 passed; capture reliability: 12 passed (including broker
  restart/fan-out); Vision UI: 8 passed; broker finder: 33 passed; firmware
  command construction: 6 passed.
- Studio smoke test: passed across 5 workspaces and 16 pages.
- Full firmware compile: passed with Espressif Arduino core 3.3.11 and the
  existing `build_command()` ESP32-S3 N16R8 options. Program storage: 1,305,566
  bytes (41%); static data: 61,284 bytes (18%). These are build sizes, not runtime
  free-heap measurements. Arduino CLI/core were installed in a temporary build
  directory; the board was not flashed.
- Physical 30-capture benchmark and interrupted on-board publication: not run,
  because no ESP32 serial device was attached. No physical speedup is claimed.
