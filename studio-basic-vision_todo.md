# Studio Basic Vision TODO

This branch should deliver one understandable vision path:

```text
detect_object photo -> zero-shot boxes -> safe Studio preview
```

It should not import `bigger-rewrite-vision` wholesale. That branch mixes the
detector with depth, model training, a generic worker protocol, storage and
network rewrites, and a much larger UI. Reuse small, proven ideas from it while
building on the broker, accounts, robots, and shared MQTT client already on
`studio-basic-vision`.

## Decisions

- Backend Python belongs under `studio/services/vision/`.
- Vision UI belongs under `studio/ui/workspaces/vision/`.
- Model inference runs in a Studio-managed subprocess and isolated virtual
  environment. Torch and Transformers must not become Studio application or
  packaged-executable dependencies.
- Runtime images, requests, status, and results cross process boundaries only
  through MQTT. Studio may create the environment and launch the process, but
  it must not give the worker a Studio database connection or image file path.
- This slice is preview-only. A `use_model:true` request is rejected and no
  motion command is ever published.
- Nine-point planning, table-plane reach, and automatic grab remain the next
  safety milestone rather than being implied by installing a detector.

## Repository shape

- [x] Replace `studio/ui/workspaces/vision.py` with a package whose
  `__init__.py` re-exports `VisionWorkspace` from `workspace.py`.
- [x] Put the Models page in its own `models.py` module without changing the
  public workspace import.
- [x] Add the backend package for manifests, contracts, firmware photo
  decoding, managed-process lifecycle, and a standalone MQTT worker entry
  point.
- [x] Add a Detections page and queued Qt/MQTT signal bridge.
- [x] Keep the workspace class thin: it owns shared state and page navigation;
  pages render and invoke services.
- [ ] Add calibration, deterministic planning, and command sequencing only in
  the later physical-motion slice.

## Curated zero-shot catalog

Start with exactly two reviewed Hugging Face models. Do not allow arbitrary
model IDs, arbitrary Python entry points, `trust_remote_code`, or moving
revisions.

| Choice | Hugging Face source | Immutable revision | Approx. weights | License | Prompt/post-process defaults |
| --- | --- | --- | ---: | --- | --- |
| **OWLv2 Base (default)** | `google/owlv2-base-patch16` | `2a1560802f8cf3c408fec9b809d705f56a2f7146` | 620 MB | Apache-2.0 | Nested prompt list; box threshold `0.25` |
| Grounding DINO Tiny | `IDEA-Research/grounding-dino-tiny` | `a2bb814dd30d776dcf7e30523b00659f4f141c71` | 690 MB | Apache-2.0 | Lowercase, period-terminated prompt; box threshold `0.4`, text threshold `0.3` |

Both use `AutoProcessor` and `AutoModelForZeroShotObjectDetection`. Keep one
Hugging Face adapter and select its prompt/post-processing behavior from the
curated manifest. Every detection result must contain the exact model ID and
revision that produced it.

### One-click install and selection

- [x] Show a model picker with friendly name, provider, approximate download
  size, license, installed state, selected state, and running state.
- [x] Select OWLv2 Base for a fresh Studio profile.
- [x] For an uninstalled selection, show one primary action: **Install & Use**.
  It creates or repairs the isolated environment, downloads the pinned model,
  persists the selection, starts the worker, and waits for matching MQTT-ready
  status.
- [x] For an installed inactive selection, show **Use & Start**.
- [x] Keep at most one detector loaded. Download a candidate before stopping a
  working detector; if the candidate fails to advertise the expected model and
  revision, restart the previous detector and keep it selected.
- [x] Show download/setup progress and actionable errors in the Models page.
  Do not hide a long download behind an indeterminate disabled button.
- [x] Support Stop and model-specific Remove actions. Removal must stop that
  model first, ask for confirmation, and leave captures/calibrations untouched.
- [x] Unit tests fake downloads and model loading. Real weights are a
  manual deployment gate and are never downloaded in ordinary CI.

Pinned isolated worker requirements:

```text
paho-mqtt==2.1.0
Pillow==11.3.0
scipy==1.18.1
torch==2.11.0
transformers==4.57.6
```

Resolve the appropriate Torch wheel per platform. Prefer CUDA when available
and fall back to CPU. Do not claim a model is ready merely because its files
exist; readiness requires a retained MQTT status for the selected model and
exact revision.

## MQTT and service lifecycle

- [x] Reuse `studio.services.network` for the broker endpoint, account records,
  and selected robots. Do not copy the old branch's network implementation.
- [x] Create or reuse a dedicated `vision` account when Studio can manage the
  broker. Give it `vision/#` plus read/write access to each enabled
  `{robot}/test` topic, not unrestricted `#` access.
- [x] Pass the recorded account secret through the worker environment. Never
  put it in generated model manifests, logs, MQTT payloads, or diagnostics.
- [x] For a broker Studio cannot administer, show the missing account/topic
  requirements instead of silently borrowing Studio's credential.
- [x] Publish retained health to `vision/{service_id}/status` and configure an
  offline Last Will. Status contains a schema/version, service ID, ready/busy
  state, device, active model ID, immutable revision, and last error.
- [x] Extend the existing shared Studio MQTT client with
  `subscribe_raw(topic, callback)`. Preserve the current JSON-only
  `subscribe()` behavior so Calibration and Manual callers do not change.
- [x] Marshal all paho callbacks into Qt with queued signals before touching
  widgets.
- [x] Keep the worker runnable from a standalone Python entry point with
  broker/model configuration supplied through config plus environment secrets.
  Do not build remote deployment or headless Studio UI yet.

## Detection contracts

Reuse the useful shape of the old `DetectionV1`/`DetectionBatchV1` contracts,
not the generic DBV1 job system.

Each detection contains:

- Label and confidence in `[0, 1]`.
- Clamped `(x0, y0, x1, y1)` pixel and normalized boxes.
- Pixel and normalized centers plus normalized area.
- Model ID and immutable model revision.

A batch contains schema `detections.v1`, image width/height, prompt, every
detection, and model identity. Select the highest-confidence detection for
planning but return all boxes for the UI. A missing or invalid scalar prompt,
invalid JPEG, empty result, reversed box, non-finite score, or out-of-range
score is a terminal failure and must not move the robot.

## Firmware photo and `detect_object` flow

All robot commands and replies remain on `{robot}/test`.

- [x] Cache an incoming JSON `detect_object` request by `action_id`. Ignore
  messages sent by `firmware` or `visual_ai` when identifying new requests.
- [x] Decode the firmware's binary envelope by locating `,"payload":`, parsing
  only the restored JSON prefix, and validating JPEG SOI/EOI bytes.
- [x] Correlate the photo with the cached request and reject missing, reused,
  or mismatched action IDs.
- [x] Run the selected model using the request phrase and its manifest defaults.
  `model_name`, when supplied, must resolve to a curated installed model.
- [x] Publish ready/busy health around inference.
- [x] Publish one correlated terminal preview result with the detection batch,
  selected detection, exact model identity, `stage:"detection_only"`, and any
  error.

Compatibility behavior:

- `use_model:false` returns `stage:"detection_only"` and never moves.
- `use_model:true` returns `automatic_execution_not_available` in this slice.
- Studio exposes preview controls only and never publishes motion commands.

## Nine-point table calibration and planning

- [ ] Reuse `calibrate_depth` as the existing firmware photo trigger for visual
  calibration; it does not run a depth model in this milestone.
- [ ] Detect exactly nine stencil circles and assign them to columns
  `-30°/0°/+30°` and table distances `0/60/120 mm`.
- [ ] Reject missing, extra, overlapping, or degenerate point layouts.
- [ ] Persist the profile in worker-owned state by robot ID and publish the
  points/profile ID back over MQTT. Studio may save the returned summary, but
  the worker must not read Studio's storage directly.
- [ ] Display the returned points and grid overlay on the Visual Calibration
  page.
- [ ] Project from the selected detection's horizontal center and a point 10%
  of the box height above its bottom edge, which approximates table contact.
- [ ] Build the old branch's deterministic 5-degree by 5-mm grid and reject a
  reference point outside its calibrated polygon. Always produce
  `z_height_mm = 0`.

## Safe reach-and-grab sequencing

- [ ] Before motion, request `calibrationvalues` and require
  `motion_calibration_ready:true`.
- [ ] Validate finite values, rotation within `±45°`, distance within
  `0..180 mm`, a known calibration profile, and hardcoded `z_height:0` before
  publishing any motion command.
- [ ] Allow only one active sequence per robot.
- [ ] Send `baseRotate` with relative `DEGREES`, then `controlik` with
  `z_height:0`, then `gripper/GRAB`.
- [ ] Give every child command a unique `rg-...` action ID and wait for the
  exact firmware `completed` response before sending the next command.
- [ ] Abort on publish failure, firmware `failed`, correlation mismatch, or
  timeout. Never overlap commands and never retry an uncertain motion command.
- [ ] Publish a final `visual_ai` result with
  `stage:"reach_and_grab_completed"` only after the gripper reports success.

The initial operation assumes the robot is already in its calibrated camera
capture pose when `detect_object` is sent. Automatic positioning for a capture
is a later safety/design task.

## Vision workspace UI

### Models page

- [x] Implement the curated picker and Install & Use flow above.
- [x] Show process state and MQTT-reported state separately so a running but
  unready process is not presented as usable.
- [x] Expose Start, Stop, retry/repair, and confirmed removal without bringing
  back the old branch's depth, MLP, provider-import, or training panels.

### Detections page

- [x] Add image-source selection for a fresh robot capture or a local photo.
- [x] Add a robot picker using the existing robot records.
- [x] Add a required phrase field and model selection inherited from Models.
- [x] Provide **Preview Detection** as the only inference action.
- [x] Pass local images as in-memory JPEG bytes rather than filesystem paths.
- [x] Display the received photo, all boxes, the selected box, confidence,
  exact model/revision, and terminal outcome.
- [x] Disable preview unless the detector is MQTT-ready, an input is selected,
  a prompt is present, and no detection is already active.

## Automated verification

- [x] Manifest validation and exact default selection.
- [x] OWLv2 nested prompts and Grounding DINO lowercase/period prompts.
- [x] Mocked adapter output normalization, thresholds, multiple boxes, and no
  detections.
- [x] Valid and malformed firmware binary envelopes, including `}` bytes inside
  JPEG data.
- [x] Request/photo/result correlation and ignoring unrelated MQTT traffic.
- [ ] Repeatable synthetic nine-point calibration, boundary cells, degenerate
  layouts, and out-of-grid rejection.
- [ ] Readiness and numeric-limit failures publish no motion.
- [ ] Exact command order and acknowledgements, one active operation, failure
  aborts, and timeout without retry.
- [x] Dedicated-account constraints and credential redaction unit coverage.
- [x] Isolated-Mosquitto ACL integration, including denial of unrelated topics.
- [ ] Full install/select/start success, cancellation, candidate failure rollback,
  process crash, retained status, reconnect, and clean shutdown.
- [x] Raw/JSON subscriptions can share a topic, and Qt updates occur on the GUI
  thread.
- [x] Models and Detections page behavior plus the existing offscreen Studio
  smoke test.
- [ ] Isolated Mosquitto integration using a fake detector and fake firmware.
- [x] Worker zipapp generation, embedded resource discovery, and packaged-app
  bootstrap self-test using uv-managed Python.

Manual release gates:

- Install and infer with both pinned real models.
- Verify CUDA and CPU fallback behavior.
- Run a real ESP32 preview and inspect its overlay/target.
- Stop, restart, switch models, restart Studio, and remove one model without
  losing calibration data.

## Explicitly deferred

- Depth inference and every nonzero Z target.
- Custom MLP inference, datasets, review, training, and activation.
- OWLv2 ensemble and arbitrary/imported providers.
- Generic DBV1 worker/job/artifact routing.
- Remote deployment management and headless Studio.
- Automatic capture-pose movement and unsupervised physical validation.
- Nine-point table calibration, XY planning, and every physical grab command.
