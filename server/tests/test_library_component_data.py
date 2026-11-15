"""SchLib request validation, map parsing, dispatch and readback tests."""

import ast
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from typing import Optional
from unittest.mock import AsyncMock
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import library_component_data as data
from altium_files import write_altium_text


class LibraryDataTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2] / ".altium-mcp" / "unit_tests"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.sch = Path(self.temp.name) / "Parts.SchLib"
        self.pcb = Path(self.temp.name) / "Parts.PcbLib"
        self.sch.touch()
        self.pcb.touch()
        self.model = {"model_name": "TEST", "library_path": str(self.pcb),
                      "pin_map": {"1": ["2", "M1"], "2": ["1"]}, "is_current": True}

    def prepare(self, **changes):
        args = dict(path=str(self.sch), symbol_name="TEST", parameters=None, footprint_models=None,
                    comment=None, description=None)
        args.update(changes)
        return data.prepare_request(**args)

    def snapshot(self):
        return data.enrich({"parameters": {"MP": "ABC"}, "comment": "", "description": "test",
                            "models": [{"model_type": "PCBLIB", "model_name": "TEST", "is_current": True,
                                        "description": "", "map_as_string": "(2:1),(1:M1, 2)",
                                        "datafile_links": [{"entity_name": "TEST", "location": str(self.pcb), "file_kind": "PCBLib"}]}]})

    def test_read_only_request(self):
        spec = self.prepare()
        self.assertIn("parameter_count=0", spec)
        self.assertIn("model_count=0", spec)

    def test_parameters_preserve_unicode_and_delimiters(self):
        value = "test \u03a9 | = , \"'\nsecond line"
        spec = self.prepare(parameters={"Part=name": value}, comment="", description=value)
        fields = dict(line.split("=", 1) for line in spec.splitlines())
        self.assertEqual(json.loads('"' + fields["p0.value"] + '"'), value)
        self.assertEqual(fields["comment"], "")

    def test_one_to_many_and_string_pin_numbers(self):
        spec = self.prepare(footprint_models=[self.model])
        self.assertIn("m0.map=(1:2,M1),(2:1)", spec)
        model = {**self.model, "pin_map": {"01": ["A1"], "A": ["02"]}}
        self.assertIn("(01:A1),(A:02)", self.prepare(footprint_models=[model]))

    def test_rejects_invalid_library_paths(self):
        for path in ("Parts.SchLib", str(self.pcb), str(self.sch.parent / "missing.SchLib")):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.prepare(path=path)
        with self.assertRaises(ValueError):
            self.prepare(footprint_models=[{**self.model, "library_path": str(self.sch)}])

    def test_rejects_reserved_duplicate_or_nonstring_parameters(self):
        for parameters in ({"Comment": "x"}, {"Designator": "x"}, {"MP": "1", "mp": "2"},
                           {"x": 1}, {"x": None}, {"": "x"}, {"x": "\x00"}, []):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                self.prepare(parameters=parameters)

    def test_rejects_malformed_models(self):
        for model in ({}, {**self.model, "save": True}, {**self.model, "is_current": 1},
                      {**self.model, "pin_map": {}}, {**self.model, "pin_map": {"1": []}},
                      {**self.model, "pin_map": {1: ["2"]}}, {**self.model, "pin_map": {"1": "2"}},
                      {**self.model, "pin_map": {"1": ["2"], "2": ["2"]}},
                      {**self.model, "pin_map": {"1": ["2", "2"]}}):
            with self.subTest(model=model), self.assertRaises(ValueError):
                self.prepare(footprint_models=[model])

    def test_rejects_map_grammar_injection(self):
        for identifier in ("1:2", "1,2", "1)", "(1", "1=2", " 1", "A B", "1\n2"):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                self.prepare(footprint_models=[{**self.model, "pin_map": {identifier: ["1"]}}])

    def test_unique_model_names_and_current_choice(self):
        with self.assertRaises(ValueError):
            self.prepare(footprint_models=[self.model, {**self.model, "model_name": "test"}])
        with self.assertRaises(ValueError):
            self.prepare(footprint_models=[self.model, {**self.model, "model_name": "OTHER"}])

    def test_parse_native_map_spacing_and_one_to_many(self):
        self.assertEqual(data.parse_pin_map("(1:1, M1),(2:2)"), {"1": ["1", "M1"], "2": ["2"]})
        self.assertEqual(data.parse_pin_map(""), {})

    def test_rejects_unsupported_native_maps(self):
        for value in ("invalid", "(1:)", "(1:1),(1:2)", "(1:1),", "(1:1)|(2:2)"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                data.parse_pin_map(value)

    def test_readback_accepts_equivalent_pad_order(self):
        self.assertEqual(data.verify(self.snapshot(), {"mp": "ABC"}, [self.model], "", "test"), [])

    def test_readback_detects_missing_values_map_link_and_current(self):
        for change in ({"pin_map": {"1": ["2"], "2": ["1"]}}, {"is_current": False},
                       {"model_name": "MISSING"}, {"description": "different"},
                       {"library_path": str(self.sch.parent / "wrong.PcbLib")}):
            with self.subTest(change=change):
                self.assertTrue(data.verify(self.snapshot(), None, [{**self.model, **change}], None, None))
        self.assertTrue(data.verify(self.snapshot(), {"MP": "wrong"}, None, None, None))

    def test_unreadable_mapping_retains_raw_diagnostics(self):
        snapshot = self.snapshot()
        snapshot["models"][0]["map_as_string"] = "unsupported"
        data.enrich(snapshot)
        self.assertIsNone(snapshot["models"][0]["pin_map"])
        self.assertIn("mapping_warning", snapshot["models"][0])

    def test_mcp_dispatch_and_readback_failure(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8"))
        function = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "set_library_component_data")
        function.decorator_list = []
        bridge = type("Bridge", (), {})()
        bridge.execute_command = AsyncMock(return_value={"success": True, "result": {
            "success": True, "changed": True, "before": self.snapshot(), "after": self.snapshot()}})
        scope = {"Context": object, "Optional": Optional, "json": json, "uuid": uuid,
                 "library_component_data": data, "altium_bridge": bridge,
                 "EXCHANGE_DIR": Path(self.temp.name), "write_altium_text": write_altium_text}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "library_tool", "exec"), scope)
        tool = scope["set_library_component_data"]
        result = json.loads(asyncio.run(tool(None, str(self.sch), "TEST", parameters={"MP": "wrong"})))
        self.assertFalse(result["success"])
        self.assertTrue(result["may_have_partial_changes"])
        self.assertFalse(list(Path(self.temp.name).glob("library_data_*.txt")))
        for modified in (False, True):
            before = self.snapshot()
            before["parameters"]["MP"] = "PREVIOUS"
            bridge.execute_command.return_value = {"success": True, "result": {
                "success": True, "changed": True, "is_modified": modified,
                "before": before, "after": self.snapshot()}}
            result = json.loads(asyncio.run(tool(None, str(self.sch), "TEST", parameters={"MP": "ABC"})))
            self.assertEqual(result["success"], modified)
            if not modified:
                self.assertIn("Modified", " ".join(result["verification"]["errors"]))
        bridge.execute_command.reset_mock()
        result = json.loads(asyncio.run(tool(None, str(self.sch), "TEST", parameters={"bad": 2})))
        self.assertFalse(result["success"])
        bridge.execute_command.assert_not_awaited()
        bridge.execute_command.return_value = {"success": False, "error": "timeout"}
        result = json.loads(asyncio.run(tool(None, str(self.sch), "TEST", parameters={"MP": "x"})))
        self.assertTrue(result["may_have_partial_changes"])
        self.assertTrue(Path(result["spec_file"]).exists())


if __name__ == "__main__":
    unittest.main()
