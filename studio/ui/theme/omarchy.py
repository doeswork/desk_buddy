"""The Omarchy skin: follow whatever theme the desktop is currently wearing.

Omarchy keeps the active theme symlinked at ~/.local/state/omarchy/current/theme,
and every theme — stock, installed, or hand-written — ships a colors.toml with
the same key set. We read that file and map its keys onto our Palette, so the
app matches Tokyo Night, Nord, Gruvbox or anything the user switches to without
knowing any of their names. Nothing here is hardcoded to a specific theme.

Only the keys present in every Omarchy theme are used; the handful that some
themes omit (orange, brown, the hyprland_* border colors) are deliberately
avoided so an unusual or custom theme can never fail to load.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from .palette import Palette

NAME = "omarchy"

STATE = Path.home() / ".local/state/omarchy/current"
COLORS = STATE / "theme" / "colors.toml"
THEME_NAME = STATE / "theme.name"

# Watching these two is enough to catch `omarchy theme set`: the name file is
# rewritten and the theme symlink is repointed on every switch.
WATCH_PATHS = (THEME_NAME, COLORS)


def is_omarchy() -> bool:
    """True on an Omarchy desktop with a theme applied.

    ID=omarchy in os-release is the distro's own marker; the colors file has to
    actually exist too, since that is what we read.
    """
    try:
        release = Path("/etc/os-release").read_text()
    except OSError:
        return False
    if not any(line.strip() == "ID=omarchy" for line in release.splitlines()):
        return False
    return COLORS.is_file()


def theme_name() -> str:
    """The active theme's display name, e.g. "Tokyo Night". Best effort."""
    try:
        slug = THEME_NAME.read_text().strip()
    except OSError:
        return "System"
    return slug.replace("-", " ").title() or "System"


def load() -> Palette | None:
    """Build a Palette from the live theme, or None if it cannot be read."""
    try:
        with COLORS.open("rb") as handle:
            c = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return None

    try:
        return _to_palette(c)
    except KeyError:
        # A theme missing one of the core keys is not one we can render.
        return None


def _to_palette(c: dict) -> Palette:
    """Map Omarchy's color roles onto ours.

    Omarchy names colors by tone; we name them by job:

        background         -> the page
        lighter_background -> panels and cards, so a card lifts off the page
        selection          -> borders and quiet fills
        foreground         -> body text
        dark_foreground    -> muted / not-built-yet text
        accent             -> the one saturated color

    "lighter" is literal, not semantic: on a light theme it is the *lighter* of
    the two, so the page and the panel swap roles to keep cards raised.
    """
    dark = c.get("mode", "dark") != "light"

    background = c["background"]
    panel = c["lighter_background"]
    if not dark:
        background, panel = panel, background

    # A few themes ship the two identical (last-horizon, solitude), which would
    # leave cards invisible. Nudge the panel until it separates from the page.
    if panel.lower() == background.lower():
        panel = _mix(panel, "#ffffff" if dark else "#000000", 0.07)

    accent = c["accent"]

    # Borders have to be visible against both surfaces they divide. A couple of
    # themes set selection equal to one of them (white), which would erase every
    # card outline, so fall back to a tint of the muted tone.
    border = _visible_border(c["selection"], background, panel, c["dark_foreground"])

    return Palette(
        name=NAME,
        label=f"System ({theme_name()})",
        text=c["foreground"],
        border=border,
        background=background,
        panel=panel,
        accent=accent,
        # No theme ships a "pressed accent", so we derive one by shading.
        accent_dark=_shade(accent, 0.82 if dark else 0.88),
        # Text drawn on the accent has to survive whatever hue the accent is —
        # accents run from #205EA6 to #82FB9C across the stock themes alone.
        on_accent=_readable_on(accent),
        # dark_foreground is the intended muted tone, but on some themes it is
        # tuned against the terminal's darkest background and goes nearly
        # invisible on our raised panels. Lift it toward the body text until it
        # is at least legible; a theme whose muted tone already reads is left
        # exactly as its author set it.
        muted=_legible(c["dark_foreground"], (panel, background), c["foreground"]),
        # The quiet fill sits one step away from the page, in the same
        # direction the panels went.
        muted_bg=c["lighter_background"] if dark else c.get("dark_background", background),
        good=c["green"],
        warn=c["yellow"],
        bad=c["red"],
        bad_hover=c.get("bright_red", c["red"]),
        # The e-stop must stay readable whatever red the theme uses; several
        # ship a light red that white text disappears into.
        on_bad=_readable_on(c["red"]),
        dark=dark,
    )


def _shade(hex_color: str, factor: float) -> str:
    """Multiply a color toward black. Used for the accent's hover/pressed step."""
    value = hex_color.lstrip("#")
    parts = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    return "#" + "".join(f"{min(255, round(p * factor)):02x}" for p in parts)


def _luminance(hex_color: str) -> float:
    """Relative luminance, WCAG-style, for picking readable overlay text."""
    value = hex_color.lstrip("#")
    r, g, b = (int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))

    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


# Muted text is meant to recede, but never to vanish. 2.6:1 keeps it clearly
# quieter than body text while staying readable at our smallest size.
MUTED_MIN_CONTRAST = 2.6


def _legible(color: str, surfaces: tuple[str, ...], toward: str) -> str:
    """Blend `color` toward `toward` until it clears the floor on every surface.

    Muted text is drawn on both the page and the panels, so the worse of the
    two is what has to pass.
    """
    def worst(candidate: str) -> float:
        return min(_contrast(candidate, s) for s in surfaces)

    if worst(color) >= MUTED_MIN_CONTRAST:
        return color
    for step in range(1, 11):
        candidate = _mix(color, toward, step / 10)
        if worst(candidate) >= MUTED_MIN_CONTRAST:
            return candidate
    return toward


def _mix(a: str, b: str, amount: float) -> str:
    """Linear blend of two hex colors; amount=0 is `a`, 1 is `b`."""
    pa = [int(a.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4)]
    pb = [int(b.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(pa, pb))


def _contrast(a: str, b: str) -> float:
    """WCAG contrast ratio between two colors."""
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


# A border only has to be a hair different from what it divides, but it must
# not be identical — at 1.0 the card outline disappears entirely.
BORDER_MIN_CONTRAST = 1.08


def _visible_border(border: str, background: str, panel: str, toward: str) -> str:
    """Ensure the border separates from BOTH surfaces it is drawn between."""
    def worst(candidate: str) -> float:
        return min(_contrast(candidate, background), _contrast(candidate, panel))

    if worst(border) >= BORDER_MIN_CONTRAST:
        return border
    # Blend the mid-surface toward the muted tone until an edge appears.
    base = _mix(background, panel, 0.5)
    for step in range(1, 11):
        candidate = _mix(base, toward, step / 10)
        if worst(candidate) >= BORDER_MIN_CONTRAST:
            return candidate
    return toward


def _readable_on(background: str) -> str:
    """Black or white, whichever has the better WCAG contrast on `background`.

    Both are computed rather than guessed from a luminance cutoff, because the
    accents span nearly the whole range and a fixed threshold misjudges the
    ones in the middle.
    """
    lum = _luminance(background)
    on_white = 1.05 / (lum + 0.05)
    on_black = (lum + 0.05) / 0.05
    return "#ffffff" if on_white >= on_black else "#000000"
