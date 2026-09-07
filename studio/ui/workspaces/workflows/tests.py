"""Workflow Studio UI, without individual step buttons.

    QT_QPA_PLATFORM=offscreen python -m studio.ui.workspaces.workflows.tests
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
)

from ....models.config.workflows import Workflow, Workflows
from ....models.config.workflow_steps import all_templates, find
from . import (
    StepPaletteCard,
    StepTable,
    WorkflowNavigator,
    WorkflowsWorkspace,
    step_details,
)

_app = QApplication.instance() or QApplication([])


def repository() -> Workflows:
    return Workflows(Path(tempfile.mkdtemp()))


def populated() -> tuple[WorkflowsWorkspace, Workflows]:
    store = repository()
    store.save(Workflow(
        "duck_walk",
        "A very particular walk",
        True,
        (
            {"subject": "servo", "servoName": "ELBOW", "position": 70},
            {"subject": "custom_quack", "volume": 11},
        ),
    ))
    store.create("morning_wave", "A polite greeting")
    return WorkflowsWorkspace(store), store


def test_sidebar_is_searchable_and_selects_workflows() -> None:
    space, _store = populated()
    side = space.side()
    assert isinstance(side, WorkflowNavigator)
    assert side.list.count() == 2
    assert side.list.item(0).text() == "duck_walk"

    side.search.setText("polite")
    assert side.list.count() == 1
    assert side.list.item(0).text() == "morning_wave"

    side.list.setCurrentRow(0)
    assert space.selected.name == "morning_wave"

    side.search.setText("missing")
    assert side.list.count() == 0
    assert side.empty.isVisibleTo(side)
    assert "match" in side.empty.text()


def test_step_overview_is_read_only_and_has_no_step_buttons() -> None:
    space, _store = populated()
    widget = space.page.widget()
    table = widget.findChild(QTableWidget, "WorkflowSteps")
    assert isinstance(table, StepTable)
    assert table.rowCount() == 2
    assert table.item(0, 1).text() == "servo"
    assert "Elbow" in table.item(0, 2).text()
    assert table.item(1, 1).text() == "custom_quack"
    assert table.findChildren(QPushButton) == []


def test_json_editor_updates_the_same_individual_file() -> None:
    space, store = populated()
    space.page.widget()
    assert isinstance(space.editor, QPlainTextEdit)

    document = json.loads(space.editor.toPlainText())
    document["workflow"]["description"] = "Now with extra waddling"
    document["steps"].append({"subject": "perch"})
    space.editor.setPlainText(json.dumps(document, indent=2))
    assert space.save() == ""

    saved = store.find("duck_walk")
    assert saved.description == "Now with extra waddling"
    assert saved.step_count == 3
    assert store.path_for("duck_walk").exists()


def test_editing_the_name_renames_the_json_file_and_sidebar_item() -> None:
    space, store = populated()
    side = space.side()
    space.page.widget()
    old_path = store.path_for("duck_walk")

    document = json.loads(space.editor.toPlainText())
    document["workflow"]["name"] = "duck_parade"
    space.editor.setPlainText(json.dumps(document, indent=2))
    assert space.save() == ""

    assert not old_path.exists()
    assert store.path_for("duck_parade").exists()
    assert {side.list.item(row).text() for row in range(side.list.count())} == {
        "duck_parade", "morning_wave",
    }


def test_invalid_json_stays_in_the_editor_with_a_specific_error() -> None:
    space, store = populated()
    space.page.widget()
    before = store.path_for("duck_walk").read_text()
    space.editor.setPlainText('{"workflow":')

    problem = space.save()
    assert "line 1" in problem
    assert store.path_for("duck_walk").read_text() == before
    assert space.editor.toPlainText() == '{"workflow":'
    errors = [
        label.text() for label in space.page.widget().findChildren(QLabel)
        if label.objectName() == "FieldError"
    ]
    assert any("line 1" in error for error in errors)


def test_creating_a_workflow_immediately_creates_its_file() -> None:
    store = repository()
    space = WorkflowsWorkspace(store)
    said: list[str] = []
    space.announce = said.append

    assert space.create("duck_walk", "A special walk") == ""
    assert store.path_for("duck_walk").exists()
    assert space.selected.name == "duck_walk"
    assert said == ["Created duck_walk.json"]


def test_toolbar_has_no_run_or_individual_step_actions() -> None:
    space, _store = populated()
    labels = [action.label for action in space.build_actions()
              if hasattr(action, "label")]
    assert labels == ["New", "Save JSON", "Delete Workflow"]
    assert "Run" not in labels
    assert "Add Step" not in labels


def test_palette_offers_a_button_for_every_template() -> None:
    space, _store = populated()
    card = StepPaletteCard(space)
    labels = {
        button.text()
        for button in card.findChildren(QPushButton)
    }
    for template in all_templates():
        assert template.label in labels, template.key


def test_palette_button_appends_a_step_to_the_draft() -> None:
    space, store = populated()
    space.select_workflow("morning_wave")
    said: list[str] = []
    space.announce = said.append

    assert space.insert_step("gripper_grab") == ""
    draft = json.loads(space.draft_for(space.selected))
    assert draft["steps"] == [{"subject": "gripper", "command": "GRAB"}]
    # Inserting stages an edit; it must not write the file on its own.
    assert store.find("morning_wave").step_count == 0
    assert said and "Grab" in said[0]


def test_palette_insert_then_save_persists_the_step() -> None:
    space, store = populated()
    space.select_workflow("morning_wave")
    assert space.insert_step("photo") == ""
    assert space.save() == ""
    saved = store.find("morning_wave")
    assert saved.steps == ({"subject": "photo"},)


def test_palette_refuses_to_clobber_unparseable_json() -> None:
    space, _store = populated()
    space.select_workflow("morning_wave")
    space.remember_draft("morning_wave", "{ not json")

    problem = space.insert_step("photo")
    assert "Fix the JSON" in problem
    assert space.draft_for(space.selected) == "{ not json"


def test_base_rotate_steps_read_as_words_not_raw_json() -> None:
    detail = step_details({
        "subject": "baseRotate", "controlType": "ENCODER",
        "direction": "RIGHT", "value": 10,
    })
    assert detail == "Rotate 10 steps right"
    assert step_details({"subject": "detect_color"}) == "Detect color"
    assert step_details(
        {"subject": "gripper", "command": "SOFTHOLD"}
    ) == "Soft Hold"
    assert step_details({"subject": "gripper", "position": 120}) == "Gripper → 120°"


def main() -> int:
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_")]
    for test in tests:
        test()
    print(f"OK: {len(tests)} workflow UI tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
