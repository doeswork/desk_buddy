# Desk Buddy Vision MQTT Contract

## GUI and Firmware Topics

```text
desk_buddy/{robot_id}/vision/request
desk_buddy/{robot_id}/vision/event
desk_buddy/{robot_id}/vision/artifact/request
desk_buddy/{robot_id}/vision/artifact/chunk
desk_buddy/{robot_id}/vision/status
desk_buddy/vision/coordinator/status
{robot_topic}/test
{robot_topic}/HEARTBEAT
```

The GUI communicates only with the coordinator. The global coordinator status is its retained MQTT Last Will authority; robot-scoped status remains available for the existing GUI-facing namespace. The coordinator is the only vision service allowed to publish to the ESP32 command topic. Firmware compatibility includes JSON control/status messages, raw JPEG messages, and the existing JSON-prefix plus raw-JPEG framing.

GUI request IDs are global idempotency keys. Republishing a request ID returns the persisted operation state and terminal result, including artifact and plan IDs, and never republishes a photo or physical command. A `status` request can include an `operation_id` or `request_id`; without either, it returns recent operations for that robot so a reconnecting GUI can recover missed terminal events.

## Internal Topics

```text
desk_buddy/vision/service/{service_id}/request
desk_buddy/vision/service/{service_id}/event
desk_buddy/vision/service/{service_id}/status
desk_buddy/vision/artifact/request
desk_buddy/vision/artifact/metadata/{service_id}
desk_buddy/vision/artifact/download/{service_id}
desk_buddy/vision/artifact/upload/{service_id}
```

The coordinator routes each model ID to exactly one configured service ID. Status advertisements verify that the configured worker is online and supports the requested model; they never select or rewrite routes.

## Delivery Rules

| Message | QoS | Retained | Retry rule |
|---|---:|---:|---|
| Worker status and Last Will | 1 | yes | broker-managed |
| GUI request/event | 1 | no | request ID is idempotent |
| Worker job/result | 1 | no | job ID is idempotent |
| Artifact metadata/chunk | 1 | no | duplicate chunk indexes ignored |
| ESP32 physical command | 0 | no | never automatically retried |

Each service uses its own broker identity. Recommended ACLs:

- GUI: publish GUI request/artifact request; subscribe GUI event/artifact/status.
- Coordinator: subscribe GUI, firmware, worker status/result, and worker artifact uploads; publish GUI, firmware, directed worker jobs, and artifact downloads.
- Worker: subscribe only its directed request/metadata/download topics; publish only its event/status, shared artifact requests, and its own upload topic.
- A worker must not publish to firmware or GUI topics.

[`deploy/mosquitto/acl.example`](../deploy/mosquitto/acl.example) is a concrete starting policy. Replace its usernames, service IDs, root prefix, and robot topics for the deployment; the examples intentionally deny model workers access to firmware command topics.

## Envelopes

GUI requests use schema `desk_buddy.vision.v1` and require `request_id`, `robot_id`, `sender`, `kind`, `created_at`, and an object `payload`. Allowed initial kinds are `photo`, `detect`, `calibration`, `artifact_get`, `model_list`, `model_activate`, `training_start`, `training_example_update`, `operation_cancel`, and `status`.

Worker jobs use `desk_buddy.vision.job.v1` and require `request_id`, `operation_id`, `job_id`, `service_id`, `kind`, `model_id`, `created_at`, `input_artifact_ids`, and an object `payload`. Initial kinds are `detector.infer`, `depth.infer`, `residual.predict`, `training.start`, and `model.activate`.

Jobs are accepted, processing, completed, failed, or cancelled. A duplicate terminal job ID returns the cached terminal result without executing the model again.

## Binary Artifact Frames

Artifact chunks use this exact binary layout:

```text
4 bytes  ASCII magic: DBV1
4 bytes  unsigned big-endian JSON-header length
N bytes  UTF-8 JSON object
rest     raw chunk bytes
```

Every header includes transfer ID, artifact ID, operation ID, job or GUI request ID, chunk index, and chunk count. Metadata includes MIME type, byte size, SHA-256, chunk size/count, dimensions, dtype, and model identity where applicable.

The default chunk size is 4096 bytes and is configurable below the broker packet limit. Receivers ignore byte-identical duplicate chunks, reject conflicting duplicates, validate total size and SHA-256, and expose or commit an artifact only after validation.

Workers never receive coordinator paths. The coordinator validates that an artifact is listed in the directed job before serving it. Uploads are written to temporary storage and atomically committed to permanent coordinator storage after validation.

## Normalized Results

Detector workers return `detections.v1`: label, score, pixel and normalized `[x0,y0,x1,y1]` boxes, pixel and normalized centers, normalized area, model ID, and model version.

Depth workers return `depth.v1` metadata and upload raw `.npy`, normalized `[0,1]` `.npy`, and preview artifacts. Every result declares `near_is_high`; the coordinator converts the map to the `features.v1` convention where `1.0` always means nearer before feature extraction. Depth Anything is explicitly marked `units: "relative"`; calibration distance remains a separate millimeter value.

Planner workers return `plan-corrections.v1`: signed rotation, distance, and z-height deltas. The coordinator clamps, applies, and validates these values before generating the same `baseRotate`, `controlik`, `gripper`, and `calibrationvalues` sequence used by deterministic planning.

## Failure and Restart Behavior

- Unknown, offline, or capability-mismatched workers produce structured errors; a different model is never selected silently.
- Residual prediction falls back to deterministic planning only when `allow_fallback` is true (default).
- Coordinator restart marks unfinished operations failed because physical state is uncertain.
- Executing motion cannot be cancelled through the vision operation API.
- Worker or transfer timeouts fail the job; physical commands are not retried.
