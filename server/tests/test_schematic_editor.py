"""Request-boundary and MCP dispatch tests; no Altium process required."""

import ast
import asyncio
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from schematic_editor import validate_request
from altium_files import write_altium_text


class SchematicEditTest(unittest.TestCase):
    def setUp(self):
        temp_root = Path(__file__).resolve().parents[2] / ".altium-mcp" / "unit_tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temp_root)
        self.addCleanup(self.temp.cleanup)
        self.sheet = Path(self.temp.name) / "test.SchDoc"
        self.sheet.touch()

    def validate(self, operations):
        return validate_request(str(self.sheet), operations)

    def add(self, kind="wire", **props):
        return {"op": "add", "type": kind, "properties": props or {"points": [0, 0, 100, 0]}}

    def test_read_only_and_exact_path(self):
        self.assertIn("count=0\n", self.validate([]))
        for path in ("test.SchDoc", str(self.sheet.with_suffix(".SchLib"))):
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_request(path, [])
        # Only Altium can distinguish an open unsaved document from a missing
        # closed path. Native preflight must make that decision without creation.
        self.assertIn("count=0", validate_request(str(self.sheet.parent / "unsaved.SchDoc"), []))

    def test_transport_keeps_unicode_delimiters_and_line_breaks(self):
        value = "\u0422\u0435\u0441\u0442 | , = \\\n\r\t\"'"
        spec = self.validate([self.add("component", source_designator="R1", designator="R999",
                                       x=10.5, y=20, comment=value, parameters={"Test=one": value})])
        fields = dict(line.split("=", 1) for line in spec.splitlines())
        self.assertEqual(json.loads('"' + fields["0.comment"] + '"'), value)
        self.assertEqual(json.loads('"' + fields["0.parameter0.value"] + '"'), value)
        self.assertEqual(fields["0.x"], "10.5")

    def test_rejects_malformed_operations_and_unknown_fields(self):
        bad = [None, {}, {"op": "rename", "type": "wire"},
               {"op": "add", "type": "sheet"}, {"op": "add", "type": []},
               {**self.add(), "save": True}, self.add("wire", points=[0, 0, 1]),
               self.add("wire", points=[0, 0, True, 0]), self.add("wire", points=[0, 0, float("nan"), 0]),
               self.add("wire", points=[0, 0, float("inf"), 0]), self.add("wire", points=[0, 0, 10**400, 0]),
               self.add("net_label", x=1, text="missing y"),
               self.add("port", x=1, y=2, name="P", width=-2),
               self.add("power_port", x=1, y=2, text="GND", show_net_name=1),
               self.add("text", x=1, y=2, text="ok", orientation=90),
               self.add("text", x=1, y=2, text="nul\x00")]
        for operation in bad:
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                self.validate([operation])

    def test_selectors_and_delete_contract(self):
        valid = {"op": "update", "type": "component", "target": {"designator": "R1"}, "properties": {"comment": ""}}
        self.assertIn("0.target_designator=R1", self.validate([valid]))
        for target in ({}, {"unique_id": "A", "designator": "R1"}, {"unique_id": ""}, {"x": 1}):
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.validate([{**valid, "target": target}])
        with self.assertRaises(ValueError):
            self.validate([{**valid, "type": "net_label", "properties": {"text": "x"}}])
        with self.assertRaises(ValueError):
            self.validate([{**valid, "op": "delete"}])
        with self.assertRaises(ValueError):
            self.validate([{**self.add(), "target": {"unique_id": "A"}}])

    def test_symbol_sources_and_parameter_names(self):
        component = self.add("component", x=0, y=0, designator="R2", source_designator="R1")
        self.validate([component])
        library = Path(self.temp.name) / "test.SchLib"
        library.touch()
        self.validate([self.add("component", x=0, y=0, designator="R2", symbol_library=str(library), symbol="R")])
        for change in ({"symbol": "R"}, {"parameters": {"Comment": "x"}},
                       {"parameters": {"test": "x", "TEST": "y"}}, {"parameters": {"x": None}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.validate([{**component, "properties": {**component["properties"], **change}}])
        with self.assertRaises(ValueError):
            self.validate([{"op": "update", "type": "component", "target": {"designator": "R1"},
                            "properties": {"symbol_library": str(library)}}])

    def test_dispatch_uses_bom_unique_files_and_keeps_uncertain_input(self):
        # Load only the tool function, avoiding main.py's global Windows bridge.
        tree = ast.parse((Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8"))
        function = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "edit_schematic")
        function.decorator_list = []
        captured = []

        async def execute(command, params):
            path = Path(params["spec_file"])
            captured.append(path)
            self.assertEqual(command, "edit_schematic")
            self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
            return {"success": True, "result": {"success": True, "saved": False}}

        bridge = type("Bridge", (), {})()
        bridge.execute_command = AsyncMock(side_effect=execute)
        scope = {"Context": object, "json": json, "validate_schematic_edit": validate_request,
                 "EXCHANGE_DIR": Path(self.temp.name), "uuid": uuid,
                 "write_altium_text": write_altium_text, "altium_bridge": bridge}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "edit_schematic", "exec"), scope)
        tool = scope["edit_schematic"]
        for _ in range(2):
            self.assertTrue(json.loads(asyncio.run(tool(None, str(self.sheet), [])))["success"])
        self.assertNotEqual(captured[0], captured[1])
        self.assertTrue(all(not p.exists() for p in captured))
        bridge.execute_command.reset_mock()
        asyncio.run(tool(None, str(self.sheet), [self.add("wire", points=[1, 2, 3])]))
        bridge.execute_command.assert_not_awaited()
        bridge.execute_command.side_effect = None
        bridge.execute_command.return_value = {"success": False, "error": "timeout"}
        result = json.loads(asyncio.run(tool(None, str(self.sheet), [self.add()])))
        self.assertTrue(result["may_have_partial_changes"])
        self.assertTrue(Path(result["spec_file"]).exists())


if __name__ == "__main__":
    unittest.main()
