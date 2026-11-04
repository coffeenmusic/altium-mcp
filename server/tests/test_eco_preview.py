"""Unit tests for eco_preview (pure Python, no Altium needed).

Run from server/:  python -m unittest tests.test_eco_preview -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import eco_preview  # noqa: E402

SOURCE = {
    "components": [r"\S1\A|R1|R0603|10k", r"\S1\B|C1|C0603|100n", r"\S1\C|U2|SOIC8|LM358",
                   r"\S1\D|R5|R0603|1k", r"\S1\E|J1|HDR2|Header"],
    "pins": ["R1|1|VIN", "R1|2|MID", "C1|1|MID", "C1|2|GND", "U2|1|MID", "U2|4|GND", "U2|8|VIN",
             "R5|1|OUT", "R5|2|SENSE", "U2|2|SENSE", "J1|1|LONELY", "J1|2|GND"],
    "component_classes": ["AMP|Amp.SchDoc|R1,C1,U2", "PWR|Power.SchDoc|R5",
                          "U_Conn|ConnTop.SchDoc|J1", "U_Conn|ConnBottom.SchDoc|R5"],
    "rooms": ["AMP|Room AMP (Scope=InComponentClass('AMP')) TopLayer"],
    "net_classes": ["POWER|VIN,GND"],
}
TARGET = {
    "components": [r"\S1\A|R1|R0603|10k", r"\S1\B|C1|C0805|100n", r"\S1\C|U3|SOIC8|LM358",
                   r"\S1\Z|R9|R0603|2k", r"|LOGO1|LOGO|", r"\S1\E|J1|HDR2|Header"],
    "pads": ["R1|1|VIN", "R1|2|N1", "C1|1|N1", "C1|2|GND", "U3|1|N1", "U3|4|GND", "U3|8|VIN",
             "U3|2|", "R9|1|X", "R9|2|X", "J1|1|LONELY", "J1|2|GND", "J1|MH|"],
    "component_classes": ["AMP|R1,C1,U3", "OLD|R9", "U_Conn|J1", "U_Conn|"],
    "net_classes": ["POWER|VIN,GND", "SPARE|X"],
    "rooms": ["AMP|InComponentClass('AMP')", "OLD|InComponentClass('OLD')"],
}


def diff(single_pin=False):
    return eco_preview.compare(eco_preview.parse_source(SOURCE), eco_preview.parse_target(TARGET), single_pin)


class ComponentTest(unittest.TestCase):
    def test_matched_by_unique_id(self):
        d = diff()
        self.assertEqual(d["add_components"], [{"designator": "R5", "footprint": "R0603", "comment": "1k"}])
        # a board-only part (no schematic link) is left alone by the ECO
        self.assertEqual([c["designator"] for c in d["remove_components"]], ["R9"])
        self.assertEqual(d["board_only_components"], [{"designator": "LOGO1", "footprint": "LOGO"}])
        self.assertEqual(d["change_designators"], [{"from": "U3", "to": "U2"}])
        self.assertEqual(d["change_footprints"], [{"designator": "C1", "from": "C0805", "to": "C0603"}])
        self.assertEqual(d["change_comments"], [])


class NetTest(unittest.TestCase):
    def test_rename_pin_changes_and_single_pin_nets(self):
        d = diff()
        self.assertEqual(d["rename_nets"], [{"from": "N1", "to": "MID"}])
        # U2-2 joins SENSE, whose other pin (R5) is not on the board yet; the
        # mounting hole pad has no schematic pin and is left alone. LONELY
        # has one pin, so with single-pin nets off the ECO leaves J1-1 and
        # its board net as they are (checked against Altium AD25's ECO)
        self.assertEqual(d["change_pin_nets"], [{"pin": "U3-2", "from": "", "to": "SENSE"}])
        self.assertEqual(d["add_nets"], ["SENSE"])
        self.assertEqual(d["remove_nets"], ["X"])
        on = diff(True)
        self.assertEqual(on["change_pin_nets"], [{"pin": "U3-2", "from": "", "to": "SENSE"}])
        self.assertEqual(on["remove_nets"], ["X"])

    def test_single_pin_net_left_out_by_default(self):
        target = dict(TARGET, pads=[p for p in TARGET["pads"] if not p.startswith("J1|1|")] + ["J1|1|"])
        d = eco_preview.compare(eco_preview.parse_source(SOURCE), eco_preview.parse_target(target))
        self.assertNotIn({"pin": "J1-1", "from": "", "to": "LONELY"}, d["change_pin_nets"])
        d = eco_preview.compare(eco_preview.parse_source(SOURCE), eco_preview.parse_target(target), True)
        self.assertIn({"pin": "J1-1", "from": "", "to": "LONELY"}, d["change_pin_nets"])
        self.assertIn("LONELY", d["add_nets"])


class ClassAndRoomTest(unittest.TestCase):
    def test_classes_rooms_and_net_classes(self):
        d = diff()
        self.assertEqual(d["add_component_classes"], [{"name": "PWR", "sheet": "Power.SchDoc", "members": 1}])
        self.assertEqual(d["remove_component_classes"], [{"name": "OLD", "members": 1}])
        # members compare by the board's designators: U3 is U2 on the sheets
        self.assertEqual(d["change_component_class_members"], [])
        self.assertEqual(d["remove_rooms"], [{"name": "OLD", "scope": "InComponentClass('OLD')"}])
        self.assertEqual(d["add_rooms"], [])
        self.assertEqual(d["add_net_classes"], [])
        self.assertEqual(d["remove_net_classes"], ["SPARE"])

    def test_member_change(self):
        source = dict(SOURCE, component_classes=["AMP|Amp.SchDoc|R1,C1"])
        d = eco_preview.compare(eco_preview.parse_source(source), eco_preview.parse_target(TARGET))
        self.assertEqual(d["change_component_class_members"], [{"name": "AMP", "add": [], "remove": ["U3"]}])

    def test_duplicate_class_explained_not_guessed(self):
        d = diff()
        dup = d["duplicate_component_classes"]
        self.assertEqual(len(dup), 1)
        self.assertEqual(dup[0]["sheet_members"], {"ConnTop.SchDoc": 1, "ConnBottom.SchDoc": 1})
        self.assertEqual((dup[0]["board_members"], dup[0]["board_copies"]), (1, 2))
        self.assertIn("Class already exists", dup[0]["note"])
        self.assertNotIn("U_Conn", [c["name"] for c in d["change_component_class_members"]])


class OutputTest(unittest.TestCase):
    def test_summary_and_limit(self):
        d = diff()
        self.assertEqual(eco_preview.summary(d)["remove_components"], 1)
        self.assertNotIn("board_only_components", eco_preview.summary(d))
        shown, cut = eco_preview.limit(d, 1)
        self.assertEqual(len(shown["remove_components"]), 1)
        self.assertEqual(sorted(cut), sorted(k for k, v in d.items() if len(v) > 1))
        self.assertNotIn("add_rooms", shown)

    def test_fields_with_bars(self):
        src = eco_preview.parse_source({"components": [r"\A|R1|R0603|a|b"]})
        self.assertEqual(src["components"]["\\a"]["comment"], "a|b")


if __name__ == "__main__":
    unittest.main()
