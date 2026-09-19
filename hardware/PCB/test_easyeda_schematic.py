"""Positive and negative tests against the serialized reconstructed schematic."""

import copy
import hashlib
import json
import unittest

from build_easyeda_schematic import (PCB, CACHE, SHA256, Transform, build,
                                     pcb_components, schematic_netlist, validate)


class SchematicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.components = pcb_components(json.loads(PCB.read_bytes()))
        cls.symbols = json.loads(CACHE.read_text())['symbols']
        document, cls.overrides = build(cls.components, cls.symbols)
        cls.document = json.loads(json.dumps(document))

    def test_all_pins_and_metadata_match_source(self):
        report = validate(self.document, self.components)
        self.assertEqual(report['components'], 29)
        self.assertEqual(report['physical_pcb_pads'], 102)
        self.assertEqual(report['logical_pins'], 100)
        self.assertEqual(report['named_nets'], 19)
        self.assertEqual(report['assigned_logical_pins'], 74)
        self.assertEqual(len(report['open_pins']), 26)
        self.assertEqual(len(self.overrides), 6)

    def test_source_pcb_unchanged(self):
        self.assertEqual(hashlib.sha256(PCB.read_bytes()).hexdigest(), SHA256)

    def test_fuse_duplicate_pads_are_two_logical_pins(self):
        actual, _, _ = schematic_netlist(self.document)
        self.assertEqual({p: n for (ref, p), n in actual.items() if ref == 'FH1'}, {'1': '+SV', '2': '+V'})
        self.assertEqual(len(self.components['FH1']['physical_pads']), 4)

    def test_polarity_is_from_supplier_symbols(self):
        expected = {'DIODE': {'1': 'K', '2': 'A'}, 'LED1': {'1': 'A', '2': 'K'}, 'LED2': {'1': 'A', '2': 'K'}}
        for shape in self.document['shape']:
            if not shape.startswith('LIB~'):
                continue
            pieces = shape.split('#@$')
            ref = next(p.split('~')[12] for p in pieces if p.startswith('T~P~'))
            if ref in expected:
                pin_names = {p.split('^^')[0].split('~')[3]: p.split('^^')[3].split('~')[4]
                             for p in pieces if p.startswith('P~')}
                self.assertEqual(pin_names, expected[ref])

    def test_missing_wire_fails(self):
        bad = copy.deepcopy(self.document)
        index = next(i for i, s in enumerate(bad['shape']) if s.startswith('W~'))
        bad['shape'].pop(index)
        with self.assertRaises(ValueError):
            validate(bad, self.components)

    def test_swapped_led_pins_fail(self):
        bad = copy.deepcopy(self.document)
        for i, shape in enumerate(bad['shape']):
            if not shape.startswith('LIB~') or '~comment~LED1~' not in shape:
                continue
            pieces = shape.split('#@$')
            for j, p in enumerate(pieces):
                if not p.startswith('P~'):
                    continue
                groups = [g.split('~') for g in p.split('^^')]
                swapped = {'1': '2', '2': '1'}[groups[0][3]]
                groups[0][3] = groups[4][4] = swapped
                pieces[j] = '^^'.join('~'.join(g) for g in groups)
            bad['shape'][i] = '#@$'.join(pieces)
        with self.assertRaisesRegex(ValueError, 'Netlist mismatch'):
            validate(bad, self.components)

    def test_off_wire_net_label_fails(self):
        bad = copy.deepcopy(self.document)
        index = next(i for i, s in enumerate(bad['shape']) if s.startswith('N~'))
        fields = bad['shape'][index].split('~')
        fields[2] = str(float(fields[2]) + 7)
        bad['shape'][index] = '~'.join(fields)
        with self.assertRaisesRegex(ValueError, 'Floating net label anchor'):
            validate(bad, self.components)

    def test_added_power_short_fails(self):
        bad = copy.deepcopy(self.document)
        bad['shape'].append('W~800 230 800 350~#008800~1~0~none~gge_test_short~0')
        with self.assertRaisesRegex(ValueError, 'Shorted net labels'):
            validate(bad, self.components)

    def test_duplicate_ids_fail(self):
        bad = copy.deepcopy(self.document)
        bad['shape'].append(bad['shape'][0])
        with self.assertRaisesRegex(ValueError, 'duplicate object ID'):
            validate(bad, self.components)

    def test_relative_pin_path_rotation(self):
        t = Transform(400, 300, 200, 300, 90)
        self.assertEqual(t.path('M380,300h10'), 'M 200 260 L 200 280')
        self.assertEqual(t.path('M 400 300 v10'), 'M 200 300 L 180 300')
        self.assertEqual(t.path('M 400 300 A 4 3 0 1 1 410 300'), 'M 200 300 A 8 6 90 1 1 200 320')

    def test_unknown_library_primitive_fails(self):
        symbols = copy.deepcopy(self.symbols)
        symbols['C2297']['data']['shape'].append('UNSUPPORTED~0')
        with self.assertRaisesRegex(ValueError, 'Unsupported library primitive'):
            build(self.components, symbols)


if __name__ == '__main__':
    unittest.main()
