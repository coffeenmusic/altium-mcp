"""Unit tests for pcb_query and the query plumbing in main (no Altium
needed: the bridge and the Windows calls are faked).

Run from server/:  python -m unittest tests.test_pcb_query -v
"""
import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import pcb_query  # noqa: E402
from pcb_query import bare_numbers, check, check_rule_kind, rule_note, with_units  # noqa: E402


class UnitsTest(unittest.TestCase):
    def test_values_are_written_in_both_units(self):
        mm, mil, found = with_units("IsVia And (HoleSize > 15mil) And (Size < 0.6mm)")
        self.assertEqual(mm, "IsVia And (HoleSize > 0.381) And (Size < 0.6)")
        self.assertEqual(mil, "IsVia And (HoleSize > 15) And (Size < 23.622047)")
        self.assertEqual(found, ["15mil", "0.6mm"])

    def test_names_and_bare_numbers_are_left_alone(self):
        e = "InNet('5V_MM') And InComponent('U12') And (Width > 6)"
        self.assertEqual(with_units(e)[:2], (e, e))
        self.assertEqual(bare_numbers(e), ["6"])
        self.assertEqual(bare_numbers("IsTrack And (Width > 6mil)"), [])

    def test_only_lengths_are_warned_about(self):
        self.assertEqual(bare_numbers("IsComponent And (Rotation = 90)"), [])
        self.assertEqual(bare_numbers("IsArc And (StartAngle >= 45) And (Radius < 20)"), ["20"])
        self.assertEqual(bare_numbers("IsVia And (X = 100) And (HoleSize <= 0.3)"), ["100", "0.3"])
        self.assertEqual(bare_numbers("IsComponent And (Name = '1')"), [])

    def test_other_units(self):
        self.assertEqual(with_units("(Width > 1in)")[0], "(Width > 25.4)")
        self.assertEqual(with_units("(Width > 100um)")[1], "(Width > 3.937008)")


class CheckTest(unittest.TestCase):
    def test_rejected(self):
        self.assertIn("empty", check("  "))
        self.assertIn("quote", check('InNet("GND")'))
        self.assertIn("dot", check("IsVia And (HoleSize > 0,3)"))
        self.assertEqual(check("IsVia And InNet('GND')"), "")


class RuleKindTest(unittest.TestCase):
    def test_pairs_and_single_objects(self):
        self.assertEqual(check_rule_kind("clearance", "IsVia"), "")
        self.assertIn("give object_b", check_rule_kind("clearance", " "))
        self.assertEqual(check_rule_kind("width", ""), "")
        self.assertIn("leave object_b empty", check_rule_kind("hole_size", "IsVia"))
        self.assertIn("unknown rule_kind", check_rule_kind("colour", ""))
        for kind in pcb_query.UNARY_RULE_KINDS:
            self.assertIn(kind, pcb_query.RULE_KINDS)

    def test_notes(self):
        self.assertEqual(rule_note("clearance", {"rule": "Clearance", "enabled": True}), "")
        self.assertIn("disabled", rule_note("clearance", {"rule": "Clearance", "enabled": False}))
        same = {"rule": "", "object_a": {"net": "GND"}, "object_b": {"net": "GND"}}
        self.assertIn("Different Nets Only", rule_note("clearance", same))
        other = {"rule": "", "object_a": {"net": "GND"}, "object_b": {"net": "VCC"}}
        self.assertEqual(rule_note("clearance", other), "No clearance rule applies to this pair.")
        self.assertEqual(rule_note("width", {"rule": "", "object_a": {"net": "GND"}}),
                         "No width rule applies to this object.")


class FakeUser32:
    """EnumWindows over a set of windows; records whose text was asked for."""
    def __init__(self, windows):
        self.windows = windows  # hwnd -> (class, process, text)
        self.asked = []

    def EnumWindows(self, cb, _):
        for h in self.windows:
            cb(h, 0)
        return True

    def IsWindowVisible(self, h):
        return True


class MessageBoxTest(unittest.TestCase):
    def test_text_is_read_from_altium_windows_only(self):
        user32 = FakeUser32({1: ("TMessageForm", "other.exe", "Error"), 2: ("TMessageForm", "X2.EXE", "Error"),
                             3: ("TMessageForm", "X2.EXE", "Information")})

        def text(h):
            user32.asked.append(h)
            return user32.windows[h][2]
        with mock.patch.object(pcb_query.ctypes, "windll", mock.Mock(user32=user32)), \
                mock.patch.object(pcb_query, "_cls", lambda h: user32.windows[h][0]), \
                mock.patch.object(pcb_query, "_process_name", lambda h: user32.windows[h][1]), \
                mock.patch.object(pcb_query, "_text", text):
            self.assertEqual(pcb_query._message_boxes("Error"), [2])
        self.assertEqual(user32.asked, [2, 3])


class FakeLock:
    def __init__(self):
        self.held = False

    async def acquire(self, timeout):
        self.held = True
        return True

    def release(self):
        self.held = False


class WatchedCommandTest(unittest.TestCase):
    """A command its watcher gives up on is stopped while the locks are held."""

    def run_watched(self, script, watcher):
        import main
        bridge = main.AltiumBridge.__new__(main.AltiumBridge)
        bridge._command_lock = asyncio.Lock()
        bridge._exchange_lock = FakeLock()
        stops = []

        async def ensure_paths(need_script=True):
            return None

        async def stop():
            stops.append(bridge._exchange_lock.held)

        bridge.ensure_paths = ensure_paths
        bridge._execute_command_locked = lambda command, params, timeout: script()
        bridge.stop_script_debugger = stop
        bridge.STOP_SETTLE = 0
        with mock.patch.object(main.sandbox, "altium_hung", lambda: False):
            response = asyncio.run(bridge.execute_command("query_objects", {}, watcher=watcher))
        return response, stops, bridge

    def test_abort_stops_the_script_with_the_lock_held(self):
        async def never():
            await asyncio.sleep(60)

        async def watcher(abort):
            await asyncio.sleep(0.01)
            abort.set()
            await asyncio.sleep(60)

        response, stops, bridge = self.run_watched(never, watcher)
        self.assertTrue(response["aborted"])
        self.assertFalse(response["success"])
        self.assertEqual(stops, [True])
        self.assertFalse(bridge._exchange_lock.held)

    def test_answer_wins_and_nothing_is_stopped(self):
        async def answer():
            return {"success": True, "result": {"matched": 0}}

        async def watcher(abort):
            await asyncio.sleep(60)

        response, stops, _ = self.run_watched(answer, watcher)
        self.assertEqual(response, {"success": True, "result": {"matched": 0}})
        self.assertEqual(stops, [])


class RunQueryTest(unittest.TestCase):
    def run_query(self, replies, dialog=None):
        import main
        calls = []

        async def fake(command, params, timeout=None, watcher=None):
            calls.append(command)
            if watcher is not None:
                await watcher(asyncio.Event())
            return replies[command]

        async def watch_errors(errors, abort):
            # the dialogs Altium raised while the command ran
            errors.extend(dialog or [])

        with mock.patch.object(main.altium_bridge, "execute_command", fake), \
                mock.patch.object(pcb_query, "watch_errors", watch_errors):
            result = asyncio.run(main._run_query("query_objects", {"board_path": "b.PcbDoc"}))
        return result, calls

    def test_stopped_query_restores_the_selection(self):
        replies = {"query_objects": {"success": False, "aborted": True, "error": "stopped"},
                   "query_restore": {"success": True, "result": {"success": True, "restored": True}}}
        (result, error), calls = self.run_query(replies, dialog=["Undeclared identifier: InPad"])
        self.assertIsNone(result)
        self.assertIn("Undeclared identifier: InPad", error)
        self.assertEqual(calls, ["query_objects", "query_restore"])

    def test_plain_answer(self):
        replies = {"query_objects": {"success": True, "result": {"matched": 3}}}
        (result, error), calls = self.run_query(replies)
        self.assertEqual((result, error), ({"matched": 3}, ""))
        self.assertEqual(calls, ["query_objects"])


if __name__ == "__main__":
    unittest.main()
