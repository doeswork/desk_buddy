"""Studio-side MQTT client for training, model transfer, and artifact receipt."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol

from ..contracts import JobEnvelope, JobEvent
from ..frames import BinaryArtifact, encode_artifact
from ..topics import ModelRoute, VisionTopics, WorkerRegistry
from .dataset import build_dataset_bundle
from .storage import VisionStore


class BuilderTransport(Protocol):
    def publish(self, topic: str, payload: bytes | str | Mapping[str, Any], *, qos: int, retain: bool = False) -> bool: ...


@dataclass
class _TrainingJob:
    robot_id: str
    worker_id: str
    metadata: dict[str, Any] | None = None
    artifact: BinaryArtifact | None = None
    saved: bool = False


class ModelBuilderClient:
    def __init__(
        self,
        *,
        transport: BuilderTransport,
        topics: VisionTopics,
        registry: WorkerRegistry,
        store: VisionStore,
        trainer_worker_id: str,
        inference_worker_id: str,
    ) -> None:
        self.transport = transport
        self.topics = topics
        self.registry = registry
        self.store = store
        self.trainer_worker_id = trainer_worker_id
        self.inference_worker_id = inference_worker_id
        self._training: dict[str, _TrainingJob] = {}
        self._load_jobs: dict[str, str] = {}
        self.on_update: Callable[[str, dict[str, Any]], None] | None = None
        self.registry.set_route(ModelRoute("ik-mlp-builder", trainer_worker_id, "ik_model.train"))
        for model in store.models():
            self._configure_model_routes(model["model_id"])

    def start_training(self, robot_id: str, model_name: str, *, epochs: int | None = None) -> str:
        route = self.registry.route("ik-mlp-builder", "ik_model.train")
        bundle, metadata = build_dataset_bundle(self.store.training_examples(robot_id))
        job_id = uuid.uuid4().hex
        payload: dict[str, Any] = {"model_name": _safe_model_name(model_name), "dataset": metadata}
        if epochs is not None:
            payload["epochs"] = max(1, int(epochs))
        job = JobEnvelope(
            job_id=job_id,
            request_id=uuid.uuid4().hex,
            capture_id=f"training-{job_id}",
            robot_id=robot_id,
            worker_id=route.worker_id,
            kind="ik_model.train",
            model_id="ik-mlp-builder",
            payload=payload,
            input_artifact_roles=("dataset",),
        )
        frame = encode_artifact(
            job_id=job_id,
            artifact_id=f"{job_id}-dataset",
            role="dataset",
            mime_type="application/x-npz",
            payload=bundle,
            metadata=metadata,
        )
        self._training[job_id] = _TrainingJob(robot_id=robot_id, worker_id=route.worker_id)
        if not self.transport.publish(self.topics.request(route.worker_id), job.as_dict(), qos=1):
            self._training.pop(job_id, None)
            raise RuntimeError("could not publish training request")
        if not self.transport.publish(self.topics.artifact_in(route.worker_id), frame, qos=1):
            raise RuntimeError("could not publish training dataset")
        self._emit("training", {"job_id": job_id, "state": "submitted", "examples": metadata["example_count"]})
        return job_id

    def load_model(self, model_id: str) -> str:
        record = self.store.model(model_id)
        if record is None:
            raise ValueError(f"unknown model {model_id}")
        self._configure_model_routes(model_id)
        route = self.registry.route(model_id, "ik_model.load")
        payload = self.store.artifacts.get(record["artifact_id"])
        job_id = uuid.uuid4().hex
        job = JobEnvelope(
            job_id=job_id,
            request_id=uuid.uuid4().hex,
            capture_id=f"model-load-{job_id}",
            robot_id=record["robot_id"],
            worker_id=route.worker_id,
            kind="ik_model.load",
            model_id=model_id,
            payload={"model": record["metadata"]},
            input_artifact_roles=("model",),
        )
        frame = encode_artifact(
            job_id=job_id,
            artifact_id=record["artifact_id"],
            role="model",
            mime_type="application/x-pytorch",
            payload=payload,
            metadata=record["metadata"],
        )
        self._load_jobs[job_id] = model_id
        if not self.transport.publish(self.topics.request(route.worker_id), job.as_dict(), qos=1):
            self._load_jobs.pop(job_id, None)
            raise RuntimeError("could not publish model load request")
        if not self.transport.publish(self.topics.artifact_in(route.worker_id), frame, qos=1):
            raise RuntimeError("could not publish model artifact")
        self._emit("model", {"model_id": model_id, "state": "loading"})
        return job_id

    def request_resend(self, training_job_id: str, artifact_id: str = "") -> str:
        state = self._training.get(training_job_id)
        if state is None:
            raise ValueError("unknown training job")
        request_id = uuid.uuid4().hex
        job = JobEnvelope(
            job_id=request_id, request_id=request_id, capture_id=f"resend-{request_id}",
            robot_id=state.robot_id, worker_id=state.worker_id, kind="artifact.resend",
            model_id="ik-mlp-builder",
            payload={"target_job_id": training_job_id, "artifact_id": artifact_id},
        )
        self.transport.publish(self.topics.request(state.worker_id), job.as_dict(), qos=1)
        return request_id

    def handle_artifact(self, artifact: BinaryArtifact) -> bool:
        state = self._training.get(artifact.job_id)
        if state is None:
            return False
        if artifact.role != "model":
            return True
        if state.artifact is not None and state.artifact.sha256 != artifact.sha256:
            raise ValueError("conflicting model artifact for training job")
        state.artifact = artifact
        self._finish_training(artifact.job_id, state)
        return True

    def handle_event(self, event: JobEvent) -> bool:
        state = self._training.get(event.job_id)
        if state is not None:
            if event.status == "failed":
                self._emit("training", {"job_id": event.job_id, "state": "failed", "error": event.error or {}})
            elif event.status == "completed":
                state.metadata = dict(event.payload.get("model") or {})
                self._finish_training(event.job_id, state)
            else:
                self._emit("training", {"job_id": event.job_id, "state": event.status})
            return True
        model_id = self._load_jobs.get(event.job_id)
        if model_id is not None:
            state_name = "loaded" if event.status == "completed" else event.status
            self._emit("model", {"model_id": model_id, "state": state_name, "error": event.error or {}})
            if event.status in {"completed", "failed"}:
                self._load_jobs.pop(event.job_id, None)
            return True
        return False

    def _finish_training(self, job_id: str, state: _TrainingJob) -> None:
        if state.saved or state.metadata is None or state.artifact is None:
            return
        metadata = state.metadata
        model_id = str(metadata.get("model_id") or "")
        model_version = str(metadata.get("model_version") or "")
        if not model_id or not model_version:
            raise ValueError("training worker returned incomplete model metadata")
        self.store.artifacts.put(state.artifact.artifact_id, state.artifact.payload, ".pt")
        self.store.save_model(
            model_id=model_id, model_version=model_version, robot_id=state.robot_id,
            artifact_id=state.artifact.artifact_id, metadata=metadata,
        )
        self._configure_model_routes(model_id)
        state.saved = True
        self._acknowledge(job_id, state)
        self._emit("training", {"job_id": job_id, "state": "completed", "model": metadata})

    def _acknowledge(self, job_id: str, state: _TrainingJob) -> None:
        ack_id = uuid.uuid4().hex
        ack = JobEnvelope(
            job_id=ack_id, request_id=ack_id, capture_id=f"ack-{ack_id}", robot_id=state.robot_id,
            worker_id=state.worker_id, kind="job.status", model_id="ik-mlp-builder",
            payload={"target_job_id": job_id, "acknowledge_artifacts": True},
        )
        self.transport.publish(self.topics.request(state.worker_id), ack.as_dict(), qos=1)

    def _configure_model_routes(self, model_id: str) -> None:
        self.registry.set_route(ModelRoute(model_id, self.inference_worker_id, "ik_model.load"))
        self.registry.set_route(ModelRoute(model_id, self.inference_worker_id, "ik_model.infer"))

    def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        if self.on_update:
            self.on_update(kind, payload)


def _safe_model_name(value: str) -> str:
    normalized = "".join(character.lower() if character.isalnum() else "-" for character in value.strip())
    normalized = "-".join(part for part in normalized.split("-") if part)
    return normalized[:64] or "custom-ik"
