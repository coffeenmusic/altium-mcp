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
                             "area": "10000000|15000000|30000000|20000000", "designators": "",
                             "margin": "400000", "include_locked": "false"})
        self.assertEqual(filters(None, "", "", None)[0]["kinds"], "|track|arc|via|polygon|region|fill|")
        self.assertEqual(filters([], "", "", None)[0]["kinds"], "|track|arc|via|polygon|region|fill|")
        f = filters(["cutout"], "", "", None, ["U1", "C3"], 25, True)[0]
        self.assertEqual((f["kinds"], f["designators"], f["margin"], f["include_locked"]),
                         ("|cutout|", "U1;C3", "250000", "true"))

    def test_rejected(self):
        self.assertIn("kinds", filters(["pad"], "", "", None)[1])
        self.assertIn("area", filters(None, "", "", [0, 0, 10])[1])
        self.assertIn("no size", filters(None, "", "", [0, 0, 0, 10])[1])
        self.assertIn("net", filters(None, 'A"B', "", None)[1])
        self.assertIn("designators", filters(None, "", "", None, ["U1;U2"])[1])
        self.assertIn("100,000", filters(None, "", "", [0, 0, 200000, 10])[1])

    def test_a_delete_must_be_narrowed(self):
        self.assertFalse(narrowed(None, "", "", None))
        self.assertFalse(narrowed(["track", "arc", "via", "polygon", "region", "fill"], "", "", None))
        self.assertTrue(narrowed(["via"], "", "", None))
        self.assertTrue(narrowed(None, "GND", "", None))
        self.assertTrue(narrowed(None, "", "", [0, 0, 1, 1]))
        self.assertTrue(narrowed(None, "", "", None, ["U1"]))

    def test_case_does_not_bypass_the_guard(self):
        # #74 review: kinds in another case passed the guard and matched everything
        self.assertFalse(narrowed(["Track", "Arc", "Via", "Polygon", "Region", "Fill"], "", "", None))
        self.assertFalse(narrowed([], "", "", None))
        self.assertFalse(narrowed(["track", "arc", "via", "polygon", "region", "fill", "cutout"], "", "", None))
        self.assertTrue(narrowed(["cutout"], "", "", None))


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
