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
    from .services.network import broker_commands as commands
    from .services.network import detect, report
    from .services.network.broker_finder import DEFAULT_PORT, SYSTEM_PORT, port_open

    status = detect()
    live = report()

    lines = [
        f"mosquitto    {status.broker.path or 'NOT FOUND'}",
        f"version      {status.broker.version or '(none reported)'}",
        f"passwd tool  {status.passwd_tool.path or 'NOT FOUND'}",
        f"installed    {status.installed}   partial={status.partial}",
        "",
        f"our port     {DEFAULT_PORT}  open={port_open(DEFAULT_PORT)}",
        f"system port  {SYSTEM_PORT}  open={port_open(SYSTEM_PORT)}",
        f"running_port {live.port or 0}",
        f"is_ours      {commands.is_ours()}",
        "",
        f"headline     {live.headline!r}",
        f"detail       {live.detail!r}",
        f"chip         {live.chip!r}",
    ]

    try:
        config = commands.config_path()
        lines += ["", f"config       {config}", f"config exists {config.exists()}"]
    except OSError as error:
        lines += ["", f"config       unavailable: {error}"]

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
        from .services.network.broker_commands import broker_dir

        directory = broker_dir()
        lines.append(f"broker dir   {directory}")
        if directory.exists():
            for child in sorted(directory.iterdir()):
                mode = oct(child.stat().st_mode)[-3:]
                lines.append(f"  {child.name:16} {mode}  {child.stat().st_size}b")
    except OSError as error:
        lines.append(f"broker dir   unavailable: {error}")

    return _section("Paths", lines)


def _window(window) -> list[str]:
    """What the UI is showing — the half a log file never captures."""
    workspace = window.workspace
    buttons = window.context_bar.buttons

    lines = [
        f"workspace    {workspace.key}",
        f"page         {workspace.page.key}",
        f"theme        {window.theme}   zoom={window.zoom}",
        f"BAR 1 chips  {window.nav_bar.broker_label.text()!r}"
        f" / {window.nav_bar.connection_label.text()!r}",
        f"BAR 2        {list(buttons) or 'EMPTY'}",
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
