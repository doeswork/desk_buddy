"""Versioned, dependency-light Desk Buddy Vision MQTT contracts."""

from .artifacts import BinaryFrame, decode_binary_frame, encode_binary_frame
from .envelopes import (
    JOB_SCHEMA,
    VISION_SCHEMA,
    ContractError,
    JobEnvelope,
    JobResult,
    VisionEvent,
    VisionRequest,
)
from .jobs import ServiceCapability, TopicLayout
from .observations import (
    DepthMap,
    Detection,
    DetectionBatch,
    FeatureSet,
    PlanCorrections,
)
from .worker import ArtifactInput, GeneratedArtifact, MQTTWorker, WorkerOutput
from .service_config import BrokerSettings, ServiceSettings, load_service_settings

__all__ = [
    "BinaryFrame",
    "BrokerSettings",
    "ArtifactInput",
    "ContractError",
    "DepthMap",
    "Detection",
    "DetectionBatch",
    "FeatureSet",
    "GeneratedArtifact",
    "JOB_SCHEMA",
    "JobEnvelope",
    "JobResult",
    "PlanCorrections",
    "MQTTWorker",
    "ServiceCapability",
    "ServiceSettings",
    "TopicLayout",
    "VISION_SCHEMA",
    "VisionEvent",
    "VisionRequest",
    "WorkerOutput",
    "decode_binary_frame",
    "encode_binary_frame",
    "load_service_settings",
]
