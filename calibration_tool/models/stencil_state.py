from __future__ import annotations

from typing import Any, Dict


class StencilState:
    """Last known stencil_calibration payload from the robot.

    Held as the raw wire dict rather than a fixed dataclass: the firmware
    owns this schema and can add fields to it, and the view only ever reads
    keys by name to render them, so there is nothing a stricter shape would
    buy here.
    """

    def __init__(self) -> None:
        self.last_response: Dict[str, Any] = {}

    def update_from_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """Merge a stencil_calibration payload in, if the response has one.

        Returns the dict that should be rendered: the new payload if present,
        else whatever was last known (a STATUS poll can arrive with a bare
        ack and no stencil_calibration block, and the view should keep
        showing the last real snapshot rather than blank out).
        """
        stencil = response.get("stencil_calibration") if isinstance(response, dict) else None
        if isinstance(stencil, dict):
            self.last_response = stencil
        return self.last_response


def format_stencil_value(value: Any, format_bool) -> str:
    if isinstance(value, bool):
        return format_bool(value)
    if isinstance(value, (int, float)):
        return format_stencil_number(value)
    return str(value)


def format_stencil_number(value: Any) -> str:
    if value is None:
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}"
