"""Read the page on screen back as text.

The problem this solves is a reporting one. Describing what a page shows —
to a bug report, to a coworker, to an LLM — currently means a screenshot,
which cannot be pasted into a text field, diffed, or grepped. A Qt app has
no View Source, so this is it: walk the live widget tree and print what is
actually on screen.

What it walks is the real tree, not a model of it. Every value is read off
the widget at the moment of the call — a combo box's current item, a check
box's tick, the row a table is scrolled to — so what comes out is the state
the user is looking at, not the state a page was built with.

Three rules make the output readable rather than exhaustive:

    Only what is visible.    A widget on a QStackedWidget's other page is
                             still a live object; it is not on screen, and
                             printing it would describe a page the user
                             cannot see.
    Only what carries text.  Layout widgets — the QWidget that exists to
                             hold an QHBoxLayout — say nothing. They are
                             walked through, never printed, so nesting
                             depth in the output tracks meaning rather
                             than plumbing.
    Interactive state, named. A button is not just its label; it may be
                             disabled, and that is usually the thing the
                             report is about.

Indentation is the nesting. Nothing here is Studio-specific: it takes any
QWidget, so the debug tray points it at the main window and gets whatever
page happens to be showing. The one thing the widgets cannot say is where
they are — which workspace is active, which page — so the caller passes that
in as a `header` rather than this file learning Studio's navigation.
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTextEdit,
    QWidget,
)

INDENT = "  "

# Long free text — a serial log, an error pane — is summarized rather than
# reproduced. The point of this tab is the shape of the page; a reader who
# wants the whole log has a tab that is nothing but the whole log.
TEXT_PREVIEW_LIMIT = 400

# A table or list that is really a data dump would bury the page around it.
ROW_LIMIT = 50

# A label's object name is the app's own word for what that label is: the
# stylesheet already sorts Title from CardBody, so the roles are there to be
# read rather than guessed at from font size or position. Anything unnamed
# is just text.
LABEL_ROLES = {
    "Title": "title",
    "Subtitle": "subtitle",
    "CardTitle": "heading",
    "FieldError": "error",
    "StatusChip": "chip",
}


def page_text(
    root: QWidget | Iterable[QWidget | tuple[str, QWidget | None] | None] | None,
    header: str | Iterable[str] | None = None,
) -> str:
    """Everything visible under `root`, as indented text.

    Takes one widget, or several to read in order — the tray hands over the
    toolbar and the page, which are siblings in the window rather than one
    tree, and wants them read as one document. Pass `(name, widget)` pairs
    to head each one, which is what keeps a toolbar's Start distinguishable
    from a card's Start further down.

    `header` is where the reader is, printed above the reading: which
    workspace is active, which page it is showing. That is not something the
    widgets can be asked — a page's title label is what the designer chose to
    show, and some pages show none — so the caller, which navigates by these
    names, states them. Without it a capture pasted into a bug report says
    what was on screen but not where on screen was.
    """
    sections = _sections(root)
    if not sections:
        return "Nothing to inspect."

    lines: list[str] = _header_lines(header)
    read_something = False
    for name, widget in sections:
        found: list[str] = []
        # A root is not visibility-checked: the caller picked it, and a
        # widget that is not itself in a shown window — a page handed over
        # directly, anything under test — still has contents worth reading.
        # Everything below it is checked normally.
        _report(widget, 0, found)
        if not found:
            continue  # An empty section is not worth a header of its own.
        read_something = True
        if name:
            if lines and lines[-1]:
                # A blank line between sections — but the header block below
                # already ends in one, and two would read as a gap.
                lines.append("")
            lines.append(f"== {name} ==")
        lines.extend(found)
    if not read_something:
        # The location still stands even where nothing under it could be
        # read: an empty page is a finding, and one worth knowing the name of.
        lines.append("Nothing visible on this page.")
    return "\n".join(lines)


def _header_lines(header) -> list[str]:
    """The location block, blank-line separated from the reading below it."""
    if header is None:
        return []
    entries = [header] if isinstance(header, str) else list(header)
    said = [line for line in entries if line]
    return said + [""] if said else []


def _sections(root) -> list[tuple[str, QWidget]]:
    """Normalize what the caller passed into (name, widget) pairs."""
    if root is None:
        return []
    if isinstance(root, QWidget):
        return [("", root)]

    sections: list[tuple[str, QWidget]] = []
    for entry in root:
        if entry is None:
            continue
        name, widget = entry if isinstance(entry, tuple) else ("", entry)
        if widget is not None:
            sections.append((name, widget))
    return sections


def _walk(widget: QWidget, depth: int, lines: list[str]) -> None:
    """Describe `widget`, then its children, depth-first in layout order.

    A widget that describes itself fully — a table, a tab bar — reports its
    own contents and stops; walking into it would print the same rows twice,
    once as data and once as whatever widgets Qt built them from.
    """
    if not _visible(widget):
        return
    _report(widget, depth, lines)


def _report(widget: QWidget, depth: int, lines: list[str]) -> None:
    """Describe `widget` and walk into it, with visibility already settled."""
    described = _describe(widget)
    if described is not None:
        # A description can run to several lines — a table's rows, a text
        # box's preview. All of them sit at this widget's depth, so the
        # indent is applied per line rather than to the block.
        pad = INDENT * depth
        lines.extend(pad + line for line in described.split("\n"))
        depth += 1

    if _is_leaf(widget):
        return

    for child in _children(widget):
        _walk(child, depth, lines)


def _visible(widget: QWidget) -> bool:
    """On screen, as far as this widget itself can say.

    `isVisible()` is False for everything while the window is still being
    built, and False for every widget in an app that never showed one — a
    test, a headless run. `isHidden()` asks the narrower question this needs:
    was this widget, or an ancestor, explicitly hidden? That is what a
    stacked widget's other pages are, and what a collapsed section is.
    """
    return not widget.isHidden()


def _children(widget: QWidget) -> list[QWidget]:
    """Direct child widgets, in the order the layout places them.

    `findChildren` returns Qt's construction order, which is the order the
    page's code happened to build things in — close to visual order, but not
    reliably. Reading the layout instead means the output matches what the
    user's eye scans: a toolbar's buttons come out left to right.
    """
    layout = widget.layout()
    if layout is None:
        return [child for child in widget.children() if isinstance(child, QWidget)]

    ordered: list[QWidget] = []
    for index in range(layout.count()):
        item = layout.itemAt(index)
        child = item.widget() if item is not None else None
        if child is not None:
            ordered.append(child)
        elif item is not None and item.layout() is not None:
            # A nested layout holds widgets without a widget of its own to
            # hang them off. Its contents belong to this same level.
            ordered.extend(_layout_widgets(item.layout()))

    # A child parented to this widget but never added to its layout still
    # paints — an overlay, a manually positioned button. Append rather than
    # drop, so nothing on screen goes unreported.
    placed = set(ordered)
    ordered.extend(
        child for child in widget.children()
        if isinstance(child, QWidget) and child not in placed
    )
    return ordered


def _layout_widgets(layout) -> list[QWidget]:
    found: list[QWidget] = []
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item is None:
            continue
        if item.widget() is not None:
            found.append(item.widget())
        elif item.layout() is not None:
            found.extend(_layout_widgets(item.layout()))
    return found


def _is_leaf(widget: QWidget) -> bool:
    """Whether `_describe` already said everything this widget contains."""
    return isinstance(
        widget,
        (QAbstractButton, QAbstractItemView, QComboBox, QLabel, QLineEdit,
         QPlainTextEdit, QProgressBar, QSlider, QSpinBox, QTabWidget,
         QTextEdit),
    )


# ---- one widget, one line -------------------------------------------------
def _describe(widget: QWidget) -> str | None:
    """One line for `widget`, or None for one that carries no information.

    Order matters: the specific classes come before the general ones they
    inherit from, since QCheckBox is a QAbstractButton and would otherwise
    print as a button with no tick.
    """
    if isinstance(widget, QStackedWidget):
        return None  # Pure plumbing: only its current page is visible anyway.

    if isinstance(widget, QTabWidget):
        return _tabs(widget)
    if isinstance(widget, QGroupBox):
        return f"[group] {widget.title()}" if widget.title() else None
    if isinstance(widget, QLabel):
        return _text_line(LABEL_ROLES.get(widget.objectName(), "text"), widget.text())
    if isinstance(widget, QCheckBox):
        return f"[checkbox{_state(widget)}] {_ticked(widget)} {widget.text()}"
    if isinstance(widget, QRadioButton):
        return f"[radio{_state(widget)}] {_ticked(widget)} {widget.text()}"
    if isinstance(widget, QPushButton):
        return _button(widget)
    if isinstance(widget, QAbstractButton):
        # A QToolButton, or anything else clickable. Icon-only buttons are
        # common here, so fall back to whatever names them for a screen reader.
        label = widget.text() or widget.accessibleName() or widget.toolTip()
        return f"[button{_state(widget)}] {label}" if label else None
    if isinstance(widget, QComboBox):
        return _combo(widget)
    if isinstance(widget, QLineEdit):
        return _line_edit(widget)
    if isinstance(widget, (QPlainTextEdit, QTextEdit)):
        return _long_text(widget)
    if isinstance(widget, QSpinBox):
        return f"[number{_state(widget)}] {widget.value()}"
    if isinstance(widget, QSlider):
        return (f"[slider{_state(widget)}] {widget.value()} "
                f"of {widget.minimum()}–{widget.maximum()}")
    if isinstance(widget, QProgressBar):
        return f"[progress] {widget.value()} of {widget.maximum()}"
    if isinstance(widget, QTableWidget):
        return _table(widget)
    if isinstance(widget, QListWidget):
        return _list(widget)
    return None  # A layout container. Walked through, not printed.


def _state(widget: QWidget) -> str:
    """The suffix that says a control cannot be used right now.

    Disabled is usually the reason a page is being reported at all — the
    button that should have been clickable and was not — so it is never
    left off a control that has it.
    """
    return "" if widget.isEnabled() else ", disabled"


def _ticked(widget: QAbstractButton) -> str:
    return "[x]" if widget.isChecked() else "[ ]"


def _button(widget: QPushButton) -> str | None:
    label = widget.text() or widget.accessibleName() or widget.toolTip()
    if not label:
        return None
    checked = ", on" if widget.isCheckable() and widget.isChecked() else ""
    return f"[button{_state(widget)}{checked}] {label}"


def _combo(widget: QComboBox) -> str:
    """The chosen item, and what else was on offer.

    The alternatives are half the report: a picker showing the only model
    installed reads very differently from one showing the first of six.
    """
    others = [widget.itemText(i) for i in range(widget.count())
              if i != widget.currentIndex()]
    line = f"[dropdown{_state(widget)}] {widget.currentText()}"
    return f"{line}  (of: {', '.join(others)})" if others else line


def _line_edit(widget: QLineEdit) -> str:
    """A one-line field, with its password masked.

    Studio's fields hold broker passwords, and this output exists to be
    pasted somewhere else. Echo mode is the widget's own statement that its
    contents are not for reading, so it is honored rather than second-guessed.
    """
    if widget.echoMode() != QLineEdit.Normal:
        filled = "set" if widget.text() else "empty"
        return f"[password{_state(widget)}] ({filled})"
    if widget.text():
        return f"[field{_state(widget)}] {widget.text()}"
    return f"[field{_state(widget)}] (empty: {widget.placeholderText()})"


def _long_text(widget: QPlainTextEdit | QTextEdit) -> str:
    text = widget.toPlainText()
    if not text:
        return f"[text box] (empty: {widget.placeholderText()})"
    lines = text.count("\n") + 1
    return f"[text box] {lines} lines\n{_indented_preview(text)}"


def _tabs(widget: QTabWidget) -> str:
    """The tab bar itself. Its current page is walked as a normal child."""
    names = []
    for index in range(widget.count()):
        name = widget.tabText(index)
        names.append(f"*{name}*" if index == widget.currentIndex() else name)
    return f"[tabs] {' | '.join(names)}"


def _table(widget: QTableWidget) -> str:
    headers = [_header(widget, column) for column in range(widget.columnCount())]
    rows = [f"[table] {widget.rowCount()} rows: {' | '.join(headers)}"]
    for row in range(min(widget.rowCount(), ROW_LIMIT)):
        cells = []
        for column in range(widget.columnCount()):
            item = widget.item(row, column)
            cells.append(item.text() if item is not None else "")
        rows.append(INDENT + " | ".join(cells))
    if widget.rowCount() > ROW_LIMIT:
        rows.append(INDENT + f"… {widget.rowCount() - ROW_LIMIT} more rows")
    return "\n".join(rows)


def _header(widget: QTableWidget, column: int) -> str:
    item = widget.horizontalHeaderItem(column)
    return item.text() if item is not None else str(column)


def _list(widget: QListWidget) -> str:
    rows = [f"[list] {widget.count()} items"]
    for index in range(min(widget.count(), ROW_LIMIT)):
        item = widget.item(index)
        marker = "> " if index == widget.currentRow() else "  "
        rows.append(INDENT + marker + (item.text() if item is not None else ""))
    if widget.count() > ROW_LIMIT:
        rows.append(INDENT + f"  … {widget.count() - ROW_LIMIT} more items")
    return "\n".join(rows)


def _text_line(kind: str, text: str) -> str | None:
    """A label. Multi-line text is kept, indented under its own first line."""
    text = text.strip()
    if not text:
        return None
    if "\n" not in text:
        return f"[{kind}] {text}"
    first, rest = text.split("\n", 1)
    return f"[{kind}] {first}\n{_indent_block(rest)}"


def _indented_preview(text: str) -> str:
    body = text.strip()
    if len(body) > TEXT_PREVIEW_LIMIT:
        body = body[:TEXT_PREVIEW_LIMIT].rstrip() + " …"
    return _indent_block(body)


def _indent_block(text: str) -> str:
    return "\n".join(INDENT + line for line in text.split("\n"))
