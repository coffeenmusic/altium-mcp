"""Unit tests for render (pure Python, no Altium needed).

Run from server/:  python -m unittest tests.test_render -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import render  # noqa: E402


def px(image, cv_box, x, y, dpi):
    """The pixel at board/sheet point (x, y) of an image drawn for cv_box."""
    k = dpi / 1000.0
    return image.getpixel((int((x - cv_box[0]) * k), int((cv_box[3] - y) * k)))


class PcbTest(unittest.TestCase):
    BOX = [0, 0, 400, 200]
    OBJECTS = [
        {"kind": "track", "layer": "Top Layer", "x1": 20, "y1": 100, "x2": 380, "y2": 100, "width": 20},
        {"kind": "track", "layer": "Mid Layer 1", "x1": 200, "y1": 0, "x2": 200, "y2": 200, "width": 20},
        {"kind": "region", "layer": "Bottom Layer", "bounds": [0, 0, 100, 60], "contours": [
            {"hole": False, "points": [(0, 0), (100, 0), (100, 60), (0, 60)]},
            {"hole": True, "points": [(40, 20), (60, 20), (60, 40), (40, 40)]}]},
        {"kind": "via", "x": 300, "y": 150, "size": 30, "hole": 12, "low_layer": "Top Layer",
         "high_layer": "Bottom Layer"},
        {"kind": "pad", "layer": "Multi Layer", "x": 350, "y": 40, "x_size": 60, "y_size": 60, "shape": "round",
         "hole": 30, "rotation": 0},
        {"kind": "text", "layer": "Top Overlay", "x": 10, "y": 180, "height": 20, "rotation": 0, "text": "U1",
         "hidden": True},
        {"kind": "polygon", "layer": "Top Layer", "bounds": [0, 0, 400, 200]},
    ]

    def test_layers_drawn_in_colour(self):
        image, info = render.render_pcb(self.OBJECTS, self.BOX, dpi=1000)
        self.assertEqual(info["size_px"], [400, 200])
        self.assertEqual(px(image, self.BOX, 100, 100, 1000), (190, 0, 0))     # top track, see-through on black
        self.assertEqual(px(image, self.BOX, 10, 10, 1000)[2] > 100, True)     # bottom pour, blue
        self.assertEqual(px(image, self.BOX, 50, 30, 1000), (0, 0, 0))        # the hole in it
        self.assertEqual(px(image, self.BOX, 300, 150, 1000), render.HOLE_COLOUR)
        self.assertEqual(px(image, self.BOX, 300, 163, 1000), render.VIA_COLOUR)
        self.assertEqual(px(image, self.BOX, 370, 40, 1000), (192, 192, 192))  # Multi Layer pad ring
        self.assertEqual(info["drawn"]["multi layer"], 1)
        # mid layers are not drawn unless asked; hidden text and polygons never
        self.assertEqual(px(image, self.BOX, 200, 30, 1000), (0, 0, 0))
        self.assertEqual(info["drawn"]["texts"], 0)
        image, info = render.render_pcb(self.OBJECTS, self.BOX, layers=["Mid Layer 1"], dpi=1000)
        self.assertNotEqual(px(image, self.BOX, 200, 30, 1000), (0, 0, 0))
        self.assertIn("mid layer 1", info["drawn"])

    def test_multi_layer_by_any_spelling(self):
        pad = dict(self.OBJECTS[4])
        for asked in (["Multi Layer"], ["multi-layer"], ["MultiLayer"], ["Bottom Layer"]):
            image, info = render.render_pcb([pad], self.BOX, layers=asked, dpi=1000)
            self.assertEqual(px(image, self.BOX, 370, 40, 1000), (192, 192, 192), asked)
        # not with an overlay alone
        image, info = render.render_pcb([pad], self.BOX, layers=["Top Overlay"], dpi=1000)
        self.assertEqual(px(image, self.BOX, 370, 40, 1000), (0, 0, 0))
        self.assertEqual(render.layer_colour("multi-layer"), render.layer_colour("Multi Layer"))

    def test_vias_only_on_layers_they_span(self):
        stack = ["Top Layer", "Mid Layer 1", "Mid Layer 2", "Bottom Layer"]
        blind = {"kind": "via", "x": 100, "y": 100, "size": 30, "hole": 12, "low_layer": "Top Layer",
                 "high_layer": "Mid Layer 1"}
        through = dict(blind, x=300, low_layer="Top Layer", high_layer="Bottom Layer")
        image, info = render.render_pcb([blind, through], self.BOX, layers=["Bottom Layer"], dpi=1000, stack=stack)
        self.assertEqual(info["drawn"]["vias"], 1)
        self.assertEqual(px(image, self.BOX, 100, 113, 1000), (0, 0, 0))
        self.assertEqual(px(image, self.BOX, 300, 113, 1000), render.VIA_COLOUR)
        _, info = render.render_pcb([blind, through], self.BOX, layers=["Mid Layer 1"], dpi=1000, stack=stack)
        self.assertEqual(info["drawn"]["vias"], 2)
        # without the stack the span is not known: drawn with any copper layer
        _, info = render.render_pcb([blind], self.BOX, layers=["Bottom Layer"], dpi=1000)
        self.assertEqual(info["drawn"]["vias"], 1)
        _, info = render.render_pcb([blind, through], self.BOX, layers=["Top Overlay"], dpi=1000, stack=stack)
        self.assertNotIn("vias", info["drawn"])

    def test_arc_width_centred_on_its_radius(self):
        arc = {"kind": "arc", "layer": "Top Layer", "cx": 200, "cy": 100, "radius": 50, "start_angle": 0,
               "end_angle": 180, "width": 20}
        image, _ = render.render_pcb([arc], self.BOX, dpi=1000)
        self.assertEqual(px(image, self.BOX, 200, 157, 1000), (190, 0, 0))    # r + w/2 - 3
        self.assertEqual(px(image, self.BOX, 200, 143, 1000), (190, 0, 0))    # r - w/2 + 3
        self.assertEqual(px(image, self.BOX, 200, 165, 1000), (0, 0, 0))
        self.assertEqual(px(image, self.BOX, 200, 135, 1000), (0, 0, 0))

    def test_outline_arcs_box_and_direction(self):
        # a round board of two semicircles: the box is the circle's, not 1 mil high
        circle = [{"x": 0, "y": 0, "kind": 1, "cx": 500, "cy": 0, "radius": 500, "start_angle": 180,
                   "end_angle": 0},
                  {"x": 1000, "y": 0, "kind": 1, "cx": 500, "cy": 0, "radius": 500, "start_angle": 0,
                   "end_angle": 180}]
        box = render.outline_box(circle)
        self.assertAlmostEqual(box[1], -500, delta=1)
        self.assertAlmostEqual(box[3], 500, delta=1)
        self.assertAlmostEqual(box[2] - box[0], 1000, delta=1)
        # a 270 degree arc goes the long way round, as its angles say
        notch = [{"x": 100, "y": 0, "kind": 1, "cx": 0, "cy": 0, "radius": 100, "start_angle": 0,
                  "end_angle": 270},
                 {"x": 0, "y": -100, "kind": 0, "cx": 0, "cy": 0, "radius": 0}]
        box = render.outline_box(notch)
        self.assertAlmostEqual(box[0], -100, delta=1)
        self.assertAlmostEqual(box[3], 100, delta=1)
        # walked from its end angle, the same arc turns clockwise
        back = [{"x": 0, "y": -100, "kind": 1, "cx": 0, "cy": 0, "radius": 100, "start_angle": 0,
                 "end_angle": 270},
                {"x": 100, "y": 0, "kind": 0, "cx": 0, "cy": 0, "radius": 0}]
        self.assertAlmostEqual(render.outline_box(back)[0], -100, delta=1)
        # without angles, the short way
        short = [dict(notch[0]), notch[1]]
        del short[0]["start_angle"], short[0]["end_angle"]
        self.assertGreater(render.outline_box(short)[0], -1)

    def test_copper_inside_a_pour_hole_shows(self):
        pour = {"kind": "region", "layer": "Top Layer", "bounds": [0, 0, 400, 200], "contours": [
            {"hole": False, "points": [(0, 0), (400, 0), (400, 200), (0, 200)]},
            {"hole": True, "points": [(100, 50), (300, 50), (300, 150), (100, 150)]}]}
        # a pad and a track of another net in the cut-out, drawn before or after the pour
        pad = {"kind": "pad", "layer": "Top Layer", "x": 150, "y": 100, "x_size": 40, "y_size": 40,
               "shape": "rectangle", "hole": 0, "rotation": 0}
        track = {"kind": "track", "layer": "Top Layer", "x1": 220, "y1": 100, "x2": 280, "y2": 100, "width": 20}
        for objects in ([pour, pad, track], [pad, track, pour]):
            image, _ = render.render_pcb(objects, self.BOX, dpi=1000)
            self.assertEqual(px(image, self.BOX, 150, 100, 1000), (190, 0, 0))   # pad
            self.assertEqual(px(image, self.BOX, 250, 100, 1000), (190, 0, 0))   # track
            self.assertEqual(px(image, self.BOX, 200, 130, 1000), (0, 0, 0))     # the cut-out around them
            self.assertEqual(px(image, self.BOX, 50, 100, 1000), (190, 0, 0))    # the pour
        # a hole of one region does not cut another region of the layer
        other = {"kind": "region", "layer": "Top Layer", "bounds": [180, 80, 220, 120], "contours": [
            {"hole": False, "points": [(180, 80), (220, 80), (220, 120), (180, 120)]}]}
        image, _ = render.render_pcb([other, pour], self.BOX, dpi=1000)
        self.assertEqual(px(image, self.BOX, 200, 100, 1000), (190, 0, 0))

    def test_dpi_capped_and_outline(self):
        image, info = render.render_pcb([], [0, 0, 100000, 1000], dpi=1000,
                                        outline=[{"x": 0, "y": 0, "kind": 0, "cx": 0, "cy": 0, "radius": 0},
                                                 {"x": 100000, "y": 0, "kind": 0, "cx": 0, "cy": 0, "radius": 0},
                                                 {"x": 100000, "y": 1000, "kind": 0, "cx": 0, "cy": 0, "radius": 0}])
        self.assertEqual(max(info["size_px"]), render.MAX_PIXELS)
        self.assertLess(info["dpi"], 1000)
        self.assertIn(render.OUTLINE_COLOUR, [c for _, c in image.getcolors(1 << 16)])

    def test_layer_colours_and_order(self):
        self.assertEqual(render.layer_colour("Top Layer"), (255, 0, 0))
        self.assertEqual(render.layer_colour("Mid Layer 2"), render.MID_COLOURS[1])
        self.assertEqual(render.layer_colour("Mechanical 13"), render.MECH_COLOUR)
        order = sorted(["Top Overlay", "Top Layer", "Mid Layer 1", "Bottom Layer"], key=render.layer_order)
        self.assertEqual(order, ["Bottom Layer", "Mid Layer 1", "Top Layer", "Top Overlay"])


class SheetTest(unittest.TestCase):
    # Coordinates as a SchDoc stores them: in units of 10 mil
    RECORDS = [
        {"RECORD": "31", "FONTIDCOUNT": "1", "SIZE1": "10", "FONTNAME1": "Times New Roman"},
        {"RECORD": "1", "CURRENTPARTID": "1", "DISPLAYMODE": "0", "LOCATION.X": "10", "LOCATION.Y": "10"},
        {"RECORD": "14", "OWNERINDEX": "0", "OWNERPARTID": "1", "LOCATION.X": "10", "LOCATION.Y": "10",
         "CORNER.X": "20", "CORNER.Y": "20", "ISSOLID": "T", "AREACOLOR": "11599871", "COLOR": "128"},
        # the same body for part 2 of the component: not shown
        {"RECORD": "14", "OWNERINDEX": "0", "OWNERPARTID": "2", "LOCATION.X": "30", "LOCATION.Y": "10",
         "CORNER.X": "40", "CORNER.Y": "20", "ISSOLID": "T", "AREACOLOR": "255"},
        {"RECORD": "2", "OWNERINDEX": "0", "OWNERPARTID": "1", "LOCATION.X": "20", "LOCATION.Y": "15",
         "PINLENGTH": "3", "PINCONGLOMERATE": "56", "NAME": "IN", "DESIGNATOR": "1"},
        {"RECORD": "27", "LOCATIONCOUNT": "2", "X1": "23", "Y1": "15", "X2": "50", "Y2": "15"},
        {"RECORD": "29", "LOCATION.X": "50", "LOCATION.Y": "15"},
        {"RECORD": "25", "LOCATION.X": "40", "LOCATION.Y": "16", "TEXT": "NET1"},
        {"RECORD": "39"},
        {"RECORD": "13", "OWNERINDEX": "7", "LOCATION.X": "0", "LOCATION.Y": "0", "CORNER.X": "300",
         "CORNER.Y": "300"},
    ]

    def test_records_drawn(self):
        box = [0, 0, 1000, 400]
        image, info = render.render_sheet(self.RECORDS, box, dpi=1000)
        self.assertEqual(info["size_px"], [1000, 400])
        self.assertEqual(px(image, box, 150, 120, 1000), (255, 255, 176))      # part 1 body
        self.assertEqual(px(image, box, 350, 120, 1000), render.SCH_BACKGROUND)  # part 2: not drawn
        self.assertEqual(px(image, box, 350, 150, 1000), render.WIRE)
        self.assertEqual(px(image, box, 500, 150, 1000), render.JUNCTION)
        self.assertEqual(px(image, box, 215, 150, 1000), render.TEXT)          # the pin
        # the template's line (owned by record 39) is left out
        self.assertEqual(px(image, box, 700, 700 - 400, 1000), render.SCH_BACKGROUND)

    def test_auto_bounds_cover_drawing(self):
        image, info = render.render_sheet(self.RECORDS, dpi=100)
        x0, y0, x1, y1 = info["bbox"]
        self.assertLessEqual(x0, 100)
        self.assertGreaterEqual(x1, 500)
        self.assertLess(x1, 2000)          # the template's 3000 mil line does not count


if __name__ == "__main__":
    unittest.main()
