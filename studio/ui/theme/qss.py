"""The stylesheet template. One QSS, rendered against whichever Palette."""

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
/* No ::title rule: the side dock has no title bar. BAR 1 already names the
   workspace, so the panel is its page list and nothing else. */
QDockWidget > QWidget {{ background: {p.panel}; border-right: {HAIRLINE} solid {p.border}; }}

QListWidget, QListWidget#SidePanel {{ background: {p.panel}; border: none; padding: 0; outline: none; }}

/* Each state has to be legible on its own, so they differ by more than a few
   percent of fill: hover tints, pressed goes darker still, and the selected
   row carries the accent rule — the same mark BAR 1 uses for the active tab,
   turned on its side. The transparent left border on the resting state is
   what keeps text from shifting when that rule appears. */
QListWidget::item {{
    padding: {ROW_V} {ROW_H};
    border-radius: 0;
    border-left: {px(3)} solid transparent;
    color: {p.text};
}}
QListWidget::item:hover {{ background: {p.muted_bg}; border-left-color: {p.border}; }}
QListWidget::item:pressed {{ background: {p.border}; }}
QListWidget::item:selected {{
    background: {p.border};
    border-left-color: {p.accent};
    color: {p.text};
    font-weight: 600;
}}
/* Selected *and* hovered is a distinct state: without this the hover rule
   above would repaint the accent border as a plain one. */
QListWidget::item:selected:hover {{ background: {p.muted_bg}; border-left-color: {p.accent}; }}
QListWidget::item:disabled {{ color: {p.muted}; border-left-color: transparent; background: transparent; }}

/* A list that is page content, not the side panel: it sits inside a card's
   worth of space, so it takes the card's border and its own row rhythm
   rather than the panel's flush-to-the-edge one. */
QListWidget#AccountList {{
    background: {p.panel};
    border: {HAIRLINE} solid {p.border};
    border-radius: {RADIUS};
}}
QListWidget#AccountList::item {{ padding: {ROW_V} {CARD_PAD}; }}

/* ---- Tables: page content, so they read as a card with rows ---- */
QTableWidget {{
    background: {p.panel};
    border: {HAIRLINE} solid {p.border};
    border-radius: {RADIUS};
    gridline-color: transparent;
    alternate-background-color: {p.muted_bg};
    outline: none;
}}
QHeaderView::section {{
    background: {p.panel};
    border: none;
    border-bottom: {HAIRLINE} solid {p.border};
    padding: {ROW_V} {ROW_H};
    color: {p.muted};
    font-size: {px(11)};
    font-weight: 600;
    text-align: left;
}}
/* Only horizontal padding here: Qt lays a cell out inside the width the
   header computed, so vertical padding on ::item pushes the text out of its
   own row. Row height is set on the widget instead. */
QTableWidget::item {{
    padding: 0 {ROW_H};
    border: none;
    color: {p.text};
}}
QTableWidget::item:hover {{ background: {p.muted_bg}; }}
QTableWidget::item:selected {{ background: {p.border}; color: {p.text}; }}

/* A validation message, next to the field it is about. */
QLabel#FieldError {{ color: {p.bad}; font-size: {px(11)}; }}

/* Row actions: small text buttons inside a table cell, not BAR 2 controls.
   Compact on purpose — the row already carries the account's name, so the
   button only needs to name the verb. */
/* Spacing is set in layout code (setContentsMargins), not here: QSS padding
   on a QLabel switches it onto Qt's styled-frame path, which then adds its
   own unpredictable margin on top of the number given. */
QLabel#RowAction, QLabel#RowActionBad {{
    border-radius: {RADIUS};
    color: {p.text};
    font-size: {px(11)};
}}
QLabel#RowAction:hover {{ background: {p.muted_bg}; }}
QLabel#RowActionBad {{ color: {p.bad}; }}
QLabel#RowActionBad:hover {{ background: {p.bad}; color: {p.on_bad}; }}

/* ---- Cards ---- */
QFrame#Card {{
    background: {p.panel};
    border: {HAIRLINE} solid {p.border};
    border-radius: {RADIUS};
}}
/* Labels must not paint their own background over cards. */
QLabel {{ background: transparent; }}
QLabel#Title {{ font-size: {px(18)}; font-weight: 600; color: {p.text}; }}
QLabel#Subtitle {{ font-size: {px(12)}; color: {p.muted}; }}
QLabel#CardTitle {{ font-size: {px(13)}; font-weight: 600; color: {p.text}; }}
QLabel#CardBody {{ font-size: {px(12)}; color: {p.muted}; }}
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
