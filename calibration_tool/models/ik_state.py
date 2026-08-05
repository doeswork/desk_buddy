from __future__ import annotations

from typing import Any, Dict

# Maps a (plane, kind) calibration row to the firmware's calibrationvalues key
# for that saved point. z25 has no firmware key: it is validation-only and
# never persisted, so it is deliberately absent here.
FIRMWARE_KEY_BY_ROW = {
    ("z0", "min"): "hover_over_min",
    ("z0", "mid"): "hover_over_mid",
    ("z0", "max"): "hover_over_max",
    ("z50", "min"): "hover_min_120",
    ("z50", "mid"): "hover_mid_120",
    ("z50", "max"): "hover_max_120",
}

# Starting distance (mm) shown in each row before firmware or the operator
# has set a real value, per plane/kind.
DEFAULT_DISTANCE_MM = {
    "z0": {"min": 0, "mid": 60, "max": 120},
    "z25": {"min": 15, "mid": 67.5, "max": 120},
    "z50": {"min": 30, "mid": 75, "max": 120},
}

PLANES = (
    ("z0", "z=0 saved firmware points"),
    ("z50", "z=50 saved firmware points"),
    ("z25", "z=25 validation only"),
)


def firmware_points_signature(values: Dict[str, Any]) -> str:
    """A stable string identifying the current saved-hover-point values.

    Used to skip re-syncing rows when nothing has actually changed on the
    firmware side, so an in-progress edit is never clobbered by a heartbeat
    that repeats the same values.
    """
    import json

    payload = {key: values.get(key) for key in FIRMWARE_KEY_BY_ROW.values()}
    return json.dumps(payload, sort_keys=True, default=str)
