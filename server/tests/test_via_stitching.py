"""Unit tests for via_stitching (pure Python, no Altium needed).

Run from server/:  python -m unittest tests.test_via_stitching -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import via_stitching as vs  # noqa: E402

STITCH = (b"|SELECTION=FALSE|UNIONTYPE=2|FUNIONINDEX=1048578|X1=0mil|STEP=118.1102mil|STAGGER=TRUE"
          b"|NETNAME=GND|VIAPADCLEARANCE=0.3mm|PULLBACK=0mil|DIAMETER=35.4331mil|HOLESIZE=17.7165mil"
          b"|ISCONSTRAINAREA=FALSE|KIND0=0\x00")
SHIELD = (b"|UNIONTYPE=6|FUNIONINDEX=2097156|STEP=47.2441mil|NETNAME=CLK|VIANETNAME=GND"
          b"|DISTANCE=59.0551mil|ROWCOUNT=2|ADDCOPPER=FALSE|ADDPOLYCUTOUT=TRUE\x00")


class SettingsTest(unittest.TestCase):
    def test_checks(self):
        self.assertEqual(vs.check_settings({"grid_mils": 100, "via_diameter_mils": 20, "via_hole_mils": 10}), "")
        self.assertIn("positive", vs.check_settings({"grid_mils": 0}))
        self.assertIn("smaller", vs.check_settings({"via_diameter_mils": 10, "via_hole_mils": 10}))
        self.assertIn("number", vs.check_settings({"pullback_mils": "5"}))
        self.assertIn("rows", vs.check_shield_settings({}, 0))
        self.assertEqual(vs.check_shield_settings({"via_spacing_mils": 40, "distance_mils": 0}, 2), "")

    def test_values_go_to_the_dialog_in_mils(self):
        self.assertEqual(vs.mil_text(11.81102), "11.811mil")
        self.assertEqual(vs.mil_text(100), "100mil")
        self.assertEqual(vs.mils_of("0.254mm"), 10.0)
        self.assertEqual(vs.mils_of("100mil"), 100.0)
        self.assertIsNone(vs.mils_of("N/A"))


class StoredGroupsTest(unittest.TestCase):
    def test_stitching_record(self):
        g = vs.stored_groups(b"junk" + STITCH + SHIELD)
        self.assertEqual(list(g), [1048578])
        self.assertEqual(g[1048578], {"net": "GND", "grid_mils": 118.1102, "stagger": True,
                                      "via_pad_clearance_mils": 11.811, "pullback_mils": 0.0,
                                      "via_diameter_mils": 35.4331, "via_hole_mils": 17.7165,
                                      "constrained_to_area": False})

    def test_shielding_record(self):
        g = vs.stored_shielding(STITCH + SHIELD)
        self.assertEqual(g[2097156]["shielded_net"], "CLK")
        self.assertEqual(g[2097156]["rows"], 2)
        self.assertTrue(g[2097156]["add_cutout"])
        self.assertFalse(g[2097156]["add_copper"])


class StaggerTest(unittest.TestCase):
    def test_measured_from_rows(self):
        straight = [(y * 150.0, 31.5) for y in range(5)]
        stagger = [(y * 150.0, 31.5 + (75 if y % 2 else 0)) for y in range(5)]
        self.assertFalse(vs.staggered(straight, 150))
        self.assertTrue(vs.staggered(stagger, 150))
        # a row that starts further in (copper missing at its left) is still a grid row
        self.assertFalse(vs.staggered([(0, 31.5), (150, 331.5), (300, 31.5)], 150))
        self.assertIsNone(vs.staggered([(0, 0)], 150))


class TickTest(unittest.TestCase):
    def test_tick_is_light_pixels_in_the_box(self):
        from PIL import Image, ImageDraw
        empty = Image.new("L", (200, 32), 60)
        ImageDraw.Draw(empty).rectangle((2, 2, 29, 29), outline=200)   # box border, lit by hover
        ticked = empty.copy()
        ImageDraw.Draw(ticked).line((9, 16, 14, 22, 23, 9), fill=255, width=2)
        self.assertFalse(vs.tick_drawn(empty))
        self.assertTrue(vs.tick_drawn(ticked))


if __name__ == "__main__":
    unittest.main()
