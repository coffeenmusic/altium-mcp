"""Unit tests for project_options (pure Python, no Altium needed).

Run from server/:  python -m unittest tests.test_project_options -v
"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import project_options as po  # noqa: E402

PRJ = ("\ufeff" + "\r\n".join([
    "[Design]",
    "Version=1.0",
    "HierarchyMode=2",
    "ChannelDesignatorFormatString=$Component_$RoomName",
    "NetlistSinglePinNets=0",
    "AutoCrossReferences=1",
    "CrossRefSheetStyle=1",
    "CrossRefPorts=3",
    "",
    "[Document1]",
    "DocumentPath=Power.SchDoc",
    "ClassGenCCAutoEnabled=1",
    "ClassGenCCAutoRoomEnabled=0",
    "ClassGenNCAutoScope=None",
    "",
    "[Document2]",
    r"DocumentPath=SCH_Heater\Heater.SchDoc",
    "ClassGenCCAutoEnabled=1",
    "ClassGenCCAutoRoomEnabled=1",
    "",
    "[Document3]",
    "DocumentPath=Board.PcbDoc",
    "",
    "[PrjClassGen]",
    "CompClassManualEnabled=1",
    "CompClassManualRoomEnabled=1",
    "",
    "[Comparison Options]",
    "ComparisonOptions0=Kind=Net|MinPercent=75|MinMatch=3|ShowMatch=0|UseName=-1|InclAllRules=0",
    "ComparisonOptions1=Kind=Differential Pair|MinPercent=50|MinMatch=1|ShowMatch=0|UseName=0|InclAllRules=0",
    "",
])).encode("utf-8")


class ReadTest(unittest.TestCase):
    def test_groups_sheets_and_comparison(self):
        o = po.read_options(PRJ)
        self.assertTrue(o["cross_references"]["auto_cross_references"])
        self.assertEqual(o["cross_references"]["sheet_style"], 1)
        # not a boolean: kept as the stored number
        self.assertEqual(o["cross_references"]["ports"], 3)
        self.assertIsNone(o["cross_references"]["location_style"])          # not in the file
        self.assertFalse(o["netlist"]["single_pin_nets"])
        self.assertEqual(o["netlist"]["hierarchy_mode"], 2)
        self.assertEqual(o["multichannel"]["designator_format"], "$Component_$RoomName")
        self.assertTrue(o["class_generation"]["rooms_for_directive_classes"])
        self.assertEqual([s["sheet"] for s in o["sheets"]], ["Power.SchDoc", r"SCH_Heater\Heater.SchDoc"])
        self.assertEqual((o["sheets"][0]["room"], o["sheets"][1]["room"]), (False, True))
        self.assertEqual(o["sheets"][0]["net_classes"], "None")
        self.assertEqual(o["comparison"][0], {"kind": "Net", "min_match_percent": 75, "min_match_members": 3,
                                              "match_by_name": True})
        self.assertFalse(o["comparison"][1]["match_by_name"])


class ChangeTest(unittest.TestCase):
    def test_changes_only_their_lines(self):
        new, applied, errors = po.change_options(PRJ, {
            "cross_references": {"auto_cross_references": False, "ports": 1, "location_style": 2},
            "sheets": {"SCH_Heater\\*": {"room": False}, "power.schdoc": {"component_class": True}},
            "raw": [{"section": "Design", "key": "Custom", "value": "x"}]})
        self.assertEqual(errors, [])
        self.assertTrue(new.startswith(b"\xef\xbb\xbf"))
        old_lines, new_lines = PRJ.decode("utf-8").split("\r\n"), new.decode("utf-8").split("\r\n")
        changed = [(a, b) for a, b in zip(old_lines, new_lines) if a != b]
        self.assertIn(("AutoCrossReferences=1", "AutoCrossReferences=0"), changed)
        self.assertIn(("CrossRefPorts=3", "CrossRefPorts=1"), changed)
        # missing keys go at the end of their section, before its blank line
        design = new.decode("utf-8").split("\r\n\r\n")[0].split("\r\n")
        self.assertEqual(design[-2:], ["CrossRefLocationStyle=2", "Custom=x"])
        self.assertIn(r"ClassGenCCAutoRoomEnabled=0", new.decode("utf-8").split("[Document2]")[1].split("[")[0])
        self.assertEqual(len(new_lines), len(old_lines) + 2)
        # component_class was already on for Power: not reported
        self.assertEqual([a.get("option") for a in applied],
                         ["cross_references.auto_cross_references", "cross_references.ports",
                          "cross_references.location_style", "room", None])
        self.assertEqual(applied[3], {"sheet": r"SCH_Heater\Heater.SchDoc", "option": "room", "from": True,
                                      "to": False})

    def test_new_section_and_round_trip(self):
        same, applied, _ = po.change_options(PRJ, {"netlist": {"single_pin_nets": False}})
        self.assertEqual((same, applied), (PRJ, []))
        new, _, _ = po.change_options(PRJ, {"raw": [{"section": "Extra", "key": "A", "value": "1"}]})
        self.assertTrue(new.decode("utf-8").endswith("[Extra]\r\nA=1\r\n\r\n"))

    def test_errors_change_nothing(self):
        for changes, msg in (({"netlist": {"single_pin_nets": "yes"}}, "true or false"),
                             ({"cross_references": {"sheet_style": "big"}}, "whole number"),
                             ({"netlist": {"nope": 1}}, "unknown option"),
                             ({"sheets": {"Missing.SchDoc": {"room": True}}}, "no sheet matches"),
                             ({"sheets": {"*": {"colour": 1}}}, "unknown option"),
                             ({"weird": {}}, "unknown group"),
                             ({"multichannel": {"designator_format": "a\nb"}}, "line break"),
                             ({"raw": [{"section": "Design"}]}, "needs section and key"),
                             # a cross-reference flag is a number whose meaning is not mapped
                             ({"cross_references": {"ports": True}}, "whole number"),
                             # line injection through a raw key or section
                             ({"raw": [{"section": "Design", "key": "A=1\r\nInjected", "value": "1"}]}, "line breaks"),
                             ({"raw": [{"section": "Design", "key": "A\nB", "value": "1"}]}, "line breaks"),
                             ({"raw": [{"section": "Design]\n[Other", "key": "A", "value": "1"}]}, "line breaks"),
                             ({"raw": [{"section": "Design", "key": "A", "value": "1\r\n[X]"}]}, "line break"),
                             # bad input shapes
                             (True, "changes must be an object"),
                             ({"netlist": True}, "netlist must be an object"),
                             ({"sheets": {"*": False}}, "must be an object of options"),
                             ({"raw": {"section": "Design"}}, "raw must be a list"),
                             ({"raw": ["Design/A=1"]}, "each item must be"),
                             ({"multichannel": {"designator_format": {"a": 1}}}, "expected text")):
            new, applied, errors = po.change_options(PRJ, changes)
            self.assertEqual((new, applied), (PRJ, []), changes)
            self.assertIn(msg, errors[0], changes)

    def test_lf_file_without_bom(self):
        data = PRJ.decode("utf-8").lstrip("\ufeff").replace("\r\n", "\n").encode("utf-8")
        new, applied, _ = po.change_options(data, {"netlist": {"single_pin_nets": True}})
        self.assertFalse(new.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r\n", new)
        self.assertIn(b"NetlistSinglePinNets=1\n", new)



class SetToolTest(unittest.TestCase):
    """set_project_options in main.py, with the bridge faked."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.prj = Path(self.dir.name) / "Main.PrjPcb"
        self.prj.write_bytes(PRJ)
        self.calls = []

    def tearDown(self):
        self.dir.cleanup()

    def run_tool(self, changes, close_reply, on_close=None):
        import main

        async def fake(command, params, timeout=None):
            self.calls.append(command)
            if command == "close_project_for_edit":
                if on_close:
                    on_close()
                reply = close_reply
            else:
                reply = {"project_open": True, "reopened": []}
            return {"success": True, "result": json.dumps(reply)}
        with mock.patch.object(main.altium_bridge, "execute_command", fake), \
                mock.patch.object(main, "EXCHANGE_DIR", Path(self.dir.name)):
            return json.loads(asyncio.run(main.set_project_options(None, changes, project_path=str(self.prj))))

    def test_not_closed_writes_nothing(self):
        reply = self.run_tool({"netlist": {"single_pin_nets": True}},
                              {"was_open": True, "closed": False, "open_documents": []})
        self.assertFalse(reply["success"])
        self.assertIn("did not close", reply["error"])
        self.assertEqual(self.prj.read_bytes(), PRJ)
        self.assertEqual(self.calls, ["close_project_for_edit", "reopen_project"])

    def test_read_after_the_close(self):
        # Altium writes its copy while closing: the change goes on top of that
        def altium_writes():
            self.prj.write_bytes(PRJ.replace(b"HierarchyMode=2", b"HierarchyMode=1"))
        reply = self.run_tool({"netlist": {"single_pin_nets": True}},
                              {"was_open": True, "closed": True, "open_documents": []}, altium_writes)
        self.assertTrue(reply["success"])
        text = self.prj.read_bytes()
        self.assertIn(b"HierarchyMode=1", text)
        self.assertIn(b"NetlistSinglePinNets=1", text)
        self.assertTrue(reply["closed_and_reopened"])

    def test_bad_input_never_reaches_altium(self):
        reply = self.run_tool({"netlist": True}, {})
        self.assertFalse(reply["success"])
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
