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

STYLESHEET = f"""
QMainWindow, QWidget {{
    background: {CLOUD};
    color: {CHARCOAL};
    font-family: {FONT_STACK};
    font-size: 13px;
}}

/* ---- Qt menu bar: thin, quiet ---- */
QMenuBar {{
    background: {CLOUD};
    border-bottom: 1px solid {SAND};
    padding: 2px 6px;
}}
QMenuBar::item {{ padding: 5px 10px; border-radius: 5px; background: transparent; }}
QMenuBar::item:selected {{ background: {SAND}; }}
QMenu {{ background: {PANEL}; border: 1px solid {SAND}; padding: 5px; }}
QMenu::item {{ padding: 6px 22px; border-radius: 5px; }}
QMenu::item:selected {{ background: {SAND}; }}
QMenu::item:disabled {{ color: {MUTED}; }}
QMenu::separator {{ height: 1px; background: {SAND}; margin: 5px 8px; }}

/* ---- BAR 1: page switcher. Never changes. ---- */
QToolBar#NavBar {{
    background: {PANEL};
    border: none;
    border-bottom: 1px solid {SAND};
    padding: 7px 10px;
    spacing: 5px;
}}
QToolBar#NavBar QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 9px;
    padding: 9px 15px;
    margin: 0 1px;
    color: {CHARCOAL};
    font-size: 13px;
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
    padding: 6px 12px;
    spacing: 3px;
}}
QToolBar#ContextBar QToolButton {{
    background: {PANEL};
    border: 1px solid {SAND};
    border-radius: 7px;
    padding: 6px 13px;
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
QToolBar::separator {{ background: {SAND}; width: 1px; margin: 5px 7px; }}
/* Stretch spacers are plain QWidgets; keep them invisible. */
QToolBar QWidget#Spacer {{ background: transparent; border: none; }}

/* ---- Docks ---- */
QDockWidget {{ titlebar-close-icon: none; titlebar-normal-icon: none; }}
QDockWidget::title {{
    background: {MUTED_BG};
    padding: 8px 12px;
    border-bottom: 1px solid {SAND};
    font-size: 11px;
    font-weight: 600;
    color: {MUTED};
}}
QDockWidget > QWidget {{ background: {PANEL}; border-right: 1px solid {SAND}; }}

QListWidget {{ background: {PANEL}; border: none; padding: 7px; outline: none; }}
QListWidget::item {{ padding: 9px 11px; border-radius: 7px; color: {CHARCOAL}; }}
QListWidget::item:hover {{ background: {MUTED_BG}; }}
QListWidget::item:selected {{ background: {SAND}; color: {CHARCOAL}; }}
QListWidget::item:disabled {{ color: {MUTED}; }}

/* ---- Cards ---- */
QFrame#Card {{
    background: {PANEL};
    border: 1px solid {SAND};
    border-radius: 13px;
}}
QFrame#CardMuted {{
    background: {MUTED_BG};
    border: 1px dashed {SAND};
    border-radius: 13px;
}}

/* Labels must not paint their own background over cards. */
QLabel {{ background: transparent; }}
QLabel#Title {{ font-size: 27px; font-weight: 600; color: {CHARCOAL}; }}
QLabel#Subtitle {{ font-size: 14px; color: {MUTED}; }}
QLabel#CardTitle {{ font-size: 15px; font-weight: 600; color: {CHARCOAL}; }}
QLabel#CardBody {{ font-size: 13px; color: {MUTED}; }}
QLabel#NotBuilt {{
    font-size: 11px;
    font-weight: 600;
    color: {MUTED};
    background: {MUTED_BG};
    border: 1px dashed {SAND};
    border-radius: 5px;
    padding: 3px 9px;
}}

/* ---- Status strip ---- */
QStatusBar {{
    background: {PANEL};
    border-top: 1px solid {SAND};
    color: {MUTED};
    padding: 3px 8px;
}}
QStatusBar::item {{ border: none; }}

/* ---- E-stop. Always visible, always live-looking. ---- */
QPushButton#EStop {{
    background: {BAD};
    color: #ffffff;
    border: none;
    border-radius: 7px;
    padding: 7px 20px;
    font-weight: 700;
    font-size: 12px;
}}
QPushButton#EStop:hover {{ background: #9c463d; }}

QPushButton {{
    background: {PANEL};
    border: 1px solid {SAND};
    border-radius: 7px;
    padding: 7px 15px;
    color: {CHARCOAL};
}}
QPushButton:hover:!disabled {{ border-color: {MATCHA}; }}
QPushButton:disabled {{ background: {MUTED_BG}; border: 1px dashed {SAND}; color: {MUTED}; }}

QScrollArea {{ border: none; background: {CLOUD}; }}
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {SAND}; border-radius: 5px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {MUTED}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
"""
