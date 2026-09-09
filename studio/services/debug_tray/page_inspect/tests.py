"""What the page reader must get right.

The output is meant to be pasted into a bug report, so the tests are about
faithfulness rather than formatting: a control that is disabled must say so,
a page that is not on screen must not appear, and a password must not be
printed anywhere.

    QT_QPA_PLATFORM=offscreen python -m studio.services.debug_tray.page_inspect.tests
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .get_page_text import page_text

_app = QApplication.instance() or QApplication([])


def column(*widgets: QWidget) -> QWidget:
    holder = QWidget()
    layout = QVBoxLayout(holder)
    for widget in widgets:
        layout.addWidget(widget)
    return holder


def test_a_label_is_read_by_its_role() -> None:
    """The object name is the app's own word for what a label is."""
    title = QLabel("Broker")
    title.setObjectName("Title")
    body = QLabel("The MQTT hub.")
    body.setObjectName("CardBody")

    lines = page_text(column(title, body)).splitlines()
    assert lines == ["[title] Broker", "[text] The MQTT hub."], lines


def test_a_disabled_button_says_so() -> None:
    """Usually the whole reason the page is being reported."""
    live = QPushButton("Send")
    dead = QPushButton("Preview Detection")
    dead.setEnabled(False)

    text = page_text(column(live, dead))
    assert "[button] Send" in text, text
    assert "[button, disabled] Preview Detection" in text, text


def test_a_checkbox_reports_its_tick() -> None:
    ticked = QCheckBox("Show heartbeats")
    ticked.setChecked(True)
    text = page_text(column(ticked, QCheckBox("Wrap")))
    assert "[checkbox] [x] Show heartbeats" in text, text
    assert "[checkbox] [ ] Wrap" in text, text


def test_a_dropdown_lists_what_else_was_on_offer() -> None:
    """A picker with one option reads very differently from one with six."""
    picker = QComboBox()
    for name in ("studio", "black", "vision"):
        picker.addItem(name)
    picker.setCurrentIndex(1)

    line = page_text(column(picker))
    assert line == "[dropdown] black  (of: studio, vision)", line


def test_a_password_is_never_printed() -> None:
    """This output exists to be pasted somewhere else."""
    secret = QLineEdit("hunter2")
    secret.setEchoMode(QLineEdit.Password)
    text = page_text(column(secret))
    assert "hunter2" not in text, text
    assert "[password] (set)" in text, text


def test_an_offscreen_page_is_not_reported() -> None:
    """A stacked widget's other pages are live objects, not visible ones."""
    stack = QStackedWidget()
    stack.addWidget(QLabel("On screen"))
    stack.addWidget(QLabel("Behind it"))
    stack.setCurrentIndex(0)
    stack.show()
    _app.processEvents()

    text = page_text(stack)
    assert "On screen" in text, text
    assert "Behind it" not in text, text


def test_a_table_comes_out_as_rows() -> None:
    table = QTableWidget(2, 2)
    table.setHorizontalHeaderLabels(["Username", "Topics"])
    for row, (name, topics) in enumerate((("black", "black/#"), ("vision", "#"))):
        table.setItem(row, 0, QTableWidgetItem(name))
        table.setItem(row, 1, QTableWidgetItem(topics))

    lines = page_text(column(table)).splitlines()
    assert lines[0] == "[table] 2 rows: Username | Topics", lines
    assert lines[1].strip() == "black | black/#", lines
    assert lines[2].strip() == "vision | #", lines


def test_a_layout_container_adds_no_line_and_no_indent() -> None:
    """Only widgets that carry text nest the output.

    A QWidget that exists to hold a QHBoxLayout is plumbing. Printing it
    would make indentation track construction rather than meaning.
    """
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.addWidget(QLabel("Elbow"))
    layout.addWidget(QLabel("90"))

    lines = page_text(column(row)).splitlines()
    assert lines == ["[text] Elbow", "[text] 90"], lines


def test_widgets_come_out_in_layout_order() -> None:
    """Reading order, not Qt's construction order."""
    second = QLabel("second")
    first = QLabel("first")
    holder = QWidget()
    layout = QVBoxLayout(holder)
    layout.addWidget(first)
    layout.addWidget(second)

    lines = page_text(holder).splitlines()
    assert lines == ["[text] first", "[text] second"], lines


def test_named_sections_keep_two_Starts_apart() -> None:
    """The toolbar's Start and a card's Start are not the same button."""
    toolbar = column(QPushButton("Start"))
    page = column(QLabel("Image Interpreter"), QPushButton("Start"))

    lines = page_text([("Toolbar", toolbar), ("Page", page)]).splitlines()
    assert lines[0] == "== Toolbar ==", lines
    assert lines.index("== Page ==") > lines.index("[button] Start"), lines


def test_an_empty_section_gets_no_header() -> None:
    """A workspace with no toolbar should not print an empty one."""
    page = column(QLabel("Broker"))
    text = page_text([("Toolbar", QWidget()), ("Page", page)])
    assert "Toolbar" not in text, text
    assert "[text] Broker" in text, text


def test_a_page_never_visited_reads_as_nothing() -> None:
    """`None` stands for a page the user has not opened, not a failure."""
    assert page_text([("Toolbar", None), ("Page", None)]) == "Nothing to inspect."


def test_the_header_says_where_the_capture_was_taken() -> None:
    """A capture with no location names what was on screen, not where."""
    page = column(QLabel("Broker"))
    lines = page_text(
        [("Page", page)],
        ["Workspace: Network (network)", "Page: Broker (broker)"],
    ).splitlines()
    assert lines[0] == "Workspace: Network (network)", lines
    assert lines[1] == "Page: Broker (broker)", lines
    assert lines[2] == "", lines  # The location is its own block.
    assert "[text] Broker" in lines, lines


def test_the_header_is_separated_from_the_first_section_by_one_blank() -> None:
    """The header's own blank line is the separator; two would read as a gap."""
    lines = page_text(
        [("Page", column(QLabel("Broker")))], "Page: Broker (broker)"
    ).splitlines()
    assert lines == [
        "Page: Broker (broker)", "", "== Page ==", "[text] Broker",
    ], lines


def test_a_header_survives_an_empty_page() -> None:
    """Which page had nothing on it is the whole point of that report."""
    text = page_text(QWidget(), "Workspace: Vision (vision)")
    assert text.startswith("Workspace: Vision (vision)\n\n"), text
    assert text.endswith("Nothing visible on this page."), text


def test_one_header_line_may_be_passed_as_a_string() -> None:
    lines = page_text(column(QLabel("Broker")), "Page: Broker").splitlines()
    assert lines[:2] == ["Page: Broker", ""], lines


def test_nothing_to_inspect_is_said_rather_than_crashed() -> None:
    assert page_text(None) == "Nothing to inspect."
    assert page_text(QWidget()) == "Nothing visible on this page."


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
    print(f"OK: {len(tests)} page inspect tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
