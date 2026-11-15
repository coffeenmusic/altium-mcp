"""Project selection boundaries, transport and readback; no Altium required."""

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
import project_selection as data


class ProjectSelectionTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2] / ".altium-mcp" / "unit_tests"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "Controller.PrjPcb"
        self.documents = [{"path": str(self.root / "Main.SchDoc"), "kind": "SCH", "state": "modified"}]
        self.bridge = type("Bridge", (), {})()
        self.bridge.execute_command = AsyncMock(return_value={"success": True, "result": self.result()})

    def result(self, **changes):
        return {"success": True, "active_project_path": str(self.project), "saved": False, "project_saved": False,
                "changed": True, "documents_unchanged": True, "documents_before": copy.deepcopy(self.documents),
                "documents_after": copy.deepcopy(self.documents), **changes}

    def call(self, path=None):
        return json.loads(asyncio.run(data.execute(self.bridge, self.root, str(path or self.project))))

    def test_open_named_project_does_not_require_a_disk_file(self):
        self.assertFalse(self.project.exists())
        self.assertEqual(data.prepare(str(self.project)), str(self.project))
        self.assertEqual(data.prepare(str(self.root / "Parts.LibPkg")), str(self.root / "Parts.LibPkg"))

    def test_rejects_relative_script_document_and_malformed_paths(self):
        for path in (None, "", "Controller.PrjPcb", "C:Controller.PrjPcb", str(self.root / "Script.PrjScr"),
                     str(self.root / "Main.SchDoc"), str(self.root / "missing" / "Controller.PrjPcb"),
                     str(self.root / "bad\nname.PrjPcb"), str(self.root / "bad:ads.PrjPcb")):
            with self.subTest(path=path), self.assertRaises((ValueError, TypeError)):
                data.prepare(path)
        result = self.call(self.root / "Main.SchDoc")
        self.assertFalse(result["success"])
        self.assertFalse(result["may_have_changed_focus"])
        self.bridge.execute_command.assert_not_awaited()

    def test_bom_unicode_exact_transport_and_unique_cleanup(self):
        paths = []
        project = self.root / "Controller_\u03a9_\u0416.PrjPcb"

        async def respond(command, args):
            self.assertEqual(command, "select_project")
            spec = Path(args["spec_file"])
            paths.append(spec)
            self.assertTrue(spec.read_bytes().startswith(b"\xef\xbb\xbf"))
            fields = dict(line.split("=", 1) for line in spec.read_text(encoding="utf-8-sig").splitlines())
            self.assertEqual(json.loads('"' + fields["project_path"] + '"'), str(project))
            return {"success": True, "result": json.dumps(self.result(active_project_path=str(project)))}

        self.bridge.execute_command.side_effect = respond
        for _ in range(2):
            self.assertTrue(self.call(project)["verification"]["passed"])
        self.assertNotEqual(paths[0], paths[1])
        self.assertTrue(all(not path.exists() for path in paths))

    def test_readback_accepts_case_insensitive_identity_and_dirty_documents(self):
        result = self.result(active_project_path=str(self.project).upper())
        self.assertTrue(data.verify(result, str(self.project))["verification"]["passed"])
        self.assertEqual(result["documents_after"][0]["state"], "modified")

    def test_readback_rejects_wrong_project_saves_and_changed_or_missing_states(self):
        bad_states = [{**self.documents[0], "state": "open"}]
        for changes in ({"active_project_path": str(self.root / "Other.PrjPcb")}, {"saved": True},
                        {"project_saved": True}, {"documents_after": bad_states},
                        {"documents_unchanged": False}, {"documents_before": None, "documents_after": None}):
            with self.subTest(changes=changes):
                self.bridge.execute_command.return_value = {"success": True, "result": self.result(**changes)}
                result = self.call()
                self.assertFalse(result["success"])
                self.assertFalse(result["verification"]["passed"])
                self.assertTrue(result["may_have_changed_focus"])

    def test_native_closed_project_failure_preserves_details(self):
        native = {"success": False, "error": "exact project must already be open exactly once; found 0",
                  "active_project_path": "previous", "changed": False, "saved": False,
                  "available_project_paths": ["previous"], "may_have_changed_focus": False}
        self.bridge.execute_command.return_value = {"success": True, "result": native}
        self.assertEqual(self.call(), native)
        self.assertFalse(list(self.root.glob("select_project_*.txt")))

    def test_uncertain_timeout_and_exception_retain_spec(self):
        self.bridge.execute_command.return_value = {"success": False, "error": "timeout"}
        result = self.call()
        self.assertTrue(result["may_have_changed_focus"])
        self.assertFalse(result["saved"])
        self.assertTrue(Path(result["spec_file"]).is_file())
        self.bridge.execute_command.side_effect = RuntimeError("bridge disconnected")
        with self.assertRaises(RuntimeError):
            self.call()
        self.assertEqual(len(list(self.root.glob("select_project_*.txt"))), 2)

    def test_mcp_dispatch_uses_the_shared_exact_path_executor(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8"))
        function = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "select_project")
        function.decorator_list = []
        scope = {"Context": object, "project_selection": data, "altium_bridge": self.bridge, "EXCHANGE_DIR": self.root}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "select_project", "exec"), scope)
        result = json.loads(asyncio.run(scope["select_project"](None, str(self.project))))
        self.assertTrue(result["success"])
        self.bridge.execute_command.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
