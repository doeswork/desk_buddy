"""Workflow Studio UI.

The Rails app's searchable workflow navigator and whole-document JSON editor
are kept here in a native Qt shape.

The JSON *is* the workflow, so the page is the editor and little else: a line
naming the open document, a strip of facts about it, and then the editor for
every remaining pixel of the window. The ordered step table that used to sit
between them is gone — it restated the document it sat above, and charged a
third of the height to do it. Steps are added from the toolbar, which the
workspace owns and every page keeps.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .steps import GROUPS
from .steps import find as find_template
from ....models.config.workflows import (
    NAME_RULE,
    Workflow,
    Workflows,
    step_inserted_into_text,
    workflows,
)
from ...components import ActionSpec, Card, Column, Separator, spacer
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
    button.setObjectName("ToolbarPrimary" if primary else "ToolbarAction")
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


class WorkflowMetaStrip(QWidget):
    """One line of facts above the document.

    What used to be a summary card and a steps table is a single strip. Both
    said what the document below them already says — the file the JSON is
    saved to, how many steps it has — and both cost the editor a third of the
    window to say it. A row of small facts says the same at the top of the
    page and gives the height back.
    """

    def __init__(self, workflow: Workflow, path: str) -> None:
        super().__init__()
        self.setObjectName("WorkflowMeta")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(ROW_PADDING)

        layout.addWidget(self._fact(path, mono=True))
        layout.addWidget(self._dot())
        layout.addWidget(
            self._fact(
                f"{workflow.step_count} step"
                f"{'s' if workflow.step_count != 1 else ''}"
            )
        )
        layout.addWidget(self._dot())
        layout.addWidget(
            self._fact(
                f"validation {'on' if workflow.validation else 'off'}"
            )
        )
        if workflow.description:
            layout.addWidget(self._dot())
            layout.addWidget(self._fact(workflow.description))
        layout.addStretch(1)

    @staticmethod
    def _fact(text: str, *, mono: bool = False) -> QLabel:
        label = QLabel(text)
        label.setObjectName("WorkflowFile" if mono else "WorkflowMetaFact")
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        return label

    @staticmethod
    def _dot() -> QLabel:
        dot = QLabel("·")
        dot.setObjectName("WorkflowMetaDot")
        return dot


class JsonDocument(QWidget):
    """The workflow JSON, given the whole page.

    Not a card. A card frames a block among other blocks, and this is the
    only thing on the page — the frame would just be a second border drawn
    around the editor's own. The strip of facts and the error line sit above
    it; everything left over is the editor.
    """

    def __init__(self, workspace: "WorkflowsWorkspace", workflow: Workflow) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(CARD_GAP)
        layout.addWidget(
            WorkflowMetaStrip(
                workflow, str(workspace.repository.path_for(workflow.name))
            )
        )

        if workspace.problem:
            problem = QLabel(workspace.problem)
            problem.setObjectName("FieldError")
            problem.setWordWrap(True)
            layout.addWidget(problem)

        editor = QPlainTextEdit(workspace.draft_for(workflow))
        editor.setObjectName("WorkflowJson")
        editor.setAccessibleName("Workflow JSON")
        editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        editor.setTabStopDistance(
            2 * editor.fontMetrics().horizontalAdvance(" ")
        )
        # No minimum height any more: the stretch below is what sizes it, and
        # a minimum tall enough to matter is one that forces a scrollbar on a
        # short window instead of letting the editor shrink with it.
        editor.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        editor.textChanged.connect(
            lambda: workspace.remember_draft(workflow.name, editor.toPlainText())
        )
        workspace.editor = editor
        layout.addWidget(editor, 1)


class EditorPage(Page):
    key = "editor"
    label = "Editor"
    title = "Workflow Studio"
    # No subtitle. The page is one document and a toolbar of steps; a line of
    # prose above it explains what the document below already shows, and cost
    # two rows of the height the document wants.
    fills_height = True

    @property
    def status(self) -> str:
        workflow = self.workspace.selected
        if workflow is None:
            return "No workflow selected"
        return (
            f"{workflow.filename} · {workflow.step_count} "
            f"step{'s' if workflow.step_count != 1 else ''}"
        )

    def build_header(self) -> QWidget:
        """The open workflow's name, and nothing else.

        A page whose body must fill the window cannot afford a title block
        that repeats what the side panel's highlighted row and the status
        strip both already say. One line naming the open document is what is
        left, and it is the one thing neither of those says at a glance.
        """
        workflow = self.workspace.selected
        title = QLabel(workflow.name if workflow is not None else self.title)
        title.setObjectName("Title")
        return title

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
            # The empty state is not a document: it keeps its natural height
            # and sits at the top rather than stretching down the window.
            return Column(empty, spacer())

        # The step palette is the toolbar, not a card here: adding steps is
        # the work of this workspace, and a palette below the document is one
        # the user scrolls past to reach the thing it edits.
        return JsonDocument(self.workspace, workflow)


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
        if current is None:
            return "No workflow is open."

        # The editor widget is the live text when the page is on screen, but
        # it is not the only place the text lives: `insert_step` stages its
        # result as a draft, and a workflow can be selected and edited before
        # the page has ever been built. Falling back to the draft is what
        # makes "insert a step, then save" work in that order — without it,
        # a staged edit reported "No workflow is open" and was silently lost.
        text = (
            self.editor.toPlainText()
            if self.editor is not None
            else self.draft_for(current)
        )
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
        """The workflow's own verbs, then the whole step vocabulary.

        Every firmware action is a button here rather than a card in the page
        body: adding steps *is* the work of this workspace, and a palette
        buried below the document is one the user scrolls past. The bar wraps,
        so all of them stay visible at any window width.

        Steps are disabled until a workflow is open, for the same reason Save
        is — there is nothing to add them to.
        """
        workflow = self.selected
        actions = [
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

        for group in GROUPS:
            actions.append(Separator())
            for template in group.templates:
                actions.append(
                    ActionSpec(
                        template.label,
                        on_click=(
                            partial(self.insert_step, template.key)
                            if workflow is not None
                            else None
                        ),
                    )
                )
        return actions


__all__ = ["EditorPage", "WorkflowsWorkspace"]
