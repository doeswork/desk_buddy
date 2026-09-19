#!/usr/bin/env python3
"""Reconstruct one editable EasyEDA Standard sheet from the fixed PCB.

The input PCB is read-only. Embedded symbols come from the cached official
public library, with original PCB footprint UUIDs and component IDs retained.
The generated sheet is re-parsed to verify every logical pin/net assignment.
"""

import argparse
import copy
import hashlib
import html
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
PCB = HERE / 'PCB_DeskBuddy_SMD_v1.0_FIXED_2026-09-19.json'
CACHE = HERE / 'DeskBuddy_Schematic_Symbol_Sources.json'
OUT = HERE / 'SCH_DeskBuddy_SMD_v1.0_RECONSTRUCTED_2026-09-19.json'
SHA256 = '1ccdc89fb0edde693c4eb8525345681ae8917bf12867a2816e3d31251f47badc'
WIDTH, HEIGHT = 2000, 1440


def require(condition, message):
    if not condition:
        raise ValueError(message)


def num(n):
    return f'{float(n):.5f}'.rstrip('0').rstrip('.') or '0'


def join(values):
    return '~'.join(str(v) for v in values)


def parameters(value):
    tokens = value.split('`')
    if len(tokens) % 2 and tokens[-1] == '':
        tokens.pop()
    require(len(tokens) % 2 == 0, 'Malformed component parameters')
    return dict(zip(tokens[::2], tokens[1::2]))


def pcb_components(data):
    result = {}
    for shape in data['shape']:
        if not shape.startswith('LIB~'):
            continue
        pieces = shape.split('#@$')
        header = pieces[0].split('~')
        texts = {p.split('~')[1]: p.split('~')[10] for p in pieces[1:] if p.startswith('TEXT~')}
        pins, physical = {}, []
        for piece in pieces[1:]:
            f = piece.split('~')
            if f[0] != 'PAD':
                continue
            require(f[8] not in pins or pins[f[8]] == f[7], 'Duplicate pad number on different nets')
            pins[f[8]] = f[7]
            physical.append(dict(number=f[8], net=f[7], id=f[12]))
        ref = texts['P']
        require(ref not in result, 'Duplicate PCB reference')
        result[ref] = dict(ref=ref, value=texts['N'], id=header[6], footprint_uuid=header[8],
                           params=parameters(header[3]), pins=pins, physical_pads=physical)
    return result


class Transform:
    """Uniform 2x scale, orthogonal rotation, then placement; all paths absolute."""
    def __init__(self, ox, oy, x, y, angle=0):
        self.ox, self.oy, self.x, self.y = map(float, (ox, oy, x, y))
        self.angle = angle

    def point(self, x, y):
        x, y = (float(x) - self.ox) * 2, (float(y) - self.oy) * 2
        x, y = {0: (x, y), 90: (-y, x), 180: (-x, -y), 270: (y, -x)}[self.angle]
        return self.x + x, self.y + y

    def fields(self, a, i, j):
        a[i], a[j] = map(num, self.point(a[i], a[j]))

    def points(self, text):
        v = text.replace(',', ' ').split()
        require(len(v) % 2 == 0, 'Invalid coordinate list')
        return ' '.join(num(n) for x, y in zip(v[::2], v[1::2]) for n in self.point(x, y))

    def path(self, path):
        tokens = re.findall(r'[A-Za-z]|[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?', path)
        out, current, start = [], (0., 0.), (0., 0.)
        i, command = 0, None
        counts = {'M': 2, 'L': 2, 'H': 1, 'V': 1, 'A': 7, 'Z': 0}
        while i < len(tokens):
            if tokens[i].isalpha():
                command = tokens[i]
                i += 1
            require(command is not None and command.upper() in counts, f'Unsupported path {path}')
            upper = command.upper()
            if upper == 'Z':
                out.append('Z')
                current = start
                command = None
                continue
            n = counts[upper]
            values = list(map(float, tokens[i:i + n]))
            require(len(values) == n, 'Truncated SVG path')
            i += n
            relative = command.islower()
            if upper in ('M', 'L', 'A'):
                x, y = values[-2:]
                if relative:
                    x, y = current[0] + x, current[1] + y
            elif upper == 'H':
                x, y = values[0] + (current[0] if relative else 0), current[1]
            else:
                x, y = current[0], values[0] + (current[1] if relative else 0)
            current = (x, y)
            transformed = ' '.join(map(num, self.point(x, y)))
            if upper == 'A':
                arc = [values[0] * 2, values[1] * 2, (values[2] + self.angle) % 360,
                       values[3], values[4]]
                out.append('A ' + ' '.join(map(num, arc)) + ' ' + transformed)
            else:
                out.append(('M ' if upper == 'M' else 'L ') + transformed)
            if upper == 'M':
                start = current
                command = 'l' if relative else 'L'
        return ' '.join(out)


class Sheet:
    def __init__(self, components, symbols):
        self.components, self.symbols = components, symbols
        self.shapes, self.pins, self.placed, self.footprint_overrides = [], {}, set(), []
        self.next_id = 0x7100000000000000

    def id(self):
        self.next_id += 1
        return f'gge{self.next_id:016x}'

    def text(self, x, y, text, size=10, color='#334155', anchor='start', mark='L', bold=False, visible=1):
        require(not any(s in text for s in ('~', '#@$', '^^')), 'Invalid text delimiter')
        return join(['T', mark, num(x), num(y), 0, color, 'Arial', f'{size}pt',
                     'bold' if bold else '', '', '', 'comment', text, visible, anchor, self.id(), 0, ''])

    def note(self, x, y, text, **kwargs):
        self.shapes.append(self.text(x, y, text, **kwargs))

    def panel(self, x, y, w, h, title, subtitle):
        self.shapes.append(join(['R', x, y, 0, 0, w, h, '#CBD5E1', 1, 0, 'none', self.id(), 0]))
        self.note(x + 20, y + 30, title, size=15, color='#0F172A', bold=True)
        self.note(x + 20, y + 53, subtitle, size=9, color='#64748B')

    def transform_shape(self, source, transform):
        if source.startswith('P~'):
            groups = [g.split('~') for g in source.split('^^')]
            a = groups[0]
            transform.fields(a, 4, 5)
            a[6], a[7] = str((int(a[6] or 0) - transform.angle) % 360), self.id()
            transform.fields(groups[1], 0, 1)
            groups[2][0] = transform.path(groups[2][0])
            for t in groups[3:5]:
                transform.fields(t, 1, 2)
                t[3] = '0'
                t[7] = '7pt'
            # Keep the library pin names (including A/K) as hidden metadata.
            # Show actual footprint pin numbers in an upright, consistent style.
            groups[3][0], groups[4][0] = '0', '1'
            x, y = map(float, groups[1][:2])
            angle = int(a[6])
            nx, ny = {0: (x - 12, y - 7), 180: (x + 12, y - 7),
                      90: (x + 10, y + 15), 270: (x + 10, y - 7)}[angle]
            groups[4][1:4] = [num(nx), num(ny), '0']
            groups[4][5] = 'middle' if angle in (0, 180) else 'start'
            transform.fields(groups[5], 1, 2)
            groups[6][1] = transform.path(groups[6][1])
            return '^^'.join(join(g) for g in groups)
        a = source.split('~')
        kind = a[0]
        if kind == 'PL':
            a[1], a[3], a[6] = transform.points(a[1]), '1.5', self.id()
        elif kind == 'PT':
            a[1], a[3], a[6] = transform.path(a[1]), '1.5', self.id()
        elif kind == 'A':
            a[1] = transform.path(a[1])
            require(not a[2], 'Unexpected arc helper coordinates')
            a[4], a[7] = '1.5', self.id()
        elif kind == 'R':
            x, y, w, h = map(float, (a[1], a[2], a[5], a[6]))
            corners = [transform.point(x, y), transform.point(x + w, y + h)]
            a[1], a[2] = num(min(p[0] for p in corners)), num(min(p[1] for p in corners))
            a[5], a[6] = num(abs(corners[1][0] - corners[0][0])), num(abs(corners[1][1] - corners[0][1]))
            a[3], a[4] = num(2 * float(a[3] or 0)), num(2 * float(a[4] or 0))
            a[8], a[11] = '1.5', self.id()
        elif kind == 'E':
            transform.fields(a, 1, 2)
            a[3], a[4] = num(float(a[3]) * 2), num(float(a[4]) * 2)
            if transform.angle % 180:
                a[3], a[4] = a[4], a[3]
            a[6], a[9] = '1.5', self.id()
        else:
            raise ValueError(f'Unsupported library primitive {kind}')
        return join(a)

    def place(self, ref, x, y, angle=0, caption=None, text_xy=None):
        require(ref not in self.placed, f'Duplicate placement: {ref}')
        self.placed.add(ref)
        c = self.components[ref]
        symbol = self.symbols[c['params']['Supplier Part']]
        data = symbol['data']
        names = {s.split('^^')[0].split('~')[3] for s in data['shape'] if s.startswith('P~')}
        require(names == set(c['pins']), f'Library/PCB pin mismatch: {ref}')
        transform = Transform(data['head']['x'], data['head']['y'], x, y, angle)
        shapes = [self.transform_shape(s, transform) for s in data['shape']]
        pin_points = {}
        for shape in shapes:
            if shape.startswith('P~'):
                groups = shape.split('^^')
                number = groups[0].split('~')[3]
                pin_points[number] = tuple(map(float, groups[1].split('~')))
                self.pins[(ref, number)] = pin_points[number]
        if symbol['footprint_uuid'] != c['footprint_uuid']:
            self.footprint_overrides.append(dict(ref=ref, library_footprint=symbol['footprint_uuid'],
                                                 preserved_pcb_footprint=c['footprint_uuid']))
        params = copy.deepcopy(c['params'])
        params['Value'] = c['value']
        params['Reconstruction'] = 'From fixed PCB; original instance and footprint IDs retained'
        attrs = '`'.join(v for pair in params.items() for v in pair)
        head = join(['LIB', x, y, attrs, (-angle) % 360, 0, c['id'], c['footprint_uuid'],
                     symbol['uuid'], 0, '', 'yes', 'yes'])
        if text_xy:
            tx, ty = text_xy
            anchor = 'start'
        else:
            tx, ty = x, min(p[1] for p in pin_points.values()) - 42
            anchor = 'middle'
        shapes.insert(0, self.text(tx, ty, ref, size=12, mark='P', bold=True, anchor=anchor, color='#0F172A'))
        shapes.insert(1, self.text(tx, ty + 20, c['value'], size=9, mark='N', anchor=anchor,
                                   visible=0 if caption else 1))
        if caption:
            shapes.insert(2, self.text(tx, ty + 20, caption, size=9, anchor=anchor, color='#64748B'))
        self.shapes.append('#@$'.join([head, *shapes]))
        return pin_points

    def wire(self, *points):
        require(len(points) >= 2, 'Wire needs endpoints')
        for a, b in zip(points, points[1:]):
            require(a != b and (a[0] == b[0] or a[1] == b[1]), 'Wire is zero length or off-axis')
        self.shapes.append(join(['W', ' '.join(num(v) for p in points for v in p),
                                 '#008800', 1.5, 0, 'none', self.id(), 0]))

    def label(self, x, y, name, anchor='start'):
        self.shapes.append(join(['N', num(x), num(y), 0, '#006B38', name, self.id(), anchor,
                                 num(x + (3 if anchor == 'start' else -3)), num(y - 4), 'Arial', '9pt', 0]))

    def junction(self, x, y):
        self.shapes.append(join(['J', x, y, 2.5, '#008800', self.id(), 0]))

    def header(self, ref, x, y, caption):
        pins = self.place(ref, x, y, caption=caption)
        for number, (px, py) in pins.items():
            net = self.components[ref]['pins'][number]
            if net:
                self.wire((px - 50, py), (px, py))
                self.label(px - 50, py, net, anchor='end')
            else:
                self.note(px - 12, py + 4, 'open', size=8, anchor='end', color='#94A3B8')

    def document(self):
        require(self.placed == set(self.components), f'Missing components: {set(self.components) - self.placed}')
        return dict(head=dict(docType='1', editorVersion='6.5.51', newgId=True, hasIdFlag=True,
                              x='0', y='0', c_para={'Title': 'DeskBuddy SMD v1.0 - PCB reconstruction',
                                                    'Prefix Start': '1', 'Source PCB SHA256': SHA256},
                              c_spiceCmd=None),
                    canvas=f'CA~{WIDTH}~{HEIGHT}~#FFFFFF~yes~#CCCCCC~10~{WIDTH}~{HEIGHT}~line~5~pixel~5~0~0',
                    shape=self.shapes, BBox=dict(x=20, y=20, width=1960, height=1400), colors={})


def build(components, symbols):
    s = Sheet(components, symbols)
    s.shapes.append(join(['R', 20, 20, 0, 0, 1960, 1400, '#334155', 1.5, 0, 'none', s.id(), 0]))
    s.note(45, 61, 'DESKBUDDY / SMD v1.0', size=26, color='#0F172A', bold=True)
    s.note(45, 89, 'Editable schematic reconstructed from the repaired PCB • 29 components • 19 named nets', size=12)
    s.note(1955, 61, 'PCB-DERIVED / REV A', size=13, anchor='end', bold=True)
    s.note(1955, 86, '2026-09-19   |   Sheet 1 of 1', size=10, anchor='end')
    s.panel(40, 120, 1150, 470, '01 / POWER INPUT, FUSE HOLDER & FILTERING',
            'Net names are copied literally from the PCB; this board does not contain a voltage regulator.')
    s.header('PWR', 190, 285, '2-pin / 5.00 mm')
    s.place('FH1', 480, 230, angle=180, caption='XF-506P fuse holder')
    s.wire((320, 230), (400, 230))
    s.label(320, 230, '+V', anchor='end')
    s.wire((560, 230), (1100, 230))
    s.label(1100, 230, '+SV', anchor='end')
    for ref, x in [('C7', 800), ('C8', 1050)]:
        pins = s.place(ref, x, 290, angle=90, text_xy=(x + 35, 287))
        s.wire((x, 230), pins['1'])
        s.junction(x, 230)
        s.wire(pins['2'], (x, 350))
    s.wire((800, 350), (1100, 350))
    s.junction(1050, 350)
    s.label(1100, 350, 'GND', anchor='end')
    s.place('DIODE', 480, 410, angle=180, caption='SS34 • A=2 / K=1')
    s.wire((320, 230), (320, 410), (440, 410))
    s.junction(320, 230)
    s.wire((520, 410), (780, 410))
    s.label(650, 410, 'ESP32_+V')
    s.place('FERRITE', 820, 410, caption='BLM21PG221SN1D / 220Ω')
    s.wire((860, 410), (1130, 410))
    s.label(1130, 410, '+5V', anchor='end')
    for ref, x, angle in [('C9', 650, 270), ('1UF', 930, 90), ('C6', 1080, 90)]:
        pins = s.place(ref, x, 475, angle=angle, text_xy=(x + 32, 470))
        top, bottom = sorted(pins.values(), key=lambda p: p[1])
        s.wire((x, 410), top)
        s.junction(x, 410)
        s.wire(bottom, (x, 535))
    s.wire((650, 535), (1130, 535))
    for x in (930, 1080):
        s.junction(x, 535)
    s.label(1130, 535, 'GND', anchor='end')
    s.note(60, 565, 'FH1: fuse rating is not specified by the PCB.  “3V” elsewhere is also a literal net name.', size=9)

    s.panel(1220, 120, 740, 650, '02 / CONTROLLER SOCKETS',
            'U3 and U4 are separate 1×20 headers, not an assumed MCU pinout.')
    s.header('U3', 1460, 470, '1×20 female / 2.54 mm')
    s.header('U4', 1810, 470, '1×20 female / 2.54 mm')
    s.note(1240, 716, '“open” = no net or copper connection on the source PCB.', size=10)
    s.note(1240, 740, 'No no-connect flags were added; confirm intent before changing these pins.', size=9)

    s.panel(40, 620, 1150, 480, '03 / SERVO CONNECTORS & LOCAL DECOUPLING',
            'Each output keeps its existing signal / +SV / GND pin order. Motors and controller are off-board.')
    for ref, x in zip(('BASE', 'BICEP', 'ELBOW', 'TWIST', 'GRIPPER'), (200, 420, 640, 860, 1080)):
        s.header(ref, x, 790, '3-pin / 2.54 mm')
    for ref, x in zip(('C1', 'C2', 'C3', 'C4', 'C5'), (180, 400, 620, 840, 1060)):
        pins = s.place(ref, x, 970, angle=90, text_xy=(x + 35, 965))
        s.wire((x, 900), pins['1'])
        s.label(x, 900, '+SV')
        s.wire(pins['2'], (x, 1040))
        s.label(x, 1040, 'GND')

    s.panel(40, 1130, 1150, 260, '04 / INPUT & AUXILIARY HEADERS',
            'Reference names and pin assignments are retained exactly, including the name BICEP1.')
    for ref, x, caption in [('BTNFING2', 250, '2-pin / B1'), ('BTNFING1', 510, '2-pin / B2'),
                             ('U1', 760, '2-pin / M1'), ('BICEP1', 1080, '3-pin auxiliary')]:
        s.header(ref, x, 1280, caption)
    s.note(60, 1365, 'BICEP1 carries CLK / 3V / GND; it is not the S2 / +SV / GND servo connector named BICEP.', size=10)

    s.panel(1220, 800, 740, 310, '05 / INDICATORS',
            'Supplier symbols confirm LED pin 1 = anode, pin 2 = cathode.')
    for led, resistor, net, y, caption in [('LED1', 'R1', '41', 950, 'KT-0805G / green'),
                                          ('LED2', 'R2', '42', 1060, 'KT-0805B / blue')]:
        s.place(led, 1430, y, angle=180, caption=caption)
        s.place(resistor, 1720, y)
        s.wire((1290, y), (1390, y))
        s.label(1290, y, net)
        s.wire((1470, y), (1680, y))
        s.label(1570, y, resistor)
        s.wire((1760, y), (1900, y))
        s.label(1900, y, 'GND', anchor='end')

    s.panel(1220, 1140, 740, 250, 'RECONSTRUCTION NOTES',
            'An as-wired companion to the PCB, not a recovered original schematic.')
    for y, text in [(1228, 'All component IDs, footprint UUIDs, pad numbers and named nets are retained.'),
                     (1256, 'FH1 has four physical pads but only two electrical terminals (1 and 2).'),
                     (1284, 'The PCB export does not define external module circuitry or supply limits.'),
                     (1312, 'Do not treat a netlist match or manufacturing DRC as circuit approval.'),
                     (1340, 'Open the JSON in EasyEDA Standard; see the import notes before Update PCB.'),
                     (1370, 'Source PCB SHA-256: 1ccdc89fb0ed…   |   Original PCB is unchanged.')]:
        s.note(1240, y, text, size=9, color='#334155')
    return s.document(), s.footprint_overrides


def schematic_netlist(document):
    """Parse actual output pins, wires and labels into a geometric net graph.

    Crossings are joined conservatively. No cross-net wire crossings are
    allowed in this sheet, removing ambiguity about automatic junctions.
    Graphics (PL/PT) are never treated as electrical connections.
    """
    parent, pins, labels, segments, ids, refs = {}, {}, {}, [], set(), {}

    def unique(id):
        require(id and id not in ids, f'Missing or duplicate object ID: {id}')
        ids.add(id)

    def root(p):
        parent.setdefault(p, p)
        if parent[p] != p:
            parent[p] = root(parent[p])
        return parent[p]

    def union(a, b):
        parent[root(a)] = root(b)

    def point(a, i, j):
        return float(a[i]), float(a[j])

    index = {'R': 11, 'E': 9, 'PL': 6, 'PT': 6, 'A': 7, 'T': 15, 'W': 6, 'N': 6, 'J': 5}
    for shape in document['shape']:
        pieces = shape.split('#@$')
        head = pieces[0].split('~')
        ref = None
        if head[0] == 'LIB':
            unique(head[6])
            ref = next(p.split('~')[12] for p in pieces[1:] if p.startswith('T~P~'))
            require(ref not in refs, f'Duplicate symbol reference {ref}')
            refs[ref] = dict(id=head[6], footprint_uuid=head[7], symbol_uuid=head[8], params=parameters(head[3]))
            pieces = pieces[1:]
        for piece in pieces:
            a = piece.split('~')
            kind = a[0]
            if kind == 'P':
                groups = [g.split('~') for g in piece.split('^^')]
                unique(groups[0][7])
                require(ref, 'Pin outside symbol')
                require(groups[0][3] == groups[4][4], 'Pin number fields disagree')
                xy = point(groups[1], 0, 1)
                require(xy == point(groups[0], 4, 5), 'Pin dot and position disagree')
                key = (ref, groups[4][4])
                require(key not in pins, f'Duplicate schematic pin {key}')
                pins[key] = xy
                root(xy)
            else:
                require(kind in index, f'Unsupported schematic primitive {kind}')
                unique(a[index[kind]])
                if kind == 'W':
                    values = list(map(float, a[1].split()))
                    require(len(values) >= 4 and len(values) % 2 == 0, 'Invalid wire')
                    points = list(zip(values[::2], values[1::2]))
                    for p, q in zip(points, points[1:]):
                        require(p != q and (p[0] == q[0] or p[1] == q[1]), 'Nonorthogonal wire')
                        union(p, q)
                        segments.append((p, q))
                elif kind == 'N':
                    xy = point(a, 1, 2)
                    root(xy)
                    labels.setdefault(a[5], []).append(xy)
                elif kind == 'J':
                    root(point(a, 1, 2))

    def on(p, a, b):
        return min(a[0], b[0]) <= p[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= p[1] <= max(a[1], b[1])

    # Endpoint, pin and label contacts, including T junctions.
    for points in labels.values():
        for p in points:
            require(p in pins.values() or any(on(p, a, b) for a, b in segments), 'Floating net label anchor')
    for p in list(parent):
        for a, b in segments:
            if on(p, a, b):
                union(p, a)
    for i, (a, b) in enumerate(segments):
        for c, d in segments[:i]:
            if (a[0] == b[0]) == (c[0] == d[0]):
                continue
            p = (a[0], c[1]) if a[0] == b[0] else (c[0], a[1])
            if on(p, a, b) and on(p, c, d):
                union(a, c)
    for points in labels.values():
        for p in points[1:]:
            union(points[0], p)
    root_names = {}
    for name, points in labels.items():
        for p in points:
            root_names.setdefault(root(p), set()).add(name)
    require(all(len(names) == 1 for names in root_names.values()), f'Shorted net labels: {root_names}')
    root_pins = {}
    for key, p in pins.items():
        root_pins.setdefault(root(p), []).append(key)
    for r, group in root_pins.items():
        require(r in root_names or len(group) == 1, f'Unnamed connected pins: {group}')
    require(all(root(p) in root_pins for points in labels.values() for p in points), 'Floating net label')
    result = {key: next(iter(root_names[root(p)])) if root(p) in root_names else '' for key, p in pins.items()}
    return result, refs, len(ids)


def validate(document, components):
    actual, refs, object_count = schematic_netlist(document)
    expected = {(ref, number): net for ref, c in components.items() for number, net in c['pins'].items()}
    require(actual == expected, f'Netlist mismatch: missing={set(expected) - set(actual)}, '
            f'extra={set(actual) - set(expected)}, '
            f'changed={[(k, expected[k], actual.get(k)) for k in expected if actual.get(k) != expected[k]]}')
    for ref, component in components.items():
        require(refs[ref]['id'] == component['id'], f'Instance ID changed: {ref}')
        require(refs[ref]['footprint_uuid'] == component['footprint_uuid'], f'Footprint binding changed: {ref}')
        for key, value in component['params'].items():
            require(refs[ref]['params'][key] == value, f'Component metadata changed: {ref}/{key}')
    nets = {net: sorted(f'{ref}.{pin}' for (ref, pin), n in actual.items() if n == net)
            for net in sorted(set(actual.values()) - {''})}
    return dict(components=len(refs), logical_pins=len(actual), physical_pcb_pads=sum(len(c['physical_pads']) for c in components.values()),
                named_nets=len(nets), assigned_logical_pins=sum(bool(n) for n in actual.values()),
                open_pins=sorted(f'{ref}.{pin}' for (ref, pin), net in actual.items() if not net),
                nets=nets, netlist_matches_pcb=True, original_component_ids_retained=True,
                original_footprint_uuids_retained=True, unique_object_ids=object_count,
                native_easyeda_import_tested=False, native_easyeda_erc_tested=False)


def svg(document):
    """Render the saved schematic primitives, not a separately drawn mockup."""
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="2000" height="1440" viewBox="0 0 {WIDTH} {HEIGHT}">',
           '<rect width="100%" height="100%" fill="white"/>']

    def text(x, y, value, color, size, anchor='start', weight='', visible=True):
        if visible:
            out.append(f'<text x="{x}" y="{y}" fill="{color}" font-family="Arial, sans-serif" '
                       f'font-size="{size or "7pt"}" text-anchor="{anchor}" font-weight="{weight or "normal"}">{html.escape(value)}</text>')

    def path(d, color, width=1.5, fill='none'):
        out.append(f'<path d="{d}" stroke="{color}" stroke-width="{width}" fill="{fill}" stroke-linejoin="round"/>')

    for shape in document['shape']:
        pieces = shape.split('#@$')
        if pieces[0].startswith('LIB~'):
            pieces = pieces[1:]
        for piece in pieces:
            a = piece.split('~')
            kind = a[0]
            if kind == 'P':
                groups = [g.split('~') for g in piece.split('^^')]
                path(*groups[2][:2])
                for g in groups[3:5]:
                    text(g[1], g[2], g[4], g[8] if len(g) > 8 else '#000080', g[7], g[5], visible=g[0] == '1')
            elif kind == 'R':
                out.append(f'<rect x="{a[1]}" y="{a[2]}" rx="{a[3] or 0}" ry="{a[4] or 0}" width="{a[5]}" height="{a[6]}" '
                           f'stroke="{a[7]}" stroke-width="{a[8]}" fill="{a[10]}"/>')
            elif kind == 'E':
                out.append(f'<ellipse cx="{a[1]}" cy="{a[2]}" rx="{a[3]}" ry="{a[4]}" stroke="{a[5]}" stroke-width="{a[6]}" fill="{a[8]}"/>')
            elif kind in ('PL', 'W'):
                out.append(f'<polyline points="{a[1]}" stroke="{a[2]}" stroke-width="{a[3]}" fill="{a[5]}"/>')
            elif kind == 'PT':
                path(a[1], a[2], a[3], a[5])
            elif kind == 'A':
                path(a[1], a[3], a[4], a[6])
            elif kind == 'T':
                text(a[2], a[3], a[12], a[5], a[7], a[14], a[8], a[13] == '1')
            elif kind == 'N':
                text(a[8], a[9], a[5], a[4], a[11], a[7])
            elif kind == 'J':
                out.append(f'<circle cx="{a[1]}" cy="{a[2]}" r="{a[3]}" fill="{a[4]}"/>')
            else:
                raise ValueError(f'Unsupported preview object {kind}')
    out.append('</svg>')
    return '\n'.join(out) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf', action='store_true', help='Also render a PDF (requires CairoSVG)')
    args = parser.parse_args()
    raw = PCB.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == SHA256, 'Fixed PCB source revision changed')
    components = pcb_components(json.loads(raw))
    symbols = json.loads(CACHE.read_text())['symbols']
    document, overrides = build(components, symbols)
    serialized = (json.dumps(document, indent=2, ensure_ascii=False) + '\n').encode()
    # Check the serialized document, not the generator's intended connections.
    report = validate(json.loads(serialized), components)
    report.update(source_pcb=PCB.name, source_pcb_sha256=SHA256, schematic=OUT.name,
                  schematic_sha256=hashlib.sha256(serialized).hexdigest(),
                  symbol_snapshot_sha256=hashlib.sha256(CACHE.read_bytes()).hexdigest(),
                  footprint_overrides=overrides,
                  scope='As-wired PCB reconstruction; not original design intent, native ERC, or circuit approval.')
    report_path = HERE / 'DeskBuddy_Schematic_Validation.json'
    if OUT.exists():
        require(report_path.exists(), 'Existing schematic has no generation record; refusing to overwrite')
        previous_hash = json.loads(report_path.read_text())['schematic_sha256']
        require(hashlib.sha256(OUT.read_bytes()).hexdigest() == previous_hash,
                'Schematic has been edited since generation; refusing to overwrite it')
    OUT.write_bytes(serialized)
    preview = svg(json.loads(serialized))
    (HERE / 'DeskBuddy_Schematic_Preview.svg').write_text(preview)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    if args.pdf:
        import cairosvg
        cairosvg.svg2pdf(bytestring=preview.encode(), write_to=str(HERE / 'DeskBuddy_Schematic_Preview.pdf'))
    print(json.dumps({k: v for k, v in report.items() if k != 'nets'}, indent=2))


if __name__ == '__main__':
    main()
