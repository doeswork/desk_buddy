"""Workflow Studio UI: one document, given the whole window.

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
    QFrame,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QWidget,
)

from ....models.config.workflows import Workflow, Workflows
from .palette import GroupButton, StepPalette
from .steps import GROUPS, JOINTS, all_templates, find
from . import WorkflowNavigator, WorkflowsWorkspace, WorkflowTitle

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


def test_the_sidebar_is_the_search_and_the_list_and_nothing_else() -> None:
    """It carried a "Workflows" heading and a New button above the search.

    Both were second copies: the workspace bar already names the workspace,
    and New is a document verb that belongs on the toolbar beside Save and
    Delete. A panel that says its own name is the app saying it twice.
    """
    space, _store = populated()
    side = space.side()

    assert not [label for label in side.findChildren(QLabel)
                if label.text() == "Workflows"]
    assert not [button for button in side.findChildren(QPushButton)
                if button.text() == "New"]

    # And New is still reachable, on the bar where the other verbs are.
    assert "New" in [action.label for action in space.build_actions()
                     if hasattr(action, "label")]


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


def test_the_page_is_the_document_and_nothing_else() -> None:
    """No card, no step table, no prose — just facts and the editor.

    The step overview restated the JSON below it and charged a third of the
    window to do it, so the page carries one strip of facts and the editor.
    """
    space, _store = populated()
    widget = space.page.widget()

    assert space.page.fills_height
    assert widget.findChild(QTableWidget) is None
    assert widget.findChild(QFrame, "Card") is None
    assert widget.findChild(QWidget, "WorkflowMeta") is not None

    # The one heading is the open workflow's name, not a fixed page title.
    titles = [button.text() for button in widget.findChildren(WorkflowTitle)]
    assert titles == ["duck_walk"]


def test_the_editor_takes_the_height_the_window_gives_it() -> None:
    """100% of what is left, at any window size — the point of the change."""
    space, _store = populated()
    widget = space.page.widget()

    for height in (500, 900):
        widget.resize(1000, height)
        widget.show()
        _app.processEvents()

        editor = space.editor
        assert editor is not None
        # Everything above the editor is one heading and one line of facts,
        # so the editor gets the overwhelming majority of the page.
        assert editor.height() > 0.7 * height, (height, editor.height())
        # And the page never grows a scrollbar of its own around it.
        assert not widget.verticalScrollBar().isVisible()
        widget.hide()


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


def test_the_toolbar_holds_the_documents_verbs_and_nothing_else() -> None:
    """The bar makes, saves and deletes workflows. That is all it does.

    The step vocabulary used to live here too — thirty buttons of permanent
    workspace chrome to edit the one document below them. It moved to the
    page header, so the bar is short enough to read again.
    """
    space, _store = populated()
    labels = [action.label for action in space.build_actions()
              if hasattr(action, "label")]
    assert labels == ["New", "Save JSON", "Delete Workflow"]
    # Still no per-step run/edit controls; steps are read-only for now.
    assert "Run" not in labels


def test_no_step_template_is_a_toolbar_button() -> None:
    """The regression the move exists to prevent: a step that drifts back
    onto the bar puts the wall back one button at a time."""
    space, _store = populated()
    labels = {action.label for action in space.build_actions()
              if hasattr(action, "label")}
    for template in all_templates():
        assert template.label not in labels, template.key


def test_the_palette_is_one_menu_per_group_not_a_button_per_step() -> None:
    """Nine buttons to scan instead of thirty, each opening its own group."""
    space, _store = populated()
    palette = StepPalette(space.insert_step)

    groups = palette.findChildren(GroupButton)
    assert [button.text() for button in groups] == [
        group.label for group in GROUPS
    ]
    for button, group in zip(groups, GROUPS):
        entries = [action.text() for action in button.menu().actions()]
        assert entries == [t.label for t in group.templates], group.label


def test_the_palette_offers_every_template_exactly_once() -> None:
    """Grouping is a rearrangement, not a cull: nothing the firmware can do
    may be lost behind a menu that forgot it."""
    palette = StepPalette(lambda key: None)
    keys = [
        action.data()
        for button in palette.findChildren(GroupButton)
        for action in button.menu().actions()
    ]
    assert sorted(keys) == sorted(t.key for t in all_templates())
    assert len(keys) == len(set(keys))


def test_the_palette_sits_on_the_header_line_beside_the_name() -> None:
    """Level with the workflow it appends to, not a window's height below
    it in the body."""
    space, _store = populated()
    header = space.page.build_header()
    titles = [button.text() for button in header.findChildren(WorkflowTitle)]
    assert titles == ["duck_walk"]
    assert header.findChildren(StepPalette), "palette belongs in the header"

    body = space.page.build_page()
    assert not body.findChildren(StepPalette), "and only in the header"


def test_servos_are_one_group_rather_than_three_loose_buttons() -> None:
    """Elbow, Wrist and Twist are three names for one verb: they belong
    behind it, not competing with Photo for the same row."""
    servo = next(group for group in GROUPS if group.label == "Servo")
    assert [t.label for t in servo.templates] == ["Elbow", "Wrist", "Twist"]
    assert {t.subject for t in servo.templates} == {"servo"}
    assert {t.fields["servoName"] for t in servo.templates} == set(JOINTS)


def test_step_menus_are_dead_until_a_workflow_is_open() -> None:
    """There is nothing to append a step to, so the palette says so by being
    disabled rather than failing when picked."""
    # An empty store is the only way there is genuinely nothing open:
    # `selected` falls back to the first workflow whenever one exists.
    space = WorkflowsWorkspace(repository())
    assert space.selected is None

    # Held in a name: an unreferenced header is collected mid-test, taking
    # the palette Qt-side with it.
    header = space.page.build_header()
    palette = header.findChildren(StepPalette)
    assert palette, "the palette should still be shown, not hidden"
    groups = palette[0].findChildren(GroupButton)
    assert groups, "every group should still be listed"
    assert not any(button.isEnabled() for button in groups)


def test_picking_a_menu_entry_inserts_that_step() -> None:
    """The wiring the menus exist for: the entry's own template, not the
    group's first one."""
    space, _store = populated()
    space.select_workflow("morning_wave")
    space.announce = lambda _text: None

    palette = StepPalette(space.insert_step)
    gripper = palette.buttons["Gripper"]
    drop = next(a for a in gripper.menu().actions() if a.text() == "Drop")
    drop.trigger()

    draft = json.loads(space.draft_for(space.selected))
    assert draft["steps"] == [{"subject": "gripper", "command": "DROP"}]


def test_the_file_path_is_not_a_line_on_the_page() -> None:
    """It was the longest thing on screen and the least often wanted —
    forty unchanging characters of directory in front of the name already
    in the title. It lives in the title's menu now."""
    space, store = populated()
    body = space.page.build_page()

    path = str(store.path_for("duck_walk"))
    shown = [label.text() for label in body.findChildren(QLabel)]
    assert path not in shown
    assert not any(str(store.directory) in text for text in shown)
    # The facts worth a line are still on it.
    facts = [label.text() for label in body.findChildren(QLabel)
             if label.objectName() == "WorkflowMetaFact"]
    assert "2 steps" in facts
    assert "validation on" in facts


def test_the_title_is_the_menu_for_the_document_it_names() -> None:
    space, _store = populated()
    header = space.page.build_header()
    title = header.findChildren(WorkflowTitle)[0]

    assert title.text() == "duck_walk"
    assert [action.text() for action in title.menu().actions()] == [
        "Rename…", "Copy file path"
    ]


def test_copy_file_path_puts_the_path_on_the_clipboard() -> None:
    """Where the path went: copying it is what it was actually for."""
    space, store = populated()
    said: list[str] = []
    space.announce = said.append

    header = space.page.build_header()
    title = header.findChildren(WorkflowTitle)[0]
    title.menu_actions["copy"].trigger()

    path = str(store.path_for("duck_walk"))
    assert QApplication.clipboard().text() == path
    assert said and path in said[0]


def test_rename_moves_the_file_rather_than_copying_it() -> None:
    space, store = populated()
    said: list[str] = []
    space.announce = said.append

    assert space.rename("duck_waddle") == ""
    assert store.find("duck_waddle") is not None
    assert store.find("duck_walk") is None, "the old file must not survive"
    assert not store.path_for("duck_walk").exists()
    assert space.selected.name == "duck_waddle"
    assert said == ["Renamed to duck_waddle.json"]


def test_rename_keeps_edits_that_were_never_saved() -> None:
    """A rename is a save of the open document under a new name, so the
    unsaved work in the editor goes with it instead of being discarded."""
    space, store = populated()
    space.select_workflow("morning_wave")
    space.announce = lambda _text: None
    # Held in a name: an unreferenced body is collected mid-test, taking
    # the editor `insert_step` writes through with it.
    body = space.page.build_page()   # the editor is live, as on screen
    assert body is not None
    assert space.insert_step("photo") == ""

    assert space.rename("afternoon_wave") == ""
    saved = store.find("afternoon_wave")
    assert saved is not None
    assert saved.steps == ({"subject": "photo"},)


def test_rename_refuses_a_name_that_is_not_a_filename() -> None:
    space, store = populated()
    problem = space.rename("Duck Walk")
    assert problem and "lowercase" in problem
    assert store.find("duck_walk") is not None, "and changes nothing"


def test_rename_refuses_to_overwrite_another_workflow() -> None:
    space, store = populated()
    problem = space.rename("morning_wave")
    assert "already exists" in problem
    # Both survive, with the one that was there first untouched.
    assert store.find("duck_walk") is not None
    assert store.find("morning_wave").step_count == 0


def test_rename_to_the_same_name_is_a_no_op() -> None:
    space, _store = populated()
    said: list[str] = []
    space.announce = said.append
    assert space.rename("duck_walk") == ""
    assert said == [], "nothing happened, so nothing is announced"


def test_rename_refuses_to_rename_unparseable_json() -> None:
    """Same rule the palette follows: never write over JSON we cannot read,
    because the rewrite would silently discard whatever it says."""
    space, store = populated()
    space.remember_draft("duck_walk", "{ not json")
    problem = space.rename("duck_waddle")
    assert problem
    assert store.find("duck_walk") is not None
    assert store.find("duck_waddle") is None


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
