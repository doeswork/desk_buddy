#!/usr/bin/env python3
"""Generate a copper-only review SVG from the repaired native PCB geometry."""

import html
import json
from pathlib import Path

from shapely.geometry import Polygon

from audit_easyeda import Board
from repair_easyeda import SOURCE, OUTPUT


def path(geom):
    # Preview-only simplification; the audit uses the full-precision geometry.
    geom = geom.simplify(0.001, preserve_topology=True)
    polygons = [geom] if geom.geom_type == 'Polygon' else list(geom.geoms)
    out = []
    for polygon in polygons:
        for ring in [polygon.exterior, *polygon.interiors]:
            coords = list(ring.coords)
            out.append('M' + ' L'.join(f'{x:.4f},{-y:.4f}' for x, y in coords) + ' Z')
    return ' '.join(out)


def main():
    original = Board(json.loads(SOURCE.read_text()))
    board = Board(json.loads(OUTPUT.read_text()))
    before = {i.id: i for i in original.items}
    outline = Polygon(board.outlines[0])
    x0, y0, x1, y1 = outline.bounds
    width, height = x1 - x0, y1 - y0
    panel = width + 12
    output = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {panel * 2:.3f} {height + 20:.3f}">',
              '<rect width="100%" height="100%" fill="#111b25"/>',
              '<g font-family="sans-serif" fill="#ecf2f6">',
              '<text x="5" y="5" font-size="2.5">DeskBuddy — corrected native PCB</text>',
              '<text x="5" y="9" font-size="1.5">Copper only · both layers viewed from top · yellow = modified objects</text>',
              '</g>']
    for layer in (1, 2):
        offset = 6 + (layer - 1) * panel
        output.append(f'<text x="{offset}" y="13" font-family="sans-serif" font-size="2" fill="white">'
                      f'{"TOP" if layer == 1 else "BOTTOM"}</text>')
        output.append(f'<g transform="translate({offset - x0:.4f},{16 + y1:.4f})">')
        output.append(f'<path d="{path(outline)}" fill="#162d30" stroke="#dbe2e7" stroke-width="0.15"/>')
        for item in board.items:
            if layer not in item.layers:
                continue
            changed = not item.geom.equals(before[item.id].geom)
            color = '#ffe083' if changed else ('#e68178' if layer == 1 else '#78b4eb')
            title = html.escape(f'{item.id} {item.reference} {item.number} {item.net}')
            output.append(f'<path d="{path(item.geom)}" fill="{color}" fill-rule="evenodd"><title>{title}</title></path>')
        for _, _, _, _, hole in board.holes:
            output.append(f'<path d="{path(hole)}" fill="#111b25"/>')
        output.append('</g>')
    output.append('</svg>')
    destination = Path(__file__).with_name('DeskBuddy_EasyEDA_Copper_Review.svg')
    destination.write_text('\n'.join(output) + '\n')
    print(destination)


if __name__ == '__main__':
    main()
