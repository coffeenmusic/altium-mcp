"""Lifecycle boundary, transport and save verification tests; no live Altium."""

import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import document_management as data


class DocumentManagementTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2] / ".altium-mcp" / "unit_tests"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "Parts.SchLib"
        self.project = self.root / "Test.PrjPcb"
        self.project.write_text("[Design]\nVersion=1.0\n", encoding="ascii")
        self.bridge = type("Bridge", (), {})()
        self.bridge.execute_command = AsyncMock(return_value={"success": True, "result": {"success": True, "saved": False}})

    def call(self, action, path=None, **options):
        return json.loads(asyncio.run(data.execute(self.bridge, self.root, action, str(path or self.path), **options)))

    def test_create_kinds_and_mismatches(self):
        for extension, kind in data.KINDS.items():
            path = self.root / ("Test" + extension.upper())
            self.assertEqual(data.prepare("create_document", str(path))["document_kind"], kind)
            self.assertEqual(data.prepare("create_document", str(path), document_kind=kind.lower())["document_kind"], kind)
            with self.assertRaises(ValueError):
                data.prepare("create_document", str(path), document_kind="wrong")
        for kind in (3, {}, ""):
            with self.assertRaises(ValueError):
                data.prepare("create_document", str(self.path), document_kind=kind)

    def test_create_rejects_existing_file(self):
        self.path.touch()
        self.assertFalse(self.call("create_document")["success"])
        self.bridge.execute_command.assert_not_awaited()

    def test_rejects_invalid_paths_before_bridge(self):
        for path in ("Parts.SchLib", "C:Parts.SchLib", "", None,
                     str(self.root / "bad.txt"), str(self.root / "missing" / "Parts.SchLib"),
                     str(self.root / "CON.SchLib"), str(self.root / "bad:ads.SchLib"),
                     str(self.root / "bad\nname.SchLib"), str(self.root / "name. " / "Parts.SchLib")):
            with self.subTest(path=path), self.assertRaises((ValueError, TypeError)):
                data.prepare("create_document", path)

    def test_local_write_guard(self):
        with self.assertRaises(ValueError):
            data.writable_local_path(Path(r"\\server\share\Parts.SchLib"))
        self.path.touch()
        self.path.chmod(0o444)
        try:
            self.assertFalse(self.call("save_document")["success"])
            self.bridge.execute_command.assert_not_awaited()
        finally:
            self.path.chmod(0o666)

    def test_project_validation_and_unsaved_source(self):
        spec = data.prepare("add_document_to_project", str(self.path), project_path=str(self.project))
        self.assertEqual(spec["project_path"], str(self.project))
        package = self.root / "Test.LibPkg"
        package.touch()
        self.assertEqual(data.prepare("add_document_to_project", str(self.path), project_path=str(package))["project_path"], str(package))
        with self.assertRaises(ValueError):
            data.prepare("add_document_to_project", str(self.root / "Test.SchDoc"), project_path=str(package))
        with self.assertRaises(ValueError):
            data.prepare("add_document_to_project", str(self.path), project_path=str(self.root / "Missing.PrjPcb"))

    def test_dry_run_requires_boolean(self):
        for value in (0, 1, "true", None):
            with self.assertRaises(ValueError):
                data.prepare("save_document", str(self.path), dry_run=value)
        self.assertEqual(data.prepare("save_document", str(self.path), dry_run=True)["dry_run"], "1")

    def test_transport_unicode_and_cleanup(self):
        path = self.root / "Parts_\u03a9_\u0416.SchLib"

        async def respond(command, args):
            self.assertEqual(command, "create_document")
            fields = dict(Path(args["spec_file"]).read_text(encoding="utf-8-sig").splitlines()[i].split("=", 1)
                          for i in range(2))
            self.assertEqual(json.loads('"' + fields["document_path"] + '"'), str(path))
            return {"success": True, "result": '{"success":true,"saved":false}'}

        self.bridge.execute_command.side_effect = respond
        self.assertTrue(self.call("create_document", path)["success"])
        self.assertFalse(list(self.root.glob("document_*.txt")))

    def test_timeout_keeps_spec_and_reports_unknown_write(self):
        self.bridge.execute_command.return_value = {"success": False, "error": "timeout"}
        result = self.call("save_document")
        self.assertTrue(result["save_outcome_unknown"])
        self.assertTrue(result["may_have_partial_changes"])
        self.assertTrue(Path(result["spec_file"]).is_file())
        result = self.call("save_document", dry_run=True)
        self.assertFalse(result["save_outcome_unknown"])
        self.assertFalse(result["may_have_partial_changes"])

    def test_disk_readback_detects_missing_file_and_modified_state(self):
        self.bridge.execute_command.return_value["result"]["saved"] = True
        self.assertFalse(self.call("save_document")["success"])
        self.path.write_bytes(b"saved data")
        self.bridge.execute_command.return_value["result"]["is_modified"] = True
        self.assertFalse(self.call("save_document")["success"])
        self.bridge.execute_command.return_value["result"]["is_modified"] = False
        result = self.call("save_document")
        self.assertTrue(result["success"])
        self.assertEqual(result["disk"]["sha256_after"], data.digest(self.path))

    def test_dry_run_has_no_disk_requirement(self):
        result = self.call("save_document", dry_run=True)
        self.assertTrue(result["success"])
        self.assertNotIn("disk", result)


if __name__ == "__main__":
    unittest.main()
