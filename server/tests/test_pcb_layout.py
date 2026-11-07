"""Unit tests for pcb_layout (pure Python, no Altium needed).

Run from server/:  python -m unittest tests.test_pcb_layout -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import pcb_layout as pl  # noqa: E402

# What the bridge writes for one component, a track, an arc, a via, a pour
# with an arc edge, and a cutout (internal units: 10,000 per mil)
BRIDGE = "\n".join([
    "N|U1",
    "O|0|0|1000000|1000000",
    "K|U1|100000|200000|90000|Top Layer|False|QFN16|110000|250000|90000|300000|50000|True|"
    "120000|260000|0|600000|100000|False|0|3",
    "T|Top Layer|GND|60000|0|0|100000|100000",
    "A|Bottom Layer|3V3|80000|500000|500000|50000|0|90000",
    "V|GND|100000|100000|250000|120000|Top Layer|Bottom Layer",
    "G|Top Layer|GND|GND pour|5|1|False|True|2500000|False|50000|200000|80000|5000|12|True|True|False",
    "S|L|0|0",
    "S|A|100000|0|100000|50000|50000|270000|90000",
    "S|L|100000|100000",
    "U|Multi Layer",
    "S|L|0|0",
    "S|L|10000|0",
    "S|L|10000|10000",
    "X|2",
    "B|C:\\boards\\b.PcbDoc",
])


class ParseTest(unittest.TestCase):
    def setUp(self):
        self.lay = pl.parse_layout(BRIDGE)

    def test_component(self):
        c = self.lay["components"][0]
        self.assertEqual((c["designator"], c["x"], c["y"], c["rotation"], c["layer"]), ("U1", 10, 20, 90, "top"))
        self.assertEqual(c["designator_text"], {"x": 11, "y": 25, "rotation": 90, "height": 30, "stroke_width": 5,
                                                "visible": True, "autoposition": "manual"})
        self.assertEqual(c["comment_text"]["autoposition"], "bottom_left")
        self.assertFalse(c["comment_text"]["visible"])

    def test_copper(self):
        self.assertEqual(self.lay["tracks"][0], {"net": "GND", "layer": "Top Layer", "width": 6,
                                                 "start": [0, 0], "end": [10, 10]})
        self.assertEqual(self.lay["arcs"][0]["end_angle"], 90)
        self.assertEqual(self.lay["vias"][0]["diameter"], 25)
        p = self.lay["pours"][0]
        self.assertEqual((p["hatch"], p["pour_over"], p["remove_islands"], p["island_area_sq_mils"]),
                         ("solid", "same_net", True, 2500))
        self.assertEqual((p["remove_necks"], p["neck_width_mils"], p["pour_index"], p["obey_cutouts"]),
                         (False, 5, 12, True))
        self.assertEqual(p["outline"][1]["arc"], {"center": [10, 5], "radius": 5, "start_angle": 270, "end_angle": 90})
        self.assertEqual(self.lay["cutouts"][0]["layer"], "Multi Layer")
        self.assertEqual(len(self.lay["cutouts"][0]["outline"]), 3)
        self.assertEqual((self.lay["skipped"], self.lay["board"], self.lay["found"]), (2, "C:\\boards\\b.PcbDoc", ["U1"]))


class RoundTripTest(unittest.TestCase):
    def test_encode_gives_back_the_bridge_records(self):
        lines = pl.encode_copper(pl.parse_layout(BRIDGE))
        for rec in ("T|Top Layer|GND|60000|0|0|100000|100000",
                    "A|Bottom Layer|3V3|80000|500000|500000|50000|0|90000",
                    "V|GND|100000|100000|250000|120000|Top Layer|Bottom Layer",
                    "S|A|100000|0|100000|50000|50000|270000|90000"):
            self.assertIn(rec, lines)
        g = [l for l in lines if l.startswith("G|")][0].split("|")
        self.assertEqual(g[1:6], ["Top Layer", "GND", "GND pour", "5", "1"])
        self.assertEqual(g[7:11], ["1", "2500000", "0", "50000"])
        self.assertEqual(g[-3:], ["1", "1", "0"])

    def test_pours_come_last_in_pour_index_order(self):
        sq = [[0, 0], [10, 0], [10, 10]]
        lines = pl.encode_copper({"pours": [{"net": "A", "layer": "Top Layer", "outline": sq, "pour_index": 9},
                                            {"net": "B", "layer": "Top Layer", "outline": sq, "pour_index": 2}],
                                  "vias": [{"net": "A", "x": 1, "y": 1, "diameter": 20, "hole": 10}]})
        self.assertTrue(lines[0].startswith("V|"))
        self.assertEqual([l.split("|")[2] for l in lines if l.startswith("G|")], ["B", "A"])

    def test_angles_are_kept_without_a_transform(self):
        lines = pl.encode_copper({"arcs": [{"net": "", "layer": "Top Layer", "width": 5, "center": [0, 0],
                                            "radius": 10, "start_angle": 360, "end_angle": 90}]})
        self.assertTrue(lines[0].endswith("|360000|90000"))


class TransformTest(unittest.TestCase):
    def test_shift_rotate_and_rename(self):
        t = {"about": [100, 100], "rotation": 90, "dx": 10, "dy": 0}
        self.assertEqual(pl.transform_point([200, 100], t), [110, 200])
        lines = pl.encode_copper({"tracks": [{"net": "N1", "layer": "Top Layer", "width": 6,
                                              "start": [100, 100], "end": [200, 100]}]}, t, {"N1": "N2"})
        self.assertEqual(lines, ["T|Top Layer|N2|60000|1100000|1000000|1100000|2000000"])

    def test_mirror_reverses_arcs(self):
        a1, a2 = pl._arc_angles(0, 90, {"mirror": True})
        self.assertEqual((a1, a2), (90, 180))


class ErrorTest(unittest.TestCase):
    def test_bad_input_is_refused(self):
        with self.assertRaises(pl.LayoutError):
            pl.encode_copper({"tracks": [{"net": "A|B", "layer": "Top Layer", "width": 6, "start": [0, 0], "end": [1, 1]}]})
        with self.assertRaises(pl.LayoutError):
            pl.encode_copper({"vias": [{"net": "A", "x": 1e9, "y": 0, "diameter": 20, "hole": 10}]})
        with self.assertRaises(pl.LayoutError):
            pl.encode_copper({"pours": [{"net": "A", "layer": "Top Layer", "outline": [[0, 0], [1, 1]]}]})
        with self.assertRaises(pl.LayoutError):
            pl.encode_copper({"pours": [{"net": "A", "layer": "Top Layer", "outline": [[0, 0], [1, 1], [1, 0]],
                                         "hatch": "dotted"}]})
        with self.assertRaises(pl.LayoutError):
            pl.text_spec({"x": 1, "y": 2, "rotation": 0, "height": 30}, "designator_text")

    def test_text_spec(self):
        self.assertEqual(pl.text_spec({"x": 1, "y": 2, "rotation": 90, "height": 30, "stroke_width": 5,
                                       "visible": False, "autoposition": "center"}, "t"), "1.0;2.0;90.0;30.0;5.0;0;5")
        self.assertEqual(pl.text_spec(None, "t"), "")


class UnroutedTest(unittest.TestCase):
    def test_label_connections(self):
        pads, conns, board = pl.parse_unrouted("\n".join([
            "P|U1|1|0|0|N1", "P|R1|2|1000000|0|N1",
            "C|N1|0|0|1000000|0|Top Layer|Top Layer",
            "C|N1|0|0|5000000|0|Top Layer|Top Layer",
            "B|b.PcbDoc"]))
        out = pl.label_connections(pads, conns, [-10, -10, 200, 10])
        self.assertEqual((out[0]["from_pad"], out[0]["to_pad"], out[0]["length"], out[0]["inside"]), ("U1-1", "R1-2", 100, True))
        self.assertEqual((out[1]["to_pad"], out[1]["inside"]), (None, False))
        self.assertEqual(board, "b.PcbDoc")


if __name__ == "__main__":
    unittest.main()
