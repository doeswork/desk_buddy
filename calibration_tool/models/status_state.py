from __future__ import annotations

import json
from typing import Any, Dict


def is_positive_number(value: Any) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def format_preference_value(key: str, value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if key == "st_map":
        try:
            parsed = json.loads(value) if isinstance(value, str) else value
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = None
        if isinstance(parsed, dict):
            points = parsed.get("points")
            if isinstance(points, list):
                return f"{len(points)} saved points"
        return "Saved"
    if isinstance(value, dict):
        distance = value.get("DISTANCE")
        elbow = value.get("ELBOW")
        wrist = value.get("WRIST")
        twist = value.get("TWIST")
        return f"{distance} mm  ·  E {elbow}°  W {wrist}°  T {twist}°"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def build_calibration_status_rows(values: Dict[str, Any]) -> list[Dict[str, str]]:
    """Turn the firmware calibration dump into human-readable preference rows."""
    rows: list[Dict[str, str]] = []

    def add(
        group: str,
        label: str,
        key: str,
        value: Any,
        state: str,
        source: str,
    ) -> None:
        rows.append(
            {
                "group": group,
                "label": label,
                "key": key,
                "value": format_preference_value(key, value),
                "state": state,
                "source": source,
            }
        )

    # Veryslow angles are fixed offsets from neutral rather than learned values,
    # so there is no veryslow validation state left to report here.
    base_fields = (
        ("Profile calibrated", "base_rotation_profileCalibrated", "boolean"),
        ("Rotation calibrated", "base_rotation_calibrated", "boolean"),
        ("Left counts / revolution", "base_rotation_leftCountsPerRev", "positive"),
        ("Right counts / revolution", "base_rotation_rightCountsPerRev", "positive"),
        ("Last position trusted", "base_rotation_lastValid", "boolean"),
    )
    for label, key, check in base_fields:
        value = values.get(key)
        ready = bool(value) if check == "boolean" else is_positive_number(value)
        add(
            "Base rotation",
            label,
            key,
            value,
            "SAVED" if ready else "MISSING",
            "Calibration result" if ready else "Run base profile",
        )

    perch_effective = values.get("perch_effective")
    if not isinstance(perch_effective, dict):
        perch_effective = {}
    perch_fields = (
        ("Elbow perch angle", "PERCH_ELBOW_ANGLE", "ELBOW", 120),
        ("Wrist perch angle", "PERCH_WRIST_ANGLE", "WRIST", 90),
        ("Twist perch angle", "PERCH_TWIST_ANGLE", "TWIST", 90),
        ("Minimum reach", "PERCH_MIN", "MIN", 0),
        ("Middle reach", "PERCH_MID", "MID", 50),
        ("Maximum reach", "PERCH_MAX", "MAX", 100),
    )
    for label, key, effective_key, fallback in perch_fields:
        saved_value = values.get(key)
        if saved_value is None:
            add(
                "Perch",
                label,
                key,
                perch_effective.get(effective_key, fallback),
                "DEFAULT",
                "Firmware default",
            )
        else:
            add("Perch", label, key, saved_value, "SAVED", "User saved")

    hover_fields = (
        ("Table plane · minimum", "hover_over_min", True),
        ("Table plane · middle", "hover_over_mid", True),
        ("Table plane · maximum", "hover_over_max", True),
        ("Upper plane · minimum", "hover_min_120", False),
        ("Upper plane · middle", "hover_mid_120", False),
        ("Upper plane · maximum", "hover_max_120", False),
    )
    for label, key, required in hover_fields:
        value = values.get(key)
        if isinstance(value, dict):
            add("IK points", label, key, value, "SAVED", "User saved")
        else:
            add(
                "IK points",
                label,
                key,
                value,
                "MISSING" if required else "OPTIONAL",
                "Required" if required else "Not saved",
            )

    stencil_fields = (
        ("Rotation correction", "rot_off_deg"),
        ("Reach correction", "ik_off_mm"),
        ("Point calibration map", "st_map"),
    )
    for label, key in stencil_fields:
        value = values.get(key)
        add(
            "Stencil",
            label,
            key,
            value,
            "SAVED" if value is not None else "MISSING",
            "User saved" if value is not None else "Run stencil calibration",
        )

    return rows
