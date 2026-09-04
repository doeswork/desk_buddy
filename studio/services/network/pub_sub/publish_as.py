"""Publish one message authenticated as a specific account.

`MqttClient` is Studio's own always-on connection — every message it sends
goes out as Studio, whatever the JSON `sender` field says. This is for the
opposite case: proving what a *given* account can actually do, using that
account's own saved credentials (studio keeps them in the clear precisely so
it can do this — see `models.config.mqtt_users`). A real per-account
connection means a topic that account's ACL does not cover is genuinely
refused by the broker, not just faked in the payload.

Short-lived on purpose: connect, publish, disconnect. This is a test tool, not
a second thing to keep alive and reconcile against the broker lifecycle the
way `MqttClient` is.

**A PUBACK is not proof the broker's ACL accepted the message.** Verified
directly against a real ACL-scoped account: Mosquitto PUBACKs a QoS-1 publish
purely for having received it over the wire, then applies the ACL and drops a
disallowed one silently — the publisher sees success either way. There is no
MQTT-level signal, at any QoS, for "your publish was accepted onto the topic
tree." What `publish_as` reports is therefore always "the broker took delivery
of this," never "this account was allowed to publish here" — see the debug
tray / traffic recorder if the question is whether a message actually landed
on its topic.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Event

from ....models.config.mqtt_users import MqttUser
from ..broker.commands import DEFAULT_HOST, is_ours, our_port

CONNECT_TIMEOUT = 5.0


@dataclass(frozen=True)
class PublishResult:
    ok: bool
    message: str


def publish_as(account: MqttUser, topic: str, payload: str, *, qos: int = 1) -> PublishResult:
    """Connect as `account`, publish `payload` to `topic`, disconnect.

    `payload` is sent verbatim — the caller decides whether it is JSON, so
    this stays useful for provoking firmware's non-JSON/malformed-input paths
    too, not only well-formed test messages.

    Success here means the broker took delivery of the publish — not that its
    ACL let the message onto the topic. See the module docstring; there is no
    MQTT-level way to tell the two apart from the publishing side.
    """
    if not is_ours():
        return PublishResult(False, "Studio's broker is not running.")

    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        return PublishResult(False, "paho-mqtt is not installed.")

    port = our_port()
    outcome: dict = {}
    done = Event()
    pending_mid: dict = {}

    def on_connect(client, _userdata, _flags, reason_code, _properties) -> None:
        if reason_code != 0:
            outcome["error"] = f"Connection refused: {reason_code}"
            done.set()
            return
        info = client.publish(topic, payload, qos=qos)
        if qos == 0:
            # QoS 0 has no broker acknowledgment to wait for — publish() has
            # already hit the socket by the time it returns.
            outcome["published"] = True
            done.set()
        else:
            # The PUBACK arrives on this same network thread, so it must not
            # be waited for here — wait_for_publish() would block the very
            # thread that has to process it, and deadlock until timeout on
            # every publish, ACL-allowed or not. on_publish() fires from
            # that thread instead once the PUBACK is actually processed.
            pending_mid["mid"] = info.mid

    def on_publish(_client, _userdata, mid, _reason_code=None, _properties=None) -> None:
        if mid == pending_mid.get("mid"):
            outcome["published"] = True
            done.set()

    def on_connect_fail(client, _userdata) -> None:
        outcome["error"] = "Could not reach the broker."
        done.set()

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"desk-buddy-studio-send-as-{account.name}",
        protocol=mqtt.MQTTv311,
    )
    client.username_pw_set(account.name, account.password)
    client.on_connect = on_connect
    client.on_connect_fail = on_connect_fail
    client.on_publish = on_publish

    try:
        client.connect(DEFAULT_HOST, port, keepalive=10)
        client.loop_start()
        finished = done.wait(timeout=CONNECT_TIMEOUT)
        client.disconnect()
        client.loop_stop()
    except (OSError, ValueError) as error:
        return PublishResult(False, str(error))

    if not finished:
        return PublishResult(
            False, "Timed out waiting for the broker to acknowledge the publish."
        )
    if outcome.get("published"):
        return PublishResult(
            True,
            f"Delivered to the broker as {account.name}. This confirms the "
            "connection and the publish itself, not whether the ACL then let "
            f"it onto {topic!r} — check the debug tray to see if it landed.",
        )
    return PublishResult(False, outcome.get("error", "The broker refused the publish."))
