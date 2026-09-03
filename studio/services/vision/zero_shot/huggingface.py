"""Hugging Face zero-shot detector normalized to DetectionBatchV1."""

from __future__ import annotations

import inspect
import logging
from io import BytesIO
from typing import Any, Mapping, Sequence

from ..contracts import DETECTION_SCHEMA, DetectionBatchV1, DetectionV1, JobEnvelope
from ..frames import BinaryArtifact
from ..worker import WorkerOutput

LOGGER = logging.getLogger(__name__)


class HuggingFaceZeroShotHandler:
    job_kinds = ("zero_shot.infer",)
    input_schemas = ("image.jpeg.v1",)
    output_schemas = (DETECTION_SCHEMA,)

    def __init__(
        self,
        *,
        model_id: str,
        source: str,
        revision: str,
        local_files_only: bool = True,
        device: str = "auto",
        prompt_style: str = "auto",
    ) -> None:
        self.model_id = model_id
        self.source = source
        self.model_version = revision
        self.local_files_only = local_files_only
        self.device = device
        self.prompt_style = prompt_style
        self._processor: Any = None
        self._model: Any = None
        self._torch: Any = None
        self._device = "cpu"
        self._model_type = ""

    def load(self) -> None:
        if self._model is not None:
            return
        import torch
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

        target = "cuda" if self.device == "auto" and torch.cuda.is_available() else self.device
        if target == "auto":
            target = "cpu"
        LOGGER.info("loading zero-shot model %s (%s) on %s", self.model_id, self.source, target)
        self._processor = AutoProcessor.from_pretrained(
            self.source,
            revision=self.model_version,
            local_files_only=self.local_files_only,
        )
        self._model = AutoModelForZeroShotObjectDetection.from_pretrained(
            self.source,
            revision=self.model_version,
            local_files_only=self.local_files_only,
        ).to(target)
        self._model.eval()
        self._model_type = str(getattr(self._model.config, "model_type", ""))
        self._torch = torch
        self._device = str(target)

    def handle(self, job: JobEnvelope, artifacts: Mapping[str, BinaryArtifact]) -> WorkerOutput:
        from PIL import Image

        self.load()
        image_artifact = artifacts.get("image")
        if image_artifact is None:
            raise ValueError("zero_shot.infer requires the image artifact role")
        image = Image.open(BytesIO(image_artifact.payload)).convert("RGB")
        prompt = str(job.payload.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("zero-shot prompt is required")
        box_threshold = float(job.payload.get("box_threshold", 0.25))
        text_threshold = float(job.payload.get("text_threshold", 0.2))
        native = self._detect_native(
            image=image,
            prompts=[prompt],
            box_threshold=box_threshold,
            text_threshold=text_threshold,
        )
        detections = tuple(
            DetectionV1.create(
                label=item["label"],
                score=item["score"],
                bbox_px=item["box"],
                image_width=image.width,
                image_height=image.height,
                model_id=self.model_id,
                model_version=self.model_version,
            )
            for item in native
        )
        batch = DetectionBatchV1(
            image_width=image.width,
            image_height=image.height,
            prompt=prompt,
            detections=detections,
            model_id=self.model_id,
            model_version=self.model_version,
        )
        return WorkerOutput({"detection_batch": batch.as_dict()})

    def _detect_native(
        self,
        *,
        image: Any,
        prompts: Sequence[str],
        box_threshold: float,
        text_threshold: float,
    ) -> list[dict[str, Any]]:
        if self._processor is None or self._model is None or self._torch is None:
            raise RuntimeError("zero-shot model has not been loaded")
        style = self.prompt_style
        if style == "auto":
            style = "nested" if "owl" in self._model_type.lower() else "sentence"
        if style == "nested":
            text = [list(prompts)]
        elif style == "sentence":
            text = [". ".join(prompts) + "."]
        elif style == "flat":
            text = list(prompts)
        else:
            raise ValueError(f"unsupported zero-shot prompt_style: {style}")
        inputs = self._processor(images=image, text=text, return_tensors="pt").to(self._device)
        with self._torch.no_grad():
            outputs = self._model(**inputs)
        result = self._post_process(
            outputs=outputs,
            inputs=inputs,
            image=image,
            prompts=prompts,
            box_threshold=box_threshold,
            text_threshold=text_threshold,
        )
        labels = result.get("text_labels")
        if labels is None:
            labels = result.get("labels")
        if labels is None:
            labels = result.get("classes")
        scores = result.get("scores")
        boxes = result.get("boxes")
        labels = [] if labels is None else labels
        scores = [] if scores is None else scores
        boxes = [] if boxes is None else boxes
        if hasattr(labels, "tolist"):
            labels = labels.tolist()
        if hasattr(scores, "tolist"):
            scores = scores.tolist()
        if hasattr(boxes, "tolist"):
            boxes = boxes.tolist()
        normalized = []
        for label, score, box in zip(labels, scores, boxes):
            if isinstance(label, int) and 0 <= label < len(prompts):
                label = prompts[label]
            normalized.append({"label": str(label), "score": float(score), "box": [float(value) for value in box]})
        return normalized

    def _post_process(
        self,
        *,
        outputs: Any,
        inputs: Mapping[str, Any],
        image: Any,
        prompts: Sequence[str],
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
            if "classes" in inspect.signature(grounded).parameters:
                values = grounded(
                    outputs,
                    classes=list(prompts),
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
