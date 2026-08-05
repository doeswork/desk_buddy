from __future__ import annotations

from typing import Any, Dict, Optional


class VisualCalibrationState:
    """Last known Visual AI calibration result.

    Held as the raw response dict rather than a fixed schema: the Vision
    server owns this shape, and the controller only ever reads keys by name
    to render them.
    """

    def __init__(self) -> None:
        self.last_response: Optional[Dict[str, Any]] = None
        self.last_photo_path: Optional[str] = None

    def update(self, response: Dict[str, Any], photo_path: Any) -> None:
        self.last_response = response
        self.last_photo_path = str(photo_path)
