"""Per-workflow JSON persistence.

    python -m studio.models.tests.workflow_tests
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ..config.workflows import (
    Workflow,
    Workflows,
    step_inserted_into_text,
    workflow_from_text,
)


def repository() -> tuple[Workflows, Path]:
    root = Path(tempfile.mkdtemp())
    return Workflows(root), root


def test_each_workflow_is_its_own_named_json_file() -> None:
    store, root = repository()
    duck, problem = store.create("duck_walk", "A very particular walk")
    assert duck is not None and not problem
    wave, problem = store.create("morning_wave")
    assert wave is not None and not problem

    directory = root / "workflows"
    assert sorted(path.name for path in directory.glob("*.json")) == [
        "duck_walk.json", "morning_wave.json",
    ]
    raw = json.loads((directory / "duck_walk.json").read_text())
    assert raw == {
        "workflow": {
            "name": "duck_walk",
            "description": "A very particular walk",
            "validation": True,
        },
        "steps": [],
    }


def test_updating_a_workflow_updates_its_file() -> None:
    store, root = repository()
    store.create("duck_walk")
    workflow = Workflow(
        "duck_walk",
        "Waddle twice",
        True,
        (
            {"subject": "servo", "servoName": "ELBOW", "position": 70},
            {"subject": "custom_quack", "volume": 11, "pattern": [1, 2, 3]},
        ),
    )
    assert store.save(workflow) == ""

    loaded = store.find("duck_walk")
    assert loaded == workflow
    raw = json.loads((root / "workflows" / "duck_walk.json").read_text())
    assert raw["steps"][1]["subject"] == "custom_quack"
    assert raw["steps"][1]["pattern"] == [1, 2, 3]


def test_renaming_moves_the_individual_file() -> None:
    store, root = repository()
    store.create("duck_walk")
    renamed = Workflow("duck_parade", "Same duck, grander route")
    assert store.save(renamed, previous_name="duck_walk") == ""

    directory = root / "workflows"
    assert not (directory / "duck_walk.json").exists()
    assert (directory / "duck_parade.json").exists()
    assert store.find("duck_parade") == renamed


def test_rename_will_not_overwrite_another_special_workflow() -> None:
    store, _root = repository()
    store.create("duck_walk")
    store.create("duck_parade", "Keep me")

    problem = store.save(Workflow("duck_parade"), previous_name="duck_walk")
    assert "already exists" in problem
    assert store.find("duck_walk") is not None
    assert store.find("duck_parade").description == "Keep me"


def test_names_cannot_escape_the_workflows_directory() -> None:
    store, root = repository()
    for name in ("../duck_walk", "Duck Walk", "duck walk", "duck.json", ""):
        created, problem = store.create(name)
        assert created is None and problem, name
    assert not (root / "duck_walk.json").exists()


def test_bad_json_never_replaces_the_previous_file() -> None:
    store, root = repository()
    store.create("duck_walk")
    path = root / "workflows" / "duck_walk.json"
    before = path.read_text()

    saved, problem = store.save_text("duck_walk", '{"workflow":')
    assert saved is None
    assert "line 1" in problem
    assert path.read_text() == before


def test_step_objects_are_ordered_and_open_ended() -> None:
    workflow, problem = workflow_from_text("""
    {
      "workflow": {"name": "duck_walk", "validation": false},
      "steps": [
        {"subject": "left_foot", "style": {"webbed": true}},
        {"subject": "right_foot", "pause_ms": 250}
      ]
    }
    """)
    assert not problem
    assert [step["subject"] for step in workflow.steps] == [
        "left_foot", "right_foot",
    ]
    assert workflow.steps[0]["style"] == {"webbed": True}


def test_mismatched_or_damaged_files_do_not_break_the_directory() -> None:
    store, root = repository()
    store.create("good_one")
    directory = root / "workflows"
    (directory / "broken.json").write_text("not json")
    (directory / "wrong_name.json").write_text(json.dumps({
        "workflow": {"name": "something_else"}, "steps": [],
    }))

    assert [workflow.name for workflow in store.all()] == ["good_one"]


def test_inserting_a_step_appends_to_the_document() -> None:
    # A literal step, not a palette template: this is about the model's
    # insertion rules, and borrowing the UI's button list to supply sample
    # data would tie a storage test to what the toolbar happens to offer.
    text = Workflow("firsttest", "", True, ()).to_text()
    step = {"subject": "gripper", "command": "GRAB"}

    updated, problem = step_inserted_into_text(text, dict(step))
    assert problem == ""
    raw = json.loads(updated)
    assert raw["steps"] == [step]
    assert raw["workflow"]["name"] == "firsttest"

    twice, problem = step_inserted_into_text(updated, dict(step))
    assert problem == ""
    assert len(json.loads(twice)["steps"]) == 2


def test_inserting_keeps_hand_written_json_when_it_cannot_parse() -> None:
    broken = '{"workflow": {"name": "firsttest",}}'
    updated, problem = step_inserted_into_text(broken, {"subject": "photo"})
    assert updated == broken
    assert "Fix the JSON" in problem


def test_inserting_respects_an_explicit_index() -> None:
    text = Workflow(
        "firsttest", "", True,
        ({"subject": "photo"}, {"subject": "perch"}),
    ).to_text()
    updated, problem = step_inserted_into_text(
        text, {"subject": "gripper", "command": "DROP"}, index=1
    )
    assert problem == ""
    assert [s["subject"] for s in json.loads(updated)["steps"]] == [
        "photo", "gripper", "perch",
    ]


def main() -> int:
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_")]
    for test in tests:
        test()
    print(f"OK: {len(tests)} workflow model tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
