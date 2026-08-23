# Vision Rebuild Implementation Status

## Implemented

The new runtime is implemented under `vision/` as four independently deployable MQTT processes plus a lightweight shared protocol and GUI client:

- The coordinator owns ESP32 and GUI traffic, SQLite, permanent artifacts, nine-point calibration, `features.v1`, safety, motion plans, and sequential firmware commands.
- Hugging Face detector and depth workers have separate packages, dependency constraints, virtual environments, configs, MQTT clients, status/LWT behavior, and systemd units.
- The planner/training worker creates versioned residual models from immutable reviewed examples, uploads model bundles, requires explicit activation, and returns corrections rather than robot commands.
- Artifact bytes move in both directions through indexed `DBV1` MQTT chunks with correlation checks, duplicate handling, byte-size checks, and SHA-256 validation. Workers never receive coordinator filesystem paths.
- GUI requests and terminal results are idempotent. Terminal payloads are persisted, duplicate requests return the original result, and `status` requests recover operations missed while the GUI was disconnected.
- The legacy importer reads old SQLite sources without modifying them and converts reviewed grab attempts to explicit `features.v1` snapshots and correction labels.

The automated suite currently covers the in-memory distributed workflow, model boundary normalization, storage and restart recovery, calibration repeatability, deterministic and residual command equivalence, no-retry sequencing, training/activation/reload, GUI artifact reassembly, ACL separation, and legacy import.

## Production Validation Gates

These checks require deployment resources and are intentionally not claimed by the unit/in-memory integration suite:

1. Create the four virtual environments with `deploy/setup_envs.sh` and resolve host-specific Torch/CUDA wheels without weakening environment isolation.
2. Preload the configured OWLv2 and Depth Anything snapshots, then run fixture images through the real model weights.
3. Run all services against the TLS broker with separate accounts and the deployment's adapted ACL policy; verify retained status, Last Will, reconnects, and broker packet limits.
4. Run the full flow against an ESP32 on `esp32_5/test`, first with `execute=false`, then with supervised physical motion.
5. Move one worker to a second broker-connected device and repeat without changing GUI request payloads or coordinator routes other than host-side deployment settings.
6. Cut the existing `calibration_tool` automated vision screens over to `VisionMQTTClient`. Its manual firmware calibration controls may remain direct during the incremental transition.

Do not remove or start overlapping legacy command publishers until these gates pass. The exact cutover and rollback sequence is in [MIGRATION.md](MIGRATION.md).
