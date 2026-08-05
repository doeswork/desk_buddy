from __future__ import annotations

import json
from typing import Any, Dict


def perch_effective_signature(effective: Dict[str, Any]) -> str:
    """A stable string identifying the current effective perch pose.

    Used to skip re-syncing perch fields when nothing has actually changed,
    mirroring the same signature trick IK rows use for the same reason: a
    heartbeat repeating the same values must never be mistaken for new data
    worth overwriting an in-progress edit with.
    """
    return json.dumps(effective, sort_keys=True, default=str)
