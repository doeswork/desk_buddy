from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .envelopes import ContractError, JOB_SCHEMA, utc_now


@dataclass(frozen=True)
class TopicLayout:
    root: str = "desk_buddy"

    def vision_request(self, robot_id: str) -> str:
        return f"{self.root}/{robot_id}/vision/request"

    def vision_event(self, robot_id: str) -> str:
        return f"{self.root}/{robot_id}/vision/event"

    def vision_artifact_request(self, robot_id: str) -> str:
        return f"{self.root}/{robot_id}/vision/artifact/request"

    def vision_artifact_chunk(self, robot_id: str) -> str:
        return f"{self.root}/{robot_id}/vision/artifact/chunk"

    def vision_status(self, robot_id: str) -> str:
        return f"{self.root}/{robot_id}/vision/status"

    def coordinator_status(self) -> str:
        return f"{self.root}/vision/coordinator/status"

    def service_request(self, service_id: str) -> str:
        return f"{self.root}/vision/service/{service_id}/request"

    def service_event(self, service_id: str) -> str:
        return f"{self.root}/vision/service/{service_id}/event"

    def service_status(self, service_id: str) -> str:
        return f"{self.root}/vision/service/{service_id}/status"

    def artifact_request(self) -> str:
        return f"{self.root}/vision/artifact/request"

    def artifact_metadata(self, service_id: str) -> str:
        return f"{self.root}/vision/artifact/metadata/{service_id}"

    def artifact_download(self, service_id: str) -> str:
        return f"{self.root}/vision/artifact/download/{service_id}"

    def artifact_upload(self, service_id: str) -> str:
        return f"{self.root}/vision/artifact/upload/{service_id}"


@dataclass(frozen=True)
class ServiceCapability:
    service_id: str
    service_kind: str
    state: str
    ready: bool
    busy: bool
    models: tuple[dict[str, Any], ...]
    input_schemas: tuple[str, ...]
    output_schemas: tuple[str, ...]
    hostname: str
    compute_device: str
    updated_at: str
    schema: str = JOB_SCHEMA

    @classmethod
    def offline(cls, service_id: str, service_kind: str) -> "ServiceCapability":
        return cls(
            service_id=service_id,
            service_kind=service_kind,
            state="offline",
            ready=False,
            busy=False,
            models=(),
            input_schemas=(),
            output_schemas=(),
            hostname="",
            compute_device="",
            updated_at=utc_now(),
        )

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ServiceCapability":
        if raw.get("schema") != JOB_SCHEMA:
            raise ContractError("unsupported_schema", f"schema must be {JOB_SCHEMA}")
        models = raw.get("models", [])
        if not isinstance(models, list) or any(not isinstance(item, dict) for item in models):
            raise ContractError("invalid_field", "models must be a list of objects")
        service_id = str(raw.get("service_id") or "").strip()
        service_kind = str(raw.get("service_kind") or "").strip()
        if not service_id or not service_kind:
            raise ContractError("missing_field", "service_id and service_kind are required")
        return cls(
            service_id=service_id,
            service_kind=service_kind,
            state=str(raw.get("state") or "").strip(),
            ready=bool(raw.get("ready")),
            busy=bool(raw.get("busy")),
            models=tuple(dict(item) for item in models),
            input_schemas=tuple(str(item) for item in raw.get("input_schemas", [])),
            output_schemas=tuple(str(item) for item in raw.get("output_schemas", [])),
            hostname=str(raw.get("hostname") or ""),
            compute_device=str(raw.get("compute_device") or ""),
            updated_at=str(raw.get("updated_at") or ""),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "service_id": self.service_id,
            "service_kind": self.service_kind,
            "state": self.state,
            "ready": self.ready,
            "busy": self.busy,
            "models": [dict(model) for model in self.models],
            "input_schemas": list(self.input_schemas),
            "output_schemas": list(self.output_schemas),
            "hostname": self.hostname,
            "compute_device": self.compute_device,
            "updated_at": self.updated_at,
        }

    def supports(self, model_id: str, *, schema: str | None = None) -> bool:
        if self.state != "online" or not self.ready:
            return False
        if schema and schema not in self.input_schemas:
            return False
        return any(model.get("model_id") == model_id for model in self.models)
