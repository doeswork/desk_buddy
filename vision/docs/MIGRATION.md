# Migration from desk_buddy_vision_ai

## 1. Prepare Without Affecting the Working Runtime

1. Leave the existing Django, vision worker, model maker, and GUI running while configuring this project on different MQTT prefixes or a development broker.
2. Create all four isolated virtual environments with `deploy/setup_envs.sh`.
3. Configure one coordinator robot mapping and explicit detector, depth, and planner service routes.
4. Configure broker ACLs from `MQTT_CONTRACT.md` before using production robot topics.
5. Preload model caches and verify each worker advertises ready status.

## 2. Import Existing Data

Run `desk-buddy-vision-import-legacy --dry-run` first. Every legacy MQTT topic must be mapped explicitly to a new robot ID. The source databases are opened read-only.

The importer moves image blobs to hashed filesystem artifacts, creates idempotent legacy operations, imports normalized legacy detection metadata, and activates valid calibration profiles when both points and grid data exist. Reviewed grab attempts become explicit `features.v1` artifacts and correction labels. Legacy web-sync fields, callback counters, HTTP tokens, Django migrations, and executable command dictionaries are not carried forward.

Compare dry-run and real import counts. Re-running the same source is safe because request and artifact IDs are deterministically derived from the source path, table, and row ID.

## 3. Validate on Development Topics

Run these scenarios before changing the GUI:

1. Capture and retrieve a photo artifact.
2. Create and activate a nine-point calibration.
3. Detect with `execute=false` and inspect original, annotated, normalized depth, preview, and feature artifacts.
4. Detect with deterministic execution and manually acknowledge each fixture firmware command.
5. Review at least three correction examples, train a residual version, inspect metrics, activate it, and run `execute=false` with `planner=residual:<model_id>`.
6. Stop each worker independently and verify structured unavailable-service behavior or deterministic planner fallback.
7. Move one fake or real worker to another host and repeat without changing GUI payloads.

## 4. GUI Cutover

Install the lightweight `protocol/` and `client/` packages in the calibration-tool environment. Replace direct photo/detection/calibration ownership with `VisionMQTTClient` requests and event callbacks. The GUI may continue using its direct firmware controls for manual calibration screens during an incremental transition, but automated vision workflows must use the coordinator.

Do not let the GUI calculate motion plans, generate reach-and-grab child commands, read coordinator SQLite/files, subscribe to internal worker topics, or retry timed-out physical requests.

## 5. Production Cutover and Rollback

1. Stop the legacy vision worker before enabling the new coordinator on the production firmware topic; two coordinators must never command one robot.
2. Start workers, confirm retained capabilities, then start the coordinator and GUI.
3. Keep the legacy repository and databases read-only for rollback until production photo, inference, calibration, command sequencing, training, activation, and restart-recovery checks pass.
4. Roll back by stopping the new coordinator before restarting the legacy worker. Never overlap command publishers.
5. After the retention window, archive the legacy data and remove Django/HTTP/Docker startup units from the active host configuration.

