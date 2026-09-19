#!/usr/bin/env python3
"""Repair the supplied EasyEDA SMD v1.0 PCB, retaining its native objects.

Produces a separate JSON and validation report. Requires the packages in
easyeda-repair-requirements.txt. The original source is never overwritten.
"""

import copy
import hashlib
import json
from pathlib import Path

from audit_easyeda import Board, UNIT, compare_gerbers, require

HERE = Path(__file__).resolve().parent
SOURCE = HERE / 'PCB_DeskBuddy_SMD_v1.0_2026-09-19.json'
OUTPUT = HERE / 'PCB_DeskBuddy_SMD_v1.0_FIXED_2026-09-19.json'
REPORT = HERE / 'DeskBuddy_EasyEDA_Validation.json'
SOURCE_SHA256 = '78471450174e37da597a697384db525a9fe6efdecb9fda3a492f804d64988c4e'

# Gerber millimeters, converted back through the document origin. Unchanged
# vertices retain their exact original serialized coordinates.
CAM_POINT_EDITS = {
    'gge25269': {6: (21.5, 116.85034), 7: (21.5, 99.785)},
    'gge25185': {1: (25.16406, 143.02), 2: (52.306, 143.02)},
    'gge25158': {5: (48.6442, 120.179), 6: (47.695, 120.179),
                 7: (46.88, 119.36349), 8: (46.88, 104.885)},
    'gge24996': {2: (46.31537, 101.15), 3: (47.64287, 99.8225)},
    'gge25296': {2: (50.49748, 100.5), 3: (48.63548, 102.362),
                 4: (47.51322, 102.362), 5: (46.88, 102.99522),
                 6: (46.88, 124.91794)},
    'gge25146': {1: (50.3, 108.339)},
}

# Pull the widened capacitor approaches 0.15 mm toward the outside of each
# pad pair. They still overlap their original pads generously. C1's GND bend
# also moves right to clear the adjacent +SV trunk; no component moves.
POWER_POINT_EDITS = {
    'gge25239': {1: (54.864, 115.957998)},
    'gge25218': {1: (54.864, 115.957998)},
    'gge25059': {1: (54.864, 113.658002)},
    'gge25780': {0: (55.118, 100.336998)},
    'gge25248': {1: (55.118, 98.037002)},
    'gge25777': {0: (55.245, 104.387002)},
    'gge25227': {1: (55.245, 106.686998)},
    'gge25474': {2: (47.879, 161.550998)},
    'gge25471': {1: (47.5, 165.352), 2: (47.244, 166.617002)},
    'gge25443': {1: (45.886002, 168.916998), 2: (47.244, 168.916998)},
}


def number(value):
    return f'{value:.8f}'.rstrip('0').rstrip('.')


def native_point(board, mm):
    return [number(board.ox + mm[0] / UNIT), number(board.oy - mm[1] / UNIT)]


def repaired_document(original, widen=True):
    board = Board(original)
    data = copy.deepcopy(original)
    changed = []
    hits = set()
    for index, shape in enumerate(original['shape']):
        if shape.startswith('TRACK~'):
            fields = shape.split('~')
            id = fields[5]
            if id == 'gge17807':
                fields[4] = '4102.5 3204.5 4260.5 3204.5 4260.5 2960 4240.5 2960 4240.5 2889.5 4102.5 2889.5 4102.5 3204.5'
                hits.add(id)
            elif id in CAM_POINT_EDITS:
                coords = fields[4].split()
                points = [coords[i:i + 2] for i in range(0, len(coords), 2)]
                for point_index, mm in CAM_POINT_EDITS[id].items():
                    points[point_index] = native_point(board, mm)
                if id == 'gge25146':
                    points.insert(-1, native_point(board, (50.3, 143.66227)))
                fields[4] = ' '.join(v for point in points for v in point)
                hits.add(id)
            if widen and fields[2] in ('1', '2'):
                rule = board.rule(fields[3])
                if float(fields[1]) < rule['trackWidth']:
                    fields[1] = number(rule['trackWidth'])
                if id in POWER_POINT_EDITS:
                    coords = fields[4].split()
                    points = [coords[i:i + 2] for i in range(0, len(coords), 2)]
                    for point_index, mm in POWER_POINT_EDITS[id].items():
                        points[point_index] = native_point(board, mm)
                    if id == 'gge25471':
                        points.insert(-1, native_point(board, (47.5, 166.361002)))
                    fields[4] = ' '.join(v for point in points for v in point)
            revised = '~'.join(fields)
            if widen and id == 'gge25077':
                # Redundant 0.005842 mm GND stub at C6. The adjoining full
                # width GND route already overlaps the pad at this location.
                revised = None
        elif widen and shape.startswith('VIA~'):
            fields = shape.split('~')
            rule = board.rule(fields[4])
            fields[3] = number(max(float(fields[3]), rule['viaHoleDiameter']))
            fields[5] = number(max(float(fields[5]), rule['viaHoleD'] / 2))
            revised = '~'.join(fields)
        else:
            revised = shape
        if revised != shape:
            changed.append(dict(index=index, before=shape, after=revised))
            data['shape'][index] = revised
    data['shape'] = [shape for shape in data['shape'] if shape is not None]
    require(hits == set(CAM_POINT_EDITS) | {'gge17807'}, 'Repair target missing')
    # Cached board bounds follow the closed profile; component extents already
    # fit inside it. No footprint, pad, symbol/link or layer metadata is edited.
    data['BBox'] = dict(x=4102.5, y=2889.5, width=158, height=315)
    return data, changed


def main():
    raw = SOURCE.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == SOURCE_SHA256, 'Unrecognized source revision')
    source = json.loads(raw)
    before_board = Board(source)
    before = before_board.audit()
    source_cam = compare_gerbers(before_board, HERE / 'Gerber_DESKBUDDY-ROBOT-ARM-v1-SMD_DeskBuddy_SMD_v1.0_2026-09-19.zip')
    cam_only, _ = repaired_document(source, widen=False)
    cam_match = compare_gerbers(Board(cam_only), HERE / 'Gerber_DESKBUDDY_SMD_v1.0_CAM_REPAIRED_2026-09-19.zip')
    fixed, changes = repaired_document(source)
    after_board = Board(fixed)
    after = after_board.audit()
    report = dict(source=SOURCE.name, source_sha256=SOURCE_SHA256,
                  output=OUTPUT.name, original_cam_match=source_cam,
                  translated_cam_repairs_match=cam_match, changes=changes,
                  before=before, after=after)
    print(json.dumps({k: after[k] for k in ('shorts', 'disconnected_named_nets',
                                          'clearance_rule_findings', 'track_width_rule_findings',
                                          'via_size_rule_findings', 'layers')}, indent=2))
    require(before['pad_via_partition'] == after['pad_via_partition'], 'Connectivity changed')
    require(not after['shorts'] and not after['disconnected_named_nets'], 'Short or open found')
    require(not after['drilled_island_splits'] and not after['plated_holes_outside_pads']
            and after['minimum_plated_annular_ring_mm'] > 0, 'Drilling invalidates copper connectivity')
    require(not after['clearance_rule_findings'], 'Configured clearances still need correction')
    require(not after['track_width_rule_findings'] and not after['via_size_rule_findings'], 'Rule mismatch')
    require(after['outline_valid'] and not after['drills_outside_board'], 'Invalid outline or hole')
    require(all(layer['copper_outside_board_mm2'] == 0 and
                layer['minimum_island_spacing_mm'] > 0.20 and
                layer['copper_to_outline_mm'] > 0.40 for layer in after['layers'].values()),
            'Copper margin regression')
    encoded = (json.dumps(fixed, indent=2, ensure_ascii=False) + '\n').encode('utf-8')
    require(not OUTPUT.exists() or OUTPUT.read_bytes() == encoded,
            'Output already exists with different content; refusing to overwrite it')
    report['output_sha256'] = hashlib.sha256(encoded).hexdigest()
    report['validation_scope'] = ('Local copper geometry and PCB-assigned net checks; '
                                  'not native EasyEDA DRC, schematic ERC, or manufacturing approval.')
    OUTPUT.write_bytes(encoded)
    REPORT.write_text(json.dumps(report, indent=2) + '\n')
    print(OUTPUT)


if __name__ == '__main__':
    main()
