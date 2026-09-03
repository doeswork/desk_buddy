# Studio Vision Services

`studio/services/vision` is the authoritative vision implementation. Studio owns
the database, operator workflow, safety validation, and firmware publishing.
Model workers own only model loading and compute. All runtime data crosses the
MQTT topics under `desk_buddy/vision/worker/{worker_id}`; no worker receives a
Studio path or database connection.

## Processes

Run these from the repository root, each in its own virtual environment:

```bash
zero-shot-env/bin/python -m studio.services.vision.zero_shot.worker_app --config zero-shot.toml
zero-shot-ensemble-env/bin/python -m studio.services.vision.zero_shot.worker_app --config zero-shot-ensemble.toml
depth-env/bin/python -m studio.services.vision.depth.worker_app --config depth.toml
training-env/bin/python -m studio.services.vision.model_builder.training_worker --config training.toml
inference-env/bin/python -m studio.services.vision.model_builder.inference_worker --config inference.toml
```

Copy the adjacent `*.example.toml` files and set broker credentials through the
environment variables named in each file. The model revisions in production
configs should be immutable commits, not `main`. A remote worker uses the same
configuration and topics; Studio does not need its hostname or filesystem.

To create five isolated environments locally (including a separate process for
the optional OWLv2 ensemble):

```bash
studio/services/vision/setup_worker_envs.sh .vision-envs
```

The setup intentionally installs Torch/Transformers only in worker environments.
They are not Studio dependencies. Model weights must be downloaded or installed
in the corresponding worker environment before starting a config whose
`local_files_only` value is true.

Inspect configured source/revision/license/size/device metadata, then explicitly
install a pinned snapshot with:

```bash
zero-shot-env/bin/python -m studio.services.vision.install_model --config zero-shot.toml --model-id owlv2-base
zero-shot-env/bin/python -m studio.services.vision.install_model --config zero-shot.toml --model-id owlv2-base --install --accept-license
zero-shot-ensemble-env/bin/python -m studio.services.vision.install_model --config zero-shot-ensemble.toml --model-id owlv2-base-ensemble --install --accept-license
```

## Studio configuration

Studio's Vision page connects on demand. Defaults target a local broker on port
`18830`, robot `esp32_5`, legacy `google/owlv2-base-patch16`, and Depth
Anything V2 Small. The ensemble OWLv2 model is also cataloged on its own worker
route.

The page is organized as Services, Capture, Results, and Model Builder. Services
contains separate Detection, Depth, and Custom MLP tabs. A built-in or imported
provider is only a candidate until **Use & Start** sees an exact retained MQTT
status for its model ID, immutable revision, and capability. Switching failure
stops the candidate and restores the previous local service when possible.

**Download** creates the family virtual environment and pinned model snapshot.
**Repair** rebuilds them through staging, **Remove Download** removes only that
model/revision cache, and **Reset Runtime** removes only that family virtual
environment. Captures, SQLite records, and Studio-trained checkpoints live
outside the managed service directory and are not removed by these actions.
Managed children stop with Studio; **Start with Studio** relaunches them only
after Studio has connected to the configured broker.

Managed files are under Qt's application-data directory in
`vision/services/{manifests,runtimes,models,configs,logs,markers}`. Generated
broker secrets are kept in a mode-0600 file there and are injected into child
environments. They are never written into generated TOML files, logs, MQTT jobs,
or artifacts. Use **Start Broker & Set Up Vision Access** (or **Set Up/Repair
All**) on Network to create the controller, selected detector, selected depth,
MLP inference, and MLP trainer identities in one action. Existing passwords are
reused and are never silently rotated. Network and each Vision service expose
the managed username/password, copyable worker setup, and confirmed Rotate and
Revoke actions. Inactive provider identities remain listed on Network until
explicitly revoked.

When Studio owns the broker it creates one topic-restricted identity per worker
and a controller identity. Workers can use only their directed request,
artifact, event, and status topics; they cannot publish firmware commands.
Studio only mutates its broker at `127.0.0.1:18830`. An external broker is never
modified and requires credentials supplied by its operator.

Provider choices made in the page are saved; these environment values take
precedence over saved choices:

```text
DESK_BUDDY_VISION_MQTT_HOST
DESK_BUDDY_VISION_MQTT_PORT
DESK_BUDDY_STUDIO_MQTT_USERNAME
DESK_BUDDY_STUDIO_MQTT_PASSWORD
DESK_BUDDY_ROBOT_ID
DESK_BUDDY_ROBOT_TOPIC
DESK_BUDDY_DETECTOR_MODEL
DESK_BUDDY_DEPTH_MODEL
DESK_BUDDY_DETECTOR_WORKER
DESK_BUDDY_DEPTH_WORKER
DESK_BUDDY_TRAINER_WORKER
DESK_BUDDY_INFERENCE_WORKER
DESK_BUDDY_VISION_CALIBRATION
DESK_BUDDY_VISION_LOCAL_WORKERS
```

Managed worker credentials for an external broker use the worker-specific form
`DESK_BUDDY_MANAGED_<WORKER_ID>_MQTT_USERNAME` and
`DESK_BUDDY_MANAGED_<WORKER_ID>_MQTT_PASSWORD`, with punctuation converted to
underscores and the ID uppercased. The Studio controller uses the two
`DESK_BUDDY_STUDIO_MQTT_*` values above.

`DESK_BUDDY_VISION_LOCAL_WORKERS` may point at a copy of
`vision_controller/local_workers.example.toml`. Starting those commands is only
a convenience: the launched processes still communicate exclusively over MQTT.
They appear separately from managed services and do not gain install or removal
controls.

## Imported provider manifests

Detection and Depth accept a versioned TOML provider manifest. Imports may name
only an allowlisted adapter and dependency profile; Studio supplies the worker
entry point and package set. Full immutable Hugging Face commit IDs are required.
Shell commands, arbitrary Python entry points, credentials, executable code,
moving revisions, unsupported schemas, and `trust_remote_code` are rejected.

```toml
[provider]
schema = "desk_buddy.vision.provider-manifest.v1"
family = "detection"
model_id = "my-pinned-detector"
worker_id = "my-detector-1"
adapter_id = "huggingface-zero-shot.v1"
dependency_profile = "zero-shot-hf"
provider = "My organization"
source = "organization/model"
revision = "0123456789abcdef0123456789abcdef01234567"
license = "Apache-2.0"
size_mb = 620
devices = ["cpu", "cuda"]
schemas = ["image.jpeg.v1", "detections.v1"]
```

## Current executable strategy

Nine-point deterministic IK is the only executable strategy in this revision.
It requires and submits only `zero_shot.infer`; Depth and Custom MLP may be
offline. Detector-only captures retain their image, boxes, selected object,
provider version, and deterministic target, but are not eligible for the
depth-dependent MLP dataset. Model Builder and its isolated runtime are visible
as a preview; training, model loading, activation, and learned movement are
disabled. Legacy learned requests fail explicitly and never fall back to
deterministic robot movement.

## Safety and cutover

- Use Network's managed Vision access controls to create worker accounts. The
  generated ACL grants only that worker's five directed topics and cannot
  publish robot firmware topics.
- Give only Studio's broker identity permission to publish robot commands.
- The future learned path remains inactive and never falls back silently to
  deterministic movement.
- Physical commands use QoS 0, wait for the exact firmware `action_id`, and are
  never retried after an uncertain result.
- Do not run the old and new vision command publishers against one robot at the
  same time.

## Verification

```bash
venv/bin/python -m studio.services.vision.tests
venv/bin/python -m studio.services.vision.managed_tests
QT_QPA_PLATFORM=offscreen venv/bin/python -m studio.smoke_test
```

The real-model and robot checks remain deployment gates: use the Vision page to
install a pinned OWLv2 snapshot, start and health-check it, run a real ESP32
photo, supervise deterministic motion, restart it, verify start-with-Studio, and
remove the model snapshot. Keep `/home/jeffy4080/robots/vision` as a reference
until the later composed depth/MLP workflow is enabled and validated.
