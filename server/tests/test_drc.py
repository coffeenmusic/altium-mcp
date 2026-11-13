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
        self.assertEqual(drc.parse_report(""), {"violations": None, "waived": None, "rules": [],
                                                 "errors": [], "stopped": False})

    def test_stopped_at_the_violation_limit(self):
        head, rest = REPORT.split("\n\n", 1)
        r = drc.parse_report(head + "\n\nERROR : More than 500 violations detected, DRC was stopped\n\n" + rest)
        self.assertTrue(r["stopped"])
        self.assertEqual(r["errors"], ["More than 500 violations detected, DRC was stopped"])
        self.assertEqual(len(r["rules"]), 3)
        self.assertFalse(drc.parse_report(REPORT)["stopped"])

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


class ViolationTest(unittest.TestCase):
    def test_measurement_from_the_descriptor(self):
        self.assertEqual(drc.measurement("Clearance Constraint: (0.659mm < 1.27mm) Between Via (91.6mm,65.4mm)"),
                         {"kind": "Clearance Constraint", "actual_mils": 25.945, "limit_mils": 50.0,
                          "comparison": "<"})
        w = drc.measurement("Width Constraint: Track (9mm,44.2mm)(12.475mm,47.675mm) on SIG Actual Width = "
                            "0.125mm, Target Width = 0.254mm")
        self.assertEqual((w["actual_mils"], w["limit_mils"], w["comparison"]), (4.921, 10.0, "<"))
        self.assertEqual(drc.measurement("Un-Routed Net Constraint: Net GND Between Pad"),
                         {"kind": "Un-Routed Net Constraint"})

    def test_filters(self):
        v = {"rule": "Clearance_GND", "kind": "Clearance Constraint", "x_mils": 100, "y_mils": 100,
             "primitive1": {"net": "GND", "x_mils": 95, "y_mils": 100},
             "primitive2": {"net": "CLK", "x_mils": 300, "y_mils": 300}}
        self.assertTrue(drc.matches(v, rules=["clearance"]))
        self.assertFalse(drc.matches(v, rules=["width"]))
        self.assertTrue(drc.matches(v, nets=["clk"]))
        self.assertFalse(drc.matches(v, nets=["5V"]))
        self.assertTrue(drc.matches(v, area=[250, 250, 350, 350]))    # by an object's position
        self.assertFalse(drc.matches(v, area=[500, 500, 600, 600]))

    def test_rules_match_whole_names_and_kinds(self):
        def viol(rule, description):
            return {"rule": rule, "description": description, **drc.measurement(description)}

        gnd = viol("Clearance_GND", "Clearance Constraint: (0.1mm < 0.152mm) Between Via (1mm,1mm) And Via")
        holes = viol("HoleToHoleClearance", "Hole To Hole Clearance (Gap=0.1mm): (0.05mm < 0.1mm) Between Pad")
        edge = viol("BoardOutlineClearance", "Board Outline Clearance(Outline Edge): (Collision < 25mil) Between")
        net = viol("UnRoutedNet", "Un-Routed Net Constraint: Net GND Between Pad U1-1 And Pad U1-2")
        self.assertEqual(edge["kind"], "Board Outline Clearance")
        # a kind picks every rule of that kind, a name only its rule
        self.assertTrue(drc.matches(gnd, rules=["Clearance"]))
        self.assertTrue(drc.matches(gnd, rules=["clearance_gnd"]))
        self.assertFalse(drc.matches(holes, rules=["Clearance"]))
        self.assertFalse(drc.matches(edge, rules=["Clearance"]))
        self.assertTrue(drc.matches(holes, rules=["Hole To Hole Clearance"]))
        self.assertTrue(drc.matches(edge, rules=["BoardOutlineClearance"]))
        self.assertTrue(drc.matches(net, rules=["Un-Routed Net"]))
        self.assertTrue(drc.matches(net, rules=["unroutednet constraint"]))
        self.assertFalse(drc.matches(net, rules=["Net"]))


# The rules of REPORT as get_pcb_rules / run_drc read them, plus a
# differential pair rule. Its kind (51) was not ticked for Batch: the report
# has no block for it.
CLEARANCE = {"name": "Clearance", "kind": 0, "kind_name": "Clearance",
             "descriptor": "Clearance Constraint (Gap=0.15mm) (All),(All)", "filter1": "All", "filter2": "All"}
WIDTH = {"name": "Width", "kind": 2, "kind_name": "Width",
         "descriptor": "Width Constraint (Min=0.15mm) (Max=3mm) (Preferred=0.15mm) (All)",
         "filter1": "All", "filter2": ""}
HOLES = {"name": "HoleToHoleClearance", "kind_name": "Hole To Hole Clearance",
         "descriptor": "Hole To Hole Clearance (Gap=0.2mm) (IsMicroVia and ((StartLayer = 'Top Layer') "
                       "and (StopLayer = 'Inner 1'))),(All)",
         "filter1": "IsMicroVia and ((StartLayer = 'Top Layer') and (StopLayer = 'Inner 1'))", "filter2": "All"}
DIFF_PAIR = {"name": "DP_eDP", "kind": 51, "kind_name": "Differential Pairs Routing",
             "descriptor": "Differential Pairs Uncoupled Length using the Gap Constraints (Min=0.1mm) "
                           "(Max=0.1mm) (Prefered=0.1mm) and Width Constraints (Min=0.1mm) (Max=0.1mm) "
                           "(Prefered=0.1mm) (InDifferentialPairClass('eDP'))",
             "filter1": "InDifferentialPairClass('eDP')", "filter2": ""}
# The same report once kind 51 is ticked for Batch
REPORT_51 = REPORT.replace("\n\nViolations Detected", "\n\nProcessing Rule : " + DIFF_PAIR["descriptor"]
                           + "\nRule Violations :0\n\nViolations Detected")


class RulesNotCheckedTest(unittest.TestCase):
    def processed(self, report):
        return [r["rule"] for r in drc.parse_report(report)["rules"]]

    def test_rule_without_a_block_was_not_checked(self):
        skipped = drc.rules_not_checked([CLEARANCE, WIDTH, HOLES, DIFF_PAIR], self.processed(REPORT))
        self.assertEqual(skipped, [DIFF_PAIR])
        warning = drc.not_checked_warning(skipped)
        self.assertIn("DP_eDP", warning)
        self.assertIn("Batch for Differential Pairs Routing", warning)

    def test_every_rule_checked(self):
        self.assertEqual(drc.rules_not_checked([CLEARANCE, WIDTH, HOLES, DIFF_PAIR],
                                               self.processed(REPORT_51)), [])

    def test_block_text_differing_from_the_descriptor(self):
        # Batch is ticked per kind: a rule of a kind that has a block was checked
        rule = dict(CLEARANCE, descriptor="Clearance Constraint (Gap=0.150mm) (All),(All)")
        self.assertEqual(drc.rules_not_checked([rule], self.processed(REPORT)), [])
        gnd = dict(CLEARANCE, name="Clearance_GND", descriptor="Clearance Constraint (Gap=0.3mm) (InNet('GND')),(All)")
        self.assertEqual(drc.rules_not_checked([gnd, CLEARANCE], self.processed(REPORT)), [])
        # No descriptor: its kind_name stands in
        self.assertEqual(drc.rules_not_checked([dict(CLEARANCE, descriptor="")], self.processed(REPORT)), [])
        self.assertEqual(drc.rules_not_checked([dict(DIFF_PAIR, descriptor="")], self.processed(REPORT)),
                         [dict(DIFF_PAIR, descriptor="")])

class PanelsTest(unittest.TestCase):
    def test_only_panels_the_check_opened_are_closed(self):
        with mock.patch.object(drc, "_altium_windows", return_value=[7, 9]) as found, \
                mock.patch.object(drc.ctypes, "windll", create=True) as windll:
            self.assertEqual(drc.close_new_panels({7}), 1)
        found.assert_called_with(drc.FLOATING_PANEL_CLASS)
        self.assertEqual([c.args[:2] for c in windll.user32.PostMessageW.call_args_list], [(9, drc.WM_CLOSE)])


if __name__ == "__main__":
    unittest.main()
