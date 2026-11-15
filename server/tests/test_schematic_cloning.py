"""Cross-sheet selector, transport, preservation and UID readback tests."""

import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import schematic_cloning as cloning


class SchematicCloningTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2] / ".altium-mcp" / "unit_tests"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = str(self.root / "Source_\u03a9.SchDoc")
        self.target = str(self.root / "Unsaved_\u0416.SchDoc")
        self.bridge = type("Bridge", (), {})()
        self.bridge.execute_command = AsyncMock()

    def result(self, parts=(1,)):
        components = []
        for part in parts:
            state = {"designator": "U1", "part_id": part, "x": 10.5, "y": 20,
                     "pins": [{"designator": "1", "hot_x": 10, "hot_y": 20}],
                     "metadata": {"parameters": {"MP": "ABC"}, "models": [{"model_name": "TEST", "map_as_string": "(1:2)"}]}}
            old, new = f"OLD{part}", f"NEW{part}"
            original = {"unique_id": old, "child_uids": [old + "PIN"], "state": state}
            clone = {"unique_id": new, "child_uids": [new + "PIN"], "state": copy.deepcopy(state)}
            components.append({"source": original, "clone": clone,
                               "uid_map": [{"source_uid": old, "unique_id": new},
                                           {"source_uid": old + "PIN", "unique_id": new + "PIN"}]})
        return {"success": True, "source_sheet": self.source, "target_sheet": self.target,
                "saved": False, "source_unchanged": True, "source_was_modified": True,
                "source_is_modified": True, "target_is_modified": True, "cloned_count": len(components),
                "target_counts_before": {"26": 10, "19": 20}, "target_counts_after": {"26": 10 + len(components), "19": 20}, "process_depth": 0,
                "components": components}

    def call(self, designators=None):
        return json.loads(asyncio.run(cloning.execute(self.bridge, self.root, self.source, self.target, designators or ["U1"])))

    def test_exact_distinct_sheets_and_unsaved_paths(self):
        spec = cloning.prepare(self.source, self.target, ["U1", "R2"])
        self.assertEqual(spec["d1"], "R2")
        self.assertEqual(spec["count"], "2")
        with self.assertRaises(ValueError):
            cloning.prepare(self.source, self.source.upper().replace("\\", "/"), ["U1"])
        for path in ("Sheet.SchDoc", self.source.replace(".SchDoc", ".SchLib"), "C:Source.SchDoc", ""):
            with self.subTest(path=path), self.assertRaises(ValueError):
                cloning.prepare(path, self.target, ["U1"])

    def test_selector_errors(self):
        for names in (None, [], {}, "U1", ["U1", "u1"], [" U1"], [""], [None], [1], ["U\n1"], ["U1"] * 501):
            with self.subTest(names=names), self.assertRaises(ValueError):
                cloning.prepare(self.source, self.target, names)
        self.assertEqual(cloning.prepare(self.source, self.target, ["\u04221"])["d0"], "\u04221")

    def test_multipart_preservation_and_compact_output(self):
        result = cloning.verify(self.result((1, 2)), self.source, self.target, ["u1"])
        self.assertTrue(result["verification"]["passed"])
        self.assertEqual(len(result["components"]), 2)
        self.assertNotIn("source", result["components"][0])
        self.assertEqual(result["components"][0]["component"]["state"]["metadata"]["parameters"], {"MP": "ABC"})

    def test_detects_pin_model_geometry_and_source_changes(self):
        for change in ("pin", "model", "position", "source", "source_flag", "target_flag", "count", "saved", "wire", "undo"):
            result = self.result()
            state = result["components"][0]["clone"]["state"]
            if change == "pin": state["pins"][0]["hot_x"] = 999
            elif change == "model": state["metadata"]["models"][0]["map_as_string"] = "(1:1)"
            elif change == "position": state["x"] = 999
            elif change == "source": result["source_unchanged"] = False
            elif change == "source_flag": result["source_is_modified"] = False
            elif change == "target_flag": result["target_is_modified"] = False
            elif change == "count": result["cloned_count"] = 2
            elif change == "saved": result["saved"] = True
            elif change == "wire": result["target_counts_after"]["19"] += 1
            elif change == "undo": result["process_depth"] = 1
            with self.subTest(change=change):
                self.assertFalse(cloning.verify(result, self.source, self.target, ["U1"])["success"])

    def test_uid_reuse_duplicates_and_mapping_mismatch(self):
        for change in ("old", "duplicate", "map"):
            result = self.result((1, 2))
            if change == "old": result["components"][0]["clone"]["unique_id"] = "OLD1"
            elif change == "duplicate": result["components"][1]["clone"]["unique_id"] = "NEW1"
            else: result["components"][0]["uid_map"] = []
            with self.subTest(change=change):
                self.assertFalse(cloning.verify(result, self.source, self.target, ["U1"])["success"])
        self.assertFalse(cloning.verify(self.result(), self.source, self.target, ["U1", "R2"])["success"])

    def test_utf8_transport_and_completed_refusal(self):
        async def execute(command, args):
            self.assertEqual(command, "clone_schematic_components")
            path = Path(args["spec_file"])
            self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
            fields = dict(line.split("=", 1) for line in path.read_text(encoding="utf-8-sig").splitlines())
            self.assertEqual(json.loads('"' + fields["source_sheet"] + '"'), self.source)
            return {"success": False, "result": {"success": False, "cloned_count": 0, "may_have_partial_changes": False}}
        self.bridge.execute_command.side_effect = execute
        result = self.call()
        self.assertFalse(result["may_have_partial_changes"])
        self.assertFalse(list(self.root.glob("schematic_clone_*.txt")))

    def test_unknown_completion_keeps_spec(self):
        self.bridge.execute_command.return_value = {"success": False, "error": "timeout"}
        result = self.call()
        self.assertTrue(result["clone_outcome_unknown"])
        self.assertTrue(Path(result["spec_file"]).exists())
        self.bridge.execute_command.side_effect = OSError("interrupted")
        self.assertTrue(self.call()["may_have_partial_changes"])


if __name__ == "__main__":
    unittest.main()
