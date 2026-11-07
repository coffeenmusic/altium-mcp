"""Unit tests for main._bridge_json (no Altium needed: the bridge is faked).

Run from server/:  python -m unittest tests.test_bridge_json -v
"""
import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import main  # noqa: E402


def run(response):
    async def fake(command, params, timeout=None):
        return response
    with mock.patch.object(main.altium_bridge, "execute_command", fake):
        return asyncio.run(main._bridge_json("x", {}))


class BridgeJsonTest(unittest.TestCase):
    def test_replies(self):
        self.assertEqual(run({"success": True, "result": {"a": 1}}), ({"a": 1}, ""))
        self.assertEqual(run({"success": True, "result": '{"a": "x\ty"}'}), ({"a": "x\ty"}, ""))
        self.assertEqual(run({"success": False, "error": "boom"}), (None, "boom"))
        self.assertEqual(run({"success": True, "result": "ERROR: no board"}), (None, "ERROR: no board"))
        self.assertIn("unexpected", run({"success": True, "result": "garbage"})[1])
        self.assertIn("unexpected", run({"success": True, "result": [1, 2]})[1])


if __name__ == "__main__":
    unittest.main()
