"""Manual Control tab: the widgets, and what they ask the service for.

What a command puts on the wire, and when it refuses to send one, is the
service's own and is tested next to it, in
`studio/services/manual_controller/tests.py`.

    QT_QPA_PLATFORM=offscreen python -m studio.ui.components.manual_control_tests
"""

from __future__ import annotations

import os
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QApplication, QComboBox, QLabel, QPushButton, QSlider,
)

from .card import Card

from ...models.config.robots import Robot
from ...services.manual_controller import live_actions
from .manual_control import ManualControl

_app = QApplication.instance() or QApplication([])


class RecordingClient:
    def __init__(self, *, running: bool = True) -> None:
        self.running = running
        self.status = "Connected" if running else "Broker is not running"
        self.sent: list[tuple[str, dict]] = []
        self.reconciled = False

    def reconcile(self) -> None:
        self.reconciled = True

    def publish(self, topic: str, payload: dict, **_kwargs) -> str:
        body = dict(payload)
        body.setdefault("sender", "studio")
        body.setdefault("action_id", f"action-{len(self.sent) + 1}")
        self.sent.append((topic, body))
        return str(body["action_id"])


class Registry:
    def __init__(self, entries=()) -> None:
        self.entries = list(entries)

    def all(self) -> list[Robot]:
        return list(self.entries)

    def find(self, name: str) -> Robot | None:
        return next((robot for robot in self.entries if robot.name == name), None)


def tab(entries=(Robot("black", "Black Buddy"),)) -> tuple[ManualControl, RecordingClient, Registry]:
    registry = Registry(entries)
    client = RecordingClient()
    # The registry is the service's now, so that is where it is patched.
    patcher = mock.patch.object(live_actions, "robots", lambda: registry)
    patcher.start()
    control = ManualControl()
    control.live.client = lambda: client
    # Keep the patch alive for every later property read on this tab.
    control._test_registry_patch = patcher
    return control, client, registry


def close_tab(control: ManualControl) -> None:
    control._test_registry_patch.stop()
    control.deleteLater()


def test_the_tab_has_the_legacy_sliders_and_quick_actions() -> None:
    control, _client, _registry = tab()
    try:
        widget = control
        sliders = widget.findChildren(QSlider)
        assert [slider.accessibleName() for slider in sliders] == [
            "Elbow", "Wrist", "Twist", "Distance",
        ]
        assert [(slider.minimum(), slider.maximum()) for slider in sliders] == [
            (0, 180), (0, 180), (0, 180), (1, 120),
        ]

        buttons = {button.text() for button in widget.findChildren(QPushButton)}
        assert {
            "← Left", "Right →", "Home base", "Grab", "Soft hold",
            "Drop", "Perch", "Take photo",
        } <= buttons

        picker = next(
            combo for combo in widget.findChildren(QComboBox)
            if combo.accessibleName() == "Target robot"
        )
        assert picker.currentText() == "Black Buddy"
    finally:
        close_tab(control)


def test_slider_previews_without_publishing_then_sends_on_commit() -> None:
    control, client, _registry = tab()
    try:
        elbow = next(
            slider for slider in control.findChildren(QSlider)
            if slider.accessibleName() == "Elbow"
        )
        elbow.setValue(126)
        assert client.sent == [], "previewing a drag must not flood MQTT"

        elbow.committed.emit(126)
        topic, payload = client.sent[-1]
        assert topic == "black/test"
        assert payload["sender"] == "studio"
        assert payload["servoName"] == "ELBOW"
        assert payload["position"] == 126
    finally:
        close_tab(control)


def test_no_robot_leaves_the_controls_visible_but_disabled() -> None:
    control, _client, _registry = tab(entries=())
    try:
        assert all(not slider.isEnabled()
                   for slider in control.findChildren(QSlider))
        assert all(not button.isEnabled()
                   for button in control.findChildren(QPushButton))
        assert control.status.text() == "No robot marked"
    finally:
        close_tab(control)


def test_a_robot_marked_elsewhere_enables_the_controls_on_the_next_refresh() -> None:
    """The tray polls: robots are marked on Network, not in this tab."""
    control, _client, registry = tab(entries=())
    try:
        assert not any(slider.isEnabled()
                       for slider in control.findChildren(QSlider))

        registry.entries = [Robot("black", "Black Buddy")]
        control.refresh()

        sliders = control.findChildren(QSlider)
        assert sliders and all(slider.isEnabled() for slider in sliders)
        assert control.status.text() == "Black Buddy · publishing as Studio"
    finally:
        close_tab(control)


def test_picking_another_robot_rebuilds_against_it() -> None:
    control, client, _registry = tab(entries=(
        Robot("black", "Black Buddy"), Robot("white", "White Buddy"),
    ))
    try:
        picker = next(
            combo for combo in control.findChildren(QComboBox)
            if combo.accessibleName() == "Target robot"
        )
        picker.setCurrentIndex(1)
        # The rebuild is deferred off the picker's own signal; let it run.
        _app.processEvents()

        assert control.live.robot == "white"
        # The rebuilt cards publish to the new robot, not the old one.
        control.live.photo()
        topic, _payload = client.sent[-1]
        assert topic == "white/test"
    finally:
        close_tab(control)


def test_what_was_sent_is_said_in_the_tabs_own_status_line() -> None:
    """A tray tab is not on screen with the window's status strip."""
    control, _client, _registry = tab()
    try:
        control.live.photo()
        assert control.status.text() == (
            "Sent photo request to Black Buddy as Studio."
        )
    finally:
        close_tab(control)


def test_the_cards_never_run_past_the_edge_at_any_width() -> None:
    """A tray is whatever width the window is; nothing may hang off it.

    The failure this pins is a control the user cannot reach: a card wider
    than the viewport is a Take photo button pushed out of the tab with no
    horizontal scrollbar to bring it back.
    """
    control, _client, _registry = tab()
    try:
        control.show()
        # Down to the width one card insists on. A card is held to a
        # readable measure rather than squeezed indefinitely (see
        # CARD_MIN_WIDTH), so below that the tray is narrower than its own
        # content and a horizontal scrollbar is the honest answer — but no
        # dock in this app is ever that narrow.
        for width in (1500, 1100, 820, 640, 480, 380):
            control.resize(width, 400)
            for _ in range(8):
                _app.processEvents()

            scroll = control._scroll
            viewport = scroll.viewport().width()
            for card in control.findChildren(Card):
                right = card.mapTo(scroll.widget(), card.rect().topLeft()).x() + card.width()
                assert right <= viewport + 1, (width, card.title_label.text(), right, viewport)
            assert scroll.horizontalScrollBar().maximum() == 0, width
    finally:
        close_tab(control)


def test_the_cards_wrap_onto_more_rows_as_the_tray_narrows() -> None:
    """Wrapping, not just shrinking: a narrow tray stacks the cards."""
    control, _client, _registry = tab()
    try:
        control.show()

        def rows(width: int) -> int:
            control.resize(width, 400)
            for _ in range(8):
                _app.processEvents()
            return len({card.mapTo(control, card.rect().topLeft()).y()
                        for card in control.findChildren(Card)})

        assert rows(1500) < rows(700) < rows(400)
    finally:
        close_tab(control)


def test_a_short_tray_scrolls_rather_than_clipping() -> None:
    """The dock is as short as the user dragged it; nothing may be cut off."""
    control, _client, _registry = tab()
    try:
        control.show()
        control.resize(900, 140)
        for _ in range(8):
            _app.processEvents()

        scroll = control._scroll
        assert scroll.widget().height() > scroll.viewport().height()
        assert scroll.verticalScrollBar().maximum() > 0
        # And the tab itself must not demand a taller dock than it is given.
        assert control.minimumSizeHint().height() <= 140
    finally:
        close_tab(control)


def test_a_speed_label_never_wraps_away_from_its_dropdown() -> None:
    """A flow may break anywhere; a label and its control must not be split.

    "Speed" at the end of one row with its dropdown at the start of the next
    reads as a label for the wrong control.
    """
    control, _client, _registry = tab()
    try:
        control.show()
        base = next(card for card in control.findChildren(Card)
                    if card.title_label.text() == "Base rotation")
        for width in (900, 640, 420, 320):
            control.resize(width, 500)
            for _ in range(8):
                _app.processEvents()
            speed_label = next(
                label for label in base.findChildren(QLabel)
                if label.text() == "Speed"
            )
            # Same row as the control it names.
            assert speed_label.mapTo(base, speed_label.rect().center()).y() == \
                base.speed.mapTo(base, base.speed.rect().center()).y(), width
    finally:
        close_tab(control)


def main() -> int:
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_")]
    for test in tests:
        test()
    print(f"OK: {len(tests)} manual control tab tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
