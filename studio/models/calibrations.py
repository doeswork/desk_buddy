"""Calibration results, one record per step per robot.

The Calibration workspace walks five steps — base and perch, IK, visual,
reach and grab, stencil — and each produces values a robot needs to move
correctly. Those are records in the strict sense: lose them and the user
recalibrates by hand, which is minutes of work with a physical arm.

Nothing writes these yet; the workspace's steps are still placeholders. The
model is here so that when a step does produce a result it has somewhere to
put it, and so the shape is decided once rather than per step.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..storage.store import Store

# The steps, in the order the workspace runs them. Keys, not labels: a record
# written today has to still be findable when the wording on screen changes.
STEP_KEYS = ("base_perch", "ik", "visual", "reach_grab", "stencil")


@dataclass(frozen=True)
class Calibration:
    """One step's result for one robot.

    `values` is deliberately an open dict: what "the IK step produced" means
    is not settled, and pinning a schema now would mean migrating it as soon
    as the first real step is written.
    """

    robot: str
    step: str
    values: dict = field(default_factory=dict)
    saved_at: str = ""

    def to_json(self) -> dict:
        return {
            "robot": self.robot,
            "step": self.step,
            "values": self.values,
            "saved_at": self.saved_at,
        }

    @classmethod
    def from_json(cls, raw) -> "Calibration | None":
        if not isinstance(raw, dict):
            return None
        robot, step = raw.get("robot"), raw.get("step")
        if not isinstance(robot, str) or not isinstance(step, str):
            return None
        values = raw.get("values")
        saved_at = raw.get("saved_at")
        return cls(
            robot=robot,
            step=step,
            values=values if isinstance(values, dict) else {},
            saved_at=saved_at if isinstance(saved_at, str) else "",
        )


class Calibrations:
    """Every calibration result, backed by one JSON file."""

    def __init__(self, store: Store | None = None) -> None:
        self._store = store if store is not None else Store("calibrations")

    @property
    def path(self):
        return self._store.path

    def all(self) -> list[Calibration]:
        raw = self._store.read(default=[])
        if not isinstance(raw, list):
            return []
        found = [Calibration.from_json(entry) for entry in raw]
        return [item for item in found if item is not None]

    def for_robot(self, robot: str) -> list[Calibration]:
        return [item for item in self.all() if item.robot == robot]

    def find(self, robot: str, step: str) -> Calibration | None:
        return next(
            (i for i in self.all() if i.robot == robot and i.step == step), None
        )

    def save(self, calibration: Calibration) -> None:
        """Record one step's result, replacing any earlier run of that step."""
        others = [
            item
            for item in self.all()
            if not (item.robot == calibration.robot and item.step == calibration.step)
        ]
        self._save([*others, calibration])

    def clear(self, robot: str) -> None:
        self._save([item for item in self.all() if item.robot != robot])

    def _save(self, entries: list[Calibration]) -> None:
        ordered = sorted(entries, key=lambda item: (item.robot, item.step))
        self._store.write([item.to_json() for item in ordered])


_instance: Calibrations | None = None


def calibrations() -> Calibrations:
    global _instance
    if _instance is None:
        _instance = Calibrations()
    return _instance
