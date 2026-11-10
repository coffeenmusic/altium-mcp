"""Unit tests for eco_preview (pure Python, no Altium needed).

Run from server/:  python -m unittest tests.test_eco_preview -v
"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

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


# A made-up .PrjPcb, cut down to the sections the options come from. Amp
# makes its component class but no room; Power makes neither; nothing makes
# rooms or net classes. Difference Levels and Modification Levels hold one
# switch per kind (Type<n>, n = the kind's position in Altium's list + 1)
PRJ = """\ufeff[Design]
Version=1.0
NetlistSinglePinNets=0

[Document1]
DocumentPath=Amp.SchDoc
ClassGenCCAutoEnabled=1
ClassGenCCAutoRoomEnabled=0
ClassGenNCAutoScope=None

[Document2]
DocumentPath=Sheets\\Power.SchDoc
ClassGenCCAutoEnabled=0
ClassGenCCAutoRoomEnabled=0
ClassGenNCAutoScope=None

[Document3]
DocumentPath=Board.PcbDoc

[Difference Levels]
Type1=1
Type2=1
Type15=1

[Modification Levels]
Type8=1
Type17=1
Type34=1

[PrjClassGen]
CompClassManualEnabled=0
CompClassManualRoomEnabled=0
NetClassAutoBusEnabled=0
NetClassAutoCompEnabled=0
NetClassAutoNamedHarnessEnabled=0
NetClassManualEnabled=0
"""


def diff(single_pin=False):
    return eco_preview.compare(eco_preview.parse_source(SOURCE), eco_preview.parse_target(TARGET), single_pin)


def diff_with(text):
    options = eco_preview.parse_project_options(text)
    return eco_preview.compare(eco_preview.parse_source(SOURCE), eco_preview.parse_target(TARGET),
                               options["single_pin_nets"], options)


def skipped(d):
    return {s["kind"]: s["count"] for s in d["skipped_by_project_options"]}


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

    def test_multi_part_component(self):
        # U1A and U1B are one board component, matched by either part's id;
        # a new two-part U4 is one addition
        source = dict(SOURCE, components=SOURCE["components"] + [
            r"\S1\P1|U1|SOIC14|LM324", r"\S1\P2|U1|SOIC14|LM324",
            r"\S1\Q1|U4|SOIC8|LM358", r"\S1\Q2|U4|SOIC8|LM358"])
        target = dict(TARGET, components=TARGET["components"] + [r"\S1\P2|U1|SOIC14|LM324"])
        d = eco_preview.compare(eco_preview.parse_source(source), eco_preview.parse_target(target))
        self.assertEqual([c["designator"] for c in d["add_components"]], ["R5", "U4"])
        self.assertNotIn("U1", [c["designator"] for c in d["remove_components"]])

    def test_unannotated_parts_each_added(self):
        source = dict(SOURCE, components=SOURCE["components"] + [r"\S1\X1|R?|R0603|1k",
                                                                 r"\S1\X2|R?|R0603|2k"])
        d = eco_preview.compare(eco_preview.parse_source(source), eco_preview.parse_target(TARGET))
        self.assertEqual([c["designator"] for c in d["add_components"]], ["R5", "R?", "R?"])


class NetTest(unittest.TestCase):
    def test_rename_pin_changes_and_single_pin_nets(self):
        d = diff()
        self.assertEqual(d["rename_nets"], [{"from": "N1", "to": "MID"}])
        # U2-2 joins SENSE, whose other pin (R5) is not on the board yet; the
        # mounting hole pad has no schematic pin and is left alone. LONELY
        # has one pin, so with single-pin nets off J1-1 counts as on no net
        # and leaves it; the named net LONELY itself stays. OUT, on the new
        # R5 alone, is added as a net with no pins
        self.assertEqual(d["change_pin_nets"], [{"pin": "J1-1", "from": "LONELY", "to": ""},
                                                {"pin": "U3-2", "from": "", "to": "SENSE"}])
        self.assertEqual(d["add_nets"], ["OUT", "SENSE"])
        self.assertEqual(d["remove_nets"], ["X"])
        on = diff(True)
        self.assertEqual(on["change_pin_nets"], [{"pin": "U3-2", "from": "", "to": "SENSE"}])
        self.assertEqual(on["remove_nets"], ["X"])

    def test_single_pin_net_left_out_by_default(self):
        # Checked live against AD25's ECO, single-pin nets off: a net label
        # on a lone pin gives "Add Nets" for the net but no "Add Pins To Nets"
        target = dict(TARGET, pads=[p for p in TARGET["pads"] if not p.startswith("J1|1|")] + ["J1|1|"])
        d = eco_preview.compare(eco_preview.parse_source(SOURCE), eco_preview.parse_target(target))
        self.assertNotIn("J1-1", [c["pin"] for c in d["change_pin_nets"]])
        self.assertIn("LONELY", d["add_nets"])
        d = eco_preview.compare(eco_preview.parse_source(SOURCE), eco_preview.parse_target(target), True)
        self.assertIn({"pin": "J1-1", "from": "", "to": "LONELY"}, d["change_pin_nets"])
        self.assertIn("LONELY", d["add_nets"])

    def test_pin_moved_onto_a_net_of_its_own(self):
        # J1-2 goes from GND onto a net of its own: it leaves GND, as a pin
        # with no net would. An auto-named one-pin net (every unconnected
        # pin gets one) is not added; a named one is
        for net, added in (("NetJ1_2", ["OUT", "SENSE"]), ("SOLO", ["OUT", "SENSE", "SOLO"])):
            source = dict(SOURCE, pins=[p for p in SOURCE["pins"] if p != "J1|2|GND"] + [f"J1|2|{net}"])
            d = eco_preview.compare(eco_preview.parse_source(source), eco_preview.parse_target(TARGET))
            self.assertIn({"pin": "J1-2", "from": "GND", "to": ""}, d["change_pin_nets"])
            self.assertEqual(d["add_nets"], added)
            d = eco_preview.compare(eco_preview.parse_source(source), eco_preview.parse_target(TARGET), True)
            self.assertIn({"pin": "J1-2", "from": "GND", "to": net}, d["change_pin_nets"])


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


class ProjectOptionsTest(unittest.TestCase):
    def test_parse(self):
        o = eco_preview.parse_project_options(PRJ)
        self.assertFalse(o["single_pin_nets"])
        self.assertEqual(o["sheets"]["amp.schdoc"], {"component_classes": True, "rooms": False,
                                                     "net_classes": False})
        self.assertFalse(o["sheets"]["power.schdoc"]["component_classes"])
        self.assertNotIn("board.pcbdoc", o["sheets"])
        self.assertEqual((o["ignored_differences"], o["skipped_modifications"]), (set(), set()))
        self.assertFalse(o["manual_component_classes"])
        self.assertFalse(o["other_net_classes"])
        on = eco_preview.parse_project_options(PRJ.replace("NetlistSinglePinNets=0", "NetlistSinglePinNets=1"))
        self.assertTrue(on["single_pin_nets"])

    def test_class_generation(self):
        d = diff_with(PRJ)
        # Power makes no component class, so PWR is not added; the board's
        # OLD class still goes, as Amp makes component classes
        self.assertEqual(d["add_component_classes"], [])
        self.assertEqual(d["remove_component_classes"], [{"name": "OLD", "members": 1}])
        # nothing makes rooms or net classes: the ECO adds and removes none
        self.assertEqual((d["remove_rooms"], d["remove_net_classes"]), ([], []))
        self.assertEqual(skipped(d), {"add_component_classes": 1, "remove_rooms": 1, "remove_net_classes": 1})
        self.assertNotIn("remove_rooms", eco_preview.summary(d))
        self.assertNotIn("skipped_by_project_options", eco_preview.summary(d))
        # other kinds are untouched
        self.assertEqual(d["remove_components"], diff()["remove_components"])

    def test_rooms_from_a_sheet_that_makes_them(self):
        text = PRJ.replace("ClassGenCCAutoEnabled=1\nClassGenCCAutoRoomEnabled=0",
                           "ClassGenCCAutoEnabled=1\nClassGenCCAutoRoomEnabled=1")
        d = diff_with(text)
        self.assertEqual(d["remove_rooms"], [{"name": "OLD", "scope": "InComponentClass('OLD')"}])
        source = dict(SOURCE, rooms=SOURCE["rooms"] + ["PWR|Room PWR"])
        options = eco_preview.parse_project_options(text)
        d = eco_preview.compare(eco_preview.parse_source(source), eco_preview.parse_target(TARGET), False, options)
        # PWR's sheet makes no class, so no room for it either
        self.assertEqual(d["add_rooms"], [])

    def test_comparator_and_eco_generation(self):
        # Type15 = component footprint differences; Type8 = Remove Component;
        # Type17 = Add Node (a pin joining a net)
        text = PRJ.replace("Type15=1", "Type15=0").replace("Type8=1", "Type8=0").replace("Type17=1", "Type17=0")
        d = diff_with(text)
        self.assertEqual((d["change_footprints"], d["remove_components"]), ([], []))
        # only the pin joining a net is left out; one leaving its net is a Remove Node
        self.assertEqual(d["change_pin_nets"], [{"pin": "J1-1", "from": "LONELY", "to": ""}])
        reasons = {s["kind"]: s["reason"] for s in d["skipped_by_project_options"]}
        self.assertIn("Comparator", reasons["change_footprints"])
        self.assertIn("ECO Generation", reasons["remove_components"])
        self.assertIn("ECO Generation", reasons["change_pin_nets"])
        self.assertEqual(d["add_components"], diff()["add_components"])

    def test_without_options_nothing_is_left_out(self):
        for d in (diff(), diff_with("")):
            self.assertEqual(d["skipped_by_project_options"], [])
            self.assertEqual(d["remove_rooms"], [{"name": "OLD", "scope": "InComponentClass('OLD')"}])
            self.assertEqual(d["remove_net_classes"], ["SPARE"])


class OutputTest(unittest.TestCase):
    def test_summary_and_limit(self):
        d = diff()
        self.assertEqual(eco_preview.summary(d)["remove_components"], 1)
        self.assertNotIn("board_only_components", eco_preview.summary(d))
        shown, cut = eco_preview.limit(d, 1)
        self.assertEqual(len(shown["remove_components"]), 1)
        self.assertEqual(sorted(cut), sorted(k for k, v in d.items() if len(v) > 1))
        self.assertNotIn("add_rooms", shown)

    def test_no_differences_note_names_what_is_not_compared(self):
        note = eco_preview.no_differences_note()
        self.assertIn("of the kinds compared", note)
        for kind in ("net class members", "component parameters", "differential pairs",
                     "rules from parameter sets", "room scope"):
            self.assertIn(kind, note)

    def test_fields_with_bars(self):
        src = eco_preview.parse_source({"components": [r"\A|R1|R0603|a|b"]})
        self.assertEqual(src["components"]["\\a"]["comment"], "a|b")


class ToolTest(unittest.TestCase):
    """preview_eco in main.py, with the bridge faked."""

    def run_tool(self, replies, **kwargs):
        import main
        calls = []

        async def fake(command, params, timeout=None):
            calls.append(command)
            return {"success": True, "result": json.dumps(replies[command])}
        with mock.patch.object(main.altium_bridge, "execute_command", fake):
            return json.loads(asyncio.run(main.preview_eco(None, **kwargs))), calls

    def test_same_sides_note_only_what_is_compared(self):
        side = {"components": [r"\S1\A|R1|R0603|10k", r"\S1\B|R2|R0603|10k"],
                "pins": ["R1|1|A", "R1|2|B", "R2|1|A", "R2|2|B"]}
        replies = {"get_eco_source": dict(side, project_path="", pcb_path="board.PcbDoc"),
                   "get_eco_target": {"components": side["components"], "pads": side["pins"]}}
        reply, calls = self.run_tool(replies)
        self.assertEqual(calls, ["get_eco_source", "get_eco_target"])
        self.assertEqual(reply["summary"], {})
        self.assertIn("of the kinds compared", reply["note"])
        self.assertIn("could not be read", reply["project_options_note"])

    def test_project_options_read_from_the_project_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            prj = os.path.join(tmp, "Test.PrjPcb")
            with open(prj, "w", encoding="utf-8", newline="\r\n") as f:
                f.write(PRJ)
            replies = {"get_eco_source": dict(SOURCE, project_path=prj, pcb_path="board.PcbDoc"),
                       "get_eco_target": TARGET}
            reply, _ = self.run_tool(replies)
        self.assertNotIn("project_options_note", reply)
        self.assertNotIn("remove_rooms", reply["summary"])
        self.assertIn({"kind": "remove_rooms", "count": 1,
                       "reason": "the project generates no rooms (Project Options > Class Generation)"},
                      reply["skipped_by_project_options"])

    def test_bad_project_path_refused_before_altium(self):
        reply, calls = self.run_tool({}, project_path="C:/nowhere/x.txt")
        self.assertFalse(reply["success"])
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
