"""Compiler-model reconciliation and expected-net checks, without Altium."""

import ast
import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import schematic_connectivity as connectivity


def pin(designator, number, net, **extra):
    return {"component_uid": designator + "UID", "designator": designator,
            "logical_designator": designator, "pin_number": number, "pin_name": "",
            "part_id": 1, "instance_id": designator + "-" + number,
            "x": 100, "y": 200, "net_name": net, "logical_net_name": net, **extra}


def raw_model():
    model = [pin("R1", "1", "GND"), pin("R2", "2", "GND"), pin("R3", "1", "VCC")]
    return {"success": True, "compile_performed": True, "compile_errors": 0, "compile_fatals": 0,
            "sheet_pins": [{k: v for k, v in p.items() if k not in ("instance_id", "net_name", "logical_net_name")}
                           for p in model], "model_pins": model, "project_net_pin_counts": {"GND": 40, "VCC": 1}}


def selector(designator, number="1", **extra):
    return {"designator": designator, "pin_number": number, **extra}


class ConnectivityTest(unittest.TestCase):
    def setUp(self):
        self.result = connectivity.reconcile(raw_model())

    def check(self, pins, **fields):
        return connectivity.check_connections(self.result, [{"pins": pins, **fields}])

    def test_resolved_coverage_and_project_scope(self):
        self.assertTrue(self.result["complete"])
        self.assertEqual(self.result["pin_count"], 3)
        self.assertEqual(self.result["nets"][0]["project_pin_count"], 40)
        self.assertEqual(self.result["nets"][0]["sheet_pin_count"], 2)

    def test_shared_net_name_and_exact_ignore_other_sheets(self):
        report = self.check([selector("R1"), selector("r2", "2")], net_name="gnd", exact=True)
        self.assertTrue(report["passed"])

    def test_expected_name_mismatch(self):
        report = self.check([selector("R1")], net_name="VCC")
        self.assertFalse(report["passed"])
        self.assertEqual(report["results"][0]["issues"][0]["code"], "unexpected_net")

    def test_open_connection_and_unexpected_short(self):
        self.assertFalse(self.check([selector("R1"), selector("R3")])["passed"])
        self.assertTrue(self.check([selector("R1"), selector("R3")], connected=False)["passed"])
        self.assertFalse(self.check([selector("R1"), selector("R2", "2")], connected=False)["passed"])

    def test_exact_detects_extra_pin_on_sheet(self):
        report = self.check([selector("R1")], net_name="GND", exact=True)
        self.assertEqual(report["results"][0]["issues"][0]["code"], "unexpected_members")

    def test_missing_and_ambiguous_pin_fail(self):
        self.assertFalse(self.check([selector("MISSING")], net_name="GND")["passed"])
        self.result["pins"].append({**self.result["pins"][0], "instance_id": "other-channel", "index": 3})
        self.assertFalse(self.check([selector("R1")], net_name="GND")["passed"])
        self.assertTrue(self.check([selector("R1", instance_id="R1-1")], net_name="GND")["passed"])

    def test_unknown_nets_do_not_prove_connection_or_disconnection(self):
        raw = raw_model()
        raw["model_pins"][0]["net_name"] = "?"
        self.result = connectivity.reconcile(raw)
        self.assertFalse(self.result["complete"])
        self.assertIsNone(self.result["pins"][0]["net_id"])
        self.assertFalse(self.check([selector("R1"), selector("R3")], connected=False)["passed"])

    def test_missing_compiler_pins_are_retained(self):
        raw = raw_model()
        raw["model_pins"].pop(0)
        result = connectivity.reconcile(raw)
        self.assertEqual(result["pin_count"], 3)
        self.assertEqual(result["missing_model_pin_count"], 1)
        self.assertEqual(result["pins"][0]["resolution"], "not_in_compiled_model")

    def test_unused_compiler_pins_are_reported(self):
        raw = raw_model()
        raw["model_pins"].append(pin("UNUSED", "1", "?"))
        result = connectivity.reconcile(raw)
        self.assertEqual(len(result["model_only_pins"]), 1)
        self.assertFalse(result["complete"])

    def test_compiler_coordinates_override_editor_math(self):
        raw = raw_model()
        raw["model_pins"][0]["x"] = 123.456
        result = connectivity.reconcile(raw)
        self.assertEqual(result["pins"][0]["x"], 123.456)
        self.assertEqual(result["pins"][0]["editor_x"], 100)
        self.assertFalse(result["pins"][0]["geometry_matches"])

    def test_repeated_channels_and_shared_pins(self):
        raw = raw_model()
        raw["sheet_pins"][0]["part_id"] = 0
        raw["model_pins"].append({**raw["model_pins"][0], "designator": "R1_2", "instance_id": "channel2/R1-1"})
        raw["model_pins"].append(copy.deepcopy(raw["model_pins"][0]))
        result = connectivity.reconcile(raw)
        self.assertEqual(result["pin_count"], 4)
        self.assertTrue(result["complete"])

    def test_cached_mode_is_explicit(self):
        raw = raw_model()
        raw["compile_performed"] = False
        result = connectivity.reconcile(raw)
        self.assertTrue(result["may_be_stale"])
        self.assertTrue(result["warnings"])

    def test_contradictory_compiler_records_do_not_choose_a_net(self):
        raw = raw_model()
        raw["model_pins"].append({**raw["model_pins"][0], "net_name": "VCC"})
        self.result = connectivity.reconcile(raw)
        self.assertFalse(self.result["complete"])
        self.assertTrue(any(p["resolution"] == "ambiguous_model" for p in self.result["pins"]))
        self.assertFalse(self.check([selector("R1")], net_name="GND")["passed"])

    def test_pin_numbers_remain_strings_and_leading_zero_is_significant(self):
        self.assertFalse(self.check([selector("R1", "01")], net_name="GND")["passed"])


class BoundaryTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2] / ".altium-mcp" / "unit_tests"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.sheet = Path(self.temp.name) / "test.SchDoc"
        self.sheet.touch()

    def test_valid_request_and_optional_checks(self):
        for checks in (None, [], [{"pins": [selector("R1")], "net_name": "GND"}]):
            self.assertEqual(connectivity.validate_request(str(self.sheet), True, checks), str(self.sheet))

    def test_malformed_requests_rejected(self):
        for checks in ({}, [None], [{"pins": []}], [{"pins": [selector("R1")]}],
                       [{"pins": [selector("R1", 1)], "net_name": "GND"}],
                       [{"pins": [selector("R1"), selector("r1")]}],
                       [{"pins": [selector("R1")], "net_name": "GND", "unexpected": True}],
                       [{"pins": [selector("R1"), selector("R2")], "connected": 0}],
                       [{"pins": [selector("R1"), selector("R2")], "connected": False, "net_name": "GND"}],
                       [{"pins": [selector("R1", part_id=-1)], "net_name": "GND"}]):
            with self.subTest(checks=checks), self.assertRaises(ValueError):
                connectivity.validate_request(str(self.sheet), True, checks)
        for path, compiled in (("relative.SchDoc", True), (str(self.sheet), "true"), (str(self.sheet.with_suffix(".SchLib")), True)):
            with self.assertRaises(ValueError):
                connectivity.validate_request(path, compiled, None)

    def test_mcp_dispatch_validates_before_execution(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8"))
        function = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "get_schematic_connectivity")
        function.decorator_list = []
        bridge = type("Bridge", (), {})()
        bridge.execute_command = AsyncMock(return_value={"success": True, "result": raw_model()})
        scope = {"Context": object, "Optional": __import__("typing").Optional, "json": json,
                 "schematic_connectivity": connectivity, "altium_bridge": bridge}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "connectivity_tool", "exec"), scope)
        tool = scope["get_schematic_connectivity"]
        result = json.loads(asyncio.run(tool(None, str(self.sheet), True, [{"pins": [selector("R1")], "net_name": "GND"}])))
        self.assertTrue(result["checks"]["passed"])
        bridge.execute_command.assert_awaited_once_with("get_schematic_connectivity", {"sheet_path": str(self.sheet), "compiled": True})
        bridge.execute_command.reset_mock()
        self.assertFalse(json.loads(asyncio.run(tool(None, "bad.SchDoc")))["success"])
        bridge.execute_command.assert_not_awaited()
        bridge.execute_command.return_value = {"success": False, "error": "no compiled model"}
        self.assertFalse(json.loads(asyncio.run(tool(None, str(self.sheet), False)))["success"])


if __name__ == "__main__":
    unittest.main()
