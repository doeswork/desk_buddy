"""Palette definition and the skins the app ships with.

Every color the QSS needs is a field on Palette. Adding a skin means adding
another Palette here and listing it in BUILTINS — no QSS is duplicated.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    name: str          # key used by the View menu and by stylesheet()
    label: str         # human-readable, shown in the menu
    dark: bool         # whether this is a dark skin

    text: str          # primary text
    border: str        # borders, dividers, quiet fills
    background: str    # page background
    panel: str         # card / panel background
    accent: str        # the one saturated color
    accent_dark: str   # accent, pressed / hovered
    on_accent: str     # text drawn on top of the accent
    muted: str         # disabled / not-built-yet text
    muted_bg: str      # disabled / not-built-yet fill
    good: str
    warn: str
    bad: str
    bad_hover: str
    on_bad: str        # text drawn on top of `bad` (the e-stop)


# Warm minimalism: charcoal, sand, cloud white, matcha green accent.
LIGHT = Palette(
    name="light",
    label="Light",
    dark=False,
    text="#2c2c2a",
    border="#e8e2d6",
    background="#faf8f4",
    panel="#ffffff",
    accent="#657a5a",
    accent_dark="#55684b",
    on_accent="#ffffff",
    muted="#9c968d",
    muted_bg="#f2efe9",
    good="#5b8c5a",
    warn="#b8894a",
    bad="#b5544a",
    bad_hover="#9c463d",
    on_bad="#ffffff",
)

# The same warmth after dark: the sand tones become brown-greys, and matcha
# lifts a few steps so it still reads as the one saturated color.
DARK = Palette(
    name="dark",
    label="Dark",
    dark=True,
    text="#ece8e0",
    border="#3a3a37",
    background="#1c1c1a",
    panel="#252523",
    accent="#8faa81",
    accent_dark="#7c9070",
    on_accent="#1c1c1a",
    muted="#8a8681",
    muted_bg="#2e2e2b",
    good="#7aab78",
    warn="#d1a466",
    bad="#c96b60",
    bad_hover="#b5544a",
    on_bad="#1c1c1a",
)

# Light and Dark are defined in this module; the bundled Omarchy palettes are
# created lazily in available() so package initialization stays cycle-free.
BUILTINS = (LIGHT, DARK)
DEFAULT_THEME = LIGHT.name


def available() -> dict[str, Palette]:
    """The palettes offered on this machine.

    Light, Dark, and all bundled Omarchy palettes are always there. The live
    System skin only appears on an Omarchy desktop, and is re-read on every
    call so it reflects whatever theme is active right now.
    """
    # Imported here, not at module scope: omarchy.py imports Palette from this
    # module, so a top-level import would be a cycle.
    from . import omarchy

    from .bundled_omarchy import palettes as bundled_omarchy_palettes

    palettes = {p.name: p for p in (*BUILTINS, *bundled_omarchy_palettes())}
    if omarchy.is_omarchy():
        live = omarchy.load()
        if live is not None:
            palettes[live.name] = live
    return palettes
