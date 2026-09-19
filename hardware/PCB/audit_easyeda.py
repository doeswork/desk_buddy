#!/usr/bin/env python3
"""Local geometry/net audit for this two-layer EasyEDA Standard PCB.

This is not the EasyEDA DRC engine or schematic ERC. Unsupported copper
primitives fail closed. Lengths in the JSON are 10 mil (0.254 mm) units.
The supplied Gerbers/drills independently check the coordinate conventions.
Requires gerbonara==1.6.3 and shapely==2.1.2.
"""

import argparse
from collections import defaultdict
from dataclasses import dataclass
import json
import math
from pathlib import Path
import tempfile
import warnings
import zipfile

from gerbonara import ExcellonFile, GerberFile, graphic_primitives as gp
from shapely.affinity import rotate
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import nearest_points, unary_union

UNIT = 0.254
QUADS = 128
TOLERANCE = 0.0001  # Geometry/coordinate-rounding tolerance, mm; not a DRC waiver.
COPPER_FILES = {1: 'Gerber_TopLayer.GTL', 2: 'Gerber_BottomLayer.GBL'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


@dataclass
class Item:
    id: str
    kind: str
    net: str
    layers: tuple
    geom: object
    reference: str = ''
    number: str = ''
    width: float = 0
    hole: float = 0

    def describe(self):
        return dict(id=self.id, kind=self.kind, net=self.net,
                    reference=self.reference, pad=self.number)


class Board:
    def __init__(self, data):
        require(data['head']['docType'] == '3', 'Expected a PCB document')
        self.data = data
        self.ox, self.oy = float(data['head']['x']), float(data['head']['y'])
        self.items, self.holes, self.outlines, self.components = [], [], [], []
        for shape in data['shape']:
            parts = shape.split('#@$')
            reference = ''
            if parts[0].startswith('LIB~'):
                labels = {p.split('~')[1]: p.split('~')[10]
                          for p in parts[1:] if p.startswith('TEXT~')}
                reference = labels.get('P', '')
                self.components.append(reference)
                parts = parts[1:]
            for part in parts:
                self.parse(part.split('~'), reference)
        require(len({i.id for i in self.items}) == len(self.items), 'Duplicate copper object IDs')

    def xy(self, x, y):
        return ((float(x) - self.ox) * UNIT, (self.oy - float(y)) * UNIT)

    def points(self, text):
        a = text.replace(',', ' ').split()
        require(len(a) % 2 == 0, 'Odd coordinate count')
        return [self.xy(x, y) for x, y in zip(a[::2], a[1::2])]

    def parse(self, a, ref):
        kind = a[0]
        if kind == 'TRACK':
            layer = int(a[2])
            points = self.points(a[4])
            if layer == 10:
                self.outlines.append(points)
            elif layer in (1, 2):
                width = float(a[1]) * UNIT
                geom = LineString(points).buffer(width / 2, quad_segs=QUADS)
                self.items.append(Item(a[5], kind, a[3], (layer,), geom, ref, width=width))
        elif kind == 'PAD':
            layer = int(a[6])
            require(layer in (1, 2, 11), f'Unsupported pad layer {layer}')
            xy = self.xy(a[2], a[3])
            width, height = float(a[4]) * UNIT, float(a[5]) * UNIT
            if a[1] == 'RECT':
                # EasyEDA exports the size/rotation/center, rather than the
                # cached corner coordinates (which can differ by ~0.1 um).
                geom = rotate(box(xy[0] - width / 2, xy[1] - height / 2,
                                  xy[0] + width / 2, xy[1] + height / 2),
                              -float(a[11]), origin=xy)
            elif a[1] == 'ELLIPSE':
                require(abs(width - height) < 1e-8, 'Unsupported elliptical pad')
                geom = Point(xy).buffer(width / 2, quad_segs=QUADS)
            elif a[1] == 'OVAL':
                geom = LineString(self.points(a[10])).buffer(min(width, height) / 2, quad_segs=QUADS)
            else:
                raise ValueError(f'Unsupported pad shape {a[1]}')
            require(geom.is_valid and not geom.is_empty, f'Invalid pad {a[12]}')
            diameter = 2 * float(a[9]) * UNIT
            if diameter:
                plated = a[15] == 'Y'
                if float(a[13] or 0):
                    ends = self.points(a[14])
                    holegeom = LineString(ends).buffer(diameter / 2, quad_segs=QUADS)
                else:
                    ends = [self.xy(*a[19].split(','))] if len(a) > 19 and a[19] else [xy]
                    holegeom = Point(ends[0]).buffer(diameter / 2, quad_segs=QUADS)
                self.holes.append((a[12], plated, diameter, ends, holegeom))
            require(not diameter or a[15] == 'Y', 'Unexpected non-plated copper pad')
            self.items.append(Item(a[12], kind, a[7], (1, 2) if layer == 11 else (layer,),
                                   geom, ref, a[8], hole=diameter))
        elif kind == 'VIA':
            xy = self.xy(a[1], a[2])
            diameter, hole = float(a[3]) * UNIT, 2 * float(a[5]) * UNIT
            self.items.append(Item(a[6], kind, a[4], (1, 2),
                                   Point(xy).buffer(diameter / 2, quad_segs=QUADS), width=diameter, hole=hole))
            self.holes.append((a[6], True, hole, [xy], Point(xy).buffer(hole / 2, quad_segs=QUADS)))
        elif kind == 'HOLE':
            xy = self.xy(a[1], a[2])
            diameter = 2 * float(a[3]) * UNIT
            self.holes.append((a[4], False, diameter, [xy], Point(xy).buffer(diameter / 2, quad_segs=QUADS)))
        else:
            layer_field = {'SOLIDREGION': 1, 'COPPERAREA': 2, 'ARC': 2, 'CIRCLE': 5,
                           'RECT': 5, 'TEXT': 7}.get(kind)
            if layer_field is not None:
                require(a[layer_field] not in ('1', '2', '11'), f'Unsupported copper primitive {kind}')
            elif kind == 'SVGNODE':
                require(json.loads(a[1]).get('layerid') not in ('1', '2', '11'), 'Unsupported copper SVG')
            else:
                raise ValueError(f'Unsupported object {kind}')

    def rule(self, net):
        rules = self.data['DRCRULE']
        for value in rules.values():
            if isinstance(value, dict) and net and net in value.get('nets', []):
                return value
        return rules['Default']

    def copper(self, layer):
        return unary_union([i.geom for i in self.items if layer in i.layers])

    def audit(self):
        parent = list(range(len(self.items)))

        def root(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        shorts, clearances = [], []
        for i, a in enumerate(self.items):
            for j, b in enumerate(self.items[:i]):
                layers = sorted(set(a.layers) & set(b.layers))
                if not layers:
                    continue
                distance = a.geom.distance(b.geom)
                if distance == 0:
                    parent[root(i)] = root(j)
                same_net = a.net and a.net == b.net
                if not same_net:
                    required = max(self.rule(a.net)['clearance'], self.rule(b.net)['clearance']) * UNIT
                    detail = dict(a=a.describe(), b=b.describe(), layers=layers,
                                  clearance_mm=distance, configured_minimum_mm=required)
                    if distance == 0:
                        shorts.append(detail)
                    elif distance < required - TOLERANCE:
                        near = nearest_points(a.geom, b.geom)
                        detail['location_gerber_mm'] = [sum(p.x for p in near) / 2, sum(p.y for p in near) / 2]
                        clearances.append(detail)
        groups = defaultdict(list)
        for index, item in enumerate(self.items):
            groups[root(index)].append(item)
        partition = sorted(sorted(i.id for i in group if i.kind in ('PAD', 'VIA'))
                           for group in groups.values())
        by_net = defaultdict(set)
        for index, item in enumerate(self.items):
            if item.net:
                by_net[item.net].add(root(index))
        opens = {net: [[i.describe() for i in groups[r] if i.kind in ('PAD', 'VIA')]
                       for r in sorted(roots)] for net, roots in sorted(by_net.items()) if len(roots) > 1}
        widths, vias = [], []
        for item in self.items:
            rule = self.rule(item.net)
            if item.kind == 'TRACK' and item.width < rule['trackWidth'] * UNIT - TOLERANCE:
                widths.append(dict(**item.describe(), actual_width_mm=item.width,
                                   configured_width_mm=rule['trackWidth'] * UNIT))
            if item.kind == 'VIA' and (item.width < rule['viaHoleDiameter'] * UNIT - TOLERANCE
                                       or item.hole < rule['viaHoleD'] * UNIT - TOLERANCE):
                vias.append(dict(**item.describe(), actual_diameter_mm=item.width, actual_drill_mm=item.hole,
                                 configured_diameter_mm=rule['viaHoleDiameter'] * UNIT,
                                 configured_drill_mm=rule['viaHoleD'] * UNIT))
        outline_valid = (len(self.outlines) == 1 and self.outlines[0][0] == self.outlines[0][-1]
                         and LineString(self.outlines[0]).is_simple and Polygon(self.outlines[0]).is_valid)
        layers = {}
        holes = unary_union([h[4] for h in self.holes])
        drilled_splits = []
        for layer in (1, 2):
            copper = self.copper(layer)
            islands = list(copper.geoms) if copper.geom_type == 'MultiPolygon' else [copper]
            # The connectivity graph uses filled Gerber pad envelopes. Verify
            # that drilling cannot split or erase any connected copper island.
            for index, island in enumerate(islands):
                drilled = island.difference(holes)
                if drilled.is_empty or drilled.geom_type != 'Polygon':
                    drilled_splits.append(dict(layer=layer, island=index, result=drilled.geom_type))
            spacing = min(a.distance(b) for i, a in enumerate(islands) for b in islands[:i])
            layers[str(layer)] = dict(copper_islands=len(islands), minimum_island_spacing_mm=spacing)
            if outline_valid:
                border = Polygon(self.outlines[0])
                layers[str(layer)].update(copper_to_outline_mm=copper.distance(border.boundary),
                                          copper_outside_board_mm2=copper.difference(border).area)
        clearances.sort(key=lambda x: x['clearance_mm'])
        by_id = {i.id: i for i in self.items}
        annuli = [dict(id=id, minimum_ring_mm=by_id[id].geom.boundary.distance(hole),
                       hole_outside_pad_mm2=hole.difference(by_id[id].geom).area)
                  for id, plated, _, _, hole in self.holes if plated]
        return dict(components=len(self.components), pads=sum(i.kind == 'PAD' for i in self.items),
                    vias=sum(i.kind == 'VIA' for i in self.items), named_nets=len(by_net),
                    unassigned_pads=[i.describe() for i in self.items if i.kind == 'PAD' and not i.net],
                    physical_connectivity_groups=len(groups), pad_via_partition=partition,
                    shorts=shorts, disconnected_named_nets=opens, layers=layers,
                    outline_valid=outline_valid, clearance_rule_findings=clearances,
                    track_width_rule_findings=widths, via_size_rule_findings=vias,
                    drilled_features=len(self.holes),
                    drilled_island_splits=drilled_splits,
                    minimum_plated_annular_ring_mm=min(a['minimum_ring_mm'] for a in annuli),
                    plated_holes_outside_pads=[a for a in annuli if a['hole_outside_pad_mm2'] > 1e-8],
                    drills_outside_board=[] if not outline_valid else
                    [id for id, _, _, _, geom in self.holes if not border.covers(geom)])


def gerber_shape(obj):
    shapes = []
    for p in obj.to_primitives():
        require(p.polarity_dark, 'Unexpected clear Gerber primitive')
        if isinstance(p, gp.Circle):
            shapes.append(Point(p.x, p.y).buffer(p.r, quad_segs=QUADS))
        elif isinstance(p, gp.Line):
            shapes.append(LineString([(p.x1, p.y1), (p.x2, p.y2)]).buffer(p.width / 2, quad_segs=QUADS))
        else:
            if isinstance(p, gp.Rectangle):
                p = p.to_arc_poly()
            require(isinstance(p, gp.ArcPoly) and not any(p.arc_centers), 'Unsupported Gerber geometry')
            shapes.append(Polygon(p.outline))
    return unary_union(shapes)


def compare_gerbers(board, archive, compare_drills=True):
    result = {}
    with zipfile.ZipFile(archive) as z, tempfile.TemporaryDirectory(prefix='deskbuddy-cam-audit-') as tmp:
        for layer, name in COPPER_FILES.items():
            # Only explicit known filenames are read; no arbitrary ZIP extraction.
            g = GerberFile.from_string(z.read(name).decode('ascii'))
            cam = unary_union([gerber_shape(o) for o in g.objects])
            native = board.copper(layer)
            missing = cam.difference(native.buffer(TOLERANCE)).area
            extra = native.difference(cam.buffer(TOLERANCE)).area
            result[str(layer)] = dict(missing_outside_tolerance_mm2=missing,
                                     extra_outside_tolerance_mm2=extra, tolerance_mm=TOLERANCE)
            require(missing < 1e-8 and extra < 1e-8, f'JSON/Gerber copper mismatch {name}: {result[str(layer)]}')
        if compare_drills:
            drill_results = []
            for plated, name in [(True, 'Drill_PTH_Through.DRL'), (False, 'Drill_NPTH_Through.DRL')]:
                path = Path(tmp) / name
                path.write_bytes(z.read(name))
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore', SyntaxWarning)
                    objects = ExcellonFile.open(path).objects
                candidates = [(id, diameter, ends) for id, is_plated, diameter, ends, _ in board.holes
                              if plated == is_plated]
                require(len(objects) == len(candidates), f'Drill count mismatch {name}')
                worst = 0
                for obj in objects:
                    ends = [(obj.x1, obj.y1), (obj.x2, obj.y2)] if hasattr(obj, 'x1') else [(obj.x, obj.y)]
                    def error(c):
                        if len(c[2]) != len(ends):
                            return math.inf
                        return min(max(math.dist(a, b) for a, b in zip(ends, pts))
                                   for pts in (c[2], list(reversed(c[2]))))
                    candidate = min(candidates, key=error)
                    displacement = error(candidate)
                    require(displacement < 0.001, f'Drill position mismatch {candidate[0]}')
                    require(abs(candidate[1] - obj.aperture.diameter) < 0.001,
                            f'Drill diameter mismatch {candidate[0]}')
                    candidates.remove(candidate)
                    worst = max(worst, displacement)
                drill_results.append(dict(file=name, count=len(objects), max_position_difference_mm=worst))
            result['drills'] = drill_results
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pcb', type=Path)
    parser.add_argument('--gerbers', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    board = Board(json.loads(args.pcb.read_text()))
    report = board.audit()
    if args.gerbers:
        report['gerber_comparison'] = compare_gerbers(board, args.gerbers)
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + '\n')
    summary = {k: v for k, v in report.items() if k not in ('pad_via_partition', 'unassigned_pads',
                                                          'clearance_rule_findings')}
    summary['unassigned_pads_count'] = len(report['unassigned_pads'])
    summary['clearance_rule_findings_count'] = len(report['clearance_rule_findings'])
    summary['closest_clearance_findings'] = report['clearance_rule_findings'][:8]
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
