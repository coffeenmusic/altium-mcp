"""Unit tests for pcb_shapes (pure Python, no Altium needed).

Run from server/:  python -m unittest tests.test_pcb_shapes -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from pcb_shapes import area_box, encode, outline_points, polygon_names, unmatched_names  # noqa: E402


class OutlineTest(unittest.TestCase):
    def test_square_in_internal_units(self):
        pts, err = outline_points([[0, 0], [100, 0], [100, 50.5], [0, 50.5]])
        self.assertEqual(err, "")
        self.assertEqual(pts, [(0, 0), (1000000, 0), (1000000, 505000), (0, 505000)])
        self.assertEqual(encode(pts), "0|0;1000000|0;1000000|505000;0|505000")

    def test_explicit_closing_corner_is_dropped(self):
        pts, _ = outline_points([[0, 0], [10, 0], [10, 10], [0, 0]])
        self.assertEqual(len(pts), 3)

    def test_concave_shape_is_fine(self):
        self.assertEqual(outline_points([[0, 0], [30, 0], [30, 30], [20, 30], [20, 10],
                                         [10, 10], [10, 30], [0, 30]])[1], "")

    def test_rejected(self):
        self.assertIn("three", outline_points([[0, 0], [1, 1]])[1])
        self.assertIn("repeats", outline_points([[0, 0], [10, 0], [10, 0], [0, 10]])[1])
        self.assertIn("no area", outline_points([[0, 0], [10, 0], [20, 0]])[1])
        self.assertIn("cross", outline_points([[0, 0], [20, 0], [20, 10], [10, -5], [0, 10]])[1])
        self.assertIn("[x, y]", outline_points([[0, 0, 0], [1, 1], [2, 0]])[1])
        self.assertIn("[x, y]", outline_points([["a", 0], [1, 1], [2, 0]])[1])


class AreaTest(unittest.TestCase):
    def test_area_box(self):
        self.assertEqual(area_box(None), ("", ""))
        self.assertEqual(area_box([100, 50, 0, 0]), ("0|0|1000000|500000", ""))
        self.assertIn("no size", area_box([0, 0, 0, 10])[1])
        self.assertIn("[x1, y1, x2, y2]", area_box([0, 0, 10])[1])
        self.assertIn("[x1, y1, x2, y2]", area_box([0, 0, float("inf"), 10])[1])

    def test_area_beyond_the_design_space_is_clamped(self):
        # unclamped, -1e7 mils is -1e11 internal units: past 32 bits in Altium
        self.assertEqual(area_box([-1e7, 0, 1e7, 10]), ("-1000000000|0|1000000000|100000", ""))


class PolygonNamesTest(unittest.TestCase):
    def test_encoding(self):
        self.assertEqual(polygon_names(None), ([], "", ""))
        self.assertEqual(polygon_names([" GND ", "", "VBAT"]), (["GND", "VBAT"], "|GND|VBAT|", ""))
        self.assertIn("'|'", polygon_names(["A|B"])[2])

    def test_unmatched_ignores_case_and_counts_shelved(self):
        result = {"polygons": [{"name": "GND"}], "shelved_skipped": ["P018 (Bottom Layer)"]}
        self.assertEqual(unmatched_names(["gnd", "p018", "NOPE"], result), ["NOPE"])


if __name__ == "__main__":
    unittest.main()
