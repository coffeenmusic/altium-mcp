"""Unit tests for sch_edit (pure Python, no Altium needed).

Run from server/:  python -m unittest tests.test_sch_edit -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from sch_edit import encode_operations  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
