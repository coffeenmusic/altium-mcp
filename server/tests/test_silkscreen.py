"""Offline tests for the silkscreen agent support (no Altium needed).

Run: python -m unittest discover -s server/tests -p "test_silkscreen.py"
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import silkscreen as ss  # noqa: E402


def board_text(*lines, size=2000):
    """A square board with 3 mil silk-to-silk and 4 mil silk-to-mask rules."""
    outline = [f"O|0|{x}|{y}|0|0|0|0|0" for x, y in ((0, 0), (size, 0), (size, size), (0, size))]
    return "\n".join(["V|1", "RULE|S2S|3", "RULE|S2M|4"] + outline + list(lines))


def resistor(des, x, y, side="T", vertical=False, text_rot=0):
    """A 0402-like two-pad part with its designator centred on it."""
    rot = 90 if vertical else 0
    if vertical:
        pads = [(x, y - 20), (x, y + 20)]
    else:
        pads = [(x - 20, y), (x + 20, y)]
    lines = []
    for i, (px, py) in enumerate(pads, 1):
        lines.append(f"MP|{side}|{des}|{i}|{px}|{py}|{rot}|2|24|24|{px - 12}|{py - 12}|{px + 12}|{py + 12}")
    tw, th = (40, 90) if text_rot % 180 == 90 else (90, 40)
    ext = (min(p[0] for p in pads) - 12, min(p[1] for p in pads) - 12,
           max(p[0] for p in pads) + 12, max(p[1] for p in pads) + 12)
    mirror = "1" if side == "B" else "0"
    lines.insert(0, f"C|{des}|{side}|{x}|{y}|{rot}|1|{ext[0]}|{ext[1]}|{ext[2]}|{ext[3]}|"
                    f"{x - tw / 2}|{y - th / 2}|{x + tw / 2}|{y + th / 2}|{text_rot}|30|5|5|{mirror}|RES0402")
    return lines


def legal(placer, comp, cand):
    return not placer.blockers(cand.rect, comp.side)


class GeometryTests(unittest.TestCase):
    def test_rect_segment_distance(self):
        r = (0, 0, 10, 10)
        self.assertEqual(ss._rect_seg_dist(r, -5, 5, 15, 5), 0.0)          # crosses
        self.assertAlmostEqual(ss._rect_seg_dist(r, 13, -5, 13, 20), 3.0)  # beside
        self.assertAlmostEqual(ss._rect_seg_dist(r, 13, 14, 20, 20), 5.0)  # off a corner

    def test_obstacle_shapes(self):
        r = (0, 0, 10, 10)
        circle = ss.Obstacle((12, 0, 16, 4), ss.CIRCLE, (14, 2, 2), ss.MASK, "", "via")
        self.assertAlmostEqual(circle.distance(r), 2.0)
        capsule = ss._seg_obstacle(20, -5, 20, 20, 3.0, ss.SILK, "", "track")
        self.assertAlmostEqual(capsule.distance(r), 7.0)
        diamond = ss.Obstacle((12, 0, 20, 10), ss.POLY, [(16, 1), (20, 5), (16, 9), (12, 5)], ss.MASK, "", "pad")
        self.assertAlmostEqual(diamond.distance(r), 2.0)

    def test_polygon_index(self):
        idx = ss.PolygonIndex([(0, 0), (100, 0), (100, 100), (50, 150), (0, 100)])
        self.assertTrue(idx.contains(50, 120))
        self.assertFalse(idx.contains(90, 140))


class ParseTests(unittest.TestCase):
    def test_truetype_box_is_kept(self):
        lines = resistor("R1", 1000, 1000)
        lines[0] += "|1"
        b = ss.Board(board_text(*lines))
        self.assertEqual(b.components["R1"].text_size0(), (90, 40))

    def test_extent_and_rules(self):
        b = ss.Board(board_text(*resistor("R1", 1000, 1000)))
        self.assertEqual((b.s2s, b.s2m), (3.0, 4.0))
        self.assertEqual(b.components["R1"].extent, (968, 988, 1032, 1012))
        # Altium's 90x40 box, less half the 5 mil stroke on each side
        self.assertEqual(b.components["R1"].text_size0(), (85, 35))

    def test_board_sized_cutout_is_ignored(self):
        # Altium reports the 'Layer Stack Region' with the board-cutout kind
        b = ss.Board(board_text("K|0|0|2000|0|2000|2000|0|2000", "K|100|100|200|100|200|200|100|200"))
        self.assertEqual(len(b.cutouts), 1)

    def test_vertical_text_size(self):
        b = ss.Board(board_text(*resistor("R1", 1000, 1000, text_rot=90)))
        self.assertEqual(b.components["R1"].text_size0(), (85, 35))


def best(board, des, **kw):
    """The top-ranked option for one designator."""
    return ss.options_for(board, [des], **kw)[des]["options"][0]


class OptionTests(unittest.TestCase):
    def test_single_part_goes_above(self):
        b = ss.Board(board_text(*resistor("R1", 1000, 1000)))
        cand = best(b, "R1")
        self.assertEqual((cand.side_name, cand.rotation), ("N", 0))
        self.assertAlmostEqual(cand.center[0], 1000.0)
        self.assertEqual(ss.evaluate(b, {"R1": (cand.rect, cand.rotation)})["R1"], [])
        # Tight: just outside the pads' mask clearance
        self.assertLess(cand.rect[1] - 1012, 10)

    def test_options_are_distinct_and_legal(self):
        b = ss.Board(board_text(*resistor("R1", 1000, 1000)))
        opts = ss.options_for(b, ["R1"], count=5)["R1"]["options"]
        self.assertGreaterEqual(len(opts), 4)     # above, below, left, right
        for i, o in enumerate(opts):
            self.assertEqual(ss.evaluate(b, {"R1": (o.rect, o.rotation)})["R1"], [])
            area = (o.rect[2] - o.rect[0]) * (o.rect[3] - o.rect[1])
            for p in opts[i + 1:]:
                self.assertLessEqual(ss.rect_overlap_area(o.rect, p.rect), 0.3 * area)

    def test_avoids_silk_above(self):
        lines = resistor("R1", 1000, 1000) + ["ST|T||900|1030|1100|1030|8"]
        b = ss.Board(board_text(*lines))
        cand = best(b, "R1")
        self.assertNotEqual(cand.side_name, "N")
        self.assertEqual(ss.evaluate(b, {"R1": (cand.rect, cand.rotation)})["R1"], [])

    def test_vertical_part_gets_vertical_text(self):
        b = ss.Board(board_text(*resistor("R1", 1000, 1000, vertical=True)))
        self.assertEqual(best(b, "R1").rotation, 90)

    def test_bottom_side_readable_rotations(self):
        b = ss.Board(board_text(*resistor("R1", 1000, 1000, side="B", vertical=True)))
        self.assertIn(best(b, "R1").rotation, (0, 270))

    def test_other_designators_are_obstacles(self):
        r1 = resistor("R1", 1000, 1000)
        r2 = resistor("R2", 1000, 1150)
        c = r2[0].split("|")
        c[11:15] = ["955", "1017", "1045", "1057"]      # R2's label sits right above R1
        r2[0] = "|".join(c)
        b = ss.Board(board_text(*(r1 + r2)))
        cand = best(b, "R1")
        self.assertGreaterEqual(ss._rect_rect_dist(cand.rect, b.components["R2"].text_box), 3.0)

    def test_labels_never_sit_closer_to_a_same_type_part(self):
        lines = []
        for i in range(6):
            lines += resistor(f"R{i + 1}", 800 + 110 * i, 1000)
        b = ss.Board(board_text(*lines))
        placer = ss.Placer(b, list(b.components), ss.Options())
        for des, found in ss.options_for(b, list(b.components)).items():
            for cand in found["options"]:
                comp = b.components[des]
                gap, d_own, other, d_other, _, d_same = placer.association(comp, cand.rect)
                self.assertFalse(placer.misleading(comp, gap, d_own, other, d_other, d_same), des)

    def test_stays_inside_board(self):
        b = ss.Board(board_text(*resistor("R1", 40, 1000, vertical=True)))
        self.assertGreaterEqual(best(b, "R1").rect[0], 10.0)

    def test_board_outline_clearance_rule(self):
        # 25 mil text-to-edge rule, measured by Altium from the text's bounding
        # box (ink box + half the 5 mil stroke)
        b = ss.Board(board_text("RULE|EDGE|25|25", *resistor("R1", 40, 1000, vertical=True)))
        self.assertEqual((b.edge, b.edge_cut), (25.0, 25.0))
        placer = ss.Placer(b, ["R1"], ss.Options(extra_clearance=0.1))
        self.assertAlmostEqual(placer.clearance[ss.EDGE], 27.6)
        self.assertGreaterEqual(best(b, "R1").rect[0], 27.5)
        problems = ss.evaluate(b, {"R1": ((15, 960, 50, 1040), 90)})["R1"]
        self.assertTrue(any("board edge" in p for p in problems), problems)

    def test_boxed_in_part_reports_blockers(self):
        lines = resistor("R1", 1000, 1000)
        for y in (955, 1045):
            lines.append(f"ST|T||850|{y}|1150|{y}|10")
        for x in (905, 1095):
            lines.append(f"ST|T||{x}|940|{x}|1060|10")
        b = ss.Board(board_text(*lines))
        found = ss.options_for(b, ["R1"], options=ss.Options(max_gap=30))["R1"]
        self.assertEqual(found["options"], [])
        self.assertTrue(any("silk track" in x for x in found["blocked_by"]))

    def test_smaller_text_when_allowed(self):
        lines = resistor("R1", 1000, 1000)
        # A 33 mil channel above the part: fits the 29 mil ink of 25 mil
        # text, not the 35 mil ink of 30 mil text
        lines += ["ST|T||800|1055|1200|1055|4", "ST|T||800|945|1200|945|4",
                  "ST|T||930|960|930|1060|4", "ST|T||1070|960|1070|1060|4"]
        b = ss.Board(board_text(*lines))
        self.assertEqual(ss.options_for(b, ["R1"], options=ss.Options(max_gap=30))["R1"]["options"], [])
        cand = best(b, "R1", options=ss.Options(max_gap=30, min_height=20))
        self.assertEqual(cand.note, "reduced height")
        self.assertLess(cand.height, 30)


class EvaluateTests(unittest.TestCase):
    def test_label_on_its_part_is_flagged(self):
        b = ss.Board(board_text(*resistor("R1", 1000, 1000)))
        box = b.components["R1"].text_box                # centred on the part
        self.assertTrue(any("overlaps pad R1-1" in p for p in ss.evaluate(b, {"R1": (box, 0)})["R1"]))
        quality = ss.evaluate(b, {"R1": (box, 0)}, geometry=False)["R1"]
        self.assertFalse(any("pad" in p for p in quality))

    def test_designators_hidden_in_the_same_step_are_not_obstacles(self):
        # R2's label sits where R1's label is proposed; hiding R2 frees it
        lines = resistor("R1", 1000, 1000) + resistor("R2", 1000, 1150)
        b = ss.Board(board_text(*lines))
        spot = b.components["R2"].text_box
        self.assertTrue(ss.evaluate(b, {"R1": (spot, 0)})["R1"])
        problems = ss.evaluate(b, {"R1": (spot, 0)}, hiding={"R2"})["R1"]
        self.assertFalse([p for p in problems if "designator R2" in p], problems)

    def test_proposals_are_checked_against_each_other(self):
        b = ss.Board(board_text(*(resistor("R1", 1000, 1000) + resistor("R2", 1000, 1150))))
        same = (955, 1017, 1045, 1057)
        problems = ss.evaluate(b, {"R1": (same, 0), "R2": (same, 0)})
        self.assertIn("overlaps designator R2", problems["R1"])

    def test_upside_down_and_misleading(self):
        b = ss.Board(board_text(*(resistor("R1", 1000, 1000) + resistor("R2", 1110, 1000))))
        rect = (1065, 1017, 1155, 1057)                  # right above R2
        problems = ss.evaluate(b, {"R1": (rect, 180)})["R1"]
        self.assertTrue(any("upside down" in p for p in problems))
        self.assertTrue(any("R2" in p for p in problems))


class ResolveTests(unittest.TestCase):
    def test_relative_sides(self):
        b = ss.Board(board_text(*resistor("R1", 1000, 1000)))
        e = b.components["R1"].extent
        rect, rot, _, _ = ss.resolve_placement(b, {"designator": "R1", "side": "above", "gap": 5})
        self.assertAlmostEqual(rect[1], e[3] + 5)
        self.assertAlmostEqual((rect[0] + rect[2]) / 2, 1000)
        rect, rot, _, _ = ss.resolve_placement(b, {"designator": "R1", "side": "left", "gap": 6, "rotation": 90})
        self.assertAlmostEqual(rect[2], e[0] - 6)
        self.assertAlmostEqual(rect[2] - rect[0], 35)    # vertical text: the box turns
        self.assertEqual(rot, 90)

    def test_absolute_and_smaller(self):
        b = ss.Board(board_text(*resistor("R1", 1000, 1000)))
        rect, _, height, _ = ss.resolve_placement(b, {"designator": "R1", "x": 500, "y": 600, "height": 25})
        self.assertAlmostEqual((rect[0] + rect[2]) / 2, 500)
        self.assertEqual(height, 25)
        self.assertLess(rect[3] - rect[1], 35)

    def test_upside_down_current_rotation_is_not_kept(self):
        b = ss.Board(board_text(*resistor("R1", 1000, 1000, text_rot=180)))
        _, rot, _, _ = ss.resolve_placement(b, {"designator": "R1", "side": "below"})
        self.assertEqual(rot, 0)

    def test_bad_side(self):
        b = ss.Board(board_text(*resistor("R1", 1000, 1000)))
        with self.assertRaises(ValueError):
            ss.resolve_placement(b, {"designator": "R1", "side": "diagonal"})


class ModuleAndRotatedTextTests(unittest.TestCase):
    def test_parts_under_a_module_are_not_flagged_for_it(self):
        # A big module body (a SOM) covers R1: R1's label may sit under it too
        lines = resistor("R1", 1000, 1000) + [
            "C|U9|T|1000|1000|0|1|600|600|1400|1400|500|500|560|540|0|60|6|5|0|SOM",
            "Y|U9|600|600|1400|1400"]
        b = ss.Board(board_text(*lines))
        cand = best(b, "R1")
        self.assertEqual(ss.evaluate(b, {"R1": (cand.rect, cand.rotation)}, geometry=False)["R1"], [])

    def test_rotated_text_is_a_rotated_rectangle(self):
        # 300 x 35 mil text at 320 deg: its axis-aligned box is mostly empty
        a = math.radians(320)
        c, s = abs(math.cos(a)), abs(math.sin(a))
        w, h = 300 * c + 35 * s, 300 * s + 35 * c
        box = (1000 - w / 2, 1000 - h / 2, 1000 + w / 2, 1000 + h / 2)
        poly = ss._rotated_text_polygon(box, 320)
        self.assertIsNotNone(poly)
        ob = ss.Obstacle(box, ss.POLY, poly, ss.SILK, "", "silk text")
        corner = (box[0], box[1], box[0] + 20, box[1] + 20)     # empty corner of the box
        self.assertGreater(ob.distance(corner), 20)
        self.assertEqual(ob.distance((990, 990, 1010, 1010)), 0.0)   # the text itself
        self.assertIsNone(ss._rotated_text_polygon((0, 0, 100, 30), 0))


class RenderTests(unittest.TestCase):
    def test_view_renders(self):
        b = ss.Board(board_text(*resistor("R1", 1000, 1000)))
        opts = ss.options_for(b, ["R1"])["R1"]["options"]
        png = ss.render_view(b, {"R1": (b.components["R1"].text_box, 0)},
                             focus=ss.focus_box(b, ["R1"]), targets=["R1"], options={"R1": opts})
        self.assertTrue(png.startswith(bytes([0x89]) + b"PNG"))


if __name__ == "__main__":
    unittest.main()
