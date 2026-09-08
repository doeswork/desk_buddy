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
from .steps import GROUPS, all_templates, find
from . import (
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


def test_the_toolbar_leads_with_the_workflows_own_verbs() -> None:
    """The document's actions come first, before the step vocabulary — a
    user looking for Save should not have to scan 28 step names to find it."""
    space, _store = populated()
    labels = [action.label for action in space.build_actions()
              if hasattr(action, "label")]
    assert labels[:3] == ["New", "Save JSON", "Delete Workflow"]
    # Still no per-step run/edit controls; steps are read-only for now.
    assert "Run" not in labels


def test_the_toolbar_offers_a_button_for_every_template() -> None:
    """The palette is the toolbar now, not a card in the page body.

    Adding steps is the work of this workspace, and a palette below the
    document is one the user scrolls past to reach the thing it edits.
    """
    space, _store = populated()
    labels = {action.label for action in space.build_actions()
              if hasattr(action, "label")}
    for template in all_templates():
        assert template.label in labels, template.key


def test_step_buttons_are_dead_until_a_workflow_is_open() -> None:
    """There is nothing to append a step to, so the button says so by being
    disabled rather than failing when pressed."""
    # An empty store is the only way there is genuinely nothing open:
    # `selected` falls back to the first workflow whenever one exists.
    space = WorkflowsWorkspace(repository())
    assert space.selected is None

    steps = [action for action in space.build_actions()
             if hasattr(action, "label")
             and action.label not in ("New", "Save JSON", "Delete Workflow")]
    assert steps, "the palette should still be listed"
    assert not any(action.clickable for action in steps)


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


# ---- The step palette ----------------------------------------------------

def test_every_template_produces_a_step_the_model_accepts() -> None:
    store = repository()
    for template in all_templates():
        workflow = Workflow("probe", "", True, (template.to_step(),))
        assert store.save(workflow) == "", template.key
        saved = store.find("probe")
        assert saved is not None
        assert saved.steps[0]["subject"] == template.subject
        store.delete("probe")


def test_template_keys_are_unique_and_findable() -> None:
    keys = [template.key for template in all_templates()]
    assert len(keys) == len(set(keys))
    for key in keys:
        assert find(key) is not None
    assert find("no_such_template") is None
    assert GROUPS, "the palette needs at least one group"


def test_templates_hand_out_private_copies() -> None:
    template = find("servo_elbow")
    assert template is not None
    first = template.to_step()
    first["position"] = 5
    assert template.to_step()["position"] == 90


def test_the_palette_covers_every_firmware_action() -> None:
    """The palette exists so nobody has to memorise the firmware vocabulary.

    A missing action defeats that quietly: the user simply never learns the
    robot can do it. So the list is checked against firmware/ActionRouter.cpp
    — the route table is the real contract, not the README beside it.
    """
    import re
    from pathlib import Path

    from .steps import all_templates

    router = Path(__file__).resolve().parents[4] / "firmware" / "ActionRouter.cpp"
    if not router.exists():          # firmware not checked out beside Studio
        return

    table = router.read_text()
    table = table[table.index("ROUTES[]"):]
    actions = set(re.findall(r'\{"(\w+)",\s', table))
    assert actions, "no routes parsed — has the table's shape changed?"

    offered = {template.subject for template in all_templates()}
    missing = sorted(actions - offered)
    assert not missing, f"firmware actions with no palette button: {missing}"


def test_calibration_templates_send_the_field_their_type_expects() -> None:
    """`calibrate` is really several actions behind one name.

    A hover point carries `distance`; a perch value carries `value`. Sending
    the wrong one is accepted by the transport and rejected by the robot, so
    the template has to pair them correctly or the button is a trap.
    """
    from .steps import (
        HOVER_TYPES, PERCH_ANGLE_TYPES, PERCH_REACH_TYPES, all_templates,
    )

    for template in all_templates():
        if template.subject != "calibrate":
            continue
        step = template.to_step()
        kind = step["calibration_type"]
        if kind in HOVER_TYPES:
            assert "distance" in step, template.key
        elif kind in PERCH_ANGLE_TYPES + PERCH_REACH_TYPES:
            assert "value" in step, template.key
        else:
            raise AssertionError(f"unknown calibration_type: {kind}")


def test_enumerated_fields_use_values_the_firmware_accepts() -> None:
    """A default that is not in the enumeration is a button that fails."""
    from .steps import (
        DIRECTIONS, GRIPPER_COMMANDS, JOINTS, SPEEDS, STENCIL_COMMANDS,
        all_templates,
    )

    for template in all_templates():
        step = template.to_step()
        if step.get("subject") == "servo":
            assert step["servoName"] in JOINTS, template.key
        if step.get("subject") == "gripper" and "command" in step:
            assert step["command"] in GRIPPER_COMMANDS, template.key
        if step.get("subject") == "stencilCalibrate":
            assert step["command"] in STENCIL_COMMANDS, template.key
        if "direction" in step:
            assert step["direction"] in DIRECTIONS, template.key
        if isinstance(step.get("speed"), str):
            assert step["speed"] in SPEEDS, template.key


def main() -> int:
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_")]
    for test in tests:
        test()
    print(f"OK: {len(tests)} workflow UI tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
