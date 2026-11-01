"""Unit tests for pcb_shapes (pure Python, no Altium needed).

Run from server/:  python -m unittest tests.test_pcb_shapes -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from pcb_shapes import encode, outline_points  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
