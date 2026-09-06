"""Model tests: JSON configuration and SQLite history, with no broker involved.

    python -m studio.models.tests.tests

Everything here runs against throwaway storage. What needs a real Mosquitto to
mean anything — that credentials connect and traffic is observed — lives in
`studio.services.network.tests.account_tests` instead.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings

from ...storage.settings import Settings
from ...storage.store import Store
from ..config.calibrations import Calibration, Calibrations
from ..config.mqtt_topics import TOPIC_PATTERN, default_topics, validate_topic
from ..config.mqtt_users import STUDIO_NAME, MqttUser, Users
from ..config.prefrences import Preferences
from ..config.robots import Robots
from ..data.app_errors import AppErrors
from ..data.database import Database, SCHEMA_VERSION
from ..data.mqtt_messages import MqttMessages


def fresh_users() -> tuple[Users, Path]:
    directory = Path(tempfile.mkdtemp())
    return Users(Store("mqtt_users", directory=directory)), directory


def fresh_preferences() -> tuple[Preferences, Path]:
    path = Path(tempfile.mktemp(suffix=".ini"))
    return Preferences(Settings(QSettings(str(path), QSettings.IniFormat))), path


# ---- Preferences --------------------------------------------------------

def test_broker_starts_on_launch_by_default_and_can_be_disabled() -> None:
    preferences, path = fresh_preferences()
    assert preferences.mqtt_broker_auto_start
    preferences.set_mqtt_broker_auto_start(False)

    reloaded = Preferences(Settings(QSettings(str(path), QSettings.IniFormat)))
    assert not reloaded.mqtt_broker_auto_start


# ---- Topics --------------------------------------------------------------

def test_topic_pattern_follows_mqtt_rules() -> None:
    for good in ("#", "a", "a/b", "a/+/c", "a/#", "+", "+/+", "a b"):
        assert TOPIC_PATTERN.match(good), good
    # '#' must end the filter, and empty levels are not a topic.
    for bad in ("a/#/b", "a//b", "", "a/b#"):
        assert not TOPIC_PATTERN.match(bad), bad


def test_topic_validation_explains_itself() -> None:
    assert validate_topic("") == "A topic is required."
    assert "spaces" in validate_topic(" a/b ")
    assert validate_topic("a/#/b")
    assert "already" in validate_topic("a/b", ("a/b",))
    assert validate_topic("a/b", ("c/d",)) == ""


def test_new_accounts_start_with_one_topic() -> None:
    """The getting-started path: one choice, not an empty list to fill in."""
    assert default_topics("robot-1") == ("robot-1/#",)
    assert default_topics("vision", full_access=True) == ("#",)


# ---- Users ---------------------------------------------------------------

def test_an_account_round_trips_through_the_file() -> None:
    users, directory = fresh_users()
    created, problem = users.add("robot-1")
    assert created and not problem

    # A genuinely fresh reader of the same file, as a new launch would be.
    reloaded = Users(Store("mqtt_users", directory=directory))
    found = reloaded.find("robot-1")
    assert found is not None
    assert found.password == created.password
    assert found.topics == ("robot-1/#",)


def test_studio_keeps_its_password_across_reads() -> None:
    """The bug the records layer exists to fix.

    Studio's password used to live only in Mosquitto's hash, which cannot be
    read back, so every launch minted a new one.
    """
    users, directory = fresh_users()
    first = users.ensure_studio()
    assert users.ensure_studio().password == first.password

    reloaded = Users(Store("mqtt_users", directory=directory))
    assert reloaded.ensure_studio().password == first.password
    assert reloaded.find(STUDIO_NAME).full_access


def test_studio_is_reserved_and_permanent() -> None:
    users, _ = fresh_users()
    users.ensure_studio()
    assert "reserved" in users.validate(STUDIO_NAME)
    assert "cannot be removed" in users.remove(STUDIO_NAME)
    assert users.find(STUDIO_NAME) is not None


def test_names_that_would_wreck_a_topic_are_refused() -> None:
    users, _ = fresh_users()
    for name in ("Robot", "with space", "üñî", "a", "x" * 33, "-leading"):
        assert users.validate(name), name
    for name in ("black", "esp32-5", "vision_server", "a1"):
        assert users.validate(name) == "", name


def test_duplicate_names_are_refused() -> None:
    users, _ = fresh_users()
    users.add("black")
    assert "already exists" in users.validate("black")
    created, problem = users.add("black")
    assert created is None and problem


def test_topics_can_be_added_and_removed() -> None:
    users, _ = fresh_users()
    users.add("black")

    topics, problem = users.add_topic("black", "black/status")
    assert not problem and topics == ("black/#", "black/status")

    topics, problem = users.add_topic("black", "shared/news")
    assert not problem and topics == ("black/#", "black/status", "shared/news")
    assert users.find("black").topics == topics

    topics, problem = users.remove_topic("black", "black/status")
    assert not problem and topics == ("black/#", "shared/news")

    # Down to nothing is allowed — a real, if useless, state.
    for topic in list(topics):
        topics, problem = users.remove_topic("black", topic)
        assert not problem
    assert users.find("black").topics == ()


def test_topic_edits_are_rejected_sensibly() -> None:
    users, _ = fresh_users()
    users.add("black")

    assert "already" in users.add_topic("black", "black/#")[1]
    assert users.add_topic("black", "bad/#/topic")[1]
    assert "No account" in users.add_topic("ghost", "anything")[1]
    assert "does not have" in users.remove_topic("black", "never/added")[1]
    assert "No account" in users.remove_topic("ghost", "anything")[1]


def test_reset_password_changes_only_the_password() -> None:
    users, _ = fresh_users()
    created, _ = users.add("black")
    users.add_topic("black", "shared/news")

    password, problem = users.reset_password("black")
    assert not problem and password != created.password

    found = users.find("black")
    assert found.password == password
    assert found.topics == ("black/#", "shared/news"), "topics were disturbed"


def test_removing_an_account_that_is_not_there() -> None:
    users, _ = fresh_users()
    assert "No account" in users.remove("ghost")


def test_passwords_are_long_and_unique() -> None:
    users, _ = fresh_users()
    first, _ = users.add("one")
    second, _ = users.add("two")
    assert len(first.password) >= 16
    assert first.password != second.password


def test_a_damaged_file_costs_only_what_is_damaged() -> None:
    """A hand-edited file with one bad entry keeps the rest of the accounts."""
    users, directory = fresh_users()
    users.add("black")
    users.add("silver")

    store = Store("mqtt_users", directory=directory)
    raw = store.read(default=[])
    raw.append({"not": "an account"})
    raw.append("nor is this")
    store.write(raw)

    names = Users(store).names()
    assert names == ["black", "silver"], names


def test_an_unreadable_file_does_not_stop_the_app() -> None:
    directory = Path(tempfile.mkdtemp())
    (directory / "mqtt_users.json").write_text("{ this is not json")
    assert Users(Store("mqtt_users", directory=directory)).all() == []


# ---- MQTT history --------------------------------------------------------

def fresh_messages() -> MqttMessages:
    directory = Path(tempfile.mkdtemp())
    return MqttMessages(Database(directory / "studio.sqlite3"))


def test_mqtt_messages_round_trip_raw_payloads() -> None:
    messages = fresh_messages()
    saved = messages.append(
        "black/telemetry", b"\x00\xffcamera", qos=1, retained=True,
        received_at="2026-09-03T12:34:56+00:00",
    )

    found = messages.recent(include_heartbeats=True)
    assert found == [saved]
    assert found[0].payload == b"\x00\xffcamera"
    assert "8 bytes" in found[0].payload_text
    assert found[0].qos == 1 and found[0].retained


def test_heartbeats_are_stored_even_when_hidden_from_the_tray() -> None:
    messages = fresh_messages()
    messages.append("black/status", '{"sender":"firmware","log":"heartbeat"}')
    normal = messages.append("black/status", '{"status":"completed"}')

    assert messages.count() == 2
    assert messages.recent() == [normal]
    assert len(messages.recent(include_heartbeats=True)) == 2


def test_mqtt_history_is_bounded_only_when_read_and_can_be_cleared() -> None:
    messages = fresh_messages()
    for number in range(5):
        messages.append("test/topic", str(number))

    assert [message.payload_text for message in messages.recent(2)] == ["3", "4"]
    assert messages.count() == 5
    messages.clear()
    assert messages.count() == 0


def test_database_starts_at_the_current_schema() -> None:
    messages = fresh_messages()
    with messages.database.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


# ---- Application errors -------------------------------------------------

def fresh_errors() -> AppErrors:
    directory = Path(tempfile.mkdtemp())
    return AppErrors(Database(directory / "studio.sqlite3"))


def test_app_errors_round_trip_with_context() -> None:
    errors = fresh_errors()
    saved = errors.append(
        "ValueError",
        "bad angle",
        "Traceback...\nValueError: bad angle\n",
        source="thread:motion",
        occurred_at="2026-09-03T12:34:56+00:00",
    )

    assert errors.recent() == [saved]
    assert "thread:motion" in saved.heading
    assert "ValueError: bad angle" in saved.display


def test_app_error_records_a_real_traceback() -> None:
    errors = fresh_errors()
    try:
        raise RuntimeError("camera unavailable")
    except RuntimeError as exception:
        saved = errors.record_exception(
            type(exception), exception, exception.__traceback__
        )

    assert "RuntimeError: camera unavailable" in saved.traceback
    assert errors.count() == 1


def test_app_error_history_can_be_bounded_and_cleared() -> None:
    errors = fresh_errors()
    for number in range(4):
        errors.append("Problem", str(number))

    assert [error.message for error in errors.recent(2)] == ["2", "3"]
    errors.clear()
    assert errors.count() == 0


def test_account_describes_its_own_reach() -> None:
    assert MqttUser("black", topics=("black/#",)).topics == ("black/#",)
    assert MqttUser("vision", topics=("#",)).full_access
    assert not MqttUser("black", topics=("black/#",)).full_access
    assert MqttUser("black", topics=("a", "b")).topics_display == "a, b"
    assert MqttUser("black").topics_display == "(none)"
    assert "black/#" in MqttUser("black", topics=("black/#",)).description


# ---- Calibrations --------------------------------------------------------

def test_a_calibration_round_trips_and_replaces() -> None:
    directory = Path(tempfile.mkdtemp())
    store = Calibrations(Store("calibrations", directory=directory))

    store.save(Calibration(robot="black", step="ik", values={"reach": 120}))
    found = store.find("black", "ik")
    assert found is not None and found.values == {"reach": 120}

    # Running a step again replaces its result rather than stacking one up.
    store.save(Calibration(robot="black", step="ik", values={"reach": 140}))
    assert len(store.for_robot("black")) == 1
    assert store.find("black", "ik").values == {"reach": 140}

    store.save(Calibration(robot="silver", step="ik", values={"reach": 90}))
    assert len(store.all()) == 2
    store.clear("black")
    assert [c.robot for c in store.all()] == ["silver"]


# ---- Robots ----------------------------------------------------------------

def fresh_robots() -> tuple[Robots, Users, Path]:
    directory = Path(tempfile.mkdtemp())
    return (
        Robots(Store("robots", directory=directory)),
        Users(Store("mqtt_users", directory=directory)),
        directory,
    )


def test_a_robot_must_be_an_existing_non_studio_account() -> None:
    bots, users, _ = fresh_robots()

    robot, problem = bots.add("ghost")
    assert robot is None and "Create it on Network" in problem

    users.ensure_studio()
    robot, problem = bots.add(STUDIO_NAME)
    assert robot is None and "not a robot" in problem

    users.add("black")
    robot, problem = bots.add("black", "Black Buddy")
    assert robot is not None and not problem
    assert robot.display_name == "Black Buddy"


def test_a_robot_round_trips_and_falls_back_to_its_account_name() -> None:
    bots, users, directory = fresh_robots()
    users.add("black")
    bots.add("black")

    reloaded = Robots(Store("robots", directory=directory))
    found = reloaded.find("black")
    assert found is not None
    assert found.display_name == "black", "an unlabeled robot shows its account name"


def test_a_robot_cannot_be_marked_twice() -> None:
    bots, users, _ = fresh_robots()
    users.add("black")
    bots.add("black")

    robot, problem = bots.add("black")
    assert robot is None and "already marked" in problem


def test_unmarking_a_robot_leaves_its_account_alone() -> None:
    bots, users, _ = fresh_robots()
    users.add("black")
    bots.add("black")

    assert bots.remove("black") == ""
    assert bots.find("black") is None
    assert users.find("black") is not None, "unmarking must not touch the account"

    assert "not marked" in bots.remove("black")


def test_record_keeps_a_password_for_an_account_made_elsewhere() -> None:
    """The broker hashes a password; this is where the original survives.

    Without it, provisioning a robot means asking the user for a secret
    Studio generated for them and showed once.
    """
    store, _ = fresh_users()
    store.record("black", "s3cret")
    assert store.find("black").password == "s3cret"

    # Recording again replaces rather than duplicating: a password reset
    # must not leave the old one findable beside the new one.
    store.record("black", "newer")
    assert [u.name for u in store.all()] == ["black"]
    assert store.find("black").password == "newer"


def test_forget_drops_a_record_for_a_deleted_account() -> None:
    """A password for an account that no longer exists is worse than none —
    a later account reusing the name would inherit a dead credential."""
    store, _ = fresh_users()
    store.record("black", "s3cret")
    store.forget("black")
    assert store.find("black") is None

    store.forget("never-existed")  # silent, not an error


def test_forget_may_drop_studio_where_remove_may_not() -> None:
    """`remove()` guards Studio's account because removing it would cut
    Studio off. `forget()` is called after the broker has already deleted
    it, so refusing there would strand a record of a dead credential."""
    store, _ = fresh_users()
    store.ensure_studio()
    assert store.remove(STUDIO_NAME)  # refused, returns a reason
    store.forget(STUDIO_NAME)
    assert store.find(STUDIO_NAME) is None


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
    print(f"OK: {len(tests)} model tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
