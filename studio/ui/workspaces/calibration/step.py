"""StepPage: what every calibration step has in common.

A step publishes one or more MQTT commands to the selected robot, waits for
the firmware's reply, and — on success — records the result in Calibrations.
That lifecycle (waiting text, success, failure, "no robot selected") is the
same shape on every step; only the form fields and which calibration_type
they send differ, so it lives here once rather than five times.
"""

from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget

from ....models.config.calibrations import Calibration, calibrations
from ....services.network.pub_sub.robot_topics import event_topic
from ...components import Card, Column
from ...pages.base import Page
from ...theme.metrics import CARD_GAP, ROW_PADDING


class ReplyBridge(QObject):
    """Carries one MQTT reply from the network thread to the GUI thread.

    paho runs `on_message` on its own thread, and every reply here ends in
    `rebuild()` — which deletes and constructs Qt widgets. Doing that off the
    GUI thread is not merely discouraged: it segfaulted the app mid-layout
    during a base-rotation run, with the main thread inside
    `QBoxLayout::setGeometry` while this thread built a widget underneath it.

    A signal emitted from the network thread to a receiver that lives on the
    GUI thread is delivered by Qt as a queued connection: the payload is put
    on the GUI thread's event queue and unpacked there, which is the one
    supported way to get from one to the other.
    """

    arrived = Signal(dict)


class StepPage(Page):
    """One calibration step. Subclasses supply the form and the send logic."""

    # Set by subclasses: the key under STEP_KEYS this page writes results for.
    step_key = ""

    def __init__(self, workspace=None) -> None:
        super().__init__(workspace)
        # Created on the GUI thread — which is what makes the connection
        # below a queued one, and is the whole point of the bridge.
        self._bridge = ReplyBridge()
        self._bridge.arrived.connect(self._handle_reply)
        # "" when idle, else the action_id currently awaiting a reply — used
        # to ignore stale replies from a request the user has since replaced.
        self._pending = ""
        self._waiting_text = ""
        self._error = ""
        self._unsubscribe = None
        # The form, kept across rebuilds so the values in it survive a reply.
        self._form: QWidget | None = None
        # Whether the waiting card has earned its place on screen. A robot on
        # the LAN answers a servo command in milliseconds, so showing it the
        # instant a command goes out made it appear and vanish between two
        # frames — on a slider that sends on every release, that is a card
        # flashing under the user's hand. It waits for the robot to actually
        # be slow before saying so.
        self._waiting_visible = False
        # Parented to the bridge: a Page is a plain object, not a QObject, so
        # it cannot own a timer. The bridge is the page's QObject and lives
        # exactly as long as it does.
        self._waiting_timer = QTimer(self._bridge)
        self._waiting_timer.setSingleShot(True)
        self._waiting_timer.timeout.connect(self._waiting_overdue)
        # Which robot the kept form was built for. A form is built from one
        # robot's saved values, so switching robots has to discard it rather
        # than leave another machine's numbers on screen.
        self._form_robot = ""

    @property
    def status(self) -> str:
        robot = self.workspace.robot
        if not robot:
            return "No robot selected"
        if self._pending:
            return f"{robot} — waiting for a reply"
        saved = calibrations().find(robot, self.step_key)
        return f"{robot} — saved" if saved else f"{robot} — not calibrated"

    # ---- body ------------------------------------------------------------
    def build_page(self) -> QWidget:
        """The step, with its form kept alive across rebuilds.

        The form is built once and reused. It used to be swapped out for a
        "Waiting…" card whenever a command was in flight, which meant every
        reply destroyed and rebuilt the controls — and any value the user had
        dialled into them went with it. On a page whose inputs are sliders
        that send on release, that fired on every drag: the handle vanished
        mid-gesture and the other fields reset around it.

        So waiting is a banner above the form now, not a replacement for it.
        Pages that genuinely want a fresh form each time can say so with
        `form_is_disposable`; the default is to keep it, because a control
        holding something the user typed or dragged is the common case.
        """
        sections = [self.build_robot_picker()]

        robot = self.workspace.robot
        if not robot:
            sections.append(Card(
                "No robot marked yet",
                "Mark a user as a robot on Network → Robots to enable "
                "the form below.",
            ))

        if self._pending and self._waiting_visible:
            sections.append(Card("Waiting for the robot…", self._waiting_text))
        elif self._error:
            sections.append(Card("The robot reported a problem", self._error))

        # Shown either way, so a step's fields are always visible to read and
        # fill in — only sending is gated on a robot being selected.
        sections.append(self.form())

        sections.append(self.build_saved_values())
        return Column(*sections)

    # A page whose form carries no user state — one rebuilt wholly from the
    # reply, with nothing typed or dragged to lose — may set this True and
    # get the old behaviour back.
    form_is_disposable = False

    def form(self) -> QWidget:
        """This step's form, built once unless the page opts out.

        Reparenting is what makes reuse safe: `Page.rebuild()` deletes the
        widgets it took out of the layout, so the kept form is pulled clear
        first and handed to the new column, rather than being deleted as a
        child of the old one.
        """
        if self.form_is_disposable:
            return self.build_form()

        if self._form is not None and self._form_robot != self.workspace.robot:
            self.discard_form()

        if self._form is None:
            self._form = self.build_form()
            self._form_robot = self.workspace.robot
        else:
            self._form.setParent(None)
        self.form_refreshed(self._form)
        return self._form

    def form_refreshed(self, form: QWidget) -> None:
        """Bring a kept form up to date with the page's state.

        Called on every rebuild, with the same widget as last time. The
        default handles the one thing every step shares — whether its
        controls may act right now — by asking the form, if it can say.
        Override for anything else a page needs to refresh in place.
        """
        setter = getattr(form, "set_enabled", None)
        if callable(setter):
            setter(self.can_send)

    def discard_form(self) -> None:
        """Drop the kept form so the next rebuild constructs a fresh one.

        For the state changes a form cannot absorb — a different robot
        selected, whose saved values the form is built from.
        """
        if self._form is not None:
            self._form.setParent(None)
            self._form.deleteLater()
            self._form = None

    @property
    def can_send(self) -> bool:
        """Whether the form's buttons should be clickable right now."""
        return bool(self.workspace.robot) and not self._pending

    def build_robot_picker(self) -> QWidget:
        """Which robot this step calibrates — always shown, even for one.

        Hiding the picker below two robots meant the only way to see what
        Studio was aimed at was to read the topic in the MQTT log. A robot
        reflashed under a new account name is a *different* robot to the
        registry, so "you only have one" is exactly when being pointed at the
        wrong one is hardest to notice.
        """
        available = self.workspace.robots()
        if not available:
            return QWidget()

        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, CARD_GAP)
        layout.setSpacing(ROW_PADDING)

        label = QLabel("Robot")
        label.setObjectName("CardBody")
        layout.addWidget(label)

        combo = QComboBox()
        names = [robot.name for robot in available]
        labels = [robot.display_name for robot in available]
        combo.addItems(labels)
        current = self.workspace.robot
        if current in names:
            combo.setCurrentIndex(names.index(current))
        combo.currentIndexChanged.connect(
            lambda index: self.workspace.select_robot(names[index])
        )
        layout.addWidget(combo, 1)
        return holder

    def build_form(self) -> QWidget:
        """The step's own fields and send button. Override in every step."""
        raise NotImplementedError

    def build_saved_values(self) -> QWidget:
        robot = self.workspace.robot
        saved = calibrations().find(robot, self.step_key) if robot else None
        if saved is None:
            return Card("Saved values", "Nothing stored.")

        lines = "\n".join(f"{key}: {value}" for key, value in saved.values.items())
        return Card(f"Saved values — {saved.saved_at}", lines or "Nothing stored.")

    # ---- sending a command and waiting for its reply ----------------------
    # How long the robot may take before the page says it is waiting. Short
    # enough that a real stall is reported promptly, long enough that an
    # ordinary servo command — answered in milliseconds — never shows a card
    # at all.
    WAITING_GRACE_MS = 600

    def send(self, action_id: str, *, waiting_text: str = "Sending…") -> None:
        """Start waiting on one action_id. The workspace's client delivers the
        matching reply to `_on_reply` on the robot's own reply topic."""
        self._cancel_wait()
        self._pending = action_id
        self._waiting_text = waiting_text
        self._error = ""
        self._waiting_timer.start(self.WAITING_GRACE_MS)

        robot = self.workspace.robot
        topic = event_topic(robot)
        self._unsubscribe = self.workspace.client().subscribe(topic, self._on_reply)
        self.rebuild()

    def _waiting_overdue(self) -> None:
        """The robot has taken long enough that the wait is worth showing."""
        if not self._pending:
            return
        self._waiting_visible = True
        self.rebuild()

    def _on_reply(self, _topic: str, payload: dict) -> None:
        """Called on paho's network thread. Does nothing but hand over.

        Every branch below touches widgets, so none of it may run here — see
        ReplyBridge. Keep this method free of anything but the emit.
        """
        self._bridge.arrived.emit(payload)

    def _handle_reply(self, payload: dict) -> None:
        """The same reply, now on the GUI thread, where widgets are legal."""
        if payload.get("action_id") != self._pending:
            return
        status = payload.get("status")
        if status == "progress":
            # A robot that reports progress is telling you this will take a
            # while — the base rotation profile runs for minutes. No grace
            # period for that: show it now and keep showing it.
            self._waiting_text = str(payload.get("progress", payload))
            self._waiting_timer.stop()
            self._waiting_visible = True
            self.rebuild()
            return
        if status == "failed":
            self._cancel_wait()
            self._error = str(payload.get("error") or payload.get("message") or "failed")
            self.rebuild()
            return
        if not self.is_terminal(payload):
            return

        self._cancel_wait()
        self.on_completed(payload)
        self.rebuild()

    def is_terminal(self, payload: dict) -> bool:
        """Whether this reply is the one to treat as success."""
        return payload.get("status") == "completed"

    # Envelope fields, never results: they identify the exchange rather than
    # say anything about the robot.
    ENVELOPE = ("sender", "action_id", "status", "type", "uptime_ms",
                "free_heap", "workflow_id", "workflow_event_id", "phrase")

    def on_completed(self, payload: dict) -> None:
        """A terminal `completed` reply arrived. Default: merge its results
        into whatever this step has already recorded.

        Merged rather than replaced because a step is usually several
        commands — Base + Perch alone sends six, one per field — and each
        reply carries only the value it just wrote. Replacing would leave
        the last one sent as the only one saved, which looks exactly like
        the other five having failed.
        """
        robot = self.workspace.robot
        existing = calibrations().find(robot, self.step_key)
        values = dict(existing.values) if existing is not None else {}
        values.update(self.results(payload))

        calibrations().save(Calibration(
            robot=robot,
            step=self.step_key,
            values=values,
            saved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ))

    def results(self, payload: dict) -> dict:
        """The values worth keeping out of one terminal reply.

        The firmware reports a written value as a nested object — the perch
        steps send `{"PERCH_ELBOW_ANGLE": {"TYPE": ..., "VALUE": 44}}`, and
        the hover steps a flat object of angles. Unwrapping the single-value
        shape here is what stops the saved-values card from showing a
        literal Python dict where a number belongs.
        """
        found = {}
        for key, value in payload.items():
            if key in self.ENVELOPE:
                continue
            if isinstance(value, dict):
                # {"TYPE": ..., "VALUE": n} is one measurement, however the
                # firmware chose to label it. Anything else is a real group
                # of values (hover angles, a stencil session) and is kept
                # whole, flattened one level so the card can read it.
                if "VALUE" in value:
                    found[value.get("TYPE", key)] = value["VALUE"]
                else:
                    found.update(value)
            else:
                found[key] = value
        return found

    def _cancel_wait(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        self._pending = ""
        # Stop the grace timer and hide the card together: a reply that beat
        # the timer must not let it fire afterwards, and one that did not
        # must take the card back down.
        self._waiting_timer.stop()
        self._waiting_visible = False
