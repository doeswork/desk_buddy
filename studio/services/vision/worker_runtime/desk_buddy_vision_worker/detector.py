"""One Hugging Face adapter for the two curated zero-shot models."""

from __future__ import annotations

import inspect
from io import BytesIO
from typing import Any

from .catalog import validate_model
from .contracts import normalize_batch


def formatted_prompt(prompt: str, style: str):
    """Return exactly the text shape expected by the selected processor."""
    clean = " ".join(str(prompt).strip().split())
    if not clean:
        raise ValueError("a detection prompt is required")
    if style == "nested":
        return [[clean]]
    if style == "sentence":
        return [clean.lower().rstrip(".") + "."]
    raise ValueError(f"unsupported prompt style: {style}")


class HuggingFaceDetector:
    def __init__(self, model: dict) -> None:
        validate_model(model)
        self.settings = dict(model)
        self.catalog_id = str(model["model_id"])
        self.source = str(model["source"])
        # Wire contracts use the canonical Hugging Face repository ID. The
        # shorter catalog ID is only a local Studio preference key.
        self.model_id = self.source
        self.revision = str(model["revision"])
        self.prompt_style = str(model["prompt_style"])
        self.box_threshold = float(model["box_threshold"])
        value = model.get("text_threshold")
        self.text_threshold = float(value) if value is not None else 0.3
        self.cache_dir = str(model["cache_dir"])
        self.processor: Any = None
        self.model: Any = None
        self.torch: Any = None
        self.device = "cpu"

    def load(self) -> None:
        import torch
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

        if torch.cuda.is_available():
            device = "cuda"
        elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            device = "mps"
        else:
            device = "cpu"
        self.processor = AutoProcessor.from_pretrained(
            self.source,
            revision=self.revision,
            cache_dir=self.cache_dir,
            local_files_only=True,
            trust_remote_code=False,
        )
        self._verify_processor()
        self.model = AutoModelForZeroShotObjectDetection.from_pretrained(
            self.source,
            revision=self.revision,
            cache_dir=self.cache_dir,
            local_files_only=True,
            trust_remote_code=False,
        ).to(device)
        self.model.eval()
        self.torch = torch
        self.device = device

    def _verify_processor(self) -> None:
        """Exercise lazy image dependencies before advertising MQTT-ready."""
        from PIL import Image

        probe = Image.new("RGB", (32, 32), color=(0, 0, 0))
        self.processor(
            images=probe,
            text=formatted_prompt("object", self.prompt_style),
            return_tensors="pt",
        )

    def detect(
        self,
        jpeg: bytes,
        prompt: str,
        *,
        box_threshold: float | None = None,
        text_threshold: float | None = None,
    ) -> dict:
        from PIL import Image

        if self.model is None or self.processor is None or self.torch is None:
            raise RuntimeError("detector is not loaded")
        image = Image.open(BytesIO(jpeg))
        image.verify()
        image = Image.open(BytesIO(jpeg)).convert("RGB")
        if image.width <= 0 or image.height <= 0 or image.width * image.height > 40_000_000:
            raise ValueError("unsupported image dimensions")

        effective_box = self.box_threshold if box_threshold is None else float(box_threshold)
        effective_text = self.text_threshold if text_threshold is None else float(text_threshold)
        if not 0 <= effective_box <= 1 or not 0 <= effective_text <= 1:
            raise ValueError("detection thresholds must be between zero and one")

        text = formatted_prompt(prompt, self.prompt_style)
        inputs = self.processor(images=image, text=text, return_tensors="pt")
        if hasattr(inputs, "to"):
            inputs = inputs.to(self.device)
        else:
            inputs = {
                key: value.to(self.device) if hasattr(value, "to") else value
                for key, value in inputs.items()
            }
        with self.torch.no_grad():
            outputs = self.model(**inputs)
        native = self._post_process(
            outputs, inputs, image.width, image.height,
            effective_box, effective_text, prompt, text,
        )
        return normalize_batch(
            width=image.width,
            height=image.height,
            prompt=prompt.strip(),
            model_id=self.model_id,
            revision=self.revision,
            detections=native,
        )

    def _post_process(
        self,
        outputs,
        inputs,
        width: int,
        height: int,
        box_threshold: float,
        text_threshold: float,
        prompt: str,
        formatted_text,
    ) -> list[dict]:
        target_sizes = [(height, width)]
        grounded = getattr(self.processor, "post_process_grounded_object_detection", None)
        values = None
        if callable(grounded):
            try:
                parameters = inspect.signature(grounded).parameters
            except (TypeError, ValueError):
                parameters = {}
            kwargs = {"target_sizes": target_sizes}
            if "box_threshold" in parameters:
                kwargs["box_threshold"] = box_threshold
            elif "threshold" in parameters:
                kwargs["threshold"] = box_threshold
            elif "score_threshold" in parameters:
                kwargs["score_threshold"] = box_threshold
            if "text_threshold" in parameters:
                kwargs["text_threshold"] = text_threshold
            if "text_labels" in parameters:
                kwargs["text_labels"] = formatted_text
            if "input_ids" in parameters:
                kwargs["input_ids"] = inputs.get("input_ids")
            try:
                values = grounded(outputs, **kwargs)
            except TypeError:
                # Transformers 4.x Grounding DINO used input_ids positionally.
                values = grounded(
                    outputs,
                    inputs.get("input_ids"),
                    box_threshold=box_threshold,
                    text_threshold=text_threshold,
                    target_sizes=target_sizes,
                )
        if values is None:
            ordinary = getattr(self.processor, "post_process_object_detection", None)
            if not callable(ordinary):
                raise RuntimeError("processor has no supported detection post-processor")
            values = ordinary(outputs, target_sizes=target_sizes, threshold=box_threshold)
        result = values[0] if isinstance(values, list) and values else {}
        boxes = _to_list(result.get("boxes"))
        scores = _to_list(result.get("scores"))
        labels = _to_list(
            result.get("text_labels", result.get("labels", result.get("classes", [])))
        )
        normalized = []
        for label, score, box in zip(labels, scores, boxes):
            if isinstance(label, int):
                label = prompt
            normalized.append({"label": str(label), "score": score, "box": box})
        return normalized


def _to_list(value):
    if value is None:
        return []
    return value.tolist() if hasattr(value, "tolist") else list(value)
