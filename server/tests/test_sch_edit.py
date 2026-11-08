"""Unit tests for sch_edit (pure Python, no Altium needed).

Run from server/:  python -m unittest tests.test_sch_edit -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from sch_edit import OpError, encode_operations, find_pin, pin_designators, resolve_pins  # noqa: E402


def one(op, unit="mil"):
    lines, err = encode_operations([op], unit)
    if err:
        raise AssertionError(err)
    return lines[0]


class EncodeTest(unittest.TestCase):
    def test_units(self):
        self.assertEqual(one({"op": "add_junction", "x": 100, "y": 2.5}), "JUNCTION|1000000|25000")
        # 1 mm is 39.37 mil: kept exact in internal units
        self.assertEqual(one({"op": "add_junction", "x": 1, "y": 25.4}, "mm"), "JUNCTION|393701|10000000")

    def test_move_component_keeps_what_is_not_given(self):
        self.assertEqual(one({"op": "move_component", "designator": "R1", "rotation": 90}),
                         "MOVE|R1|0|||1|-1")
        self.assertEqual(one({"op": "move_component", "designator": "U1", "part": 2, "x": 10, "y": 20,
                              "mirror": True}), "MOVE|U1|2|100000|200000|-1|1")

    def test_move_text_from_a_check_schematic_suggestion(self):
        self.assertEqual(one({"op": "move_text", "designator": "C49", "name": "Designator", "x": 73.99,
                              "y": 180, "justification": 5, "orientation": 0}, "mm"),
                         "TEXT|C49|0|Designator|29129921|70866142|5|0|-1")

    def test_parameter_and_labels(self):
        self.assertEqual(one({"op": "set_parameter", "designator": "R1", "name": "Value", "value": "4k7",
                              "visible": True}), "PARAM|R1|0|Value|4k7|1")
        self.assertEqual(one({"op": "add_net_label", "x": 1, "y": 2, "text": "SDA", "justification": 2}),
                         "LABEL|10000|20000|0|2|SDA")
        self.assertEqual(one({"op": "add_power_port", "x": 0, "y": 0, "text": "GND", "style": "signal_ground",
                              "orientation": 3, "show_net_name": False}), "POWER|0|0|3|5|0|GND")

    def test_wire_and_delete(self):
        self.assertEqual(one({"op": "add_wire", "points": [[0, 0], [10, 0], [10, 5]]}),
                         "WIRE|0|0|100000|0|100000|50000")
        self.assertEqual(one({"op": "delete_at", "kind": "net_label", "x": 1, "y": 1, "text": "X"}),
                         "DELETE_AT|net_label|10000|10000|X")

    def test_place_component(self):
        self.assertEqual(one({"op": "place_component", "library": "C:\\lib\\R.SchLib", "symbol": "RES",
                              "designator": "R9", "x": 1, "y": 2, "rotation": 270}),
                         "PLACE|R9|C:\\lib\\R.SchLib|RES|10000|20000|3|0|")

    def test_errors_name_the_operation(self):
        cases = [
            ({"op": "spin"}, "must be one of"),
            ({"op": "delete_component"}, "needs designator"),
            ({"op": "add_junction", "x": 1, "y": 2, "size": 3}, "does not take size"),
            ({"op": "move_component", "designator": "R1", "x": 1}, "both x and y"),
            ({"op": "move_component", "designator": "R1", "rotation": 45}, "rotation"),
            ({"op": "move_text", "designator": "R1", "name": "Value", "justification": 9}, "0..8"),
            ({"op": "set_parameter", "designator": "R1", "name": "Value", "value": "a|b"}, "'|'"),
            ({"op": "add_wire", "points": [[0, 0]]}, "at least two"),
            ({"op": "delete_at", "kind": "pin", "x": 0, "y": 0}, "kind is one of"),
            ({"op": "place_component", "library": "x.IntLib", "symbol": "R", "designator": "R1",
              "x": 0, "y": 0}, ".SchLib"),
        ]
        for op, expected in cases:
            lines, err = encode_operations([{"op": "add_junction", "x": 0, "y": 0}, op])
            self.assertEqual(lines, [], op)
            self.assertIn("operation 2", err)
            self.assertIn(expected, err, op)
        self.assertEqual(encode_operations([], "mil"), ([], "operations is empty"))
        self.assertIn("unit", encode_operations([{"op": "add_junction", "x": 0, "y": 0}], "inch")[1])

    def test_move_net_label(self):
        lines, err = encode_operations([{"op": "move_net_label", "x": 88.9, "y": 12.7, "text": "FAR",
                                         "to_x": 76.2, "to_y": 12.7, "orientation": 0, "justification": 2}], "mm")
        self.assertEqual((lines, err), (["LABELMOVE|35000000|5000000|FAR|30000000|5000000|0|2|"], ""))
        lines, _ = encode_operations([{"op": "move_net_label", "x": 1, "y": 2, "snap_to_wire_end": True}])
        self.assertEqual(lines, ["LABELMOVE|10000|20000||||||1000000"])
        self.assertIn("together", encode_operations([{"op": "move_net_label", "x": 1, "y": 2, "to_x": 3}])[1])


PINS = [
    {"designator": "U20", "pin": "1", "name": "VCC", "x": 1000000, "y": 2000000},
    {"designator": "U20", "pin": "2", "name": "GND", "x": 1000000, "y": 1900000},
    {"designator": "U20", "pin": "3", "name": "GND", "x": 1000000, "y": 1800000},
]


class PinRefTest(unittest.TestCase):
    def test_designators_named(self):
        ops = [{"op": "add_wire", "points": ["u20.VCC", [0, 0]]}, {"op": "add_junction", "at": "R1.2"},
               {"op": "add_net_label", "x": 1, "y": 2, "text": "A.B"}]
        self.assertEqual(pin_designators(ops), ["R1", "U20"])

    def test_found_by_number_then_name(self):
        self.assertEqual(find_pin("U20.1", PINS)["name"], "VCC")
        self.assertEqual(find_pin("u20.vcc", PINS)["pin"], "1")
        with self.assertRaisesRegex(OpError, "give the pin number"):
            find_pin("U20.GND", PINS)
        with self.assertRaisesRegex(OpError, "no pin"):
            find_pin("U20.FOO", PINS)
        with self.assertRaisesRegex(OpError, "no component"):
            find_pin("U9.1", PINS)

    def test_resolved_into_points(self):
        ops, err = resolve_pins([{"op": "add_wire", "points": ["U20.VCC", [50, 200]]},
                                 {"op": "add_net_label", "at": "U20.3", "text": "N"}], PINS, "mil")
        self.assertEqual(err, "")
        self.assertEqual(ops[0]["points"], [[100.0, 200.0], [50, 200]])
        self.assertEqual((ops[1]["x"], ops[1]["y"]), (100.0, 180.0))
        self.assertNotIn("at", ops[1])
        self.assertIn("not both", resolve_pins([{"op": "add_junction", "at": "U20.1", "x": 1, "y": 1}], PINS)[1])

    def test_port(self):
        lines, err = encode_operations([{"op": "add_port", "x": 10, "y": 20, "name": "LATCH_EN",
                                         "io_type": "input", "style": "right", "width": 600}])
        self.assertEqual((lines, err), (["PORT|100000|200000|2|2|6000000|LATCH_EN"], ""))
        self.assertIn("io_type", encode_operations([{"op": "add_port", "x": 0, "y": 0, "name": "P",
                                                     "io_type": "sideways"}])[1])

    def test_port_styles_follow_altium(self):
        # TPortArrowStyle: none, left, right, left_right, none_vertical, top, bottom, top_bottom
        for name, n in (("none", 0), ("left_right", 3), ("none_vertical", 4), ("top", 5),
                        ("bottom", 6), ("top_bottom", 7)):
            lines, err = encode_operations([{"op": "add_port", "x": 0, "y": 0, "name": "P", "style": name}])
            self.assertEqual(lines[0].split("|")[4], str(n), name)
        self.assertIn("style", encode_operations([{"op": "add_port", "x": 0, "y": 0, "name": "P",
                                                   "style": 8}])[1])

    def test_part_changed_earlier_is_refused(self):
        moved = [{"op": "move_component", "designator": "U20", "x": 0, "y": 0},
                 {"op": "add_junction", "at": "U20.1"}]
        self.assertIn("earlier operation", resolve_pins(moved, PINS)[1])
        placed = [{"op": "place_component", "designator": "u20", "library": "C:/a.SchLib", "symbol": "S",
                   "x": 0, "y": 0}, {"op": "add_wire", "points": ["U20.1", [0, 0]]}]
        self.assertIn("operation 2", resolve_pins(placed, PINS)[1])
        # A reference before the move still sees the pin where it was read
        before = [{"op": "add_junction", "at": "U20.1"},
                  {"op": "move_component", "designator": "U20", "x": 0, "y": 0}]
        self.assertEqual(resolve_pins(before, PINS)[1], "")


MULTI = [
    # Pin 7 (VCC) is common to every part: listed with each placed part
    {"designator": "U1", "part": 1, "pin": "1", "name": "OUT", "x": 100000, "y": 0},
    {"designator": "U1", "part": 1, "pin": "7", "name": "VCC", "x": 200000, "y": 0},
    {"designator": "U1", "part": 2, "pin": "4", "name": "OUT", "x": 300000, "y": 0},
    {"designator": "U1", "part": 2, "pin": "7", "name": "VCC", "x": 400000, "y": 0},
]


class MultiPartTest(unittest.TestCase):
    def test_part_letter(self):
        self.assertEqual(find_pin("U1B.7", MULTI)["x"], 400000)
        self.assertEqual(find_pin("u1a.VCC", MULTI)["x"], 200000)
        self.assertEqual(find_pin("U1.4", MULTI)["part"], 2)
        with self.assertRaisesRegex(OpError, "part C of U1 is not on the sheet"):
            find_pin("U1C.1", MULTI)

    def test_common_pin_needs_the_part(self):
        with self.assertRaisesRegex(OpError, r"common to every part.*U1A, U1B.*U1A\.7"):
            find_pin("U1.7", MULTI)
        with self.assertRaisesRegex(OpError, "common to every part"):
            find_pin("U1.VCC", MULTI)
        with self.assertRaisesRegex(OpError, r"give the pin number and the part \(U1A, U1B\)"):
            find_pin("U1.OUT", MULTI)

    def test_letter_designators_asked_with_base(self):
        self.assertEqual(pin_designators([{"op": "add_junction", "at": "U1A.3"}]), ["U1", "U1A"])

    def test_part_named_with_letter(self):
        # Should Altium report U1A rather than U1, "U1.1" still finds it
        pins = [dict(p, designator="U1A") for p in MULTI[:2]]
        self.assertEqual(find_pin("U1.1", pins)["x"], 100000)
        self.assertEqual(find_pin("U1A.1", pins)["x"], 100000)


class SheetAndWireTest(unittest.TestCase):
    def test_sheet_symbol(self):
        lines, error = encode_operations([{"op": "edit_sheet_symbol", "sheet": "Filter", "width": 800, "height": 300,
                                           "fill_color": "#FFFFB0", "line_width": "small", "file_x": 10, "file_y": 20}])
        self.assertEqual(error, "")
        self.assertEqual(lines, ["SHEETSYM|Filter|||8000000|3000000|11599871||1|||100000|200000"])
        self.assertIn("together", encode_operations([{"op": "edit_sheet_symbol", "sheet": "F", "name_x": 1}])[1])
        self.assertIn("#RRGGBB", encode_operations([{"op": "edit_sheet_symbol", "sheet": "F", "fill_color": "red"}])[1])
        self.assertIn("positive", encode_operations([{"op": "edit_sheet_symbol", "sheet": "F", "width": 0}])[1])

    def test_sheet_entry(self):
        lines, error = encode_operations([{"op": "edit_sheet_entry", "sheet": "F", "name": "IN", "side": "right",
                                           "distance": 2.54, "io_type": "input"}], "mm")
        self.assertEqual(lines, ["SHEETENTRY|F|IN|1|1000000|2||"])
        self.assertIn("side", encode_operations([{"op": "edit_sheet_entry", "sheet": "F", "name": "IN",
                                                  "side": "up"}])[1])
        # The distance runs from the left edge on the top and bottom sides
        error = encode_operations([{"op": "edit_sheet_entry", "sheet": "F", "name": "IN", "side": "top",
                                    "distance": -1}])[1]
        self.assertIn("left edge on the top and bottom", error)

    def test_sheet_entry_styles_follow_altium(self):
        # The same TPortArrowStyle order as ports: top_bottom is 7, none_vertical 4
        for name, n in (("left_right", 3), ("none_vertical", 4), ("top", 5), ("bottom", 6), ("top_bottom", 7)):
            lines, _ = encode_operations([{"op": "edit_sheet_entry", "sheet": "F", "name": "IN", "style": name}])
            self.assertEqual(lines[0].split("|")[6], str(n), name)

    def test_wire_vertex_and_label(self):
        lines, _ = encode_operations([{"op": "move_wire_vertex", "x": 1, "y": 2, "to_x": 3, "to_y": 4},
                                      {"op": "move_net_label", "x": 1, "y": 2, "text": "A", "snap_to_wire_end": True},
                                      {"op": "move_net_label", "x": 1, "y": 2, "to_x": 5, "to_y": 6,
                                       "orientation": 1}])
        self.assertEqual(lines, ["WIREVTX|10000|20000|30000|40000", "LABELMOVE|10000|20000|A|||||1000000",
                                 "LABELMOVE|10000|20000||50000|60000|1||"])
        self.assertIn("together", encode_operations([{"op": "move_net_label", "x": 1, "y": 2, "to_x": 5}])[1])


if __name__ == "__main__":
    unittest.main()
