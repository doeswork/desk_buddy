"""Layout metrics — the spacing half of the look.

The QSS owns anything Qt can style (padding, borders, radii). Everything Qt
cannot reach lives in layout code, so those numbers live here rather than
scattered as literals across pages and components.

The scale is deliberately tight. A CAD tool packs its chrome so the work has
the room; generous card padding and airy gutters read as a web page, not an
instrument. Chrome is dense, content gets a little more, and nothing floats.
"""

from __future__ import annotations

# Page body: the frame around the whole scrolling area.
PAGE_MARGIN_H = 18
PAGE_MARGIN_V = 14
PAGE_SPACING = 14      # header -> body

# Cards.
CARD_MARGIN_H = 12
CARD_MARGIN_V = 9
CARD_SPACING = 3       # title -> body
CARD_GAP = 6           # between stacked cards

# Header.
HEADER_GAP = 8         # title -> badge
HEADER_SPACING = 4     # title -> subtitle


def scaled(value: int, zoom: float) -> int:
    """A metric at the current zoom. Layouts are not styled by the QSS, so
    anything that has to grow with zoom passes through here."""
    return max(1, round(value * zoom))
