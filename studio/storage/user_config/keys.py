"""Every setting the app persists, declared once.

A key is a name, a type, and a default. Nothing else in the app may invent a
settings key inline: a typo in a raw string is a silently-lost preference that
nobody notices until a user reports "it forgets my theme", whereas a typo here
is an ImportError at startup.

The type matters more than it looks. QSettings stores values as text, and the
backends disagree about what comes back — a float written on Linux may return
as the string "1.3" on macOS. Declaring the type lets Settings.get() coerce on
read, so callers always get what they expect.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Key:
    name: str          # the "section/key" path written to the file
    type: type         # what get() coerces to
    default: Any       # used on first run, and whenever the stored value is junk


# ---- Appearance ---------------------------------------------------------
THEME = Key("appearance/theme", str, "light")
ZOOM_INDEX = Key("appearance/zoom_index", int, 2)   # index into ZOOM_LEVELS

# ---- Window -------------------------------------------------------------
# Qt serialises these itself; we only carry the bytes.
GEOMETRY = Key("window/geometry", bytes, b"")
WINDOW_STATE = Key("window/state", bytes, b"")
LAST_PAGE = Key("window/last_page", int, 0)

# ---- Vision providers ---------------------------------------------------
# Model and worker stay separate because routing is explicit: seeing the same
# model advertised by a second worker must never silently move a job.
VISION_DETECTOR_MODEL = Key("vision/detector_model", str, "owlv2-base")
VISION_DETECTOR_WORKER = Key("vision/detector_worker", str, "zero-shot-hf-1")
VISION_DEPTH_MODEL = Key("vision/depth_model", str, "depth-anything-v2-small")
VISION_DEPTH_WORKER = Key("vision/depth_worker", str, "depth-hf-1")
VISION_DETECTOR_CANDIDATE = Key("vision/detector_candidate", str, "owlv2-base")
VISION_DEPTH_CANDIDATE = Key("vision/depth_candidate", str, "depth-anything-v2-small")
VISION_MLP_CANDIDATE = Key("vision/mlp_candidate", str, "")
VISION_DETECTOR_AUTOSTART = Key("vision/detector_autostart", bool, False)
VISION_DEPTH_AUTOSTART = Key("vision/depth_autostart", bool, False)
VISION_MLP_AUTOSTART = Key("vision/mlp_autostart", bool, False)
VISION_TRAINER_AUTOSTART = Key("vision/trainer_autostart", bool, False)

ALL = (
    THEME,
    ZOOM_INDEX,
    GEOMETRY,
    WINDOW_STATE,
    LAST_PAGE,
    VISION_DETECTOR_MODEL,
    VISION_DETECTOR_WORKER,
    VISION_DEPTH_MODEL,
    VISION_DEPTH_WORKER,
    VISION_DETECTOR_CANDIDATE,
    VISION_DEPTH_CANDIDATE,
    VISION_MLP_CANDIDATE,
    VISION_DETECTOR_AUTOSTART,
    VISION_DEPTH_AUTOSTART,
    VISION_MLP_AUTOSTART,
    VISION_TRAINER_AUTOSTART,
)
