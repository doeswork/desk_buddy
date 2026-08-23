# Desk Buddy Vision

Desk Buddy Vision is the MQTT-only companion-computer pipeline for the ESP32 Desk Buddy robot arm. It saves camera images, runs selectable zero-shot detector and depth models, applies a deterministic nine-point calibration, optionally asks a trained residual model for bounded corrections, and sends the same compact firmware commands regardless of planner source.

There is no HTTP server, REST callback, Django application, Docker runtime, shared model environment, or cross-service filesystem handoff in this project.

## Services

The initial deployment runs four native processes on one computer:

```text
GUI + ESP32 <-- MQTT --> vision coordinator
                              |
                              +-- MQTT --> detector worker
                              +-- MQTT --> depth worker
                              +-- MQTT --> planner/training worker
```

- `coordinator/` owns GUI and firmware communication, SQLite metadata, permanent artifacts, calibration, features, safety, motion plans, and command sequencing. It does not require Torch or Transformers.
- `services/detector_huggingface/` owns OWLv2 and compatible zero-shot detector dependencies.
- `services/depth_huggingface/` owns Depth Anything and compatible depth dependencies.
- `services/planner_training/` trains and serves a small residual correction model. It never emits robot commands.
- `protocol/` is the dependency-light contract package installed in every environment.

Each worker has a separate virtual environment and communicates only through MQTT. A worker can move to another broker-connected host without changing GUI requests, operation logic, artifact IDs, or result schemas.

Model-native details stop at the worker boundary. Detector workers publish standardized boxes, labels, and scores; depth workers publish standardized artifacts plus their depth direction, and the coordinator converts every map to the stable `features.v1` convention where larger values mean nearer objects.

## Initial Setup

Copy and edit each example configuration:

```bash
cp coordinator/config.example.toml coordinator/config.toml
cp services/detector_huggingface/config.example.toml services/detector_huggingface/config.toml
cp services/depth_huggingface/config.example.toml services/depth_huggingface/config.toml
cp services/planner_training/config.example.toml services/planner_training/config.toml
```

Broker credentials are read from environment variables, not TOML. Copy `.env.example` to `.env` for the bundled systemd units and assign a different broker account to each service:

```bash
cp .env.example .env
# Edit .env with coordinator, detector, depth, and planner credentials.
```

The separate identities are what make the documented topic ACLs enforceable; do not give workers the coordinator's firmware-topic permissions.

Create the isolated environments and install their packages:

```bash
./deploy/setup_envs.sh
```

On a worker-only host, create only what that host runs, for example `./deploy/setup_envs.sh depth`. Multiple names such as `detector depth` are accepted; running the script without names creates all four environments.

The detector and depth examples default to `local_files_only = true`. Put model snapshots in the configured Hugging Face cache before starting production workers. To populate an empty cache deliberately, set `local_files_only = false` for a supervised startup; models preload before the worker advertises readiness, so downloads never begin in response to a robot operation.

## Running Locally

Run each process in its own terminal:

```bash
./deploy/run_coordinator.sh
./deploy/run_detector_huggingface.sh
./deploy/run_depth_huggingface.sh
./deploy/run_planner_training.sh
```

All scripts accept an explicit configuration as their first argument. The systemd units under `deploy/systemd/` use the same scripts and environment boundaries.

## MQTT API

The GUI sends requests to:

```text
desk_buddy/{robot_id}/vision/request
```

Example non-moving detection request:

```json
{
  "schema": "desk_buddy.vision.v1",
  "request_id": "0198f99e-detect-1",
  "robot_id": "esp32_5",
  "sender": "desk_buddy_gui",
  "kind": "detect",
  "created_at": "2026-08-22T20:00:00Z",
  "payload": {
    "phrase": "blue block",
    "detector_model": "owlv2-base",
    "depth_model": "depth-anything-v2-small",
    "planner": "deterministic",
    "execute": false,
    "save_artifacts": true
  }
}
```

Progress and terminal results are published to `desk_buddy/{robot_id}/vision/event`. Large artifacts are requested by ID and transferred as indexed `DBV1` binary frames. The complete topic, envelope, QoS, artifact, and firmware rules are in [docs/MQTT_CONTRACT.md](docs/MQTT_CONTRACT.md).

`robot_id` controls the GUI-facing namespace; the coordinator's `robots[].topic` is configured separately and should remain the firmware's existing prefix (for this repository, `esp32_5`, producing `esp32_5/test` and `esp32_5/HEARTBEAT`).

## Safety Rules

- The coordinator generates the deterministic target before any learned correction.
- Residual corrections are clamped and the final target is independently revalidated.
- Extrapolated motion is rejected by default.
- The ESP32 performs joint-level IK for `controlik`.
- Physical commands use QoS 0 and are never retried after an uncertain publish or timeout.
- Commands advance only after the matching firmware `status: "completed"` response.
- Workers cannot send physical commands when broker ACLs follow the documented policy.

## Tests

Tests require the coordinator's NumPy/OpenCV dependencies but do not connect to a broker or download models:

```bash
.venv-coordinator/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

The integration suite uses an in-memory broker and fake model adapters while exercising the real coordinator, service-job, artifact-transfer, calibration, feature, plan, and idempotency paths.

See [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) for the implemented surface and the remaining real-broker, model-cache, ESP32, remote-host, and GUI cutover gates.

## Legacy Data Import

The importer reads legacy databases in SQLite read-only mode and writes the new coordinator schema. Topic-to-robot mappings are explicit:

```bash
.venv-coordinator/bin/desk-buddy-vision-import-legacy \
  --data-dir ./data \
  --legacy-vision-db /path/to/vision_node.sqlite3 \
  --legacy-web-db /path/to/db.sqlite3 \
  --topic-map 'legacy/robot/topic=esp32_5' \
  --dry-run
```

Remove `--dry-run` after reviewing the counts. Reviewed legacy grab attempts are converted into immutable `features.v1` snapshots and explicit rotation, distance, and z-height correction columns. The importer does not train from nested legacy command JSON and is idempotent for the same source database and row IDs.

## GUI Cutover

`client/` provides `VisionMQTTClient` for the existing calibration tool. Install `protocol/` and `client/` into the GUI environment, then use the client for photo, detection, calibration, review, training, activation, status, and artifact requests. It subscribes only to coordinator topics, reassembles and hashes artifacts, and deliberately tells callers not to resend a timed-out physical operation automatically.

## Runtime Data

The coordinator creates:

```text
data/
  vision.sqlite3
  artifacts/{robot_id}/{year}/{month}/{day}/...
  tmp/
```

Workers retain only model caches and temporary transfer state. SQLite files, artifacts, model weights, certificates, credentials, environments, and logs are ignored by Git.
