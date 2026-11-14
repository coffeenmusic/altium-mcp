"""Unit tests for main.edit_outjob's handling of Altium (no Altium needed:
the bridge and the process check are faked).

Run from server/:  python -m unittest tests.test_edit_outjob_tool -v
"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import main  # noqa: E402
import outjob_edit  # noqa: E402

ADD_FAB = [{"op": "add_container", "name": "Fab", "type": "pdf", "folder": "Fab"}]


class EditOutJobToolTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "t.OutJob")
        outjob_edit.write(self.path, outjob_edit.new("t.OutJob"))
        self.commands = []

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, running, was_open=False, operations=ADD_FAB, name=None):
        self.params = {}

        async def fake(command, params, timeout=None):
            self.commands.append(command)
            self.params[command] = params
            if command == "release_outjob":
                result = {"success": True, "was_open": was_open}
                if name:
                    result["name"] = name
                return {"success": True, "result": result}
            return {"success": True, "result": {"success": True}}
        with mock.patch.object(main.altium_bridge, "execute_command", fake), \
                mock.patch.object(main.altium_bridge, "_altium_running", lambda: running):
            return json.loads(asyncio.run(main.edit_outjob(None, self.path, operations)))

    def test_altium_not_running_is_not_started(self):
        out = self.run_tool(running=False)
        self.assertTrue(out["written"])
        self.assertEqual(self.commands, [])
        self.assertIn("Fab", [c["name"] for c in outjob_edit.read(self.path).containers()])

    def test_closed_and_reopened_around_the_write(self):
        out = self.run_tool(running=True, was_open=True)
        self.assertTrue(out["written"])
        self.assertEqual(self.commands, ["release_outjob", "reopen_outjob"])
        self.assertTrue(out["reopened_in_altium"])

    def test_reopened_under_altiums_own_name(self):
        shown = self.path.upper()
        self.run_tool(running=True, was_open=True, name=shown)
        self.assertEqual(self.params["reopen_outjob"]["outjob_path"], shown)

    def test_reopened_when_the_write_fails(self):
        with mock.patch.object(outjob_edit, "write", side_effect=OSError("disk full")):
            out = self.run_tool(running=True, was_open=True)
        self.assertFalse(out["written"])
        self.assertFalse(out["success"])
        self.assertIn("disk full", out["error"])
        self.assertEqual(self.commands, ["release_outjob", "reopen_outjob"])


    def test_nothing_is_written_when_an_operation_fails(self):
        with open(self.path, "rb") as f:
            before = f.read()
        out = self.run_tool(running=True, was_open=True, operations=ADD_FAB + [
            {"op": "copy_output", "output": "Nope", "containers": ["Fab"]}])
        self.assertFalse(out["success"])
        self.assertFalse(out["written"])
        self.assertEqual(len(out["done"]), 1)
        self.assertEqual(self.commands, [])
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), before)

    def test_create_outjob_reports_a_failed_write(self):
        path = os.path.join(self.tmp.name, "new.OutJob")
        with mock.patch.object(outjob_edit, "write", side_effect=OSError("disk full")):
            out = json.loads(asyncio.run(main.create_outjob(None, path)))
        self.assertFalse(out["success"])
        self.assertIn("disk full", out["error"])

if __name__ == "__main__":
    unittest.main()
