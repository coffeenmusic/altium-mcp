"""Exercise interfaces that overlap between the fork and upstream."""

import ast
import asyncio
from collections import Counter
import json
import logging
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock
import uuid

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "server"))

from altium_files import long_path, read_altium_text, write_altium_text
from schematic_editor import validate_request
import script_bundle
import symbol_metadata
from server.tests.test_symbol_metadata import DESCRIPTION, PARAMETERS


class MergeCompatibilityTest(unittest.TestCase):
    def setUp(self):
        root = REPO / ".altium-mcp" / "unit_tests"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sheet = self.root / "test.SchDoc"
        self.sheet.touch()

    def editor(self):
        tree = ast.parse((REPO / "server" / "main.py").read_text(encoding="utf-8"))
        functions = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                     and node.name in {"edit_schematic", "_edit_schematic_actions"}]
        for node in functions:
            node.decorator_list = []
        bridge = type("Bridge", (), {})()
        bridge.execute_command = AsyncMock(return_value={"success": True, "result": {"success": True}})
        scope = {"Context": object, "json": json, "uuid": uuid, "logger": logging.getLogger(__name__),
                 "EXCHANGE_DIR": self.root, "altium_bridge": bridge, "long_path": long_path,
                 "write_altium_text": write_altium_text, "validate_schematic_edit": validate_request}
        exec(compile(ast.Module(body=functions, type_ignores=[]), "merged_editor", "exec"), scope)
        return scope["edit_schematic"], bridge

    def test_upstream_actions_use_metric_coordinates_and_isolated_specs(self):
        tool, bridge = self.editor()
        captured = []

        async def execute(command, params):
            self.assertEqual(command, "edit_schematic_actions")
            self.assertTrue(params["save"])
            path = Path(params["spec_file"])
            captured.append(path)
            self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
            self.assertIn("WIRE|0|0|10000000|0", read_altium_text(path))
            return {"success": True, "result": {"success": True, "saved": True}}

        bridge.execute_command.side_effect = execute
        for _ in range(2):
            result = json.loads(asyncio.run(tool(None, str(self.sheet),
                [{"op": "add_wire", "points": [[0, 0], [25.4, 0]]}], unit="mm", save=True)))
            self.assertTrue(result["success"])
        self.assertNotEqual(*captured)
        self.assertTrue(all(not path.exists() for path in captured))

    def test_mixed_formats_and_uid_save_are_rejected_before_dispatch(self):
        tool, bridge = self.editor()
        uid = {"op": "add", "type": "junction", "properties": {"x": 0, "y": 0}}
        action = {"op": "add_junction", "x": 0, "y": 0}
        for operations, options in [([uid, action], {}), ([uid], {"save": True}), ([uid], {"unit": "mm"})]:
            with self.subTest(operations=operations, options=options):
                result = json.loads(asyncio.run(tool(None, str(self.sheet), operations, **options)))
                self.assertFalse(result["success"])
        bridge.execute_command.assert_not_awaited()

    def test_action_timeout_retains_input_and_native_errors_fail(self):
        tool, bridge = self.editor()
        operations = [{"op": "add_junction", "x": 0, "y": 0}]
        bridge.execute_command.return_value = {"success": False, "error": "timeout"}
        result = json.loads(asyncio.run(tool(None, str(self.sheet), operations)))
        self.assertTrue(result["may_have_partial_changes"])
        self.assertTrue(Path(result["spec_file"]).exists())
        bridge.execute_command.return_value = {"success": True, "result": {"errors": ["part missing"]}}
        self.assertFalse(json.loads(asyncio.run(tool(None, str(self.sheet), operations)))["success"])

    def batch(self, contents):
        path = self.root / "batch.txt"
        write_altium_text(path, contents)
        return symbol_metadata.prepare_batch(str(path))

    def test_existing_symbol_edit_records_do_not_require_creation_metadata(self):
        text = ("EDITSYMBOL|OLD\nCOMMENT|New comment|1\nDESCRIPTION|Edited component description\n"
                "SYMPARAM|Extra|value|1|10|20|bottom_left|Arial:10\n"
                "PINPARAM|1|Test|value\nPINDESC|1|Input\nPINSYMBOL|1|inside|clock\n")
        prepared, symbols = self.batch(text)
        self.assertEqual(prepared, text)
        self.assertEqual(symbols, [])
        with self.assertRaises(ValueError):
            self.batch("EDITSYMBOL|OLD\nPIN|1|A|eElectricPassive|eRotate0|0|0")

    def test_creation_accepts_symparam_and_verifies_post_edit_metadata(self):
        text = f"SYMBOL|NEW|{DESCRIPTION}|1\n" + "\n".join(
            f"SYMPARAM|{name}|{value}|0" for name, value in PARAMETERS.items())
        text += "\nCOMMENT|Explicit comment|1\nDESCRIPTION|Precision resistor in 0603 package\nEDITSYMBOL|OLD\nPINDESC|1|Input\n"
        prepared, symbols = self.batch(text)
        self.assertEqual(len(symbols), 1)
        self.assertIn("PARAM|MP|R-10K", prepared)
        self.assertIn("EDITSYMBOL|OLD", prepared)
        request = symbols[0]
        result = {"success": True, "metadata": {"parameters": PARAMETERS,
                  "description": request["description"], "comment": request["comment"]}}
        self.assertTrue(symbol_metadata.verify(result, request["description"], request["parameters"],
                                             request["comment"])["success"])
        with self.assertRaises(ValueError):
            self.batch(text.replace("SYMPARAM|System|Power|0", ""))

    def test_production_script_project_bundles_without_namespace_collisions(self):
        project = REPO / "server" / "AltiumScript" / "Altium_API.PrjScr"
        sections = re.findall(r"^\[(Document\d+)\]", project.read_text(), re.M)
        self.assertEqual(len(sections), len(set(sections)))
        units = script_bundle.project_units(project)
        self.assertEqual(len(units), len(set(units)))
        bundled = script_bundle.bundle_text(units)
        for name in ("ExecuteEditSchematic", "ExecuteEditSchematicActions", "CreateSymbolsBatch"):
            self.assertEqual(len(re.findall(r"^function " + name + r"\(", bundled, re.M)), 1)

    def test_registered_tool_names_are_unique_and_helpers_are_private(self):
        tree = ast.parse((REPO / "server" / "main.py").read_text(encoding="utf-8"))
        tools = [node.name for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                 and any(isinstance(d, ast.Call) and isinstance(d.func, ast.Name)
                         and d.func.id == "_tool" for d in node.decorator_list)]
        self.assertFalse([name for name, count in Counter(tools).items() if count > 1])
        self.assertIn("edit_schematic", tools)
        self.assertIn("get_lcsc_component_info", tools)
        self.assertNotIn("_edit_schematic_actions", tools)


if __name__ == "__main__":
    unittest.main()
