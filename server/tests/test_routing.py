"""Unit tests for routing (pure Python, no Altium needed).

Run from server/:  python -m unittest tests.test_routing -v
"""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import routing  # noqa: E402

MIL = routing.COORD_PER_MIL


def track(net, x1, y1, x2, y2, layer="Top Layer"):
    return {"layer": layer, "net": net, "x1": x1, "y1": y1, "x2": x2, "y2": y2, "width": 10 * MIL}


def geometry(tracks, vias=(), pads=()):
    return {"tracks": list(tracks), "arcs": [], "vias": list(vias), "pads": list(pads)}


class AngleTest(unittest.TestCase):
    def test_classes(self):
        self.assertEqual(routing.angle_class(100, 0)[0], "orthogonal")
        self.assertEqual(routing.angle_class(0, -100)[0], "orthogonal")
        self.assertEqual(routing.angle_class(70, -70)[0], "45")
        self.assertEqual(routing.angle_class(0, 0)[0], "zero-length")
        kind, off = routing.angle_class(100, 10)
        self.assertEqual(kind, "off-angle")
        self.assertAlmostEqual(off, 5.71, places=2)

    def test_tolerance(self):
        # A 45 degree diagonal ending 0.1 mil off over 20 mil is ~0.14 deg out
        self.assertEqual(routing.angle_class(20 * MIL, 20 * MIL + 1000, tol_deg=0.1)[0], "off-angle")
        self.assertEqual(routing.angle_class(20 * MIL, 20 * MIL + 1000, tol_deg=0.2)[0], "45")


class CheckTest(unittest.TestCase):
    def test_off_angle_stub_and_sharp_corners(self):
        geo = geometry([
            track("A", 0, 0, 100 * MIL, 0), track("A", 100 * MIL, 0, 100 * MIL, 100 * MIL),  # 90 corner
            track("B", 0, 500 * MIL, 50 * MIL, 500 * MIL), track("B", 50 * MIL, 500 * MIL, 80 * MIL, 530 * MIL),  # 45
            track("C", 0, 900 * MIL, 34 * MIL, 937 * MIL),  # 47.4 deg stub
            track("D", 0, 1200 * MIL, 100 * MIL, 1200 * MIL), track("D", 100 * MIL, 1200 * MIL, 0, 1250 * MIL),  # acute
        ])
        r = routing.check(geo)
        self.assertEqual(r["segment_angles"], {"orthogonal": 4, "45": 1, "off-angle": 2})
        self.assertEqual([s["net"] for s in r["off_angle"]], ["C", "D"])
        self.assertEqual(r["corner_turns"], {"90": 1, "45": 1, "acute": 1})
        corner = next(c for c in r["sharp_corners"] if c["net"] == "A")
        self.assertEqual((corner["kind"], corner["at_mils"]), ("90", [100.0, 0.0]))

    def test_corner_under_via_or_rotated_pad_is_no_corner(self):
        tracks = [track("A", 0, 0, 100 * MIL, 0), track("A", 100 * MIL, 0, 100 * MIL, 100 * MIL)]
        self.assertEqual(routing.check(geometry(tracks, vias=[(100 * MIL, 0, 20 * MIL)]))["sharp_corners"], [])
        self.assertEqual(routing.check(geometry(tracks, pads=[(100 * MIL, 5 * MIL, 4 * MIL, 30 * MIL)]))["sharp_corners"], [])

    def test_different_nets_or_layers_do_not_make_corners(self):
        geo = geometry([track("A", 0, 0, 100 * MIL, 0), track("B", 100 * MIL, 0, 100 * MIL, 100 * MIL),
                        track("A", 0, 0, 0, 100 * MIL, layer="Bottom Layer")])
        self.assertEqual(routing.check(geo)["corner_turns"], {})

    def test_nets_filter(self):
        geo = geometry([track("GND", 0, 0, 34 * MIL, 37 * MIL), track("SIG", 0, 0, 30 * MIL, 37 * MIL)])
        self.assertEqual([s["net"] for s in routing.check(geo, nets=["sig"])["off_angle"]], ["SIG"])

    def test_parse_geometry_relative_to_origin(self):
        text = "\n".join(["O|1000|2000", "T|Top Layer|GND|1100|2100|1200|2100|50",
                          "T|Mid Layer 2|NET|A|1100|2100|1200|2100|50", "A|Top Layer|GND",
                          "V|1500|2500|30", "P|1600|2600|40|20"])
        geo = routing.parse_geometry(text)
        self.assertEqual(geo["tracks"], [
            {"layer": "Top Layer", "net": "GND", "x1": 100, "y1": 100, "x2": 200, "y2": 100, "width": 50},
            {"layer": "Mid Layer 2", "net": "NET|A", "x1": 100, "y1": 100, "x2": 200, "y2": 100, "width": 50}])
        self.assertEqual((geo["vias"], geo["pads"], len(geo["arcs"])), ([(500, 500, 30)], [(600, 600, 40, 20)], 1))


class RouteTest(unittest.TestCase):
    def assert_clean(self, segments, points):
        for x1, y1, x2, y2 in segments:
            self.assertIn(routing.angle_class(x2 - x1, y2 - y1)[0], ("orthogonal", "45"))
        for a, b in zip(segments, segments[1:]):
            self.assertEqual(a[2:], b[:2])  # connected end to start
        self.assertEqual(segments[0][:2], points[0])
        self.assertEqual(segments[-1][2:], points[-1])

    def test_straight_then_diagonal(self):
        pts = [(0, 0), (100 * MIL, 40 * MIL)]
        segs = routing.route(pts)
        self.assertEqual(segs, [(0, 0, 60 * MIL, 0), (60 * MIL, 0, 100 * MIL, 40 * MIL)])
        self.assert_clean(segs, pts)

    def test_diagonal_first(self):
        segs = routing.route([(0, 0), (-40 * MIL, 100 * MIL)], diagonal_first=True)
        self.assertEqual(segs, [(0, 0, -40 * MIL, 40 * MIL), (-40 * MIL, 40 * MIL, -40 * MIL, 100 * MIL)])

    def test_odd_metric_coordinates_stay_exact(self):
        # A 5.08 mm pitch pad to a point on a metric grid: the diagonal is
        # still exactly 45 degrees, whatever the units do
        pts = [(1999998, 0), (2401574, -350000)]
        segs = routing.route(pts)
        self.assert_clean(segs, pts)

    def test_points_in_line_merge_into_one_segment(self):
        segs = routing.route([(0, 0), (50 * MIL, 0), (50 * MIL, 0), (100 * MIL, 0)])
        self.assertEqual(segs, [(0, 0, 100 * MIL, 0)])

    def test_chamfer_cuts_right_angles(self):
        segs = routing.route([(0, 0), (100 * MIL, 0), (100 * MIL, 100 * MIL)], chamfer=20 * MIL)
        self.assertEqual(segs, [(0, 0, 80 * MIL, 0), (80 * MIL, 0, 100 * MIL, 20 * MIL),
                                (100 * MIL, 20 * MIL, 100 * MIL, 100 * MIL)])
        geo = geometry(track("N", *s) for s in segs)
        self.assertEqual(routing.check(geo)["sharp_corners"], [])

    def test_chamfer_at_most_half_the_shorter_leg(self):
        segs = routing.route([(0, 0), (10 * MIL, 0), (10 * MIL, 100 * MIL)], chamfer=50 * MIL)
        self.assertEqual(segs[0], (0, 0, 5 * MIL, 0))

    def test_random_routes_are_clean(self):
        rnd = random.Random(7)
        for _ in range(200):
            pts = [(rnd.randint(-10**7, 10**7), rnd.randint(-10**7, 10**7)) for _ in range(rnd.randint(2, 6))]
            if any(a == b for a, b in zip(pts, pts[1:])):
                continue
            for first in (False, True):
                self.assert_clean(routing.route(pts, diagonal_first=first), pts)
                self.assert_clean(routing.route(pts, diagonal_first=first, chamfer=rnd.randint(1, 10**6)), pts)


if __name__ == "__main__":
    unittest.main()
