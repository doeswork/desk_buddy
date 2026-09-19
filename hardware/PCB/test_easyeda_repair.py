"""Regression tests for the revision-locked repair and local auditor."""

import copy
import hashlib
import json
import unittest

from audit_easyeda import Board
from repair_easyeda import SOURCE, SOURCE_SHA256, repaired_document


class RepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = json.loads(SOURCE.read_bytes())
        cls.fixed, cls.changes = repaired_document(cls.source)
        cls.before = Board(cls.source).audit()
        cls.after = Board(cls.fixed).audit()

    def test_original_is_untouched(self):
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), SOURCE_SHA256)
        self.assertEqual(self.source, json.loads(SOURCE.read_bytes()))

    def test_components_and_metadata_unchanged(self):
        for key in self.source:
            if key not in ('shape', 'BBox'):
                self.assertEqual(self.source[key], self.fixed[key], key)
        for kind in ('LIB~', 'TEXT~', 'HOLE~', 'SVGNODE~'):
            self.assertEqual([s for s in self.source['shape'] if s.startswith(kind)],
                             [s for s in self.fixed['shape'] if s.startswith(kind)])
        before = {i.id: i for i in Board(self.source).items}
        after = {i.id: i for i in Board(self.fixed).items}
        self.assertEqual(set(before) - set(after), {'gge25077'})
        self.assertFalse(set(after) - set(before))
        for id, item in after.items():
            self.assertEqual((item.net, item.layers), (before[id].net, before[id].layers))

    def test_known_source_problems_are_detected(self):
        self.assertFalse(self.before['outline_valid'])
        self.assertEqual(len(self.before['clearance_rule_findings']), 2)
        self.assertEqual(len(self.before['track_width_rule_findings']), 10)
        self.assertEqual(len(self.before['via_size_rule_findings']), 2)

    def test_repaired_geometry_and_connectivity(self):
        self.assertEqual(self.before['pad_via_partition'], self.after['pad_via_partition'])
        self.assertEqual(self.after['named_nets'], 19)
        self.assertEqual(self.after['physical_connectivity_groups'], 45)
        self.assertTrue(self.after['outline_valid'])
        for key in ('shorts', 'disconnected_named_nets', 'clearance_rule_findings',
                    'track_width_rule_findings', 'via_size_rule_findings', 'drills_outside_board',
                    'drilled_island_splits', 'plated_holes_outside_pads'):
            self.assertFalse(self.after[key], key)
        for layer in self.after['layers'].values():
            self.assertGreater(layer['minimum_island_spacing_mm'], 0.20)
            self.assertGreater(layer['copper_to_outline_mm'], 0.40)
            self.assertEqual(layer['copper_outside_board_mm2'], 0)

    def test_added_short_is_detected(self):
        bad = copy.deepcopy(self.fixed)
        # Explicitly bridge the two opposite-net C1 pads on top copper.
        board = Board(bad)
        from repair_easyeda import native_point
        points = [native_point(board, p) for p in [(47.244, 166.767002), (47.244, 168.766998)]]
        coords = ' '.join(v for p in points for v in p)
        bad['shape'].append(f'TRACK~1~1~GND~{coords}~test_short~0')
        self.assertTrue(Board(bad).audit()['shorts'])

    def test_removed_route_is_detected(self):
        bad = copy.deepcopy(self.fixed)
        bad['shape'] = [s for s in bad['shape'] if not
                        (s.startswith('TRACK~') and s.split('~')[5] == 'gge25269')]
        self.assertIn('M1', Board(bad).audit()['disconnected_named_nets'])

    def test_unsupported_copper_fails_closed(self):
        bad = copy.deepcopy(self.fixed)
        bad['shape'].append('CIRCLE~4100~3000~1~1~1~test_circle~0')
        with self.assertRaisesRegex(ValueError, 'Unsupported copper'):
            Board(bad)


if __name__ == '__main__':
    unittest.main()
