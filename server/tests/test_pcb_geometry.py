"""Unit tests for pcb_geometry (pure Python, no Altium needed).

Run from server/:  python -m unittest tests.test_pcb_geometry -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import pcb_geometry as geo  # noqa: E402

ORIGIN = (1000000, 2000000)
RECORDS = [
    "track|Top Layer|GND||1010000|2020000|1030000|2020000|80000|0",
    "track|Top Layer|GND||1010000|2020000|1030000|2020000|80000|1",
    "arc|Bottom Layer|SIG|U1|1100000|2100000|50000|0|90000|10000|0",
    "via|Top Layer:Bottom Layer|GND||1050000|2050000|236220|98425",
    "via|Top Layer:Mid Layer 1|SIG||1060000|2060000|236220|98425",
    "pad|Multi Layer|SIG|J1|1200000|2200000|600000|600000|1|350000|90000|1",
    "pad|Top Layer|||1300000|2300000|100000|200000|2|0|0|MH|A",
    "fill|Top Layer|GND||1000000|2000000|1100000|2100000|45000",
    "region|Bottom Layer|GND||1000000|2000000|1500000|2500000|1",
    "polygon|Top Layer|GND||1000000|2000000|1500000|2500000",
    "text|Top Overlay||U1|1010000|2010000|600000|90000|U1|x",
    "component|Top Layer||U1|1100000|2100000|180000|1000000|2000000|1200000|2200000|SOIC8",
    "bogus|record",
]


class ParseTest(unittest.TestCase):
    def test_board_relative_in_mils_and_refs(self):
        t = geo.parse_object(RECORDS[0], ORIGIN, "mil")
        self.assertEqual((t["x1"], t["y1"], t["x2"], t["y2"], t["width"]), (1.0, 2.0, 3.0, 2.0, 8.0))
        self.assertEqual(t["ref"], "track|Top Layer|1010000|2020000|1030000|2020000")
        self.assertNotIn("in_polygon", t)
        self.assertTrue(geo.parse_object(RECORDS[1], ORIGIN)["in_polygon"])
        a = geo.parse_object(RECORDS[2], ORIGIN)
        self.assertEqual((a["cx"], a["radius"], a["start_angle"], a["end_angle"], a["component"]),
                         (10.0, 5.0, 0.0, 90.0, "U1"))
        v = geo.parse_object(RECORDS[3], ORIGIN, "mm")
        self.assertEqual((v["low_layer"], v["high_layer"], v["size"], v["hole"]), ("Top Layer", "Bottom Layer", 0.6, 0.25))
        self.assertEqual(v["ref"], "via|1050000|2050000")
        self.assertNotIn("layer", v)

    def test_pads_fills_regions_text_components(self):
        p = geo.parse_object(RECORDS[5], ORIGIN)
        self.assertEqual((p["name"], p["shape"], p["rotation"], p["ref"]), ("1", "round", 90.0, "pad|J1|1"))
        free = geo.parse_object(RECORDS[6], ORIGIN)
        self.assertEqual((free["name"], free["shape"], free["ref"]), ("MH|A", "rectangle", "pad|1300000|2300000"))
        self.assertEqual(geo.parse_object(RECORDS[7], ORIGIN)["rotation"], 45.0)
        r = geo.parse_object(RECORDS[8], ORIGIN)
        self.assertEqual((r["bounds"], r["in_polygon"]), ([0.0, 0.0, 50.0, 50.0], True))
        self.assertNotIn("ref", geo.parse_object(RECORDS[9], ORIGIN))
        self.assertEqual(geo.parse_object(RECORDS[10], ORIGIN)["text"], "U1|x")
        c = geo.parse_object(RECORDS[11], ORIGIN)
        self.assertEqual((c["designator"], c["rotation"], c["footprint"], c["ref"]),
                         ("U1", 180.0, "SOIC8", "component|U1"))
        self.assertIsNone(geo.parse_object(RECORDS[12]))


class SelectTest(unittest.TestCase):
    def test_layers_and_nets(self):
        top = geo.select(RECORDS, ORIGIN, "mil", layers=["top layer"])
        kinds = [o["kind"] for o in top]
        # the through via and the multi-layer pad are on every copper layer;
        # the blind via ends on Top Layer
        self.assertEqual(kinds, ["track", "track", "via", "via", "pad", "pad", "fill", "polygon", "component"])
        mid = geo.select(RECORDS, ORIGIN, "mil", layers=["Mid Layer 1"])
        self.assertEqual([o["kind"] for o in mid], ["via", "via", "pad"])
        sig = geo.select(RECORDS, ORIGIN, "mil", nets=["sig"])
        self.assertEqual([o["kind"] for o in sig], ["arc", "via", "pad"])

    def test_through_copper_only_on_copper_layers(self):
        # an overlay or mechanical layer holds no through via or Multi Layer pad
        overlay = geo.select(RECORDS, ORIGIN, "mil", layers=["Top Overlay"])
        self.assertEqual({o["kind"] for o in overlay}, {"text"})
        self.assertEqual(geo.select(RECORDS, ORIGIN, "mil", layers=["Mechanical 1"]), [])
        # Multi Layer itself, written either way
        multi = geo.select(RECORDS, ORIGIN, "mil", layers=["multi-layer"])
        self.assertEqual([o["kind"] for o in multi], ["via", "pad"])
        # with the board's copper layers known, a layer it lacks holds nothing
        stack = ["Top Layer", "Bottom Layer"]
        self.assertEqual(geo.select(RECORDS[3:6], ORIGIN, "mil", layers=["Mid Layer 3"], stack=stack), [])
        inner = geo.select(RECORDS[3:6], ORIGIN, "mil", layers=["Internal Plane 1"])
        self.assertEqual([o["kind"] for o in inner], ["via", "pad"])

    def test_layer_names_compare_loosely(self):
        self.assertEqual(geo.layer_key("Multi Layer"), geo.layer_key("multi-layer"))
        self.assertTrue(geo.is_multi_layer("MultiLayer"))
        self.assertFalse(geo.is_multi_layer("Top Layer"))
        self.assertTrue(geo.is_copper_layer("Mid-Layer 2"))
        self.assertFalse(geo.is_copper_layer("Top Overlay"))
        self.assertFalse(geo.is_copper_layer("Mid Layer 2", ["Top Layer", "Bottom Layer"]))

    def test_blind_via_on_its_middle_layers(self):
        stack = ["Top Layer", "Mid Layer 1", "Mid Layer 2", "Bottom Layer"]
        blind = "via|Top Layer:Mid Layer 2|SIG||1060000|2060000|236220|98425"
        buried = "via|Bottom Layer:Mid Layer 1|SIG||1060000|2060000|236220|98425"
        self.assertEqual(len(geo.select([blind], ORIGIN, "mil", layers=["mid layer 1"], stack=stack)), 1)
        self.assertEqual(len(geo.select([blind], ORIGIN, "mil", layers=["Bottom Layer"], stack=stack)), 0)
        # low and high in either order
        self.assertEqual(len(geo.select([buried], ORIGIN, "mil", layers=["Mid Layer 2"], stack=stack)), 1)
        self.assertEqual(len(geo.select([buried], ORIGIN, "mil", layers=["Top Layer"], stack=stack)), 0)
        # without the stack only its end layers are known
        self.assertEqual(len(geo.select([blind], ORIGIN, "mil", layers=["Mid Layer 1"])), 0)
        self.assertEqual(geo.via_layers("Top Layer", "Mid Layer 1", ["Top Layer", "Mid Layer 1", "Bottom Layer"]),
                         ["top layer", "mid layer 1"])


class AreaReplyTest(unittest.TestCase):
    TRACK = "track|{}|GND||1010000|2020000|1030000|2020000|80000|0"

    def result(self, n_top, n_bottom, total=None):
        recs = [self.TRACK.format("Bottom Layer")] * n_bottom + [self.TRACK.format("Top Layer")] * n_top
        return {"origin_x": ORIGIN[0], "origin_y": ORIGIN[1], "objects": recs,
                "total": len(recs) if total is None else total}

    def test_limit_applies_after_the_filters(self):
        self.assertEqual(geo.scan_limit(500), 500)
        self.assertEqual(geo.scan_limit(500, layers=["Top Layer"]), geo.SCAN_CAP)
        self.assertEqual(geo.scan_limit(50000, nets=["GND"]), 50000)
        # many bottom-layer objects come first, yet the top-layer ones are found
        out = geo.area_reply(self.result(3, 600), "mil", 2, layers=["Top Layer"])
        self.assertEqual((out["total"], out["returned"], out["truncated"]), (3, 2, True))
        self.assertIn("first 2 of 3", out["note"])
        out = geo.area_reply(self.result(3, 600), "mil", 10, layers=["Top Layer"])
        self.assertEqual((out["total"], out["returned"], out["truncated"]), (3, 3, False))
        self.assertNotIn("note", out)

    def test_cut_by_altium_is_said(self):
        out = geo.area_reply(self.result(1, 4, total=9000), "mil", 10, layers=["Top Layer"])
        self.assertIsNone(out["total"])
        self.assertTrue(out["truncated"])
        self.assertIn("9000", out["note"])
        # unfiltered, Altium's own count is the total
        out = geo.area_reply(self.result(2, 3, total=40), "mil", 5)
        self.assertEqual((out["total"], out["returned"], out["truncated"]), (40, 5, True))


class RefTest(unittest.TestCase):
    def test_refs_and_pad_shorthand(self):
        self.assertEqual(geo.normalize_ref(" via|1|2 "), "via|1|2")
        self.assertEqual(geo.normalize_ref("U1-3"), "pad|U1|3")
        self.assertEqual(geo.normalize_ref("U_LS-9-A1"), "pad|U_LS-9|A1")
        for bad in ("", "U1", "text|x|1", "-3"):
            with self.assertRaises(ValueError):
                geo.normalize_ref(bad)

    def test_units_and_clearance(self):
        self.assertEqual(geo.to_coord(1, "mil"), 10000)
        self.assertEqual(geo.to_coord(0.0254, "mm"), 10000)
        with self.assertRaises(ValueError):
            geo.to_coord(1, "inch")
        out = geo.describe_clearance({"distance": 78740, "rule": "Clearance", "rule_gap": 49213,
                                      "rule_violated": False}, "mm")
        self.assertEqual(out, {"distance": 0.2, "unit": "mm", "rule": "Clearance", "rule_gap": 0.125,
                               "violates_rule": False})
        self.assertIsNone(geo.describe_clearance({"distance": 1}, "mil")["rule"])


if __name__ == "__main__":
    unittest.main()
