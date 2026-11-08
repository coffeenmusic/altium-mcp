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


class ViaRecordsTest(unittest.TestCase):
    def test_defaults_and_overrides(self):
        recs, err = routing.via_records([[100, 200.5], {"x": 0, "y": 0, "net": "", "diameter_mils": 30,
                                                       "hole_mils": 15, "end_layer": "Inner 1"}],
                                        20, 10, net="GND")
        self.assertEqual(err, "")
        self.assertEqual(recs, ["1000000|2005000|200000|100000|GND|Top Layer|Bottom Layer",
                                "0|0|300000|150000||Top Layer|Inner 1"])

    def test_rejects_bad_input(self):
        self.assertIn("smaller than", routing.via_records([[0, 0]], 10, 10)[1])
        self.assertIn("expected", routing.via_records([[0]], 20, 10)[1])
        self.assertIn("expected", routing.via_records([{"x": 0, "y": 0, "size": 1}], 20, 10)[1])
        self.assertIn("numbers", routing.via_records([["a", 0]], 20, 10)[1])
        self.assertIn("cannot be sent", routing.via_records([[0, 0]], 20, 10, net="A|B")[1])
        self.assertEqual(routing.via_records([], 20, 10), (None, "vias is empty"))


class PlanPathTest(unittest.TestCase):
    def test_layer_change_at_the_end_is_refused(self):
        with self.assertRaisesRegex(ValueError, "lead nowhere"):
            routing.plan_path((0, 0, "Top Layer"), [(10, 10, None), (10, 10, "Bottom Layer")])

    def test_layer_change_drops_a_via(self):
        tracks, vias = routing.plan_path((0, 0, "Top Layer"),
                                         [(1000, 500, None), (1000, 500, "Bottom Layer"), (2000, 500, None)])
        self.assertEqual(tracks, [("Top Layer", 0, 0, 500, 0), ("Top Layer", 500, 0, 1000, 500),
                                  ("Bottom Layer", 1000, 500, 2000, 500)])
        self.assertEqual(vias, [(1000, 500, "Top Layer", "Bottom Layer")])

    def test_via_between_the_two_layers(self):
        tracks, vias = routing.plan_path((0, 0, "Top Layer"), [(500, 0, "Mid Layer 1"), (500, 800, None)],
                                         via_span="layers")
        self.assertEqual(vias, [(500, 0, "Top Layer", "Mid Layer 1")])
        self.assertEqual(tracks[-1], ("Mid Layer 1", 500, 0, 500, 800))

    def test_same_layer_waypoint_is_no_via(self):
        tracks, vias = routing.plan_path((0, 0, "Top Layer"), [(300, 0, "Top Layer"), (300, 300, None)])
        self.assertEqual(vias, [])
        self.assertEqual(len(tracks), 2)

    def test_layer_names_without_case(self):
        tracks, vias = routing.plan_path((0, 0, "Top Layer"), [(300, 0, "top layer"), (300, 300, "TOP LAYER")])
        self.assertEqual(vias, [])
        self.assertEqual({t[0] for t in tracks}, {"Top Layer"})


class ResolvePointsTest(unittest.TestCase):
    LOCATED = {
        "pad|U1|1": ("pad", 0, 0, "Top Layer", "SIG"),         # surface-mount, top
        "pad|U2|1": ("pad", 1000, 0, "Bottom Layer", "SIG"),   # surface-mount, bottom
        "pad|J1|1": ("pad", 2000, 0, "Multi-Layer", "SIG"),    # through-hole
        "pad|U3|2": ("pad", 3000, 0, "Top Layer", "OTHER"),
        "via|500|500": ("via", 500, 500, "Multi-Layer", "SIG"),
        "component|U1": ("component", 10, 10, "", ""),
    }

    def resolve(self, points, layer="", **kw):
        return routing.resolve_route_points(points, self.LOCATED, "sig", layer, **kw)

    def test_smd_pad_on_its_layer(self):
        resolved, first, warnings = self.resolve([("ref", "pad|U1|1"), ("xy", (500, 0, None)), ("ref", "pad|U3|2")],
                                                 allow_other_net=True)
        self.assertEqual(first, "Top Layer")
        self.assertEqual(resolved, [(0, 0, "Top Layer"), (500, 0, None), (3000, 0, "Top Layer")])
        self.assertEqual(len(warnings), 1)

    def test_no_via_in_an_smd_pad(self):
        # top pad to bottom pad without changing layer: the via would land in the pad
        with self.assertRaises(ValueError) as e:
            self.resolve([("ref", "pad|U1|1"), ("ref", "pad|U2|1")])
        self.assertIn("allow_via_in_pad", str(e.exception))
        # changing layer on the way is fine
        resolved, _, _ = self.resolve([("ref", "pad|U1|1"), ("xy", (500, 0, "Bottom Layer")), ("ref", "pad|U2|1")])
        tracks, vias = routing.plan_path((0, 0, "Top Layer"), resolved[1:])
        self.assertEqual(vias, [(500, 0, "Top Layer", "Bottom Layer")])
        # or allowed explicitly
        resolved, _, _ = self.resolve([("ref", "pad|U1|1"), ("ref", "pad|U2|1")], allow_via_in_pad=True)
        self.assertEqual(resolved[-1], (1000, 0, "Bottom Layer"))
        # a through-hole pad takes any layer
        self.resolve([("ref", "pad|U2|1"), ("ref", "pad|J1|1")])

    def test_start_layer_against_an_smd_pad(self):
        with self.assertRaises(ValueError):
            self.resolve([("ref", "pad|U1|1"), ("xy", (500, 0, None))], layer="Bottom Layer")
        resolved, first, _ = self.resolve([("ref", "pad|U1|1"), ("xy", (500, 0, None))], layer="Bottom Layer",
                                          allow_via_in_pad=True)
        tracks, vias = routing.plan_path((0, 0, first), resolved[1:])
        self.assertEqual(vias, [(0, 0, "Top Layer", "Bottom Layer")])
        self.assertEqual(tracks, [("Bottom Layer", 0, 0, 500, 0)])
        # same layer in another case is no via
        _, first, _ = self.resolve([("ref", "pad|U1|1"), ("xy", (500, 0, None))], layer="top layer")
        self.assertEqual(first, "Top Layer")

    def test_other_net_refused(self):
        with self.assertRaises(ValueError) as e:
            self.resolve([("ref", "pad|U1|1"), ("ref", "pad|U3|2")])
        self.assertIn("allow_other_net", str(e.exception))

    def test_points_vias_and_components(self):
        resolved, first, _ = self.resolve([("xy", (0, 0, None)), ("ref", "via|500|500"), ("ref", "component|U1")])
        self.assertEqual(first, "Top Layer")
        self.assertEqual(resolved, [(0, 0, None), (500, 500, None), (10, 10, None)])
        _, first, _ = self.resolve([("xy", (0, 0, "Bottom Layer")), ("xy", (1, 1, None))])
        self.assertEqual(first, "Bottom Layer")


if __name__ == "__main__":
    unittest.main()
