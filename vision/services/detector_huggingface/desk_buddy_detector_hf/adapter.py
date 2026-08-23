from __future__ import annotations

import inspect
import logging
from io import BytesIO
from typing import Any, Mapping, Sequence

from desk_buddy_vision_protocol import ArtifactInput, Detection, DetectionBatch, JobEnvelope, WorkerOutput

LOGGER = logging.getLogger(__name__)


class HuggingFaceZeroShotHandler:
    job_kinds = ("detector.infer",)
    input_schemas = ("image.jpeg.v1",)
    output_schemas = ("detections.v1",)

    def __init__(
        self,
        *,
        model_id: str,
        source: str,
        model_version: str,
        local_files_only: bool = True,
        compute_device: str = "auto",
    ) -> None:
        self.model_id = model_id
        self.source = source
        self.model_version = model_version
        self.local_files_only = local_files_only
        self.compute_device = compute_device
        self._processor: Any = None
        self._model: Any = None
        self._torch: Any = None
        self._device = "cpu"

    def load(self) -> None:
        if self._model is not None:
            return
        import torch
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

        if self.compute_device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            device = self.compute_device
        LOGGER.info("loading detector %s from %s on %s", self.model_id, self.source, device)
        self._processor = AutoProcessor.from_pretrained(self.source, local_files_only=self.local_files_only)
        self._model = AutoModelForZeroShotObjectDetection.from_pretrained(
            self.source, local_files_only=self.local_files_only
        ).to(device)
        self._model.eval()
        self._torch = torch
        self._device = device

    def handle(self, job: JobEnvelope, artifacts: Mapping[str, ArtifactInput]) -> WorkerOutput:
        from PIL import Image

        self.load()
        if len(artifacts) != 1:
            raise ValueError("detector.infer requires exactly one image artifact")
        image = Image.open(BytesIO(next(iter(artifacts.values())).payload)).convert("RGB")
        phrase = str(job.payload.get("phrase") or "").strip()
        if not phrase:
            raise ValueError("detector phrase is required")
        box_threshold = float(job.payload.get("box_threshold", 0.25))
        text_threshold = float(job.payload.get("text_threshold", 0.2))
        native = self._detect_native(
            image=image,
            phrases=[phrase],
            box_threshold=box_threshold,
            text_threshold=text_threshold,
        )
        detections = tuple(
            Detection.create(
                label=item["label"],
                score=item["score"],
                bbox_px=item["box"],
                image_width=image.width,
                image_height=image.height,
                source_model_id=self.model_id,
                source_model_version=self.model_version,
            )
            for item in native
        )
        batch = DetectionBatch(
            image_width=image.width,
            image_height=image.height,
            phrase=phrase,
            detections=detections,
            model_id=self.model_id,
            model_version=self.model_version,
        )
        return WorkerOutput(payload={"detection_batch": batch.as_dict()})

    def _detect_native(
        self,
        *,
        image: Any,
        phrases: Sequence[str],
        box_threshold: float,
        text_threshold: float,
    ) -> list[dict[str, Any]]:
        assert self._processor is not None and self._model is not None and self._torch is not None
        inputs = self._processor(images=image, text=list(phrases), return_tensors="pt").to(self._device)
        with self._torch.no_grad():
            if self._device.startswith("cuda"):
                with self._torch.autocast(device_type="cuda", dtype=self._torch.float16):
                    outputs = self._model(**inputs)
            else:
                outputs = self._model(**inputs)
        result = self._post_process(
            outputs=outputs,
            inputs=inputs,
            image=image,
            phrases=phrases,
            box_threshold=box_threshold,
            text_threshold=text_threshold,
        )
        labels = result.get("text_labels")
        if labels is None:
            labels = result.get("labels")
        if labels is None:
            labels = result.get("classes")
        labels = labels if labels is not None else []
        scores = result.get("scores")
        scores = scores if scores is not None else []
        boxes = result.get("boxes")
        boxes = boxes if boxes is not None else []
        if hasattr(labels, "tolist"):
            labels = labels.tolist()
        if hasattr(scores, "tolist"):
            scores = scores.tolist()
        if hasattr(boxes, "tolist"):
            boxes = boxes.tolist()
        normalized: list[dict[str, Any]] = []
        for label, score, box in zip(labels, scores, boxes):
            if isinstance(label, int) and 0 <= label < len(phrases):
                label = phrases[label]
            normalized.append(
                {"label": str(label), "score": float(score), "box": [float(value) for value in box]}
            )
        return normalized

    def _post_process(
        self,
        *,
        outputs: Any,
        inputs: Mapping[str, Any],
        image: Any,
        phrases: Sequence[str],
        box_threshold: float,
        text_threshold: float,
    ) -> dict[str, Any]:
        target_sizes = [(image.height, image.width)]
        object_detection = getattr(self._processor, "post_process_object_detection", None)
        if callable(object_detection):
            values = object_detection(outputs, target_sizes=target_sizes, threshold=box_threshold)
            return values[0] if isinstance(values, list) and values else {}
        grounded = getattr(self._processor, "post_process_grounded_object_detection", None)
        if not callable(grounded):
            raise RuntimeError("processor has no supported zero-shot post-processor")
        try:
            parameters = inspect.signature(grounded).parameters
            if "classes" in parameters:
                values = grounded(
                    outputs,
                    classes=list(phrases),
                    target_sizes=target_sizes,
                    score_threshold=box_threshold,
                    nms_threshold=text_threshold,
                )
                return values[0] if isinstance(values, list) and values else {}
        except (TypeError, ValueError):
            pass
        if "input_ids" not in inputs:
            raise RuntimeError("grounded detector requires input_ids")
        values = grounded(
            outputs,
            inputs["input_ids"],
            target_sizes=target_sizes,
            box_threshold=box_threshold,
            text_threshold=text_threshold,
        )
        return values[0] if isinstance(values, list) and values else {}

