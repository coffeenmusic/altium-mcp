"""Offline tests for the silkscreen solver (no Altium needed).

Run: python -m unittest discover -s server/tests -p "test_silkscreen.py"
"""

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
    def test_extent_and_rules(self):
        b = ss.Board(board_text(*resistor("R1", 1000, 1000)))
        self.assertEqual((b.s2s, b.s2m), (3.0, 4.0))
        self.assertEqual(b.components["R1"].extent, (968, 988, 1032, 1012))
        self.assertEqual(b.components["R1"].text_size0(), (90, 40))

    def test_board_sized_cutout_is_ignored(self):
        # Altium reports the 'Layer Stack Region' with the board-cutout kind
        b = ss.Board(board_text("K|0|0|2000|0|2000|2000|0|2000", "K|100|100|200|100|200|200|100|200"))
        self.assertEqual(len(b.cutouts), 1)

    def test_vertical_text_size(self):
        b = ss.Board(board_text(*resistor("R1", 1000, 1000, text_rot=90)))
        self.assertEqual(b.components["R1"].text_size0(), (90, 40))


class PlannerTests(unittest.TestCase):
    def test_single_part_goes_above(self):
        b = ss.Board(board_text(*resistor("R1", 1000, 1000)))
        plan = ss.plan_silkscreen(b)
        cand = plan["placements"]["R1"]
        self.assertEqual((cand.side_name, cand.rotation), ("N", 0))
        self.assertAlmostEqual(cand.center[0], 1000.0)
        self.assertTrue(legal(plan["placer"], b.components["R1"], cand))
        # Tight: just outside the pads' mask clearance
        self.assertLess(cand.rect[1] - 1012, 10)

    def test_avoids_silk_above(self):
        lines = resistor("R1", 1000, 1000) + ["ST|T||900|1030|1100|1030|8"]
        b = ss.Board(board_text(*lines))
        plan = ss.plan_silkscreen(b)
        cand = plan["placements"]["R1"]
        self.assertTrue(legal(plan["placer"], b.components["R1"], cand))
        self.assertNotEqual(cand.side_name, "N")

    def test_vertical_part_gets_vertical_text(self):
        b = ss.Board(board_text(*resistor("R1", 1000, 1000, vertical=True)))
        cand = ss.plan_silkscreen(b)["placements"]["R1"]
        self.assertEqual(cand.rotation, 90)

    def test_bottom_side_readable_rotations(self):
        b = ss.Board(board_text(*resistor("R1", 1000, 1000, side="B", vertical=True)))
        cand = ss.plan_silkscreen(b)["placements"]["R1"]
        self.assertIn(cand.rotation, (0, 270))

    def test_neighbours_do_not_collide(self):
        lines = []
        for i in range(8):
            lines += resistor(f"C{i + 1}", 800 + 60 * i, 1000, vertical=True)
        b = ss.Board(board_text(*lines))
        plan = ss.plan_silkscreen(b)
        placed = list(plan["placements"].values())
        for i, a in enumerate(placed):
            self.assertTrue(legal(plan["placer"], b.components[a.designator], a))
            for c in placed[i + 1:]:
                self.assertGreaterEqual(ss._rect_rect_dist(a.rect, c.rect), 3.0)

    def test_labels_never_sit_closer_to_a_same_type_part(self):
        lines = []
        for i in range(6):
            lines += resistor(f"R{i + 1}", 800 + 110 * i, 1000)
        b = ss.Board(board_text(*lines))
        plan = ss.plan_silkscreen(b)
        placer = plan["placer"]
        for des, cand in plan["placements"].items():
            gap, d_own, _, d_other, _, d_same = placer.association(b.components[des], cand.rect)
            self.assertFalse(placer.misleading(gap, d_own, d_other, d_same), des)

    def test_stays_inside_board(self):
        b = ss.Board(board_text(*resistor("R1", 40, 1000, vertical=True)))
        plan = ss.plan_silkscreen(b)
        cand = plan["placements"]["R1"]
        self.assertGreaterEqual(cand.rect[0], 10.0)

    def test_keeps_a_valid_position(self):
        lines = resistor("R1", 1000, 1000)
        # Move the current text box to a legal spot just above the part
        c = lines[0].split("|")
        c[11:15] = ["955", "1020", "1045", "1060"]
        lines[0] = "|".join(c)
        b = ss.Board(board_text(*lines))
        plan = ss.plan_silkscreen(b)
        self.assertEqual(plan["kept"], ["R1"])
        plan = ss.plan_silkscreen(b, options=ss.Options(keep_valid=False))
        self.assertEqual(plan["kept"], [])

    def test_boxed_in_part_is_reported(self):
        lines = resistor("R1", 1000, 1000)
        for y in (955, 1045):
            lines.append(f"ST|T||850|{y}|1150|{y}|10")
        for x in (905, 1095):
            lines.append(f"ST|T||{x}|940|{x}|1060|10")
        b = ss.Board(board_text(*lines))
        # Within 30 mil of the part there is no room inside or outside the box
        plan = ss.plan_silkscreen(b, options=ss.Options(max_gap=30))
        self.assertIn("R1", plan["unplaced"])
        self.assertIn("silk track", plan["unplaced"]["R1"])

    def test_shrinks_when_allowed(self):
        lines = resistor("R1", 1000, 1000)
        # A 36 mil channel above the part: fits a 33 mil box (25 mil text),
        # not the 40 mil box of 30 mil text
        lines += ["ST|T||800|1058|1200|1058|4", "ST|T||800|945|1200|945|4",
                  "ST|T||930|960|930|1060|4", "ST|T||1070|960|1070|1060|4"]
        b = ss.Board(board_text(*lines))
        self.assertIn("R1", ss.plan_silkscreen(b, options=ss.Options(max_gap=30))["unplaced"])
        plan = ss.plan_silkscreen(b, options=ss.Options(max_gap=30, min_height=20))
        cand = plan["placements"]["R1"]
        self.assertEqual(cand.note, "reduced height")
        self.assertLess(cand.height, 30)

    def test_scope_leaves_other_designators_as_obstacles(self):
        lines = resistor("R1", 1000, 1000) + resistor("R2", 1000, 1150)
        b = ss.Board(board_text(*lines))
        plan = ss.plan_silkscreen(b, ["R1"])
        self.assertEqual(set(plan["placements"]), {"R1"})
        r2_box = b.components["R2"].text_box
        self.assertGreaterEqual(ss._rect_rect_dist(plan["placements"]["R1"].rect, r2_box), 3.0)

    def test_preview_renders(self):
        b = ss.Board(board_text(*resistor("R1", 1000, 1000)))
        plan = ss.plan_silkscreen(b)
        png = ss.render_preview(b, {d: (c.rect, c.rotation) for d, c in plan["placements"].items()})
        self.assertTrue(png.startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
