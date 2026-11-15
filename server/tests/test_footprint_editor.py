"""Footprint edit validation, transport, failure semantics and MCP dispatch."""

import ast
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from typing import Optional
import unittest
from unittest.mock import AsyncMock, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import footprint_editor as editor
from altium_files import read_altium_text, write_altium_text


class FootprintEditorTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2] / ".altium-mcp" / "unit_tests"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = self.root / "Parts_\u03a9.PcbLib"
        self.library.touch()
        self.operation = {"target": {"type": "track", "layer": "No Layer",
                                     "x1": 0, "y1": 1, "x2": 20, "y2": 1, "width": 5},
                          "layer": "Mechanical Layer 13"}

    def pads(self, **overrides):
        args = dict(path=str(self.library), footprint_name="TEST", pad_names=["1", "2"],
                    mask_expansion_mm=0.05, paste_expansion_mm=0)
        args.update(overrides)
        return editor.prepare_pads(**args)

    def test_manual_pad_units_and_omitted_setting(self):
        spec = self.pads(paste_expansion_mm=None)
        self.assertEqual(spec["mask_mode"], "2")
        self.assertAlmostEqual(float(spec["mask_expansion"]) * .0254, .05)
        self.assertNotIn("paste_mode", spec)
        self.assertEqual(self.pads()["paste_expansion"], "0")

    def test_rule_reset_is_explicit(self):
        spec = self.pads(mode="rule", mask_expansion_mm=None, paste_expansion_mm=None)
        self.assertEqual((spec["mask_mode"], spec["paste_mode"]), ("1", "1"))
        with self.assertRaises(ValueError):
            self.pads(mode="rule")

    def test_invalid_pad_requests(self):
        for override in ({"pad_names": []}, {"pad_names": ["A", "a"]},
                         {"pad_names": [1]}, {"mask_expansion_mm": float("nan")},
                         {"mask_expansion_mm": True}, {"paste_expansion_mm": 11},
                         {"mode": "none"}, {"mask_expansion_mm": None, "paste_expansion_mm": None}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                self.pads(**override)

    def test_layer_selector_and_aliases(self):
        spec = editor.prepare_layers(str(self.library), "TEST", [self.operation])
        self.assertEqual(spec["op.0.to_layer"], "Mechanical 13")
        self.assertEqual(spec["op.0.from_layer"], "No Layer")
        self.assertEqual(editor.layer_name("eMechanical15"), "Mechanical 15")
        self.assertEqual(editor.layer_name("Mechanical13"), "Mechanical 13")

    def test_rejects_incomplete_duplicate_and_unsupported_selectors(self):
        for operations in ([], [self.operation, self.operation], [{"target": {"type": "text"}, "layer": "Top Overlay"}],
                           [{"target": {"type": "track", "layer": "No Layer"}, "layer": "Top Overlay"}],
                           [{**self.operation, "delete": True}], [{**self.operation, "layer": "No Layer"}],
                           [{**self.operation, "layer": "Mechanical 17"}]):
            with self.subTest(operations=operations), self.assertRaises(ValueError):
                editor.prepare_layers(str(self.library), "TEST", operations)

    def test_constants_are_a_whitelist(self):
        self.assertEqual(editor.prepare_constants(["eRoundedRectangular"])["name.0"], "eRoundedRectangular")
        for names in ([], ["Client.CloseDocument"], ["eRound", "eRound"], [1]):
            with self.assertRaises(ValueError):
                editor.prepare_constants(names)

    def test_batch_normalizes_private_copy_and_preserves_source(self):
        source = self.root / "spec.txt"
        write_altium_text(source, f"FPLIB|{self.library}\nFOOTPRINT|TEST|Test\nTRACK|0|0|1|1|2|Mechanical Layer 13\n")
        original = source.read_bytes()
        prepared = editor.prepare_batch_file(source, self.root)
        self.assertNotEqual(source, prepared)
        self.assertEqual(source.read_bytes(), original)
        self.assertIn("|Mechanical 13\n", read_altium_text(prepared))
        self.assertIn(str(self.library), read_altium_text(prepared))

    def test_batch_rejects_no_layer_and_missing_layer(self):
        source = self.root / "spec.txt"
        for record in ("TRACK|0|0|1|1|2|No Layer", "ARC|0"):
            write_altium_text(source, record)
            with self.assertRaises(ValueError):
                editor.prepare_batch_file(source, self.root)
        write_altium_text(source, "TEXT|0|0|1|1|0|Mechanical Layer 32")
        self.assertIn("|Mechanical 32", read_altium_text(editor.prepare_batch_file(source, self.root)))

    def bridge(self, result=None, **response):
        if result is not None:
            response["result"] = result
        return type("Bridge", (), {"execute_command": AsyncMock(return_value=response)})()

    def execute(self, bridge):
        return json.loads(asyncio.run(editor.execute(
            bridge, self.root, "set_footprint_pad_settings", editor.prepare_pads,
            str(self.library), "TEST", ["1"], .05, 0)))

    def native_result(self):
        return {"success": True, "saved": False, "library_path": str(self.library),
                "footprint_name": "TEST", "updated_count": 1, "verified_count": 1}

    def test_completed_success_checks_identity_and_cleans_spec(self):
        bridge = self.bridge(self.native_result(), success=True)
        result = self.execute(bridge)
        self.assertTrue(result["verification"]["passed"])
        command, params = bridge.execute_command.call_args.args
        self.assertEqual(command, "set_footprint_pad_settings")
        self.assertFalse(Path(params["spec_file"]).exists())

    def test_lifted_native_refusal_is_known_and_clean(self):
        bridge = self.bridge({"success": False, "error": "Selector missing", "may_have_partial_changes": False}, success=False)
        result = self.execute(bridge)
        self.assertFalse(result["may_have_partial_changes"])
        self.assertNotIn("outcome_unknown", result)
        self.assertEqual(list(self.root.glob("set_footprint_pad_settings_*.txt")), [])

    def test_timeout_retains_spec_and_unicode(self):
        result = self.execute(self.bridge(error="timeout"))
        self.assertTrue(result["outcome_unknown"])
        self.assertTrue(result["may_have_partial_changes"])
        spec = Path(result["spec_file"])
        self.assertTrue(spec.read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertIn("Parts_\u03a9.PcbLib", read_altium_text(spec))

    def test_guard_refusal_does_not_claim_unknown_mutation(self):
        result = self.execute(self.bridge(success=False, error="Not run: Altium is not responding", command_started=False))
        self.assertFalse(result["may_have_partial_changes"])
        self.assertNotIn("outcome_unknown", result)
        self.assertEqual(list(self.root.glob("set_footprint_pad_settings_*.txt")), [])

    def test_readback_mismatch_cannot_report_success(self):
        for changes in ({"library_path": str(self.root / "Other.PcbLib")}, {"library_path": None},
                        {"verified_count": 0}, {"verified_count": True}, {"saved": True}, {"footprint_name": "OTHER"}):
            result = self.execute(self.bridge({**self.native_result(), **changes}))
            self.assertFalse(result["success"])
            self.assertTrue(result["may_have_partial_changes"])

    def test_invalid_request_never_dispatches(self):
        bridge = self.bridge()
        result = json.loads(asyncio.run(editor.execute(
            bridge, self.root, "edit_footprint_primitives", editor.prepare_layers,
            str(self.library), "TEST", [])))
        self.assertFalse(result["success"])
        self.assertFalse(result["may_have_partial_changes"])
        bridge.execute_command.assert_not_called()

    def test_mcp_wrappers_dispatch_to_validated_editor(self):
        names = {"set_footprint_pad_settings", "edit_footprint_primitives", "get_pcb_api_constants"}
        tree = ast.parse((Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8"))
        functions = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name in names]
        for node in functions:
            node.decorator_list = []
        bridge = self.bridge(self.native_result())
        namespace = {"Context": object, "Optional": Optional, "footprint_editor": editor,
                     "altium_bridge": bridge, "EXCHANGE_DIR": self.root}
        exec(compile(ast.Module(body=functions, type_ignores=[]), "wrappers", "exec"), namespace)
        result = json.loads(asyncio.run(namespace["set_footprint_pad_settings"](
            None, str(self.library), "TEST", ["1"], .05, 0)))
        self.assertTrue(result["success"])
        result = json.loads(asyncio.run(namespace["edit_footprint_primitives"](
            None, str(self.library), "TEST", [self.operation])))
        self.assertTrue(result["success"])
        bridge.execute_command.return_value = {"success": True, "result": {
            "success": True, "saved": False, "constants": {"eRound": 1},
            "checked_in_running_altium": True,
            "availability": {"eRound": dict(available=False, resolved=True, alias=True, native_name="eRounded")}}}
        result = json.loads(asyncio.run(namespace["get_pcb_api_constants"](None, ["eRound"])))
        self.assertTrue(result["success"])
        self.assertEqual({call.args[0] for call in bridge.execute_command.call_args_list}, names)

    def test_constants_invalid_result_is_read_only_failure(self):
        result = json.loads(asyncio.run(editor.execute(
            self.bridge({"success": True, "saved": False, "constants": {"wrong": 1}}),
            self.root, "get_pcb_api_constants", editor.prepare_constants, ["eRound"])))
        self.assertFalse(result["success"])
        self.assertFalse(result["may_have_partial_changes"])

    def test_constants_alias_zero_and_partial_unavailability(self):
        def run(native):
            return json.loads(asyncio.run(editor.execute(self.bridge(native), self.root,
                "get_pcb_api_constants", editor.prepare_constants, ["eRound", "eNoLayer"])))
        result = dict(success=True, saved=False, constants=dict(eRound=1, eNoLayer=0),
                      checked_in_running_altium=True, availability=dict(
                          eRound=dict(available=False, resolved=True, alias=True, native_name="eRounded"),
                          eNoLayer=dict(available=True, resolved=True, alias=False, native_name="eNoLayer")))
        self.assertTrue(run(result)['verification']['passed'])
        partial = json.loads(json.dumps(result))
        partial.update(success=False, constants=dict(eNoLayer=0))
        partial['availability']['eRound']['resolved'] = False
        self.assertFalse(run(partial)['success'])
        for key, value in (("alias", False), ("native_name", "eNoLayer"), ("available", True), ("resolved", "yes")):
            invalid = json.loads(json.dumps(result))
            invalid['availability']['eRound'][key] = value
            with self.subTest(key=key):
                checked = run(invalid)
                self.assertFalse(checked['success'])
                self.assertFalse(checked['may_have_partial_changes'])

    def test_screenshot_library_request_and_wrong_kind_guard(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8"))
        function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "get_screenshot")
        function.decorator_list = []
        bridge = self.bridge({"focused_kind": "PCB", "focused_document": "Board.PcbDoc"}, success=True)
        gui = Mock()
        namespace = {"Context": object, "logger": Mock(), "win32gui": gui,
                     "altium_bridge": bridge, "json": json}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "screenshot", "exec"), namespace)
        for kind in ("schlib", "pcblib"):
            result = json.loads(asyncio.run(namespace["get_screenshot"](None, kind)))
            self.assertFalse(result["success"])
            self.assertEqual(result["focused_kind"], "PCB")
            self.assertEqual(bridge.execute_command.call_args.args[1], {"view_type": kind})
        bridge.execute_command.return_value = {"success": True, "result": {}}
        result = json.loads(asyncio.run(namespace["get_screenshot"](None, "schlib")))
        self.assertFalse(result["success"])
        bridge.execute_command.reset_mock()
        for kind, zoom in (("invalid", None), ("schlib", ["U1"]), ("pcblib", ["U1"])):
            self.assertFalse(json.loads(asyncio.run(namespace["get_screenshot"](None, kind, zoom)))["success"])
        bridge.execute_command.assert_not_called()

    def test_batch_mcp_preserves_native_refusal_and_unknown_request(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8"))
        function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "create_footprints_batch")
        function.decorator_list = []
        source = self.root / "batch.txt"
        write_altium_text(source, "FOOTPRINT|TEST|Fixture\nTRACK|0|0|10|0|5|MCP_UNKNOWN_LAYER\n")
        bridge = self.bridge({"success": False, "error": "Unknown layer", "created": 0,
                              "may_have_partial_changes": False}, success=False)
        namespace = {"Context": object, "logger": Mock(), "json": json, "footprint_editor": editor,
                     "altium_bridge": bridge, "EXCHANGE_DIR": self.root}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "batch", "exec"), namespace)
        result = json.loads(asyncio.run(namespace["create_footprints_batch"](None, str(source))))
        self.assertFalse(result["success"])
        self.assertFalse(result["may_have_partial_changes"])
        self.assertEqual(result["created"], 0)
        self.assertFalse(Path(bridge.execute_command.call_args.args[1]["spec_file"]).exists())
        bridge.execute_command.return_value = {"success": False, "error": "timeout"}
        result = json.loads(asyncio.run(namespace["create_footprints_batch"](None, str(source))))
        self.assertTrue(result["outcome_unknown"])
        self.assertTrue(Path(result["spec_file"]).exists())
        self.assertNotEqual(result["spec_file"], str(source))

    def test_mcp_catalog_exposes_new_tools_and_annotations(self):
        from mcp.server.fastmcp import FastMCP, Context
        from mcp.types import ToolAnnotations
        tree = ast.parse((Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8"))
        names = {"set_footprint_pad_settings", "edit_footprint_primitives", "get_pcb_api_constants"}
        nodes = [node for node in tree.body if
                 (isinstance(node, ast.FunctionDef) and node.name == "_tool") or
                 (isinstance(node, ast.AsyncFunctionDef) and node.name in names)]
        mcp = FastMCP("FootprintEditorTest")
        namespace = {"mcp": mcp, "READ_ONLY": ToolAnnotations(readOnlyHint=True), "Context": Context,
                     "Optional": Optional, "footprint_editor": editor, "altium_bridge": self.bridge(), "EXCHANGE_DIR": self.root}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "catalog", "exec"), namespace)
        tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
        self.assertEqual(set(tools), names)
        self.assertTrue(tools["get_pcb_api_constants"].annotations.readOnlyHint)
        self.assertEqual(set(tools["set_footprint_pad_settings"].inputSchema["required"]),
                         {"library_path", "footprint_name", "pad_names"})
        self.assertEqual(tools["set_footprint_pad_settings"].inputSchema["properties"]["mode"]["default"], "manual")
        self.assertEqual(tools["edit_footprint_primitives"].inputSchema["properties"]["operations"]["type"], "array")


if __name__ == "__main__":
    unittest.main()
