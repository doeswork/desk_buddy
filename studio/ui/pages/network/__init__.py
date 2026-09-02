"""The Network page and its sections.

    network.py   the layout: identity, actions, side panel, section order
    broker.py    the Broker card

A page grows into a directory once it has more than one section. The layout
file is the only thing the rest of the app imports.
"""

from __future__ import annotations

from .network import NetworkPage

__all__ = ["NetworkPage"]
