"""Provision the narrow MQTT identity used by the local detector."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from ...models.config.mqtt_users import generate_password
from ...models.config.robots import robots
from ..network.broker import system
from ..network.broker.finder import LOOPBACK_HOSTS, lan_address, studio_endpoint

VISION_USER = "vision"
USERNAME_ENV = "DESK_BUDDY_VISION_MQTT_USERNAME"
PASSWORD_ENV = "DESK_BUDDY_VISION_MQTT_PASSWORD"


@dataclass(frozen=True)
class VisionCredentials:
    host: str
    port: int
    username: str
    password: str
    topics: tuple[str, ...]
    source: str


def required_topics() -> tuple[str, ...]:
    return ("vision/#", *(f"{robot.name}/test" for robot in robots().all()))


def ensure_access(environment: Mapping[str, str] | None = None) -> VisionCredentials:
    env = os.environ if environment is None else environment
    host, port = studio_endpoint()
    if not host or not port:
        raise RuntimeError("The MQTT broker is not ready. Finish Network → Broker setup first.")

    env_user = str(env.get(USERNAME_ENV) or "")
    env_password = str(env.get(PASSWORD_ENV) or "")
    topics = required_topics()
    if env_user or env_password:
        if not env_user or not env_password:
            raise RuntimeError(
                f"Set both {USERNAME_ENV} and {PASSWORD_ENV} for an external broker."
            )
        return VisionCredentials(host, port, env_user, env_password, topics, "environment")

    local_hosts = {*LOOPBACK_HOSTS, lan_address()}
    if host not in local_hosts:
        raise RuntimeError(
            "The configured broker is external. Set "
            f"{USERNAME_ENV} and {PASSWORD_ENV} to a broker account with read/write access to "
            + ", ".join(topics)
            + ". Studio will not edit a remote broker account."
        )

    accounts = {account.name: account for account in system.accounts()}
    account = accounts.get(VISION_USER)
    if account is None:
        access = system.write_access()
        if not access.allowed:
            detail = access.reason or "Studio cannot edit this broker's account files."
            raise RuntimeError(
                "The dedicated 'vision' MQTT account is missing. " + detail
                + " Grant account management on Network → Users, or provide the Vision environment variables."
            )
        password = generate_password()
        changed = system.create_account(VISION_USER, password, ",".join(topics))
        if changed.problem:
            raise RuntimeError(f"Could not create the vision MQTT account: {changed.problem}")
        account_password = password
    else:
        account_password = account.password
        if not account_password:
            raise RuntimeError(
                "An account named 'vision' already exists, but Studio does not know its password. "
                "Preserving it unchanged. Record or recreate it on Network → Users."
            )
        if tuple(account.topics) != topics:
            changed = system.set_topics(VISION_USER, topics)
            if changed.problem:
                raise RuntimeError(f"Could not restrict the vision account topics: {changed.problem}")

    return VisionCredentials(host, port, VISION_USER, account_password, topics, "managed")
