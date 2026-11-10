"""Unit tests for main._bridge_json (no Altium needed: the bridge is faked).

Run from server/:  python -m unittest tests.test_bridge_json -v
"""
import asyncio
import json
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


def route(replies, **kw):
    """route_path with the bridge answering each geometry operation from
    replies; returns the reply and the operations asked for."""
    asked = []

    async def fake(command, params, timeout=None):
        asked.append(params.get("geometry"))
        return replies.get(params.get("geometry")), ""
    with mock.patch.object(main, "_bridge_json", fake):
        out = asyncio.run(main.route_path(None, **kw))
    return json.loads(out), asked


class RoutePathTest(unittest.TestCase):
    LOCATE = {"located": ["pad|0|0|Multi Layer|1|SIG"]}

    def test_through_hole_start_by_flag(self):
        # Altium names the layer "Multi Layer"; the flag says through-hole
        out, _ = route({"locate": self.LOCATE}, net="SIG", start="J1-1", waypoints=[[150, 0]], dry_run=True)
        self.assertTrue(out["success"])
        self.assertEqual(out["tracks"][0][0], "Top Layer")

    def test_removed_route_is_no_success(self):
        made = "track|Top Layer|SIG||0|0|1500000|0|100000|0"
        out, asked = route({"locate": self.LOCATE, "route": {"created": [made], "violations": [],
                                                              "removed_again": True}},
                           net="SIG", start="J1-1", waypoints=[[150, 0]], undo_on_violation=True)
        self.assertEqual(asked, ["locate", "route"])
        self.assertFalse(out["success"])
        self.assertEqual((out["tracks"], out["vias"]), ([], []))
        self.assertIn("plan", out)

    def test_undo_needs_the_check(self):
        out, asked = route({}, net="SIG", start=[0, 0, "Top Layer"], waypoints=[[150, 0]], check=False,
                           undo_on_violation=True)
        self.assertFalse(out["success"])
        self.assertIn("needs check", out["error"])
        self.assertEqual(asked, [])

    def test_route_is_a_change(self):
        self.assertTrue(main._is_mutating("geometry", {"geometry": "route"}))
        self.assertFalse(main._is_mutating("geometry", {"geometry": "locate"}))


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
