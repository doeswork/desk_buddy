"""Workflow Studio UI.

The Rails app's searchable workflow navigator and whole-document JSON editor
are kept here in a native Qt shape.

The JSON *is* the workflow, so the page is the editor and little else: a line
naming the open document, a strip of facts about it, and then the editor for
every remaining pixel of the window. The ordered step table that used to sit
between them is gone — it restated the document it sat above, and charged a
third of the height to do it. Steps are added from the palette on the header
line, one grouped menu per part of the robot; see `palette.py` for why that
is not the toolbar any more.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .palette import StepPalette
from .steps import find as find_template
from ....models.config.workflows import (
    NAME_RULE,
    Workflow,
    Workflows,
    step_inserted_into_text,
    validate_name,
    workflow_from_text,
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
    """Searchable workflow list, inspired by the Rails secondary nav.

    A search field and the list it filters. It carried a heading and a New
    button above them; both were second copies of something already on
    screen. The workspace bar names the workspace you are in — a panel
    inside Workflows captioned "Workflows" is the app saying it twice — and
    New is a document verb, so it belongs on the toolbar with Save and
    Delete rather than once in each place.
    """

    def __init__(self, *, on_select, on_filter) -> None:
        super().__init__()
        self._on_select = on_select

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

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


class WorkflowTitle(QToolButton):
    """The open workflow's name, as the menu of things to do to it.

    The name was a label. It is the biggest, most obvious word on the page
    and it did nothing, while renaming the document meant editing the `name`
    field inside the JSON and knowing that saving would move the file. This
    makes the obvious thing the real one: click the name to act on the
    document the name refers to.

    Styled to still read as the page's title rather than as a control — the
    heading weight and size are kept, and the menu arrow beside it is what
    says it can be clicked. `InstantPopup` makes the whole word the trigger,
    so there is no part of the title that looks live and is not.
    """

    def __init__(
        self,
        name: str,
        *,
        on_rename: Callable[[], None],
        on_copy_path: Callable[[], None],
    ) -> None:
        super().__init__()
        self.setObjectName("WorkflowTitle")
        self.setText(name)
        self.setPopupMode(QToolButton.InstantPopup)
        self.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.setCursor(Qt.PointingHandCursor)

        menu = QMenu(self)
        menu.setObjectName("WorkflowTitleMenu")
        rename = menu.addAction("Rename…")
        rename.triggered.connect(lambda _checked=False: on_rename())
        copy = menu.addAction("Copy file path")
        copy.triggered.connect(lambda _checked=False: on_copy_path())
        self.setMenu(menu)
        self.menu_actions = {"rename": rename, "copy": copy}


class RenameWorkflowDialog(QDialog):
    """Rename the open workflow, which is also to rename its file.

    Its own dialog rather than an inline edit on the title: a rename moves a
    file on disk and can collide with an existing one, so it needs somewhere
    to say no. The name rule is shown up front for the same reason it is in
    the New dialog — a workflow name is a filename, and the rule is not
    guessable from the field.
    """

    def __init__(self, parent: QWidget | None, current: str, *, on_rename) -> None:
        super().__init__(parent)
        self._on_rename = on_rename
        self.setWindowTitle("Rename Workflow")
        self.setModal(True)
        self.setMinimumWidth(430)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name = QLineEdit(current)
        self.name.setAccessibleName("Workflow name")
        self.name.setToolTip(NAME_RULE)
        self.name.selectAll()
        form.addRow("Name", self.name)
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
        buttons.addWidget(_button("Rename", self.rename, primary=True))
        layout.addLayout(buttons)
        self.name.returnPressed.connect(self.rename)

    def rename(self) -> None:
        problem = self._on_rename(self.name.text())
        if problem:
            self.error.setText(problem)
            self.error.show()
            return
        self.accept()


class WorkflowMetaStrip(QWidget):
    """One line of facts above the document.

    What used to be a summary card and a steps table is a single strip. Both
    said what the document below them already says — how many steps it has,
    whether it validates — and both cost the editor a third of the window to
    say it. A row of small facts says the same at the top of the page and
    gives the height back.

    The file path used to lead this line. It was the longest thing on the
    page and the least often wanted: an absolute path under
    ~/.local/share, restating the workflow's own name after forty characters
    of directory that never change between two workflows. It now lives in the
    title's menu, one click from where the name is, as something to copy
    rather than something to read.
    """

    def __init__(self, workflow: Workflow) -> None:
        super().__init__()
        self.setObjectName("WorkflowMeta")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(ROW_PADDING)

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
    def _fact(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("WorkflowMetaFact")
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
        layout.addWidget(WorkflowMetaStrip(workflow))

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
    # No subtitle. The page is one document and its step palette; a line of
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
        """The open workflow's name, and the steps you can add to it.

        A page whose body must fill the window cannot afford a title block
        that repeats what the side panel's highlighted row and the status
        strip both already say. One line naming the open document is what is
        left, and it is the one thing neither of those says at a glance.

        The name is also the menu for acting on the document it names —
        rename it, or take its path — and the step palette shares the line,
        pushed to the right. All three belong together: this workflow, what
        you can do to it, and what goes in it. The pair costs the document no
        height the name was not already spending.
        """
        workflow = self.workspace.selected
        if workflow is None:
            title: QWidget = QLabel(self.title)
            title.setObjectName("Title")
        else:
            title = WorkflowTitle(
                workflow.name,
                on_rename=self.workspace.open_rename,
                on_copy_path=self.workspace.copy_path,
            )

        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(ROW_PADDING)
        layout.addWidget(title, 0, Qt.AlignVCenter)
        layout.addStretch(1)
        layout.addWidget(
            StepPalette(
                self.workspace.insert_step if workflow is not None else None
            ),
            0,
            Qt.AlignVCenter,
        )
        return holder

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

        # The step palette is on the header line, not a card here: a
        # palette below the document is one the user scrolls past to reach
        # the thing it edits.
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

    def open_rename(self) -> None:
        workflow = self.selected
        if workflow is None:
            return
        RenameWorkflowDialog(
            self.widget(), workflow.name, on_rename=self.rename
        ).exec()

    def rename(self, name: str) -> str:
        """Rename the open workflow, and the file it is stored in.

        Goes through the same `save_text` the Save button does, on the
        document's own text with its `name` field rewritten. That is what
        makes a rename keep unsaved edits instead of silently discarding
        them — and it is `save`'s `previous_name` that moves the old file
        rather than leaving a copy behind under the old name.
        """
        workflow = self.selected
        if workflow is None:
            return "No workflow is open."

        new_name = name.strip()
        problem = validate_name(new_name)
        if problem:
            return problem
        if new_name == workflow.name:
            return ""
        if self.repository.find(new_name) is not None:
            return f"A workflow called {new_name!r} already exists."

        text = (
            self.editor.toPlainText()
            if self.editor is not None
            else self.draft_for(workflow)
        )
        current, problem = workflow_from_text(text)
        if problem or current is None:
            return problem or "Fix the JSON before renaming."

        renamed = replace(current, name=new_name)
        problem = self.repository.save(renamed, previous_name=workflow.name)
        if problem:
            return problem

        self._drafts.pop(workflow.name, None)
        self._drafts.pop(new_name, None)
        self._selected = new_name
        self.problem = ""
        self.editor = None
        self._reload_side()
        self.refresh()
        self.say(f"Renamed to {renamed.filename}")
        return ""

    def copy_path(self) -> str:
        """Put the open workflow's file path on the clipboard.

        The path left the page when it stopped being worth a line of its own
        — forty unchanging characters of directory in front of the name
        already in the title. Copying is what it was actually for.
        """
        workflow = self.selected
        if workflow is None:
            return ""
        path = str(self.repository.path_for(workflow.name))
        QApplication.clipboard().setText(path)
        self.say(f"Copied {path}")
        return path

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
        """The workflow's own verbs. The step vocabulary is not here.

        Every firmware action used to be a button on this bar, which put
        thirty of them in the workspace's permanent chrome to edit the one
        document below it. They are the document's content, not the
        workspace's toolset, so they moved to a row of grouped menus on the
        page's header line — see `palette.py`. What is left is what the bar
        was always for: make a workflow, save it, delete it.

        Save and Delete are dead until a workflow is open, because there is
        nothing for them to act on.
        """
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
