"""User-authored workflows, one readable JSON file per workflow.

Unlike the small record collections backed by one shared :class:`Store`, a
workflow is deliberately its own document. That makes workflows easy to
copy, diff, hand-edit, and share without carrying every other workflow too::

    Linux   ~/.local/share/DeskBuddy/Studio/workflows/duck_walk.json

The file shape follows the useful part of the former Rails API: workflow
metadata beside an ordered array of steps. Step dictionaries are intentionally
open-ended. Known firmware actions fit, but a custom subject and custom
parameters are preserved too — workflows are allowed to be special.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...storage.store import Store, data_dir

DIRECTORY_NAME = "workflows"
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
NAME_RULE = (
    "Use 1–64 lowercase letters, numbers, underscores, or hyphens, "
    "starting with a letter or number."
)


def validate_name(name: str) -> str:
    """Empty string when ``name`` is also a safe, portable filename."""
    if not isinstance(name, str) or not name:
        return "A workflow name is required."
    if name != name.strip():
        return "A workflow name cannot start or end with spaces."
    if not NAME_PATTERN.fullmatch(name):
        return NAME_RULE
    return ""


def _validated_steps(raw: Any) -> tuple[tuple[dict[str, Any], ...], str]:
    if not isinstance(raw, list):
        return (), "'steps' must be an array."

    steps: list[dict[str, Any]] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            return (), f"Step {index} must be a JSON object."
        subject = item.get("subject")
        if not isinstance(subject, str) or not subject.strip():
            return (), f"Step {index} needs a non-empty 'subject'."
        if subject != subject.strip():
            return (), f"Step {index}'s subject cannot have outside spaces."
        if not all(isinstance(key, str) for key in item):
            return (), f"Step {index} has a non-text property name."
        # Round-tripping creates a private deep copy and proves every custom
        # value can really be represented in the file before a write begins.
        try:
            step = json.loads(json.dumps(item))
        except (TypeError, ValueError) as error:
            return (), f"Step {index} is not valid JSON data: {error}"
        steps.append(step)
    return tuple(steps), ""


@dataclass(frozen=True)
class Workflow:
    name: str
    description: str = ""
    validation: bool = True
    steps: tuple[dict[str, Any], ...] = ()

    @property
    def filename(self) -> str:
        return f"{self.name}.json"

    @property
    def step_count(self) -> int:
        return len(self.steps)

    def to_json(self) -> dict[str, Any]:
        return {
            "workflow": {
                "name": self.name,
                "description": self.description,
                "validation": self.validation,
            },
            "steps": [dict(step) for step in self.steps],
        }

    def to_text(self) -> str:
        return json.dumps(self.to_json(), indent=2) + "\n"


def workflow_from_json(
    raw: Any, *, expected_name: str = ""
) -> tuple[Workflow | None, str]:
    """Validate and normalize one decoded workflow document."""
    if not isinstance(raw, dict):
        return None, "The workflow file must contain one JSON object."

    unexpected = set(raw) - {"workflow", "steps"}
    if unexpected:
        names = ", ".join(sorted(str(key) for key in unexpected))
        return None, f"Unexpected top-level properties: {names}."

    metadata = raw.get("workflow")
    if not isinstance(metadata, dict):
        return None, "'workflow' must be a JSON object."

    unexpected_metadata = set(metadata) - {"name", "description", "validation"}
    if unexpected_metadata:
        names = ", ".join(sorted(str(key) for key in unexpected_metadata))
        return None, f"Unexpected workflow properties: {names}."

    name = metadata.get("name")
    problem = validate_name(name)
    if problem:
        return None, problem
    if expected_name and name != expected_name:
        return None, (
            f"The file is named {expected_name}.json but contains workflow "
            f"name {name!r}."
        )

    description = metadata.get("description", "")
    if not isinstance(description, str):
        return None, "Workflow 'description' must be text."
    validation = metadata.get("validation", True)
    if not isinstance(validation, bool):
        return None, "Workflow 'validation' must be true or false."

    steps, problem = _validated_steps(raw.get("steps", []))
    if problem:
        return None, problem
    return Workflow(name, description, validation, steps), ""


def step_inserted_into_text(
    text: str, step: dict[str, Any], *, index: int | None = None
) -> tuple[str, str]:
    """Append ``step`` to the steps array of a workflow document.

    Works on the editor's current text rather than the saved file, so an
    insert lands in whatever the person is editing. Returns the new text and
    an empty problem, or the unchanged text and the reason it could not be
    parsed — a palette button must never quietly discard hand-written JSON.
    """
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as error:
        return text, (
            f"Fix the JSON before inserting a step: line {error.lineno}, "
            f"{error.msg}."
        )
    if not isinstance(raw, dict):
        return text, "The workflow file must contain one JSON object."

    steps = raw.get("steps", [])
    if not isinstance(steps, list):
        return text, "'steps' must be an array before a step can be added."

    steps = list(steps)
    position = len(steps) if index is None else max(0, min(int(index), len(steps)))
    steps.insert(position, json.loads(json.dumps(step)))
    raw["steps"] = steps
    return json.dumps(raw, indent=2) + "\n", ""


def workflow_from_text(text: str) -> tuple[Workflow | None, str]:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as error:
        return None, f"Invalid JSON on line {error.lineno}: {error.msg}."
    return workflow_from_json(raw)


class Workflows:
    """The directory of individual workflow documents."""

    def __init__(self, directory: Path | None = None) -> None:
        self._directory = directory

    @property
    def directory(self) -> Path:
        root = self._directory if self._directory is not None else data_dir()
        return root / DIRECTORY_NAME

    def path_for(self, name: str) -> Path:
        problem = validate_name(name)
        if problem:
            raise ValueError(problem)
        return self.directory / f"{name}.json"

    def all(self) -> list[Workflow]:
        directory = self.directory
        if not directory.exists():
            return []

        found: list[Workflow] = []
        try:
            paths = sorted(directory.glob("*.json"))
        except OSError:
            return []
        for path in paths:
            raw = Store(path.stem, directory=directory).read(default=None)
            workflow, _problem = workflow_from_json(raw, expected_name=path.stem)
            if workflow is not None:
                found.append(workflow)
        return sorted(found, key=lambda workflow: workflow.name)

    def find(self, name: str) -> Workflow | None:
        if validate_name(name):
            return None
        raw = Store(name, directory=self.directory).read(default=None)
        workflow, _problem = workflow_from_json(raw, expected_name=name)
        return workflow

    def create(
        self,
        name: str,
        description: str = "",
        *,
        validation: bool = True,
    ) -> tuple[Workflow | None, str]:
        problem = validate_name(name)
        if problem:
            return None, problem
        if not isinstance(description, str):
            return None, "A workflow description must be text."
        if not isinstance(validation, bool):
            return None, "Workflow validation must be true or false."
        if self.path_for(name).exists():
            return None, f"A workflow called {name!r} already exists."
        workflow = Workflow(name, description.strip(), validation, ())
        problem = self.save(workflow)
        return (None, problem) if problem else (workflow, "")

    def save(self, workflow: Workflow, *, previous_name: str = "") -> str:
        """Atomically save one document; optionally rename its old file."""
        normalized, problem = workflow_from_json(workflow.to_json())
        if problem or normalized is None:
            return problem or "The workflow is invalid."

        if previous_name and validate_name(previous_name):
            return "The original workflow name is invalid."

        target = self.path_for(normalized.name)
        if previous_name and previous_name != normalized.name and target.exists():
            return f"A workflow called {normalized.name!r} already exists."

        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self.directory.chmod(0o700)
            Store(normalized.name, directory=self.directory).write(
                normalized.to_json()
            )
            if previous_name and previous_name != normalized.name:
                Store(previous_name, directory=self.directory).delete()
        except OSError as error:
            return f"Could not save {normalized.filename}: {error}"
        return ""

    def save_text(
        self, current_name: str, text: str
    ) -> tuple[Workflow | None, str]:
        workflow, problem = workflow_from_text(text)
        if problem or workflow is None:
            return None, problem
        problem = self.save(workflow, previous_name=current_name)
        return (None, problem) if problem else (workflow, "")

    def delete(self, name: str) -> str:
        if validate_name(name):
            return "The workflow name is invalid."
        if not self.path_for(name).exists():
            return f"No workflow called {name!r} exists."
        try:
            Store(name, directory=self.directory).delete()
        except OSError as error:
            return f"Could not delete {name}.json: {error}"
        return ""


_instance: Workflows | None = None


def workflows() -> Workflows:
    global _instance
    if _instance is None:
        _instance = Workflows()
    return _instance
