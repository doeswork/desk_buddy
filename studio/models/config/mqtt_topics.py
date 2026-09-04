"""Topic filters: what one is, and what a new account starts with.

Split from `mqtt_users` because a topic is not an account's private business.
Workflows and the vision service will both want to say "is this a valid
filter" and "what does a new participant start with" without reaching into
the account record to ask.
"""

from __future__ import annotations

import re

# A topic filter, in MQTT's own terms: '/'-separated levels, each plain or a
# wildcard. '+' matches one level, '#' matches the rest and must be last and
# alone in its level — those two rules are exactly what turn an arbitrary
# string into something a broker will not reject outright.
_LEVEL = r"(?:[^+#/]+|\+)"
TOPIC_PATTERN = re.compile(rf"^{_LEVEL}(?:/{_LEVEL})*(?:/#)?$|^#$")
TOPIC_RULE = (
    "Letters, digits and most symbols, '/'-separated by level. "
    "'+' matches one level; '#' matches everything below and must end the topic."
)

# The whole tree. What Studio itself needs, and what a vision server or a web
# app is usually after.
EVERYTHING = "#"


def default_topics(name: str, *, full_access: bool = False) -> tuple[str, ...]:
    """What a newly created account starts with.

    One topic, not none: a getting-started path needs a single choice, not an
    empty list to fill in before anything works. Nothing about this is final —
    it is the seed for a list the user then edits.
    """
    return (EVERYTHING,) if full_access else (f"{name}/#",)


def validate_topic(topic: str, existing: tuple[str, ...] = ()) -> str:
    """Empty string when the topic filter is usable, else why it is not."""
    if not topic or not topic.strip():
        return "A topic is required."
    if topic != topic.strip():
        return "No leading or trailing spaces."
    if not TOPIC_PATTERN.match(topic):
        return TOPIC_RULE
    if topic in existing:
        return f"{topic!r} is already on this account."
    return ""
