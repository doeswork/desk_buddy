"""The stylesheet template. One QSS, rendered against whichever Palette.

Everything that is not built yet renders in the muted tones so it takes up its
real space without pretending to work.
"""

from __future__ import annotations

from .palette import DEFAULT_THEME, Palette, available

FONT_STACK = (
    '"Inter", "Segoe UI", "SF Pro Text", "Helvetica Neue", '
    '"Cantarell", "Noto Sans", "DejaVu Sans", sans-serif'
)


def stylesheet(scale: float = 1.0, theme: str | Palette = DEFAULT_THEME) -> str:
    """Build the QSS at a given zoom scale. Sizes below are design-size px."""
    p = theme if isinstance(theme, Palette) else available()[theme]

    def px(value: float) -> str:
        return f"{max(1, round(value * scale))}px"

    # ---- Metrics -------------------------------------------------------
    # A CAD toolbar is a continuous strip: controls butt against each other
    # and share a border, so the eye reads one instrument rather than a row
    # of separate pills. That means no gaps, no margins between controls,
    # and corners just soft enough to not look accidental.
    RADIUS = px(2)      # every corner in the app
    HAIRLINE = px(1)    # the single border weight

    # Chrome is dense; content breathes a little more.
    TIGHT_V, TIGHT_H = px(4), px(9)      # toolbar controls
    ROW_V, ROW_H = px(5), px(9)          # list rows, menu items
    CARD_PAD = px(13)                    # inside a card

    return f"""QMainWindow, QWidget {{
    background: {p.background};
    color: {p.text};
    font-family: {FONT_STACK};
    font-size: {px(12)};
}}

/* ---- Qt menu bar: thin, quiet ---- */
QMenuBar {{
    background: {p.background};
    border-bottom: {HAIRLINE} solid {p.border};
    padding: 0;
}}
QMenuBar::item {{ padding: {px(5)} {px(9)}; border-radius: 0; background: transparent; }}
QMenuBar::item:selected {{ background: {p.border}; }}
QMenu {{ background: {p.panel}; border: {HAIRLINE} solid {p.border}; padding: {px(2)}; }}
QMenu::item {{ padding: {ROW_V} {px(20)}; border-radius: 0; }}
QMenu::item:selected {{ background: {p.border}; }}
QMenu::item:disabled {{ color: {p.muted}; }}
QMenu::separator {{ height: {HAIRLINE}; background: {p.border}; margin: {px(3)} 0; }}

/* ---- BAR 1: page switcher. Never changes. ---- */
QToolBar#NavBar {{
    background: {p.panel};
    border: none;
    border-bottom: {HAIRLINE} solid {p.border};
    padding: 0;
    spacing: 0;
}}
QToolBar#NavBar QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 0;
    border-right: {HAIRLINE} solid {p.border};
    padding: {px(7)} {px(16)};
    margin: 0;
    color: {p.text};
    font-size: {px(12)};
}}
QToolBar#NavBar QToolButton:hover {{ background: {p.muted_bg}; }}
/* The active page reads as a selected tab: it keeps the panel background and
   is marked by an accent rule, the way a CAD workbench tab is. */
QToolBar#NavBar QToolButton:checked {{
    background: {p.background};
    border-bottom: {px(2)} solid {p.accent};
    color: {p.text};
    font-weight: 600;
}}

/* ---- BAR 2: context bar. Swaps per page. ---- */
QToolBar#ContextBar {{
    background: {p.panel};
    border: none;
    border-bottom: {HAIRLINE} solid {p.border};
    padding: {px(3)} {px(4)};
    spacing: 0;
}}
/* Context-bar buttons form ONE strip, not a row of separate controls.
   No borders of their own and no gaps: the bar underneath is the surface, and
   a button paints itself only when hovered, pressed, or primary. That is what
   keeps a dense toolbar from reading as a pile of stickers — an inert button
   is flat and grey, never its own dotted box. */
QPushButton#ContextAction, QPushButton#ContextPrimary {{
    border: none;
    border-radius: 0;
    padding: {TIGHT_V} {TIGHT_H};
    margin: 0;
}}
QPushButton#ContextAction {{
    background: transparent;
    color: {p.text};
}}
QPushButton#ContextAction:hover:!disabled {{ background: {p.muted_bg}; }}
QPushButton#ContextAction:pressed {{ background: {p.border}; }}
QPushButton#ContextAction:disabled {{ background: transparent; color: {p.muted}; }}

/* The one primary action is the only filled thing on the bar. */
QPushButton#ContextPrimary {{
    background: {p.accent};
    color: {p.on_accent};
    font-weight: 600;
}}
QPushButton#ContextPrimary:hover:!disabled {{ background: {p.accent_dark}; }}
QPushButton#ContextPrimary:pressed {{ background: {p.accent_dark}; }}
QPushButton#ContextPrimary:disabled {{
    background: transparent;
    color: {p.muted};
    font-weight: 600;
}}
QToolBar::separator {{ background: {p.border}; width: {HAIRLINE}; margin: {px(3)} {px(5)}; }}
/* Stretch spacers are plain QWidgets; keep them invisible. */
QToolBar QWidget#Spacer {{ background: transparent; border: none; }}

/* ---- Docks ---- */
QDockWidget {{ titlebar-close-icon: none; titlebar-normal-icon: none; }}
QDockWidget::title {{
    background: {p.muted_bg};
    padding: {px(4)} {px(9)};
    border-bottom: {HAIRLINE} solid {p.border};
    font-size: {px(11)};
    font-weight: 600;
    color: {p.muted};
}}
QDockWidget > QWidget {{ background: {p.panel}; border-right: {HAIRLINE} solid {p.border}; }}

QListWidget, QListWidget#SidePanel {{ background: {p.panel}; border: none; padding: 0; outline: none; }}
QListWidget::item {{ padding: {ROW_V} {ROW_H}; border-radius: 0; color: {p.text}; }}
QListWidget::item:hover {{ background: {p.muted_bg}; }}
QListWidget::item:selected {{ background: {p.border}; color: {p.text}; }}
QListWidget::item:disabled {{ color: {p.muted}; }}

/* ---- Cards ---- */
QFrame#Card {{
    background: {p.panel};
    border: {HAIRLINE} solid {p.border};
    border-radius: {RADIUS};
}}
QFrame#CardMuted {{
    background: {p.muted_bg};
    border: {HAIRLINE} dashed {p.border};
    border-radius: {RADIUS};
}}

/* Compact service rows reveal their heavier configuration only on demand. */
QFrame#DisclosureSummary {{
    background: {p.panel};
    border: {HAIRLINE} solid {p.border};
    border-radius: {RADIUS};
}}
QWidget#DisclosureDetails {{
    background: transparent;
    border: none;
    padding-top: {px(6)};
}}
QLabel#ServiceName {{ font-size: {px(13)}; font-weight: 600; color: {p.text}; }}
QLabel#ServiceFieldLabel {{ font-size: {px(10)}; color: {p.muted}; }}
QLabel#ServiceFieldValue {{ font-size: {px(12)}; color: {p.text}; }}
QPushButton#DisclosureToggle {{ min-width: {px(58)}; }}

/* Labels must not paint their own background over cards. */
QLabel {{ background: transparent; }}
QLabel#Title {{ font-size: {px(18)}; font-weight: 600; color: {p.text}; }}
QLabel#Subtitle {{ font-size: {px(12)}; color: {p.muted}; }}
QLabel#CardTitle {{ font-size: {px(13)}; font-weight: 600; color: {p.text}; }}
QLabel#CardBody {{ font-size: {px(12)}; color: {p.muted}; }}
QLabel#NotBuilt {{
    font-size: {px(10)};
    font-weight: 600;
    color: {p.muted};
    background: {p.muted_bg};
    border: {HAIRLINE} dashed {p.border};
    border-radius: {RADIUS};
    padding: {px(2)} {px(6)};
}}

/* ---- Status chips on BAR 1 ----
   The two always-true facts, quiet until they matter. */
QLabel#StatusChip {{
    color: {p.muted};
    font-size: {px(11)};
    padding: 0 {px(9)};
}}

/* ---- Command text ----
   A command the user is meant to copy and run. Monospace so it is obviously
   literal, and flush against its Copy button as one control. */
QLineEdit#CommandText {{
    background: {p.muted_bg};
    border: {HAIRLINE} solid {p.border};
    border-right: none;
    border-top-left-radius: {RADIUS};
    border-bottom-left-radius: {RADIUS};
    padding: {TIGHT_V} {px(8)};
    color: {p.text};
    font-family: "JetBrains Mono", "SF Mono", "Consolas", "DejaVu Sans Mono", monospace;
    font-size: {px(12)};
}}

/* ---- Status strip ---- */
QStatusBar {{
    background: {p.panel};
    border-top: {HAIRLINE} solid {p.border};
    color: {p.muted};
    padding: {px(2)} {px(8)};
}}
QStatusBar::item {{ border: none; }}

/* ---- E-stop. Always visible, always live-looking. ---- */
QPushButton#EStop {{
    background: {p.bad};
    color: {p.on_bad};
    border: none;
    border-radius: {RADIUS};
    padding: {px(4)} {px(16)};
    font-weight: 700;
    font-size: {px(12)};
}}
QPushButton#EStop:hover {{ background: {p.bad_hover}; }}

/* ---- Buttons ----
   The app has exactly three: ContextAction and ContextPrimary on BAR 2, and
   EStop. This rule is the fallback for any plain QPushButton Qt creates for
   us — add a named variant here only when a real one exists in the UI. */

QPushButton {{
    background: {p.panel};
    border: {HAIRLINE} solid {p.border};
    border-radius: {RADIUS};
    padding: {TIGHT_V} {px(13)};
    color: {p.text};
}}
QPushButton:hover:!disabled {{ border-color: {p.accent}; }}
QPushButton:disabled {{ background: {p.muted_bg}; border: {HAIRLINE} dashed {p.border}; color: {p.muted}; }}

QScrollArea {{ border: none; background: {p.background}; }}
QScrollBar:vertical {{ background: transparent; width: {px(9)}; margin: 0; }}
QScrollBar::handle:vertical {{ background: {p.border}; border-radius: 0; min-height: {px(24)}; }}
QScrollBar::handle:vertical:hover {{ background: {p.muted}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
"""
