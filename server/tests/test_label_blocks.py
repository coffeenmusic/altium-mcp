"""Offline tests for label blocks (no Altium needed).

Run: python -m unittest discover -s server/tests -p "test_label_blocks.py"
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import label_blocks as lb  # noqa: E402
import silkscreen as ss  # noqa: E402
from test_silkscreen import board_text, resistor  # noqa: E402


# Silk fills above and below the row, 45 mil deep: no room next to it
WALLS = ["SB|T||850|1036|1200|1080", "SB|T||850|920|1200|964"]


def row_of_resistors(n=5, x0=900, y=1000, pitch=60, side="T"):
    lines = []
    for i in range(n):
        lines += resistor(f"R{i + 1}", x0 + i * pitch, y, side=side, vertical=True)
    return lines


class RowTests(unittest.TestCase):
    def test_finds_the_row_in_order(self):
        b = ss.Board(board_text(*row_of_resistors()))
        axis, row = lb.find_row(b, "R3")
        self.assertEqual(axis, "x")
        self.assertEqual(row, ["R1", "R2", "R3", "R4", "R5"])

    def test_far_or_unaligned_parts_are_not_in_the_row(self):
        lines = row_of_resistors(3) + resistor("R9", 900 + 2 * 60 + 200, 1000, vertical=True) \
            + resistor("R8", 900 + 3 * 60, 1100, vertical=True)
        b = ss.Board(board_text(*lines))
        self.assertEqual(lb.find_row(b, "R1")[1], ["R1", "R2", "R3"])

    def test_mounting_holes_get_no_label(self):
        lines = row_of_resistors(3) + [l.replace("R4", "MT4") for l in resistor("R4", 900 + 3 * 60, 1000,
                                                                                 vertical=True)]
        b = ss.Board(board_text(*lines))
        self.assertEqual(lb.find_row(b, "R1")[1], ["R1", "R2", "R3"])

    def test_single_part_is_no_row(self):
        b = ss.Board(board_text(*resistor("R1", 1000, 1000)))
        self.assertEqual(lb.find_row(b, "R1"), (None, ["R1"]))


class BlockTests(unittest.TestCase):
    def test_open_space_gives_an_aligned_block_without_a_link(self):
        b = ss.Board(board_text(*row_of_resistors()))
        _, members, opts = lb.block_options(b, ["R1", "R2", "R3", "R4", "R5"], count=1)
        best = opts[0]
        self.assertEqual(best.link, "none")
        self.assertEqual(best.style, "aligned")
        xs = [(best.labels[d][0][0] + best.labels[d][0][2]) / 2 for d in members]
        self.assertEqual(xs, sorted(xs))                          # same order as the parts
        for d in members:                                         # each over its part
            e = b.components[d].extent
            self.assertTrue(e[0] <= xs[members.index(d)] <= e[2])

    def test_walled_in_row_gets_a_linked_block(self):
        # Silk walls just above and below the row: the labels must go elsewhere
        lines = row_of_resistors() + WALLS
        b = ss.Board(board_text(*lines))
        _, members, opts = lb.block_options(b, ["R1", "R2", "R3", "R4", "R5"], count=3)
        self.assertTrue(opts)
        for o in opts:
            self.assertIn(o.link, ("leader", "index"))
            self.assertTrue(o.graphics)
            placer = ss.Placer(b, members, ss.Options(extra_clearance=0.1))
            for d, (rect, _, _, _) in o.labels.items():
                self.assertFalse(placer.blockers(rect, "T"), d)

    def test_leader_points_at_the_row(self):
        lines = row_of_resistors() + WALLS
        b = ss.Board(board_text(*lines))
        _, _, opts = lb.block_options(b, ["R1", "R2", "R3", "R4", "R5"], count=3, link="leader")
        self.assertTrue(opts)
        g = opts[0].graphics
        self.assertGreaterEqual(len(g), 3)                        # line + two arrow strokes
        tip = (g[-1]["x1"], g[-1]["y1"])
        band = lb._bounds(b.components[d].extent for d in ("R1", "R2", "R3", "R4", "R5"))
        self.assertLess(ss._point_rect_dist(tip[0], tip[1], band), 10)

    def test_marker_letter_clears_its_shape(self):
        for shape in lb.SHAPES:
            g, box = lb.marker_graphics(0, 0, "W", shape)
            text = [x for x in g if x["kind"] == "text"][0]
            w, h = lb._letter_size("W", text["size"], text["stroke"])
            letter = (text["cx"] - w / 2, text["cy"] - h / 2, text["cx"] + w / 2, text["cy"] + h / 2)
            for x in g:
                if x["kind"] == "arc":
                    corner = math.hypot(max(abs(letter[0]), abs(letter[2])), max(abs(letter[1]), abs(letter[3])))
                    self.assertGreaterEqual(x["r"] - x["w"] / 2 - corner, 2.0, shape)
                elif x["kind"] == "track":
                    seg = ss._seg_obstacle(x["x1"], x["y1"], x["x2"], x["y2"], x["w"] / 2, ss.SILK, "", "")
                    self.assertGreaterEqual(seg.distance(letter), 2.0, shape)
            for x in g:
                for r in lb._graphic_rects(x):
                    self.assertTrue(box[0] - 0.01 <= r[0] and r[2] <= box[2] + 0.01, shape)

    def test_markers_are_unique(self):
        used = {("A", "circle"), ("B", "circle")}
        self.assertEqual(lb.next_marker(used), ("C", "circle"))
        everything = {(l, "circle") for l in lb.LETTERS}
        self.assertEqual(lb.next_marker(everything), ("A", "square"))

    def test_items_for_altium(self):
        g, _ = lb.marker_graphics(100, 200, "A", "circle")
        items = lb.to_items("B", g)
        self.assertEqual(items[0], "+A|B|100|200|23|0|360|4.0")
        self.assertTrue(items[1].startswith("+X|B|100|200|25.0|4.0|0|A"))
        self.assertTrue(lb.to_items("B", g, "-")[0].startswith("-A|"))


class RecordTests(unittest.TestCase):
    def test_record_is_intact_until_disturbed(self):
        lines = row_of_resistors() + WALLS
        b = ss.Board(board_text(*lines))
        _, _, opts = lb.block_options(b, ["R1", "R2", "R3", "R4", "R5"], count=1)
        opt = opts[0]
        rec = lb.record(opt)
        # The board as it would be exported after placing the block
        extra = []
        for d, (rect, rot, _, _) in opt.labels.items():
            c = b.components[d]
            c.text_box, c.text_rotation = rect, rot
        for g in opt.graphics:
            if g["kind"] == "track":
                b.free_silk.append(("T", "T", (g["x1"], g["y1"], g["x2"], g["y2"], g["w"])))
            elif g["kind"] == "arc":
                b.free_silk.append(("T", "A", (g["cx"], g["cy"], g["r"], g["a1"], g["a2"], g["w"])))
            else:
                b.free_silk.append(("T", "X", (g["cx"], g["cy"], g["text"])))
        self.assertEqual(lb.check_record(b, rec), [])
        b.components["R3"].text_box = tuple(v + 20 for v in b.components["R3"].text_box)
        self.assertTrue(any("R3" in p for p in lb.check_record(b, rec)))
        b.free_silk.pop()
        self.assertTrue(any("missing" in p for p in lb.check_record(b, rec)))

    def test_association_problems(self):
        self.assertTrue(lb.is_association_problem("93 mil from its part, in line with it"))
        self.assertTrue(lb.is_association_problem("reads as R2's label: R2 is closer"))
        self.assertFalse(lb.is_association_problem("over part J4"))


if __name__ == "__main__":
    unittest.main()
