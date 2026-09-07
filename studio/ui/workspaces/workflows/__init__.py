"""Workflow Studio UI.

The Rails app's searchable workflow navigator, ordered step overview, and
whole-document JSON editor are kept here in a native Qt shape. Step rows are
read-only for now: there are deliberately no run/edit/delete/reorder buttons
for individual steps yet.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ....models.config.workflow_steps import GROUPS, StepTemplate
from ....models.config.workflow_steps import find as find_template
from ....models.config.workflows import (
    NAME_RULE,
    Workflow,
    Workflows,
    step_inserted_into_text,
    workflows,
)
from ...components import ActionSpec, Card, Column, Separator
from ...pages.base import Page
from ...theme.metrics import CARD_GAP, ROW_PADDING
from ..base import Workspace


def _button(
    label: str,
    on_click: Callable[[], None],
    *,
    primary: bool = False,
) -> QPushButton:
    button = QPushButton(label)
    button.setObjectName("ContextPrimary" if primary else "ContextAction")
    button.setCursor(Qt.PointingHandCursor)
    button.clicked.connect(on_click)
    return button


class WorkflowNavigator(QWidget):
    """Searchable workflow list, inspired by the Rails secondary nav."""

    def __init__(self, *, on_select, on_new, on_filter) -> None:
        super().__init__()
        self._on_select = on_select

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        heading = QWidget()
        heading_layout = QHBoxLayout(heading)
        heading_layout.setContentsMargins(ROW_PADDING, ROW_PADDING, ROW_PADDING, 4)
        title = QLabel("Workflows")
        title.setObjectName("CardTitle")
        heading_layout.addWidget(title, 1)
        heading_layout.addWidget(_button("New", on_new, primary=True))
        layout.addWidget(heading)

        self.search = QLineEdit()
        self.search.setObjectName("WorkflowSearch")
        self.search.setPlaceholderText("Search workflows")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Search workflows")
        self.search.textChanged.connect(on_filter)
        layout.addWidget(self.search)

        self.list = QListWidget()
        self.list.setObjectName("SidePanel")
        self.list.setCursor(Qt.PointingHandCursor)
        self.list.currentItemChanged.connect(self._selected)
        layout.addWidget(self.list, 1)

        self.empty = QLabel(
            "No workflows yet.\nCreate one to give your Desk Buddy a routine."
        )
        self.empty.setObjectName("WorkflowNavEmpty")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setContentsMargins(
            ROW_PADDING, ROW_PADDING * 2, ROW_PADDING, ROW_PADDING * 2
        )
        layout.addWidget(self.empty)

    def reload(
        self,
        entries: list[Workflow],
        selected: str,
        filter_text: str,
    ) -> None:
        self.list.blockSignals(True)
        self.search.blockSignals(True)
        self.search.setText(filter_text)
        self.list.clear()

        needle = filter_text.casefold().strip()
        visible = [
            workflow
            for workflow in entries
            if not needle
            or needle in workflow.name.casefold()
            or needle in workflow.description.casefold()
        ]
        for workflow in visible:
            item = QListWidgetItem(workflow.name)
            item.setData(Qt.UserRole, workflow.name)
            if workflow.description:
                item.setToolTip(workflow.description)
            self.list.addItem(item)
            if workflow.name == selected:
                self.list.setCurrentItem(item)

        self.search.blockSignals(False)
        self.list.blockSignals(False)
        if entries and not visible:
            self.empty.setText("No workflows match your search.")
        else:
            self.empty.setText(
                "No workflows yet.\nCreate one to give your Desk Buddy a routine."
            )
        self.empty.setVisible(not visible)
        self.list.setVisible(bool(visible))

    def _selected(self, current: QListWidgetItem | None, _previous) -> None:
        if current is not None:
            self._on_select(str(current.data(Qt.UserRole)))


class NewWorkflowDialog(QDialog):
    def __init__(self, parent: QWidget | None, *, on_create) -> None:
        super().__init__(parent)
        self._on_create = on_create
        self.setWindowTitle("New Workflow")
        self.setModal(True)
        self.setMinimumWidth(430)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name = QLineEdit()
        self.name.setPlaceholderText("duck_walk")
        self.name.setAccessibleName("Workflow name")
        self.name.setToolTip(NAME_RULE)
        form.addRow("Name", self.name)

        self.description = QLineEdit()
        self.description.setPlaceholderText("What makes this workflow special?")
        self.description.setAccessibleName("Workflow description")
        form.addRow("Description", self.description)

        self.validation = QCheckBox("Validate known steps when support is added")
        self.validation.setChecked(True)
        form.addRow("", self.validation)
        layout.addLayout(form)

        rule = QLabel(NAME_RULE)
        rule.setObjectName("CardBody")
        rule.setWordWrap(True)
        layout.addWidget(rule)

        self.error = QLabel()
        self.error.setObjectName("FieldError")
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(_button("Cancel", self.reject))
        buttons.addWidget(_button("Create Workflow", self.create, primary=True))
        layout.addLayout(buttons)
        self.name.returnPressed.connect(self.create)

    def create(self) -> None:
        problem = self._on_create(
            self.name.text(), self.description.text(), self.validation.isChecked()
        )
        if problem:
            self.error.setText(problem)
            self.error.show()
            return
        self.accept()


class WorkflowSummaryCard(Card):
    def __init__(self, workflow: Workflow, path: str) -> None:
        super().__init__(workflow.name, workflow.description or "No description yet.")

        facts = QHBoxLayout()
        facts.setContentsMargins(0, CARD_GAP, 0, 0)
        facts.setSpacing(ROW_PADDING * 2)
        facts.addWidget(self._fact("FILE", path), 1)
        facts.addWidget(self._fact("STEPS", str(workflow.step_count)))
        facts.addWidget(
            self._fact("VALIDATION", "On" if workflow.validation else "Off")
        )
        self.layout().addLayout(facts)

    @staticmethod
    def _fact(label: str, value: str) -> QWidget:
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        key = QLabel(label)
        key.setObjectName("WorkflowFactLabel")
        text = QLabel(value)
        text.setObjectName("WorkflowFile" if label == "FILE" else "CardBody")
        text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        text.setWordWrap(True)
        layout.addWidget(key)
        layout.addWidget(text)
        return holder


def step_details(step: dict) -> str:
    """Compact human description matching the useful Rails step rows."""
    subject = str(step.get("subject", ""))
    if subject == "servo":
        name = str(step.get("servoName", "servo")).title()
        return f"{name} → {step.get('position', '—')}°"
    # Firmware names this action "baseRotate"; "rotate" was the Rails spelling
    # and still appears in older hand-written workflows.
    if subject in ("baseRotate", "rotate"):
        control = str(step.get("controlType", "")).upper()
        direction = str(step.get("direction", "")).lower()
        if control == "HOME":
            return f"Home to true north ({direction or 'right'})"
        if control == "STATUS":
            return "Report base status"
        if control in ("ENCODER", "STEPS"):
            value = step.get("value", step.get("steps", "—"))
            return f"Rotate {value} steps {direction}".strip()
    if subject == "gripper":
        if "position" in step:
            return f"Gripper → {step['position']}°"
        command = str(step.get("command", "No command"))
        return command.replace("SOFTHOLD", "Soft hold").title()
    if subject == "controlik":
        height = step.get("z_height") or 0
        reach = f"Distance {step.get('distance', '—')} mm"
        return f"{reach} at z={height} mm" if height else reach
    if subject == "photo":
        return "Take photo"
    if subject == "detect_object":
        phrase = step.get("phrase")
        if isinstance(phrase, list):
            phrase = ", ".join(str(item) for item in phrase)
        return f"Detect: {phrase}" if phrase else "Detect object"
    if subject == "detect_color":
        return "Detect color"
    if subject == "perch":
        return "Return to perch"

    custom = {key: value for key, value in step.items() if key != "subject"}
    return json.dumps(custom, separators=(",", ":")) if custom else "Custom step"


class StepTable(QTableWidget):
    COLUMNS = ("#", "Step", "Details")

    def __init__(self, steps: tuple[dict, ...]) -> None:
        super().__init__(len(steps), len(self.COLUMNS))
        self.setObjectName("WorkflowSteps")
        self.setHorizontalHeaderLabels(self.COLUMNS)
        self.verticalHeader().setVisible(False)
        self.setShowGrid(False)
        self.setAlternatingRowColors(True)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setWordWrap(True)

        for row, step in enumerate(steps):
            self.setItem(row, 0, QTableWidgetItem(str(row + 1)))
            self.setItem(row, 1, QTableWidgetItem(str(step.get("subject", ""))))
            self.setItem(row, 2, QTableWidgetItem(step_details(step)))

        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionResizeMode(QHeaderView.Fixed)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.setColumnWidth(0, 44)
        self.setColumnWidth(1, 140)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        row_height = self.fontMetrics().height() + 2 * ROW_PADDING
        for row in range(self.rowCount()):
            self.setRowHeight(row, row_height)
        self.setFixedHeight(
            self.horizontalHeader().sizeHint().height()
            + row_height * self.rowCount()
            + 2 * self.frameWidth()
        )


class StepsOverviewCard(Card):
    def __init__(self, workflow: Workflow) -> None:
        if workflow.steps:
            super().__init__(
                "Ordered steps",
                "A read-only overview for now. Step controls come later.",
            )
            self.layout().addSpacing(CARD_GAP)
            self.layout().addWidget(StepTable(workflow.steps))
        else:
            super().__init__(
                "No steps yet",
                "Add step objects in the JSON document below. Individual "
                "step buttons are intentionally not part of this pass.",
            )


class StepPaletteCard(Card):
    """Buttons that write a step's JSON so nobody has to memorize the API.

    Grouped the way the robot is built — arm, gripper, base, vision — because
    that is how someone thinks about a routine. Each button appends a complete,
    valid step with real defaults; the JSON editor below is where it gets
    tuned.
    """

    def __init__(self, workspace: "WorkflowsWorkspace") -> None:
        super().__init__(
            "Add a step",
            "Each button appends a ready-to-edit step to the JSON below. "
            "Hover any button for its firmware fields.",
        )

        for group in GROUPS:
            self.layout().addSpacing(CARD_GAP)
            heading = QLabel(group.label.upper())
            heading.setObjectName("WorkflowFactLabel")
            self.layout().addWidget(heading)

            row = QWidget()
            flow = QHBoxLayout(row)
            flow.setContentsMargins(0, 2, 0, 0)
            flow.setSpacing(4)
            for template in group.templates:
                flow.addWidget(self._chip(workspace, template))
            flow.addStretch(1)
            self.layout().addWidget(row)

    @staticmethod
    def _chip(
        workspace: "WorkflowsWorkspace", template: StepTemplate
    ) -> QPushButton:
        button = QPushButton(template.label)
        button.setObjectName("WorkflowStepChip")
        button.setCursor(Qt.PointingHandCursor)
        button.setAccessibleName(f"Add {template.label} step")

        preview = json.dumps(template.to_step(), indent=2)
        tip = [template.summary]
        if template.notes:
            tip.append(template.notes)
        tip.append(preview)
        button.setToolTip("\n\n".join(tip))
        button.clicked.connect(lambda: workspace.insert_step(template.key))
        return button


class JsonEditorCard(Card):
    def __init__(self, workspace: "WorkflowsWorkspace", workflow: Workflow) -> None:
        super().__init__(
            "Workflow JSON",
            "This document is the workflow. Saving replaces its individual "
            "JSON file atomically.",
        )

        if workspace.problem:
            problem = QLabel(workspace.problem)
            problem.setObjectName("FieldError")
            problem.setWordWrap(True)
            self.layout().addSpacing(CARD_GAP)
            self.layout().addWidget(problem)

        editor = QPlainTextEdit(workspace.draft_for(workflow))
        editor.setObjectName("WorkflowJson")
        editor.setAccessibleName("Workflow JSON")
        editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        editor.setMinimumHeight(330)
        editor.textChanged.connect(
            lambda: workspace.remember_draft(workflow.name, editor.toPlainText())
        )
        workspace.editor = editor
        self.layout().addSpacing(CARD_GAP)
        self.layout().addWidget(editor)


class EditorPage(Page):
    key = "editor"
    label = "Editor"
    title = "Workflow Studio"
    subtitle = (
        "One named JSON document per workflow, with an ordered view of its steps."
    )

    @property
    def status(self) -> str:
        workflow = self.workspace.selected
        if workflow is None:
            return "No workflow selected"
        return (
            f"{workflow.filename} · {workflow.step_count} "
            f"step{'s' if workflow.step_count != 1 else ''}"
        )

    def build_page(self) -> QWidget:
        workflow = self.workspace.selected
        if workflow is None:
            empty = Card(
                "Create something special",
                "Each workflow gets its own readable JSON file. Start with a "
                "name such as duck_walk, then shape its ordered steps directly.",
            )
            empty.layout().addSpacing(CARD_GAP)
            empty.layout().addWidget(
                _button(
                    "Create First Workflow", self.workspace.open_new, primary=True
                ),
                0,
                Qt.AlignLeft,
            )
            return Column(empty)

        return Column(
            WorkflowSummaryCard(
                workflow, str(self.workspace.repository.path_for(workflow.name))
            ),
            StepsOverviewCard(workflow),
            StepPaletteCard(self.workspace),
            JsonEditorCard(self.workspace, workflow),
        )


class WorkflowsWorkspace(Workspace):
    key = "workflows"
    label = "Workflows"
    page_classes = [EditorPage]

    def __init__(self, repository: Workflows | None = None) -> None:
        self.repository = repository if repository is not None else workflows()
        entries = self.repository.all()
        self._selected = entries[0].name if entries else ""
        self._filter = ""
        self.problem = ""
        self.editor: QPlainTextEdit | None = None
        self._drafts: dict[str, str] = {}
        super().__init__()

    @property
    def selected(self) -> Workflow | None:
        workflow = self.repository.find(self._selected) if self._selected else None
        if workflow is not None:
            return workflow
        entries = self.repository.all()
        if entries:
            self._selected = entries[0].name
            return entries[0]
        self._selected = ""
        return None

    def build_side(self) -> QWidget:
        panel = WorkflowNavigator(
            on_select=self.select_workflow,
            on_new=self.open_new,
            on_filter=self.filter_workflows,
        )
        self._reload_side(panel)
        return panel

    def _reload_side(self, panel: WorkflowNavigator | None = None) -> None:
        target = panel or self._side
        if isinstance(target, WorkflowNavigator):
            target.reload(self.repository.all(), self._selected, self._filter)

    def filter_workflows(self, text: str) -> None:
        self._filter = text
        self._reload_side()

    def select_workflow(self, name: str) -> None:
        if name == self._selected:
            return
        self._selected = name
        self.problem = ""
        self.editor = None
        self.refresh()
        self._reload_side()

    def draft_for(self, workflow: Workflow) -> str:
        return self._drafts.get(workflow.name, workflow.to_text())

    def remember_draft(self, name: str, text: str) -> None:
        self._drafts[name] = text

    def insert_step(self, template_key: str) -> str:
        """Append a palette step to the open document, then show the result."""
        workflow = self.selected
        if workflow is None:
            return "No workflow is open."
        template = find_template(template_key)
        if template is None:
            return f"Unknown step template: {template_key!r}."

        # The editor's live text wins over the saved file so an insert never
        # discards edits made since the last save.
        current = (
            self.editor.toPlainText()
            if self.editor is not None
            else self.draft_for(workflow)
        )
        updated, problem = step_inserted_into_text(current, template.to_step())
        if problem:
            self.problem = problem
            self.page.rebuild()
            return problem

        self.remember_draft(workflow.name, updated)
        self.problem = ""
        if self.editor is not None:
            self.editor.setPlainText(updated)
            cursor = self.editor.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            self.editor.setTextCursor(cursor)
        else:
            self.page.rebuild()
        self.say(f"Added {template.label} step — save to keep it")
        return ""

    def open_new(self) -> None:
        NewWorkflowDialog(self.widget(), on_create=self.create).exec()

    def create(self, name: str, description: str, validation: bool = True) -> str:
        created, problem = self.repository.create(
            name, description, validation=validation
        )
        if problem or created is None:
            return problem

        self._selected = created.name
        self._filter = ""
        self.problem = ""
        self.editor = None
        self._reload_side()
        self.refresh()
        self.say(f"Created {created.filename}")
        return ""

    def save(self) -> str:
        current = self.selected
        if current is None or self.editor is None:
            return "No workflow is open."

        text = self.editor.toPlainText()
        self.remember_draft(current.name, text)
        saved, problem = self.repository.save_text(current.name, text)
        if problem or saved is None:
            self.problem = problem
            self.page.rebuild()
            return problem

        self._drafts.pop(current.name, None)
        self._drafts.pop(saved.name, None)
        self._selected = saved.name
        self.problem = ""
        self.editor = None
        self._reload_side()
        self.refresh()
        self.say(f"Saved {saved.filename}")
        return ""

    def confirm_delete(self) -> None:
        workflow = self.selected
        if workflow is None:
            return
        answer = QMessageBox.question(
            self.widget(),
            "Delete workflow?",
            f"Delete {workflow.filename}? This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.delete_selected()

    def delete_selected(self) -> str:
        workflow = self.selected
        if workflow is None:
            return "No workflow is open."
        problem = self.repository.delete(workflow.name)
        if problem:
            self.problem = problem
            self.page.rebuild()
            return problem

        self._drafts.pop(workflow.name, None)
        entries = self.repository.all()
        self._selected = entries[0].name if entries else ""
        self.problem = ""
        self.editor = None
        self._reload_side()
        self.refresh()
        self.say(f"Deleted {workflow.filename}")
        return ""

    def enter(self) -> None:
        """Notice workflow files added or edited outside Studio."""
        self._reload_side()
        self.page.rebuild()

    def build_actions(self) -> list:
        workflow = self.selected
        return [
            ActionSpec("New", on_click=self.open_new),
            ActionSpec(
                "Save JSON",
                primary=True,
                on_click=self.save if workflow is not None else None,
            ),
            Separator(),
            ActionSpec(
                "Delete Workflow",
                on_click=self.confirm_delete if workflow is not None else None,
            ),
        ]


__all__ = ["EditorPage", "WorkflowsWorkspace"]
