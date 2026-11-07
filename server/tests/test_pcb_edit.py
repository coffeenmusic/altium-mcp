"""Unit tests for pcb_edit (pure Python, no Altium needed).

Run from server/:  python -m unittest tests.test_pcb_edit -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from pcb_edit import changes, filters, narrowed  # noqa: E402


class FiltersTest(unittest.TestCase):
    def test_encoding(self):
        f, err = filters(["Track", "via", "track"], "GND", "Top Layer", [3000, 2000, 1000, 1500])
        self.assertEqual(err, "")
        self.assertEqual(f, {"kinds": "|track|via|", "net": "GND", "layer": "Top Layer",
                             "area": "10000000|15000000|30000000|20000000"})
        self.assertEqual(filters(None, "", "", None)[0]["kinds"], "|track|arc|via|polygon|region|fill|")

    def test_rejected(self):
        self.assertIn("kinds", filters(["pad"], "", "", None)[1])
        self.assertIn("area", filters(None, "", "", [0, 0, 10])[1])
        self.assertIn("no size", filters(None, "", "", [0, 0, 0, 10])[1])
        self.assertIn("net", filters(None, 'A"B', "", None)[1])

    def test_a_delete_must_be_narrowed(self):
        self.assertFalse(narrowed(None, "", "", None))
        self.assertFalse(narrowed(["track", "arc", "via", "polygon", "region", "fill"], "", "", None))
        self.assertTrue(narrowed(["via"], "", "", None))
        self.assertTrue(narrowed(None, "GND", "", None))
        self.assertTrue(narrowed(None, "", "", [0, 0, 1, 1]))


class ChangesTest(unittest.TestCase):
    def test_encoding(self):
        c, err = changes(width_mils=7, new_net="")
        self.assertEqual(err, "")
        self.assertEqual(c, {"width": 70000, "via_size": -1, "via_hole": -1, "set_net": "true",
                             "new_net": "", "new_layer": ""})
        self.assertEqual(changes(new_layer="Bottom Layer")[0]["set_net"], "false")

    def test_rejected(self):
        self.assertIn("nothing to change", changes()[1])
        self.assertIn("positive", changes(width_mils=0)[1])
        self.assertIn("smaller", changes(via_diameter_mils=20, via_hole_mils=20)[1])


if __name__ == "__main__":
    unittest.main()
