"""Unit tests for pcb_rules (pure Python, no Altium needed).

Run from server/:  python -m unittest tests.test_pcb_rules -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from pcb_rules import RULE_KINDS, encode_values  # noqa: E402


class EncodeTest(unittest.TestCase):
    def test_mils_become_internal_units(self):
        self.assertEqual(encode_values("clearance", {"gap_mils": 8}), ("gap=80000", ""))
        self.assertEqual(encode_values("clearance", {"gap_mils": 7.874}), ("gap=78740", ""))

    def test_width_needs_all_three_and_min_not_above_max(self):
        self.assertEqual(encode_values("width", {"min_mils": 6, "max_mils": 40, "preferred_mils": 10}),
                         ("min=60000;max=400000;preferred=100000", ""))
        self.assertIn("needs", encode_values("width", {"min_mils": 6})[1])
        self.assertIn("larger", encode_values("width", {"min_mils": 50, "max_mils": 40, "preferred_mils": 45})[1])

    def test_mask_expansion_may_be_negative_gap_may_not(self):
        self.assertEqual(encode_values("solder_mask_expansion", {"expansion_mils": -2}), ("expansion=-20000", ""))
        self.assertIn("negative", encode_values("clearance", {"gap_mils": -1})[1])

    def test_polygon_connect(self):
        self.assertEqual(encode_values("polygon_connect", {"connect_style": "Relief", "entries": 2,
                                                           "air_gap_mils": 12}),
                         ("connect_style=relief;air_gap=120000;entries=2", ""))
        self.assertEqual(encode_values("polygon_connect", {"connect_style": "direct"}),
                         ("connect_style=direct", ""))
        self.assertIn("one of", encode_values("polygon_connect", {"connect_style": "thermal"})[1])
        self.assertIn("whole number", encode_values("polygon_connect", {"connect_style": "relief",
                                                                        "entries": 2.5})[1])

    def test_unknown_kind_and_key(self):
        self.assertIn("unknown rule_type", encode_values("fanout", {})[1])
        self.assertIn("not gap", encode_values("clearance", {"gap": 8})[1])
        self.assertIn("must be a number", encode_values("clearance", {"gap_mils": "eight"})[1])

    def test_every_kind_has_values(self):
        for kind, spec in RULE_KINDS.items():
            self.assertTrue(spec, kind)


if __name__ == "__main__":
    unittest.main()
