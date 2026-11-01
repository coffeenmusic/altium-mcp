"""Unit tests for drc (pure Python, no Altium needed).

Run from server/:  python -m unittest tests.test_drc -v
"""
import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import drc  # noqa: E402

REPORT = """Protel Design System Design Rule Check
PCB File : C:\\x\\board.PcbDoc
Date     : 01/11/2026
Time     : 11:44:49

Processing Rule : Clearance Constraint (Gap=0.15mm) (All),(All)
Rule Violations :3

Processing Rule : Width Constraint (Min=0.15mm) (Max=3mm) (Preferred=0.15mm) (All)
Rule Violations :0

Processing Rule : Hole To Hole Clearance (Gap=0.2mm) (IsMicroVia and ((StartLayer = 'Top Layer') and (StopLayer = 'Inner 1'))),(All)
Rule Violations :1


Violations Detected : 4
Waived Violations : 1
Time Elapsed        : 00:00:00
"""


class ReportTest(unittest.TestCase):
    def test_parse_report(self):
        r = drc.parse_report(REPORT)
        self.assertEqual(r["violations"], 4)
        self.assertEqual(r["waived"], 1)
        self.assertEqual([x["violations"] for x in r["rules"]], [3, 0, 1])
        self.assertEqual(r["rules"][0]["rule"], "Clearance Constraint (Gap=0.15mm) (All),(All)")
        self.assertIn("StopLayer = 'Inner 1'", r["rules"][2]["rule"])

    def test_parse_empty(self):
        self.assertEqual(drc.parse_report(""), {"violations": None, "waived": None, "rules": []})

    def test_find_report_beside_board_or_in_output_folder(self):
        with tempfile.TemporaryDirectory() as d:
            board = Path(d) / "board.PcbDoc"
            board.write_text("x")
            self.assertIsNone(drc.find_report(str(board), time.time()))
            beside = Path(d) / "Design Rule Check - board.drc"
            beside.write_text(REPORT)
            self.assertEqual(drc.find_report(str(board), time.time()), beside)
            out = Path(d) / "Project Outputs for P" / "Design Rule Check - board.drc"
            out.parent.mkdir()
            out.write_text(REPORT)
            later = time.time() + 5
            os.utime(out, (later, later))
            self.assertEqual(drc.find_report(str(board), time.time()), out)

    def test_old_report_is_not_this_run(self):
        with tempfile.TemporaryDirectory() as d:
            board = Path(d) / "board.PcbDoc"
            old = Path(d) / "Design Rule Check - board.drc"
            old.write_text(REPORT)
            past = time.time() - 3600
            os.utime(old, (past, past))
            self.assertIsNone(drc.find_report(str(board), time.time()))


class DriveDialogTest(unittest.TestCase):
    """drive_dialog with the window functions replaced: the outcome must say
    whether the check really ran."""

    def run_driver(self, open_states, button=1):
        states = iter(open_states)
        outcome = drc.DialogOutcome()
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(drc, "find_dialog", return_value=42), \
                mock.patch.object(drc, "_run_button", return_value=button), \
                mock.patch.object(drc, "_is_open", side_effect=lambda h: next(states, True)), \
                mock.patch.object(drc.ctypes, "windll") as windll:
            async def go():
                task = asyncio.create_task(drc.drive_dialog(outcome, Path(d) / "progress.txt", keep_alive=0.01,
                                                            settle=0, press_wait=0.5))
                for _ in range(400):
                    if outcome.pressed or outcome.cancelled:
                        break
                    await asyncio.sleep(0.01)
                await asyncio.sleep(0.05)
                task.cancel()
            asyncio.run(go())
            posted = [c.args[1] for c in windll.user32.PostMessageW.call_args_list]
        return outcome, posted

    def test_pressed_when_dialog_closes(self):
        outcome, posted = self.run_driver([True, False])
        self.assertTrue(outcome.pressed)
        self.assertFalse(outcome.cancelled)
        self.assertEqual(posted, [drc.WM_LBUTTONDOWN, drc.WM_LBUTTONUP])

    def test_closed_not_left_open_when_run_does_nothing(self):
        outcome, posted = self.run_driver([True] * 1000)
        self.assertFalse(outcome.pressed)
        self.assertTrue(outcome.cancelled)
        self.assertEqual(posted[-1], drc.WM_CLOSE)
        self.assertEqual(posted.count(drc.WM_LBUTTONDOWN), 3)


if __name__ == "__main__":
    unittest.main()
