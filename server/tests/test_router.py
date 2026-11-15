"""Unit tests for router (pure Python, no Altium needed).

Run from server/:  python -m unittest tests.test_router -v
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import router  # noqa: E402

TOP, BOT = "Top Layer", "Bottom Layer"


def board(area=(0, 0, 200, 100), layers=(TOP,), nets=("A",)):
    return router.Board(list(area), 2.5, list(layers), 6, 6, 24, set(nets))


def segments(legs):
    return [(l, a, b) for l, pts in legs for a, b in zip(pts, pts[1:])]


def angle_ok(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    return dx == 0 or dy == 0 or abs(abs(dx) - abs(dy)) < 1e-6


class ShapeTest(unittest.TestCase):
    def test_distances(self):
        seg = router.Shape("seg", "A", [TOP], ax=0, ay=0, bx=10, by=0, hw=3)
        self.assertAlmostEqual(seg.dist(5, 10), 7)
        rect = router.Shape("rect", "A", [TOP], cx=0, cy=0, hx=5, hy=2, cos=1, sin=0)
        self.assertAlmostEqual(rect.dist(8, 0), 3)
        self.assertEqual(rect.dist(1, 1), 0)
        rot = router.Shape("rect", "A", [TOP], cx=0, cy=0, hx=5, hy=2, cos=0, sin=1)   # turned 90 degrees
        self.assertAlmostEqual(rot.dist(0, 8), 3)
        poly = router.Shape("poly", "A", [TOP], pts=[(0, 0), (10, 0), (10, 10), (0, 10)])
        self.assertEqual(poly.dist(5, 5), 0)
        self.assertAlmostEqual(poly.dist(15, 5), 5)

    def test_shapes_from_layout(self):
        lay = {"pads": [{"designator": "U1", "pad": "1", "x": 10, "y": 10, "layer": "Multi Layer", "shape": "round",
                         "size": [60, 60], "rotation": 0, "hole": 35, "net": "GND"}],
               "vias": [{"net": "A", "x": 50, "y": 50, "diameter": 24, "hole": 12}],
               "arcs": [{"net": "B", "layer": TOP, "width": 6, "center": [0, 0], "radius": 20,
                         "start_angle": 0, "end_angle": 90}]}
        shapes = router.shapes_from_layout(lay, [TOP, BOT])
        self.assertEqual(shapes[0].layers, [TOP, BOT])   # a through-hole pad blocks every layer
        self.assertTrue(shapes[0].g.get("pad"))
        self.assertEqual([s.kind for s in shapes].count("seg"), len(shapes) - 2)


class RouteTest(unittest.TestCase):
    def test_straight(self):
        b = board()
        legs, vias = router.route_one(b, "A", (10, 50), (190, 50), [TOP], [TOP])
        self.assertEqual(vias, [])
        self.assertEqual(legs, [(TOP, [(10.0, 50.0), (190.0, 50.0)])])

    def test_goes_round_another_net_and_keeps_clearance(self):
        b = board(nets=("A", "B"))
        wall = router.Shape("seg", "B", [TOP], ax=100, ay=20, bx=100, by=100, hw=3)
        b.add(wall)
        legs, _ = router.route_one(b, "A", (20, 60), (180, 60), [TOP], [TOP])
        for _, a, c in segments(legs):
            self.assertTrue(angle_ok(a, c), (a, c))
            # every point of the centre line keeps clearance + half width from the wall
            for t in range(11):
                p = (a[0] + (c[0] - a[0]) * t / 10, a[1] + (c[1] - a[1]) * t / 10)
                self.assertGreaterEqual(wall.dist(*p), 6 + 3 - 1e-6)

    def test_own_net_copper_is_no_obstacle(self):
        b = board(nets=("A",))
        b.add(router.Shape("seg", "A", [TOP], ax=100, ay=0, bx=100, by=100, hw=3))
        legs, _ = router.route_one(b, "A", (10, 50), (190, 50), [TOP], [TOP])
        self.assertEqual(len(segments(legs)), 1)

    def test_changes_layer_through_a_via(self):
        b = board(layers=(TOP, BOT), nets=("A", "B"))
        b.add(router.Shape("seg", "B", [TOP], ax=100, ay=0, bx=100, by=100, hw=3))
        legs, vias = router.route_one(b, "A", (20, 50), (180, 50), [TOP], [TOP])
        self.assertEqual(len(vias), 2)
        self.assertEqual([l for l, _ in legs], [TOP, BOT, TOP])

    def test_walled_in_fails(self):
        b = board(nets=("A", "B"))
        b.add(router.Shape("seg", "B", [TOP], ax=100, ay=-10, bx=100, by=110, hw=3))
        self.assertIsNone(router.route_one(b, "A", (20, 50), (180, 50), [TOP], [TOP]))

    def test_grid_size_is_limited(self):
        with self.assertRaises(router.RouteError):
            router.Board([0, 0, 10000, 10000], 2.5, [TOP], 6, 6, 24, {"A"})


class FanoutTest(unittest.TestCase):
    def test_via_goes_outward(self):
        b = board(area=(0, 0, 300, 300), layers=(TOP, BOT), nets=("GND",))
        pad = {"x": 150, "y": 150, "size": [10, 30], "net": "GND"}
        (vx, vy), legs = router.fanout_site(b, pad, "GND", 8, 24, 80, (100, 150), [TOP])
        self.assertGreater(vx, 150)
        self.assertAlmostEqual(vy, 150)
        self.assertEqual(legs, [(TOP, [(150, 150), (vx, vy)])])

    def test_large_pad_gets_its_via_inside(self):
        b = board(area=(0, 0, 300, 300), layers=(TOP, BOT), nets=("GND",))
        pad = {"x": 150, "y": 150, "size": [100, 100], "net": "GND"}
        (vx, vy), legs = router.fanout_site(b, pad, "GND", 8, 24, 80, (150, 150), [TOP])
        self.assertEqual(((vx, vy), legs), ((150, 150), []))

    def test_blocked_on_every_side(self):
        b = board(area=(0, 0, 300, 300), layers=(TOP, BOT), nets=("GND", "X"))
        b.add(router.Shape("circle", "X", [TOP, BOT], cx=150, cy=150, r=200))
        pad = {"x": 150, "y": 150, "size": [10, 10], "net": "GND"}
        self.assertIsNone(router.fanout_site(b, pad, "GND", 8, 24, 80, (100, 150), [TOP]))


if __name__ == "__main__":
    unittest.main()
