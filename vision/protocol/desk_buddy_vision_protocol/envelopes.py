from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

VISION_SCHEMA = "desk_buddy.vision.v1"
JOB_SCHEMA = "desk_buddy.vision.job.v1"

VISION_REQUEST_KINDS = frozenset(
    {
        "photo",
        "detect",
        "calibration",
        "artifact_get",
        "model_list",
        "model_activate",
        "training_start",
        "training_example_update",
        "operation_cancel",
        "status",
    }
)
JOB_KINDS = frozenset(
    {
        "detector.infer",
        "depth.infer",
        "residual.predict",
        "training.start",
        "model.activate",
    }
)
VISION_STATUSES = frozenset(
    {
        "accepted",
        "waiting_for_photo",
        "processing",
        "planning",
        "executing",
        "completed",
        "failed",
        "cancelled",
    }
)
JOB_STATUSES = frozenset({"accepted", "processing", "completed", "failed", "cancelled"})
FORBIDDEN_REQUEST_KEYS = frozenset(
    {"path", "file_path", "filesystem_path", "model_path", "shell", "shell_command", "executable"}
)


class ContractError(ValueError):
    def __init__(self, code: str, message: str, *, field_name: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field_name = field_name

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.field_name:
            result["field"] = self.field_name
        return result


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def decode_json_object(payload: bytes | str, *, max_bytes: int = 262_144) -> dict[str, Any]:
    raw = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    if len(raw) > max_bytes:
        raise ContractError("message_too_large", f"JSON payload exceeds {max_bytes} bytes")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("invalid_json", "payload must be a UTF-8 JSON object") from exc
    if not isinstance(value, dict):
        raise ContractError("invalid_json_type", "payload must be a JSON object")
    return value


def encode_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(value), separators=(",", ":"), sort_keys=True).encode("utf-8")


def _required_text(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError("missing_field", f"{key} must be a non-empty string", field_name=key)
    return value.strip()


def _payload(raw: Mapping[str, Any]) -> dict[str, Any]:
    value = raw.get("payload", {})
    if not isinstance(value, dict):
        raise ContractError("invalid_field", "payload must be an object", field_name="payload")
    return dict(value)


def _error(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    value = raw.get("error")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ContractError("invalid_field", "error must be an object or null", field_name="error")
    return dict(value)


def _assert_no_forbidden_keys(value: Any, *, prefix: str = "payload") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            if key_text.lower() in FORBIDDEN_REQUEST_KEYS:
                raise ContractError(
                    "forbidden_field",
                    f"{prefix}.{key_text} is not accepted over MQTT",
                    field_name=f"{prefix}.{key_text}",
                )
            _assert_no_forbidden_keys(child, prefix=f"{prefix}.{key_text}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_no_forbidden_keys(child, prefix=f"{prefix}[{index}]")


@dataclass(frozen=True)
class VisionRequest:
    request_id: str
    robot_id: str
    sender: str
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    schema: str = VISION_SCHEMA

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "VisionRequest":
        if raw.get("schema") != VISION_SCHEMA:
            raise ContractError("unsupported_schema", f"schema must be {VISION_SCHEMA}", field_name="schema")
        kind = _required_text(raw, "kind")
        if kind not in VISION_REQUEST_KINDS:
            raise ContractError("unknown_request_kind", f"unsupported request kind: {kind}", field_name="kind")
        payload = _payload(raw)
        _assert_no_forbidden_keys(payload)
        return cls(
            request_id=_required_text(raw, "request_id"),
            robot_id=_required_text(raw, "robot_id"),
            sender=_required_text(raw, "sender"),
            kind=kind,
            created_at=_required_text(raw, "created_at"),
            payload=payload,
        )

    @classmethod
    def from_json(cls, payload: bytes | str) -> "VisionRequest":
        return cls.from_mapping(decode_json_object(payload))

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "request_id": self.request_id,
            "robot_id": self.robot_id,
            "sender": self.sender,
            "kind": self.kind,
            "created_at": self.created_at,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class VisionEvent:
    request_id: str
    operation_id: str
    status: str
    stage: str
    payload: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    kind: str = "operation.event"
    sender: str = "vision_coordinator"
    schema: str = VISION_SCHEMA

    def __post_init__(self) -> None:
        if self.status not in VISION_STATUSES:
            raise ContractError("invalid_status", f"unsupported vision status: {self.status}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "request_id": self.request_id,
            "operation_id": self.operation_id,
            "sender": self.sender,
            "kind": self.kind,
            "status": self.status,
            "stage": self.stage,
            "payload": dict(self.payload),
            "error": dict(self.error) if self.error else None,
        }


@dataclass(frozen=True)
class JobEnvelope:
    request_id: str
    operation_id: str
    job_id: str
    service_id: str
    kind: str
    model_id: str
    input_artifact_ids: tuple[str, ...] = ()
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    sender: str = "vision_coordinator"
    schema: str = JOB_SCHEMA

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "JobEnvelope":
        if raw.get("schema") != JOB_SCHEMA:
            raise ContractError("unsupported_schema", f"schema must be {JOB_SCHEMA}", field_name="schema")
        kind = _required_text(raw, "kind")
        if kind not in JOB_KINDS:
            raise ContractError("unknown_job_kind", f"unsupported job kind: {kind}", field_name="kind")
        artifact_ids = raw.get("input_artifact_ids", [])
        if not isinstance(artifact_ids, list) or any(not isinstance(item, str) or not item for item in artifact_ids):
            raise ContractError("invalid_field", "input_artifact_ids must be a list of strings")
        return cls(
            request_id=_required_text(raw, "request_id"),
            operation_id=_required_text(raw, "operation_id"),
            job_id=_required_text(raw, "job_id"),
            service_id=_required_text(raw, "service_id"),
            kind=kind,
            model_id=_required_text(raw, "model_id"),
            input_artifact_ids=tuple(artifact_ids),
            payload=_payload(raw),
            created_at=_required_text(raw, "created_at"),
            sender=_required_text(raw, "sender"),
        )

    @classmethod
    def from_json(cls, payload: bytes | str) -> "JobEnvelope":
        return cls.from_mapping(decode_json_object(payload))

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "request_id": self.request_id,
            "operation_id": self.operation_id,
            "job_id": self.job_id,
            "sender": self.sender,
            "service_id": self.service_id,
            "kind": self.kind,
            "model_id": self.model_id,
            "created_at": self.created_at,
            "input_artifact_ids": list(self.input_artifact_ids),
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class JobResult:
    request_id: str
    operation_id: str
    job_id: str
    service_id: str
    kind: str
    model_id: str
    model_version: str
    status: str
    payload: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    sender: str = "model_worker"
    schema: str = JOB_SCHEMA

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "JobResult":
        if raw.get("schema") != JOB_SCHEMA:
            raise ContractError("unsupported_schema", f"schema must be {JOB_SCHEMA}")
        status = _required_text(raw, "status")
        if status not in JOB_STATUSES:
            raise ContractError("invalid_status", f"unsupported job status: {status}")
        kind = _required_text(raw, "kind")
        if kind not in JOB_KINDS:
            raise ContractError("unknown_job_kind", f"unsupported job kind: {kind}", field_name="kind")
        return cls(
            request_id=_required_text(raw, "request_id"),
            operation_id=_required_text(raw, "operation_id"),
            job_id=_required_text(raw, "job_id"),
            service_id=_required_text(raw, "service_id"),
            kind=kind,
            model_id=_required_text(raw, "model_id"),
            model_version=_required_text(raw, "model_version"),
            status=status,
            payload=_payload(raw),
            error=_error(raw),
            sender=_required_text(raw, "sender"),
        )

    @classmethod
    def from_json(cls, payload: bytes | str) -> "JobResult":
        return cls.from_mapping(decode_json_object(payload))

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "request_id": self.request_id,
            "operation_id": self.operation_id,
            "job_id": self.job_id,
            "service_id": self.service_id,
            "sender": self.sender,
            "kind": self.kind,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "status": self.status,
            "payload": dict(self.payload),
            "error": dict(self.error) if self.error else None,
        }
