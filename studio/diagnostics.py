"""One block of text describing what Studio is actually doing right now.

For the moment when the app disagrees with what you expected: rather than
describing the symptom, copy this and paste it. Every line is something that
would otherwise take a question to establish.

Read-only, and safe to share — it deliberately carries no passwords, no broker
credentials, and no file contents, only paths and states.

    Help → Copy Diagnostics       puts it on the clipboard
    python -m studio.diagnostics  prints it, no window needed
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path


def _section(title: str, lines: list[str]) -> list[str]:
    return [f"## {title}", *(f"  {line}" for line in lines), ""]


def _environment() -> list[str]:
    import PySide6

    return _section("Environment", [
        f"platform     {platform.system()} {platform.release()}",
        f"python       {sys.version.split()[0]}",
        f"PySide6      {PySide6.__version__}",
        f"frozen       {getattr(sys, 'frozen', False)}",
    ])


def _broker() -> list[str]:
    from .services.network import detect, report, robot_endpoint, studio_endpoint
    from .services.network.broker import system
    from .services.network.broker.finder import SYSTEM_PORT, port_open

    status = detect()
    live = report()
    box = system.describe()
    access = system.write_access()

    lines = [
        f"mosquitto    {status.broker.path or 'NOT FOUND'}",
        f"version      {status.broker.version or '(none reported)'}",
        f"passwd tool  {status.passwd_tool.path or 'NOT FOUND'}",
        f"installed    {status.installed}   partial={status.partial}",
        "",
        f"port {SYSTEM_PORT}    open={port_open(SYSTEM_PORT)}",
        f"host         {box.host or '(none)'}:{box.port}",
        f"state        reachable={box.reachable} service={box.service_active}",
        f"account      {box.user or '(none)'} recorded={box.recorded} "
        f"verified={box.verified}",
        f"studio →     {studio_endpoint()}",
        f"robot   →    {robot_endpoint()}",
        "",
        f"may write    {access.allowed}",
        f"  passwd     {system.PASSWD_FILE} writable={access.passwd_writable}",
        f"  acl        {system.ACL_FILE} writable={access.acl_writable}",
        f"  dir        {system.CONFIG_DIR} writable={access.dir_writable}",
    ]
    if access.reason:
        lines.append(f"  why not    {access.reason}")

    lines += [
        "",
        f"headline     {live.headline!r}",
        f"detail       {live.detail!r}",
        f"chip         {live.chip!r}",
    ]

    try:
        lines += ["", f"accounts     {len(system.accounts())} on the broker"]
        for account in system.accounts():
            lines.append(f"  {account.name:16} {account.topics_display}")
    except OSError as error:
        lines += ["", f"accounts     unavailable: {error}"]

    return _section("Broker", lines)


def _paths() -> list[str]:
    from .storage.settings import settings

    lines = []
    try:
        preferences = Path(settings().path)
        lines += [
            f"preferences  {preferences}",
            f"  exists     {preferences.exists()}",
        ]
    except OSError as error:
        lines.append(f"preferences  unavailable: {error}")

    try:
        from .services.network.broker import system

        for path in (system.CONFIG_FILE, system.PASSWD_FILE, system.ACL_FILE):
            if path.exists():
                mode = oct(path.stat().st_mode)[-3:]
                lines.append(f"  {path.name:16} {mode}  {path.stat().st_size}b")
            else:
                lines.append(f"  {path.name:16} missing")
    except OSError as error:
        lines.append(f"broker files unavailable: {error}")

    try:
        from .models.data import app_errors, mqtt_messages

        history = mqtt_messages()
        errors = app_errors()
        lines += [
            f"database     {history.path}",
            f"  messages   {history.count()}",
            f"  app errors {errors.count()}",
        ]
    except (OSError, RuntimeError) as error:
        lines.append(f"database     unavailable: {error}")

    return _section("Paths", lines)


def _window(window) -> list[str]:
    """What the UI is showing — the half a log file never captures."""
    workspace = window.workspace
    buttons = window.toolbar.buttons

    lines = [
        f"workspace    {workspace.key}",
        f"page         {workspace.page.key}",
        f"theme        {window.theme}   zoom={window.zoom}",
        f"chips        {window.workspace_bar.broker_label.text()!r}"
        f" / {window.workspace_bar.connection_label.text()!r}",
        f"toolbar      {list(buttons) or 'EMPTY'}",
        f"debug tray   visible={window.debug_dock.isVisible()}",
        f"recorder     {window._traffic_recorder.status!r}",
    ]
    for label, button in buttons.items():
        lines.append(f"  {label:16} enabled={button.isEnabled()}")

    result = getattr(workspace, "last_result", None)
    if result is not None:
        lines += [
            "",
            f"last action  ok={result.ok} {result.message!r}",
            f"  detail     {result.detail!r}",
        ]

    return _section("Window", lines)


def text(window=None) -> str:
    """The full report. Pass the window to include what the UI is showing."""
    lines = ["# Desk Buddy Studio — diagnostics", ""]
    for part in (_environment, _broker, _paths):
        try:
            lines += part()
        except Exception as error:  # a diagnostic that crashes helps nobody
            lines += _section(part.__name__.strip("_").title(),
                              [f"FAILED: {type(error).__name__}: {error}"])

    if window is not None:
        try:
            lines += _window(window)
        except Exception as error:
            lines += _section("Window", [f"FAILED: {type(error).__name__}: {error}"])

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    print(text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
