"""Palette and stylesheet. Autonomous Lamp's skin over FreeCAD's bones.

Warm minimalism: charcoal, sand, cloud white, matcha green accent. Everything
that is not built yet renders in MUTED so it takes up its real space without
pretending to work.
"""

from __future__ import annotations

# Palette — see studio_menu_plan in PLAN.md.
CHARCOAL = "#2c2c2a"   # primary text
SAND = "#e8e2d6"       # warm mid tone, borders
CLOUD = "#faf8f4"      # page background
PANEL = "#ffffff"      # card / panel background
MATCHA = "#7c9070"     # accent, the one saturated color
MATCHA_DARK = "#657a5a"
MUTED = "#a8a29a"      # disabled / not-built-yet text
MUTED_BG = "#f2efe9"
GOOD = "#5b8c5a"
WARN = "#b8894a"
BAD = "#b5544a"

FONT_STACK = (
    '"Inter", "Segoe UI", "SF Pro Text", "Helvetica Neue", '
    '"Cantarell", "Noto Sans", "DejaVu Sans", sans-serif'
)

def stylesheet(scale: float = 1.0) -> str:
    """Build the QSS at a given zoom scale. Sizes below are design-size px."""

    def px(value: float) -> str:
        return f"{max(1, round(value * scale))}px"

    return f"""
QMainWindow, QWidget {{
    background: {CLOUD};
    color: {CHARCOAL};
    font-family: {FONT_STACK};
    font-size: {px(13)};
}}

/* ---- Qt menu bar: thin, quiet ---- */
QMenuBar {{
    background: {CLOUD};
    border-bottom: 1px solid {SAND};
    padding: {px(2)} {px(6)};
}}
QMenuBar::item {{ padding: {px(5)} {px(10)}; border-radius: {px(5)}; background: transparent; }}
QMenuBar::item:selected {{ background: {SAND}; }}
QMenu {{ background: {PANEL}; border: 1px solid {SAND}; padding: {px(5)}; }}
QMenu::item {{ padding: {px(6)} {px(22)}; border-radius: {px(5)}; }}
QMenu::item:selected {{ background: {SAND}; }}
QMenu::item:disabled {{ color: {MUTED}; }}
QMenu::separator {{ height: {px(1)}; background: {SAND}; margin: {px(5)} {px(8)}; }}

/* ---- BAR 1: page switcher. Never changes. ---- */
QToolBar#NavBar {{
    background: {PANEL};
    border: none;
    border-bottom: 1px solid {SAND};
    padding: {px(7)} {px(10)};
    spacing: 5px;
}}
QToolBar#NavBar QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {px(9)};
    padding: {px(9)} {px(15)};
    margin: 0 1px;
    color: {CHARCOAL};
    font-size: {px(13)};
}}
QToolBar#NavBar QToolButton:hover {{ background: {MUTED_BG}; }}
QToolBar#NavBar QToolButton:checked {{
    background: {MATCHA};
    color: #ffffff;
    font-weight: 600;
}}

/* ---- BAR 2: context bar. Swaps per page. ---- */
QToolBar#ContextBar {{
    background: {CLOUD};
    border: none;
    border-bottom: 1px solid {SAND};
    padding: {px(6)} {px(12)};
    spacing: 3px;
}}
QToolBar#ContextBar QToolButton {{
    background: {PANEL};
    border: 1px solid {SAND};
    border-radius: {px(7)};
    padding: {px(6)} {px(13)};
    margin-right: 3px;
    color: {CHARCOAL};
}}
QToolBar#ContextBar QToolButton:hover:!disabled {{ border-color: {MATCHA}; }}
/* Not built yet: visible, sized, obviously inert. */
QToolBar#ContextBar QToolButton:disabled {{
    background: {MUTED_BG};
    border: 1px dashed {SAND};
    color: {MUTED};
}}
QToolBar::separator {{ background: {SAND}; width: {px(1)}; margin: {px(5)} {px(7)}; }}
/* Stretch spacers are plain QWidgets; keep them invisible. */
QToolBar QWidget#Spacer {{ background: transparent; border: none; }}

/* ---- Docks ---- */
QDockWidget {{ titlebar-close-icon: none; titlebar-normal-icon: none; }}
QDockWidget::title {{
    background: {MUTED_BG};
    padding: {px(8)} {px(12)};
    border-bottom: 1px solid {SAND};
    font-size: {px(11)};
    font-weight: 600;
    color: {MUTED};
}}
QDockWidget > QWidget {{ background: {PANEL}; border-right: 1px solid {SAND}; }}

QListWidget {{ background: {PANEL}; border: none; padding: {px(7)}; outline: none; }}
QListWidget::item {{ padding: {px(9)} {px(11)}; border-radius: {px(7)}; color: {CHARCOAL}; }}
QListWidget::item:hover {{ background: {MUTED_BG}; }}
QListWidget::item:selected {{ background: {SAND}; color: {CHARCOAL}; }}
QListWidget::item:disabled {{ color: {MUTED}; }}

/* ---- Cards ---- */
QFrame#Card {{
    background: {PANEL};
    border: 1px solid {SAND};
    border-radius: {px(13)};
}}
QFrame#CardMuted {{
    background: {MUTED_BG};
    border: 1px dashed {SAND};
    border-radius: {px(13)};
}}

/* Labels must not paint their own background over cards. */
QLabel {{ background: transparent; }}
QLabel#Title {{ font-size: {px(27)}; font-weight: 600; color: {CHARCOAL}; }}
QLabel#Subtitle {{ font-size: {px(14)}; color: {MUTED}; }}
QLabel#CardTitle {{ font-size: {px(15)}; font-weight: 600; color: {CHARCOAL}; }}
QLabel#CardBody {{ font-size: {px(13)}; color: {MUTED}; }}
QLabel#NotBuilt {{
    font-size: {px(11)};
    font-weight: 600;
    color: {MUTED};
    background: {MUTED_BG};
    border: 1px dashed {SAND};
    border-radius: {px(5)};
    padding: {px(3)} {px(9)};
}}

/* ---- Status strip ---- */
QStatusBar {{
    background: {PANEL};
    border-top: 1px solid {SAND};
    color: {MUTED};
    padding: {px(3)} {px(8)};
}}
QStatusBar::item {{ border: none; }}

/* ---- E-stop. Always visible, always live-looking. ---- */
QPushButton#EStop {{
    background: {BAD};
    color: #ffffff;
    border: none;
    border-radius: {px(7)};
    padding: {px(7)} {px(20)};
    font-weight: 700;
    font-size: {px(12)};
}}
QPushButton#EStop:hover {{ background: #9c463d; }}

QPushButton {{
    background: {PANEL};
    border: 1px solid {SAND};
    border-radius: {px(7)};
    padding: {px(7)} {px(15)};
    color: {CHARCOAL};
}}
QPushButton:hover:!disabled {{ border-color: {MATCHA}; }}
QPushButton:disabled {{ background: {MUTED_BG}; border: 1px dashed {SAND}; color: {MUTED}; }}

QScrollArea {{ border: none; background: {CLOUD}; }}
QScrollBar:vertical {{ background: transparent; width: {px(11)}; margin: 0; }}
QScrollBar::handle:vertical {{ background: {SAND}; border-radius: {px(5)}; min-height: {px(28)}; }}
QScrollBar::handle:vertical:hover {{ background: {MUTED}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
"""


# Design-size stylesheet, for callers that never zoom.
STYLESHEET = stylesheet()
