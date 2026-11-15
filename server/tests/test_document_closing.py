"""Close policy, transport and readback tests without live Altium."""

import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import document_closing as closing


class CloseDocumentTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2] / ".altium-mcp" / "unit_tests"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = str(self.root / "Parts_\u03a9_\u0416.SchLib")
        self.result = {"success": True, "document_path": self.path, "closed": True,
                       "already_closed": False, "saved": False, "project_saved": False,
                       "unrelated_documents_preserved": True, "remaining_open_paths": [], "discarded_paths": []}
        self.bridge = type("Bridge", (), {})()
        self.bridge.execute_command = AsyncMock(return_value={"success": True, "result": self.result})

    def call(self, path=None, require_clean=True):
        return json.loads(asyncio.run(closing.execute(self.bridge, self.root, path or self.path, require_clean)))

    def test_default_clean_policy_and_deleted_paths(self):
        self.assertEqual(closing.prepare(self.path)["require_clean"], "1")
        path = self.root / "deleted_directory" / "Deleted.PrjPcb"
        self.assertEqual(closing.prepare(str(path), False)["require_clean"], "0")
        for extension in (".SchDoc", ".PcbDoc", ".SchLib", ".PcbLib", ".PrjPcb", ".LibPkg", ".OutJob", ".PCBDwf"):
            self.assertEqual(Path(closing.prepare(str(self.root / ("A" + extension)))["document_path"]).suffix, extension)

    def test_rejects_invalid_policy_and_unsafe_targets_before_bridge(self):
        for value in (None, 0, 1, "false", [], {}):
            with self.subTest(value=value):
                self.assertFalse(self.call(require_clean=value)["success"])
        for path in ("Parts.SchLib", "C:Parts.SchLib", str(self.root / "CON.SchDoc"),
                     str(self.root / "Altium_API.PrjScr"), str(self.root / "Bridge.PAS")):
            with self.subTest(path=path):
                self.assertFalse(self.call(path)["success"])
        self.bridge.execute_command.assert_not_awaited()

    def test_utf8_spec_dispatch_and_completed_cleanup(self):
        async def reply(command, parameters):
            self.assertEqual(command, "close_document")
            fields = dict(line.split("=", 1) for line in Path(parameters["spec_file"]).read_text(encoding="utf-8-sig").splitlines())
            self.assertEqual(json.loads('"' + fields["document_path"] + '"'), self.path)
            self.assertEqual(fields["require_clean"], "1")
            return {"success": True, "result": json.dumps(self.result)}
        self.bridge.execute_command.side_effect = reply
        result = self.call()
        self.assertTrue(result["verification"]["passed"])
        self.assertFalse(list(self.root.glob("close_document_*.txt")))

    def test_bridge_failure_retains_spec_and_reports_uncertainty(self):
        self.bridge.execute_command.return_value = {"success": False, "error": "timeout"}
        result = self.call()
        self.assertTrue(result["close_outcome_unknown"])
        self.assertTrue(result["may_have_partial_changes"])
        self.assertTrue(Path(result["spec_file"]).is_file())
        self.bridge.execute_command.side_effect = RuntimeError("transport interrupted")
        result = self.call()
        self.assertTrue(result["close_outcome_unknown"])
        self.assertTrue(Path(result["spec_file"]).is_file())

    def test_native_refusal_is_preserved(self):
        self.bridge.execute_command.return_value = {"success": False, "error": "lifted refusal", "result": {
            "success": False, "closed": False, "blocked_paths": [self.path], "may_have_partial_changes": False}}
        result = self.call()
        self.assertFalse(result["success"])
        self.assertEqual(result["blocked_paths"], [self.path])
        self.assertFalse(result["may_have_partial_changes"])
        self.assertFalse(list(self.root.glob("close_document_*.txt")))

    def test_readback_rejects_wrong_target_partial_close_saves_and_collateral(self):
        for change in ({"document_path": str(self.root / "Other.SchLib")}, {"document_path": None}, {"closed": False},
                       {"already_closed": None}, {"saved": True}, {"project_saved": True},
                       {"remaining_open_paths": [self.path]}, {"unrelated_documents_preserved": False},
                       {"discarded_paths": [self.path]}):
            with self.subTest(change=change):
                result = closing.verify({**copy.deepcopy(self.result), **change}, self.path, True)
                self.assertFalse(result["success"])
                self.assertTrue(result["may_have_partial_changes"])

    def test_explicit_discard_and_idempotent_close_readback(self):
        self.assertTrue(closing.verify({**self.result, "discarded_paths": [self.path]}, self.path, False)["success"])
        self.assertTrue(closing.verify({**self.result, "already_closed": True}, self.path.upper().replace("\\", "/"), True)["success"])


if __name__ == "__main__":
    unittest.main()
