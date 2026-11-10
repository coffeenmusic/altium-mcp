"""Unit tests for pcb_rules (pure Python, no Altium needed).

Run from server/:  python -m unittest tests.test_pcb_rules -v
"""
import asyncio
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from pcb_rules import RULE_KINDS, check_merged, encode_values, move_notes, scope_or_all  # noqa: E402


class EncodeTest(unittest.TestCase):
    def test_mils_become_internal_units(self):
        self.assertEqual(encode_values("clearance", {"gap_mils": 8}), ("gap=80000", ""))
        self.assertEqual(encode_values("clearance", {"gap_mils": 7.874}), ("gap=78740", ""))

    def test_width_needs_all_three_and_min_not_above_max(self):
        self.assertEqual(encode_values("width", {"min_mils": 6, "max_mils": 40, "preferred_mils": 10}),
                         ("min=60000;max=400000;preferred=100000", ""))
        self.assertIn("needs", encode_values("width", {"min_mils": 6})[1])
        self.assertIn("larger", encode_values("width", {"min_mils": 50, "max_mils": 40, "preferred_mils": 45})[1])

    def test_mask_expansion_may_be_negative_gap_may_not(self):
        self.assertEqual(encode_values("solder_mask_expansion", {"expansion_mils": -2}), ("expansion=-20000", ""))
        self.assertIn("negative", encode_values("clearance", {"gap_mils": -1})[1])

    def test_polygon_connect(self):
        self.assertEqual(encode_values("polygon_connect", {"connect_style": "Relief", "entries": 2,
                                                           "air_gap_mils": 12}),
                         ("connect_style=relief;air_gap=120000;entries=2", ""))
        self.assertEqual(encode_values("polygon_connect", {"connect_style": "direct"}),
                         ("connect_style=direct", ""))
        self.assertIn("one of", encode_values("polygon_connect", {"connect_style": "thermal"})[1])
        self.assertIn("2 or 4", encode_values("polygon_connect", {"connect_style": "relief",
                                                                  "entries": 2.5})[1])
        self.assertIn("2 or 4", encode_values("polygon_connect", {"connect_style": "relief", "entries": 3})[1])

    def test_preferred_between_min_and_max(self):
        self.assertIn("between", encode_values("width", {"min_mils": 6, "max_mils": 10, "preferred_mils": 20})[1])
        self.assertIn("between", encode_values("width", {"min_mils": 6, "max_mils": 10, "preferred_mils": 5})[1])

    def test_via_holes_inside_their_range_and_smaller_than_the_via(self):
        via = {"min_diameter_mils": 18, "max_diameter_mils": 40, "preferred_diameter_mils": 20,
               "min_hole_mils": 8, "max_hole_mils": 20, "preferred_hole_mils": 10}
        self.assertEqual(encode_values("routing_via", via)[1], "")
        self.assertIn("between", encode_values("routing_via", dict(via, preferred_hole_mils=25))[1])
        self.assertIn("smaller than preferred_diameter",
                      encode_values("routing_via", dict(via, preferred_hole_mils=20))[1])

    def test_unknown_kind_and_key(self):
        self.assertIn("unknown rule_type", encode_values("fanout", {})[1])
        self.assertIn("not gap", encode_values("clearance", {"gap": 8})[1])
        self.assertIn("must be a number", encode_values("clearance", {"gap_mils": "eight"})[1])

    def test_every_kind_has_values(self):
        for kind, spec in RULE_KINDS.items():
            self.assertTrue(spec, kind)


class PartialTest(unittest.TestCase):
    def test_partial_values(self):
        self.assertEqual(encode_values("clearance", {}, partial=True), ("", ""))
        self.assertEqual(encode_values("routing_via", {"min_hole_mils": 8}, partial=True), ("min_hole=80000", ""))
        self.assertIn("needs", encode_values("routing_via", {"min_hole_mils": 8})[1])
        self.assertIn("not", encode_values("clearance", {"width": 1}, partial=True)[1])
        self.assertIn("larger", encode_values("hole_size", {"min_mils": 9, "max_mils": 8}, partial=True)[1])


class MergedTest(unittest.TestCase):
    """An edit's values are checked together with the rule's other values."""

    def test_min_above_the_current_max_is_refused(self):
        current = {"min_mils": 5, "max_mils": 10, "preferred_mils": 8}
        error = check_merged("width", current, {"min_mils": 20})
        self.assertIn("min_mils is larger than max_mils", error)
        self.assertIn("max_mils=10", error)
        self.assertEqual(check_merged("width", current, {"min_mils": 6}), "")
        self.assertIn("between", check_merged("width", current, {"preferred_mils": 12}))

    def test_via_hole_against_current_diameter(self):
        current = {"min_diameter_mils": 16, "max_diameter_mils": 30, "preferred_diameter_mils": 20,
                   "min_hole_mils": 8, "max_hole_mils": 15, "preferred_hole_mils": 10}
        self.assertIn("smaller", check_merged("routing_via", current, {"preferred_hole_mils": 20,
                                                                        "max_hole_mils": 25}))
        self.assertEqual(check_merged("routing_via", current, {"preferred_hole_mils": 12}), "")

    def test_values_the_rule_does_not_have_are_ignored(self):
        self.assertEqual(check_merged("clearance", {"gap_mils": 8, "other": 1}, {"gap_mils": 10}), "")


class ScopeTest(unittest.TestCase):
    def test_empty_scope_is_all(self):
        self.assertEqual(scope_or_all(""), "All")
        self.assertEqual(scope_or_all("   "), "All")
        self.assertEqual(scope_or_all(None), "All")
        self.assertEqual(scope_or_all(" InNet('GND') "), "InNet('GND')")


class MoveNotesTest(unittest.TestCase):
    def test_notes(self):
        self.assertEqual(move_notes({"rules_moved": 0, "priority_before": 2, "priority": 2}, "R"), {})
        notes = move_notes({"rules_moved": 3, "priority_before": 4, "priority": 2, "name": "R",
                            "readded_from_design": ["Directive1"], "unique_id_changed": []}, "R")
        self.assertIn("priority=4", notes["undo"])
        self.assertIn("Directive1", notes["move_warning"])
        # an edit that put the rule back where it was has nothing to undo by priority
        self.assertNotIn("undo", move_notes({"rules_moved": 2, "priority_before": 2, "priority": 2}, "R"))


def call_tool(tool, replies, **kwargs):
    """Run a main tool with the bridge faked; returns (reply, commands sent)."""
    import main
    sent = []

    async def fake(command, params, timeout=None, watcher=None):
        sent.append((command, params))
        return replies.get(command, {"success": False, "error": "not faked"})
    with mock.patch.object(main.altium_bridge, "execute_command", fake):
        reply = asyncio.run(getattr(main, tool)(None, **kwargs))
    return json.loads(reply), sent


class EditToolTest(unittest.TestCase):
    INFO = {"success": True, "result": {"name": "W", "kind": "width", "per_layer_widths": False,
                                        "values": {"min_mils": 5, "max_mils": 10, "preferred_mils": 8}}}

    def test_blank_new_name_is_refused(self):
        reply, sent = call_tool("edit_pcb_rule", {}, name="W", new_name="  ")
        self.assertIn("new_name is empty", reply["error"])
        self.assertEqual(sent, [])

    def test_merged_values_are_checked(self):
        reply, sent = call_tool("edit_pcb_rule", {"pcb_rule_info": self.INFO}, name="W", values={"min_mils": 20})
        self.assertIn("larger", reply["error"])
        self.assertEqual([c for c, _ in sent], ["pcb_rule_info"])

    def test_per_layer_widths_are_refused(self):
        info = {"success": True, "result": dict(self.INFO["result"], per_layer_widths=True)}
        reply, _ = call_tool("edit_pcb_rule", {"pcb_rule_info": info}, name="W", values={"min_mils": 6})
        self.assertIn("per layer", reply["error"])

    def test_empty_scope_and_stripped_name_are_sent(self):
        edited = {"success": True, "result": {"success": True, "name": "W2", "rules_moved": 0}}
        reply, sent = call_tool("edit_pcb_rule", {"pcb_rule_info": self.INFO, "edit_pcb_rule": edited},
                                name="W", scope1="", new_name=" W2 ")
        params = dict(sent)["edit_pcb_rule"]
        self.assertEqual(params["scope1"], "All")
        self.assertEqual(params["new_name"], "W2")
        self.assertTrue(reply["success"])


class PriorityToolTest(unittest.TestCase):
    def test_bool_is_not_a_priority(self):
        reply, sent = call_tool("set_rule_priority", {}, name="R", priority=True)
        self.assertIn("whole number", reply["error"])
        self.assertEqual(sent, [])

    def test_priority_is_checked_and_clamped(self):
        for bad in (0, -3, 2.0, "2", None):
            reply, sent = call_tool("set_rule_priority", {}, name="R", priority=bad)
            self.assertIn("whole number", reply["error"])
            self.assertEqual(sent, [])
        moved = {"success": True, "result": {"success": True, "name": "R", "rules_moved": 0}}
        # past 2^31 the bridge's StrToInt would overflow; beyond the last rule means last anyway
        _, sent = call_tool("set_rule_priority", {"set_rule_priority": moved}, name="R", priority=2 ** 40)
        self.assertEqual(dict(sent)["set_rule_priority"]["priority"], 1_000_000)
        _, sent = call_tool("set_rule_priority", {"set_rule_priority": moved}, name="R", priority=3)
        self.assertEqual(dict(sent)["set_rule_priority"]["priority"], 3)


if __name__ == "__main__":
    unittest.main()
