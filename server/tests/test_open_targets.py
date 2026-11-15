"""Open/reuse boundaries and MCP readback for projects and library documents."""

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
import document_management
import project_selection


class OpenTargetsTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2] / ".altium-mcp" / "unit_tests"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "Parts.SchLib"
        self.project = self.root / "Controller.PrjPcb"
        self.bridge = type("Bridge", (), {})()
        self.bridge.execute_command = AsyncMock()
        self.documents = [{"path": str(self.path), "kind": "SCHLIB", "state": "modified"}]

    def document_result(self, **changes):
        return {"success": True, "saved": False, "document_path": str(self.path), "document_kind": "SCHLIB",
                "active_document_path": str(self.path), "focused": True, "already_open": True, "opened": False,
                "was_modified": True, "is_modified": True, **changes}

    def project_result(self, **changes):
        return {"success": True, "saved": False, "project_saved": False, "compiled": False,
                "active_project_path": str(self.project), "already_open": False, "opened": True,
                "documents_before": [], "documents_after": copy.deepcopy(self.documents), "documents_unchanged": False,
                "preexisting_documents_preserved": True, "preexisting_documents_before": copy.deepcopy(self.documents),
                "preexisting_documents_after": copy.deepcopy(self.documents), **changes}

    def document(self, path=None):
        return json.loads(asyncio.run(document_management.execute(self.bridge, self.root, "open_document", str(path or self.path))))

    def project_call(self, path=None):
        return json.loads(asyncio.run(project_selection.execute(self.bridge, self.root, str(path or self.project), open_if_closed=True)))

    def test_document_extensions_and_named_unsaved_targets_are_accepted(self):
        for suffix, kind in document_management.KINDS.items():
            path = self.root / ("Test" + suffix)
            self.assertFalse(path.exists())
            self.assertEqual(document_management.prepare("open_document", str(path))["document_kind"], kind)

    def test_new_and_existing_dirty_document_readback(self):
        for changes in ({}, {"already_open": False, "opened": True, "was_modified": False, "is_modified": False}):
            self.bridge.execute_command.return_value = {"success": True, "result": self.document_result(**changes)}
            result = self.document()
            self.assertTrue(result["success"])
            self.assertTrue(result["verification"]["passed"])
        self.bridge.execute_command.assert_awaited_with("open_document", self.bridge.execute_command.call_args.args[1])
        self.assertFalse(list(self.root.glob("document_*.txt")))

    def test_document_wrong_identity_focus_or_modified_state_fails(self):
        for changes in ({"document_path": str(self.root / "Wrong.SchLib")}, {"document_kind": "PCBLIB"},
                        {"active_document_path": str(self.root / "Wrong.SchLib")}, {"focused": False},
                        {"saved": True}, {"opened": True}, {"is_modified": False}, {"already_open": None}):
            with self.subTest(changes=changes):
                self.bridge.execute_command.return_value = {"success": True, "result": self.document_result(**changes)}
                self.assertFalse(self.document()["success"])

    def test_closed_document_error_does_not_claim_a_new_file(self):
        native = {"success": False, "error": "closed document must exist on disk; no document was created",
                  "opened": False, "already_open": False, "saved": False, "may_have_partial_changes": False}
        self.bridge.execute_command.return_value = {"success": True, "result": native}
        self.assertEqual(self.document(), native)
        self.assertFalse(self.path.exists())

    def test_new_project_accepts_loaded_sources_and_preserves_existing_dirty_documents(self):
        self.bridge.execute_command.return_value = {"success": True, "result": self.project_result()}
        result = self.project_call()
        self.assertTrue(result["success"])
        self.assertTrue(result["verification"]["passed"])
        self.assertFalse(list(self.root.glob("open_project_*.txt")))
        self.assertEqual(self.bridge.execute_command.call_args.args[0], "open_project")

    def test_existing_project_reuse_requires_identical_document_states(self):
        result = self.project_result(already_open=True, opened=False, documents_before=self.documents,
                                     documents_after=copy.deepcopy(self.documents), documents_unchanged=True)
        self.bridge.execute_command.return_value = {"success": True, "result": result}
        self.assertTrue(self.project_call()["success"])
        result["documents_after"] = []
        self.assertFalse(self.project_call()["success"])

    def test_project_missing_open_flags_saving_compilation_or_loss_of_dirty_state_fails(self):
        for changes in ({"opened": False}, {"already_open": None}, {"saved": True}, {"project_saved": True},
                        {"compiled": True}, {"preexisting_documents_preserved": False},
                        {"preexisting_documents_after": []}, {"active_project_path": str(self.root / "Wrong.PrjPcb")}):
            with self.subTest(changes=changes):
                self.bridge.execute_command.return_value = {"success": True, "result": self.project_result(**changes)}
                self.assertFalse(self.project_call()["success"])

    def test_document_and_project_input_errors_stop_before_bridge(self):
        self.assertFalse(self.document(self.root / "Wrong.IntLib")["success"])
        self.assertFalse(self.project_call(self.root / "Wrong.PrjScr")["success"])
        self.bridge.execute_command.assert_not_awaited()

    def test_unicode_project_spec_and_timeout_keeps_diagnostics(self):
        path = self.root / "Parts_\u03a9_\u0416.LibPkg"

        async def respond(command, args):
            self.assertEqual(command, "open_project")
            spec = Path(args["spec_file"])
            self.assertTrue(spec.read_bytes().startswith(b"\xef\xbb\xbf"))
            value = spec.read_text(encoding="utf-8-sig").strip().split("=", 1)[1]
            self.assertEqual(json.loads('"' + value + '"'), str(path))
            return {"success": False, "error": "timeout"}

        self.bridge.execute_command.side_effect = respond
        result = self.project_call(path)
        self.assertTrue(result["may_have_opened_project"])
        self.assertTrue(Path(result["spec_file"]).exists())
        self.bridge.execute_command.side_effect = None
        self.bridge.execute_command.return_value = {"success": False, "error": "timeout"}
        self.assertTrue(Path(self.document()["spec_file"]).exists())

    def test_mcp_wrappers_dispatch_the_correct_operations(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8"))
        for name, path, response in (("open_document", self.path, self.document_result()),
                                     ("open_project", self.project, self.project_result())):
            function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == name)
            function.decorator_list = []
            scope = {"Context": object, "document_management": document_management, "project_selection": project_selection,
                     "altium_bridge": self.bridge, "EXCHANGE_DIR": self.root}
            exec(compile(ast.Module(body=[function], type_ignores=[]), name, "exec"), scope)
            self.bridge.execute_command.return_value = {"success": True, "result": response}
            self.assertTrue(json.loads(asyncio.run(scope[name](None, str(path))))["success"])
            self.assertEqual(self.bridge.execute_command.call_args.args[0], name)


if __name__ == "__main__":
    unittest.main()
