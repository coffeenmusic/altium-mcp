"""Unit tests for eco_dialog (no Altium needed: the windows are faked).

Run from server/:  python -m unittest tests.test_eco_dialog -v
"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import eco_dialog  # noqa: E402

# As Altium AD25's Engineering Change Order dialog exposes them
ROWS = [
    "  Remove Pins From Nets(2)      ",
    " True Remove R2A-1 from 5V_6 In Board.PcbDoc   ",
    " True Remove R2A-2 from 5V In Board.PcbDoc   ",
    "  Change Net Names(1)      ",
    " True Modify NET_3 -> NET_4 In Board.PcbDoc   ",
    "  Add Component Classes(1)      ",
    " True Add U_Stack To Board.PcbDoc   Class already exists: U_Stack ",
    "  Remove Components(1)      ",
    " False Remove R9 From Board.PcbDoc   ",
]
CLEAN = [
    "  Add Nets(1)      ",
    " True Add SENSE To Board.PcbDoc   ",
    "  Add Components(1)      ",
    " True Add R5 To Board.PcbDoc   ",
]


class ParseTest(unittest.TestCase):
    def test_rows(self):
        rows = eco_dialog.parse_rows(ROWS, "Board.PcbDoc")
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0], {"group": "Remove Pins From Nets", "enabled": True,
                                   "change": "Remove R2A-1 from 5V_6 In Board.PcbDoc", "message": ""})
        self.assertEqual(rows[2]["group"], "Change Net Names")
        self.assertEqual(rows[3]["message"], "Class already exists: U_Stack")
        self.assertFalse(rows[4]["enabled"])
        self.assertEqual([r["message"] for r in rows if r["message"]], ["Class already exists: U_Stack"])
        self.assertEqual(eco_dialog.parse_list(ROWS, "Board.PcbDoc")[1], [])

    def test_row_without_board_name_is_a_problem(self):
        rows, problems = eco_dialog.parse_list(CLEAN[:1] + [" True Add SENSE To Other.PcbDoc   "], "Board.PcbDoc")
        self.assertEqual(rows[0]["message"], "")
        self.assertIn("does not name the board", problems[0])

    def test_group_counts_checked(self):
        # a collapsed group: its header says 2, one row was read
        _, problems = eco_dialog.parse_list(["  Add Nets(2)  ", " True Add A To Board.PcbDoc "], "Board.PcbDoc")
        self.assertEqual(problems, ["group 'Add Nets' lists 2 change(s) but 1 were read"])
        _, problems = eco_dialog.parse_list([" True Add A To Board.PcbDoc "], "Board.PcbDoc")
        self.assertIn("outside any group", problems[0])

    def test_execute_blockers(self):
        rows, problems = eco_dialog.parse_list(ROWS, "Board.PcbDoc")
        self.assertIn("failed validation", eco_dialog.execute_blockers(rows, problems, False, False)[0])
        # R9's removal is unticked, so it does not count
        self.assertEqual(eco_dialog.execute_blockers(rows, problems, True, False), [])
        rows, problems = eco_dialog.parse_list(CLEAN + ["  Remove Components(1)  ", " True Remove R9 From Board.PcbDoc "],
                                               "Board.PcbDoc")
        self.assertIn("remove 1 component", eco_dialog.execute_blockers(rows, problems, False, False)[0])
        self.assertEqual(eco_dialog.execute_blockers(rows, problems, False, True), [])
        self.assertEqual(eco_dialog.execute_blockers([], [], True, True), ["the dialog lists no changes"])

    def test_row_without_flag_counts_as_enabled(self):
        rows, _ = eco_dialog.parse_list(["  Remove Components(1)  ", " Remove R9 From Board.PcbDoc "],
                                        "Board.PcbDoc")
        self.assertEqual(rows[0]["change"], "Remove R9 From Board.PcbDoc")
        self.assertTrue(rows[0]["enabled"])
        self.assertIn("remove 1 component", eco_dialog.execute_blockers(rows, [], False, False)[0])

    def test_room_removal_from_altiums_list(self):
        rows, problems = eco_dialog.parse_list(["  Remove Rooms(2)  ", " True Remove Room A From Board.PcbDoc ",
                                                " False Remove Room B From Board.PcbDoc "], "Board.PcbDoc")
        self.assertEqual(eco_dialog.execute_blockers(rows, problems, False, False),
                         ["the update would remove 1 room(s) (allow_room_removal=true if that is intended)"])
        self.assertEqual(eco_dialog.execute_blockers(rows, problems, False, False, True), [])

    def test_board_in_project(self):
        prj = "[Document1]\r\nDocumentPath=Top.SchDoc\r\n[Document2]\r\nDocumentPath=pcb\\Board.PcbDoc\r\n"
        self.assertTrue(eco_dialog.board_in_project(r"c:\work\PCB\board.pcbdoc", prj, r"C:\work\Main.PrjPcb"))
        self.assertTrue(eco_dialog.board_in_project("C:/work/x/../pcb/Board.PcbDoc", prj, r"C:\work\Main.PrjPcb"))
        self.assertFalse(eco_dialog.board_in_project(r"C:\copy\pcb\Board.PcbDoc", prj, r"C:\work\Main.PrjPcb"))
        self.assertFalse(eco_dialog.board_in_project(r"C:\work\Top.SchDoc", prj, r"C:\work\Main.PrjPcb"))


class FakeWin:
    """Altium's windows as the driver sees them. A window is a dict; a
    button's click runs its action. Windows in `queue` open one after the
    other, each once the new windows before it are gone."""

    def __init__(self):
        self.w = {}
        self.next = 10
        self.queue = []
        self.closes = []       # windows sent WM_CLOSE
        self.clicks = []       # button texts clicked
        self.rows = []
        self.frame = self.add("TDXPFrame", "Altium", enabled=True)
        self.base = {self.frame}   # open before the run

    def add(self, cls, text="", buttons=(), labels=(), enabled=True, closes=True):
        h = self.next
        self.next += 10
        kids = []
        self.w[h] = {"cls": cls, "text": text, "kids": kids, "alive": True, "enabled": enabled,
                     "closes": closes, "action": None}
        for i, (label, action) in enumerate(buttons):
            kids.append(h + 1 + i)
            self.w[h + 1 + i] = {"cls": "TXPBitBtn", "text": label, "kids": [], "alive": True,
                                 "enabled": True, "closes": True, "action": action, "owner": h}
        for j, label in enumerate(labels):
            k = h + 5 + j
            kids.append(k)
            self.w[k] = {"cls": "TLabel", "text": label, "kids": [], "alive": True, "enabled": True,
                         "closes": True, "action": None}
        return h

    def kill(self, h):
        self.w[h]["alive"] = False

    def open_next(self):
        if self.queue and not any(self.w[h]["alive"] for h in self.new()):
            self.queue.pop(0)()

    def new(self):
        return [h for h, v in self.w.items() if h % 10 == 0 and h not in self.base]

    def done(self):
        return not self.queue and not any(self.w[h]["alive"] for h in self.new())

    # Win32's interface
    def windows(self):
        self.open_next()
        modal = any(self.w[h]["alive"] for h in self.new())
        self.w[self.frame]["enabled"] = not modal
        return {h for h, v in self.w.items() if h % 10 == 0 and v["alive"]}

    def cls(self, h):
        return self.w[h]["cls"]

    def text(self, h):
        return self.w[h]["text"]

    def children(self, h):
        return list(self.w[h]["kids"])

    def alive(self, h):
        return self.w[h]["alive"]

    def enabled(self, h):
        return self.w[h]["enabled"]

    def click(self, h):
        self.clicks.append(self.w[h]["text"])
        if self.w[h]["action"]:
            self.w[h]["action"]()

    def close(self, h):
        self.closes.append(h)
        if self.w[h]["closes"]:
            self.kill(h)

    def wait_idle(self, h, seconds):
        return not getattr(self, "busy", False)

    def read_rows(self, tree):
        return list(self.rows)


def drive(win, execute=False, allow_errors=False, auto_links=False, allow_component_removal=False, until=None,
          **timing):
    outcome = eco_dialog.EcoOutcome()
    timing = dict({"poll": 0.02, "grace": 0.2, "answer_wait": 0.1, "idle_slice": 0.05}, **timing)

    async def main():
        stop = asyncio.Event()
        task = asyncio.create_task(eco_dialog.drive(outcome, "Board.PcbDoc", execute, allow_errors, auto_links,
                                                    stop, {win.frame}, allow_component_removal, win=win,
                                                    **timing))
        for _ in range(500):
            if win.done() or (until and until(outcome)):
                break
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.1)
        stop.set()
        await asyncio.wait_for(task, 10)
    asyncio.run(main())
    return outcome


def eco_form(win, rows_after=None, close_button=True, closes=True, on_validate=None, on_execute=None):
    """The ECO dialog: Validate does nothing visible, Execute updates the
    rows, Close closes it."""
    def make():
        form = win.add("TChangeManagementForm", "Engineering Change Order", closes=closes)

        def execute():
            win.rows = rows_after or win.rows
            if on_execute:
                on_execute()
        buttons = [("Validate Changes", on_validate), ("Execute Changes", execute)]
        if close_button:
            buttons.append(("Close", (lambda: win.kill(form)) if closes else None))
        for i, (label, action) in enumerate(buttons):
            k = form + 1 + i
            win.w[form]["kids"].append(k)
            win.w[k] = {"cls": "TXPBitBtn", "text": label, "kids": [], "alive": True, "enabled": True,
                        "closes": True, "action": action}
        tree = form + 9
        win.w[form]["kids"].append(tree)
        win.w[tree] = {"cls": "TdxTreeList", "text": "", "kids": [], "alive": True, "enabled": True,
                       "closes": True, "action": None}
        return form
    return make


class DriverTest(unittest.TestCase):
    def test_no_differences_box_answered_and_reported(self):
        win = FakeWin()
        box = []
        win.queue.append(lambda: box.append(win.add("TMessageForm", "Information",
                                                    buttons=[("OK", lambda: win.kill(box[0]))],
                                                    labels=["No differences detected."])))
        o = drive(win)
        self.assertFalse(o.found)
        self.assertEqual(o.messages, ["Information No differences detected."])
        self.assertEqual(win.clicks, ["OK"])
        self.assertEqual(o.problem, "")

    def test_task_dialog_box_closed_with_wm_close_when_its_button_does_not(self):
        win = FakeWin()
        box = []
        win.queue.append(lambda: box.append(win.add("TThemedTaskDialog", "Information",
                                                    buttons=[("OK", None)])))
        o = drive(win)
        self.assertEqual(win.clicks, ["OK"])
        self.assertEqual(win.closes, box)
        self.assertFalse(win.alive(box[0]))

    def test_validate_only_reads_and_closes(self):
        win = FakeWin()
        win.rows = ROWS
        win.queue.append(eco_form(win))
        o = drive(win)
        self.assertTrue(o.found and o.validated and o.closed)
        self.assertFalse(o.executed)
        self.assertEqual(len(o.rows), 5)
        self.assertEqual(win.clicks, ["Validate Changes", "Close"])

    def test_component_links_cancelled_then_executed(self):
        win = FakeWin()
        win.rows = CLEAN
        done = [r.replace("   ", "") for r in CLEAN]
        links = []
        win.queue.append(lambda: links.append(win.add("TThemedTaskDialog", "Component Links", buttons=[
            ("Automatically Create Component Links", lambda: win.kill(links[0])),
            ("Cancel", lambda: win.kill(links[0]))])))
        win.queue.append(eco_form(win, rows_after=done))
        o = drive(win, execute=True)
        self.assertEqual(o.component_links, "unique id only")
        self.assertEqual(win.clicks, ["Cancel", "Validate Changes", "Execute Changes", "Close"])
        self.assertTrue(o.executed)
        self.assertEqual(o.executed_changes, [{"group": "Add Nets", "change": "Add SENSE To Board.PcbDoc"},
                                              {"group": "Add Components", "change": "Add R5 To Board.PcbDoc"}])

    def test_missing_links_button_closes_question_and_blocks_execute(self):
        win = FakeWin()
        win.rows = CLEAN
        links = []
        win.queue.append(lambda: links.append(win.add("TThemedTaskDialog", "Component Links",
                                                      buttons=[("Cancel", None)])))
        win.queue.append(eco_form(win))
        o = drive(win, execute=True, auto_links=True)
        self.assertEqual(win.closes[:1], links)
        self.assertTrue(o.found and o.validated and o.closed)
        self.assertFalse(o.executed)
        self.assertIn("Automatically Create Component Links", o.not_executed[0])

    def test_unclean_list_not_executed(self):
        win = FakeWin()
        win.rows = ["  Add Nets(2)  ", " True Add SENSE To Board.PcbDoc "]
        win.queue.append(eco_form(win))
        o = drive(win, execute=True)
        self.assertFalse(o.executed)
        self.assertEqual(o.validation_problems, ["group 'Add Nets' lists 2 change(s) but 1 were read"])
        self.assertEqual(o.not_executed, o.validation_problems)
        self.assertNotIn("Execute Changes", win.clicks)

    def test_component_removal_needs_permission(self):
        win = FakeWin()
        win.rows = ["  Remove Components(1)  ", " True Remove R9 From Board.PcbDoc "]
        win.queue.append(eco_form(win))
        o = drive(win, execute=True)
        self.assertFalse(o.executed)
        self.assertIn("allow_component_removal", o.not_executed[0])

    def test_box_during_validation_blocks_execute(self):
        win = FakeWin()
        win.rows = CLEAN
        box = []

        def warn():
            box.append(win.add("TMessageForm", "Warning", buttons=[("OK", lambda: win.kill(box[0]))],
                               labels=["Some sheets were not compiled."]))
        win.queue.append(eco_form(win, on_validate=warn))
        o = drive(win, execute=True)
        self.assertTrue(o.validated)
        self.assertFalse(o.execute_pressed or o.executed)
        self.assertIn("1 message(s) while the changes were validated", o.not_executed[0])
        self.assertNotIn("Execute Changes", win.clicks)

    def test_box_that_will_not_close_blocks_execute(self):
        win = FakeWin()
        win.rows = CLEAN
        box = []
        win.queue.append(eco_form(win, on_validate=lambda: box.append(
            win.add("TMessageForm", "Stuck", buttons=[("OK", None)], closes=False))))
        o = drive(win, execute=True, until=lambda o: o.closed)
        self.assertIn("could not close", o.problem)
        self.assertIn(o.problem, o.not_executed)
        self.assertNotIn("Execute Changes", win.clicks)

    def test_execute_pressed_is_recorded_when_its_end_is_not_seen(self):
        win = FakeWin()
        win.rows = CLEAN
        win.queue.append(eco_form(win, on_execute=lambda: setattr(win, "busy", True)))
        alive = []
        o = drive(win, execute=True, work_timeout=0.3, keep_alive=lambda: alive.append(1))
        self.assertTrue(o.execute_pressed)
        self.assertFalse(o.executed)
        self.assertEqual(o.problem, "executing the changes did not finish in time")
        # the bridge was told Altium was still at work while it waited
        self.assertGreater(len(alive), 2)

    def test_error_box_recorded_as_error(self):
        win = FakeWin()
        box = []
        win.queue.append(lambda: box.append(win.add("TMessageForm", "Error", buttons=[("OK", lambda: win.kill(box[0]))],
                                                    labels=["Access violation at address 0040"])))
        o = drive(win)
        self.assertFalse(o.found)
        self.assertEqual(o.error_messages, ["Error Access violation at address 0040"])

    def test_open_eco_dialogs(self):
        win = FakeWin()
        self.assertEqual(eco_dialog.open_eco_dialogs(win), [])
        form = eco_form(win)()
        self.assertEqual(eco_dialog.open_eco_dialogs(win), [form])

    def test_form_without_close_button_closed_with_wm_close(self):
        win = FakeWin()
        win.rows = CLEAN
        forms = []
        win.queue.append(lambda: forms.append(eco_form(win, close_button=False)()))
        o = drive(win)
        self.assertTrue(o.closed)
        self.assertEqual(win.closes, forms)

    def test_unknown_modal_closed_after_a_while_and_old_windows_left_alone(self):
        win = FakeWin()
        old = win.add("TMessageForm", "Was open before")
        win.base.add(old)
        unknown = []
        win.queue.append(lambda: unknown.append(win.add("TSomeOptionsForm", "Options")))
        o = eco_dialog.EcoOutcome()

        async def main():
            stop = asyncio.Event()
            task = asyncio.create_task(eco_dialog.drive(o, "Board.PcbDoc", False, False, False, stop,
                                                        {win.frame, old}, win=win, poll=0.02, grace=0.2,
                                                        answer_wait=0.1))
            for _ in range(200):
                if unknown and not win.alive(unknown[0]):
                    break
                await asyncio.sleep(0.02)
            stop.set()
            await asyncio.wait_for(task, 5)
        asyncio.run(main())
        self.assertFalse(win.alive(unknown[0]))
        self.assertTrue(win.alive(old))
        self.assertNotIn(old, win.closes)
        self.assertEqual(o.messages, ["Options"])



class ToolTest(unittest.TestCase):
    """update_pcb in main.py, with the bridge and the dialog faked."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.prj = os.path.join(self.dir.name, "Main.PrjPcb")
        with open(self.prj, "w") as f:
            f.write("[Document1]\nDocumentPath=Board.PcbDoc\n")
        self.board = os.path.join(self.dir.name, "Board.PcbDoc")
        self.calls = []

    def tearDown(self):
        self.dir.cleanup()

    def run_tool(self, target=None, fake_drive=None, bridge_error="", eco_open=(), **kwargs):
        import main
        source = {"project_path": self.prj, "pcb_path": self.board,
                  "components": [r"\A|R1|R0603|1k"], "pins": []}
        target = target or {"board_path": self.board, "components": [r"\A|R1|R0603|1k"], "pads": []}
        self.drive_kwargs = {}

        async def fake(command, params, timeout=None, picked_up=None):
            self.calls.append(command)
            if command == "run_update_pcb":
                self.timeout = timeout
                await asyncio.sleep(0.05)
                self.driven_before_pick_up = bool(self.drive_kwargs)
                picked_up.set()
                for _ in range(100):
                    if self.drive_kwargs:
                        break
                    await asyncio.sleep(0.01)
                if bridge_error:
                    return {"success": False, "error": bridge_error}
            reply = {"get_eco_source": source, "get_eco_target": target,
                     "run_update_pcb": {"pcb_modified": False}}[command]
            return {"success": True, "result": json.dumps(reply)}

        async def no_dialog(outcome, board_name, execute, allow_errors, auto_links, stop, before,
                            allow_component_removal=False, **kw):
            await stop.wait()

        async def recorded(*args, **kw):
            self.drive_kwargs = dict(kw, before=args[6]) or {"called": True}
            await (fake_drive or no_dialog)(*args, **kw)
        with mock.patch.object(main.altium_bridge, "execute_command", fake), \
                mock.patch.object(eco_dialog, "drive", recorded), \
                mock.patch.object(eco_dialog, "open_eco_dialogs", lambda win=None: list(eco_open)), \
                mock.patch.object(eco_dialog, "open_windows", lambda win=None: {1}):
            return json.loads(asyncio.run(main.update_pcb(None, project_path=self.prj, **kwargs)))

    def test_board_of_another_project_refused(self):
        reply = self.run_tool(pcb_path=os.path.join(self.dir.name, "copy", "Board.PcbDoc"))
        self.assertFalse(reply["success"])
        self.assertIn("is not a board of the project", reply["error"])
        self.assertEqual(self.calls, ["get_eco_source"])

    def test_preview_removals_are_information_only(self):
        # The preview says R2 goes; only Altium's own list decides
        target = {"board_path": self.board, "components": [r"\A|R1|R0603|1k", r"\B|R2|R0603|1k"], "pads": []}
        reply = self.run_tool(target=target, execute=True)
        self.assertIn("run_update_pcb", self.calls)
        self.assertEqual(reply["preview"], {"remove_components": 1})
        self.assertNotIn("warning", reply)

    def test_removal_in_altiums_list_warns_when_validating(self):
        async def listed(outcome, *args, **kwargs):
            outcome.found = outcome.validated = outcome.closed = True
            outcome.rows = [{"group": "Remove Rooms", "enabled": True, "change": "Remove Room A From Board.PcbDoc",
                             "message": ""}]
            await args[4].wait()
        reply = self.run_tool(fake_drive=listed)
        self.assertTrue(reply["success"])
        self.assertIn("allow_room_removal", reply["warning"])

    def test_driver_starts_once_the_request_is_picked_up(self):
        reply = self.run_tool(execute=True, allow_room_removal=True)
        self.assertFalse(self.driven_before_pick_up)
        self.assertEqual(self.drive_kwargs["before"], {1})
        self.assertTrue(self.drive_kwargs["allow_room_removal"])
        self.assertIsNotNone(self.drive_kwargs["keep_alive"])
        self.assertLess(self.timeout, 1800)

    def test_open_eco_dialog_refuses_the_run(self):
        reply = self.run_tool(eco_open=[123])
        self.assertFalse(reply["success"])
        self.assertIn("already open", reply["error"])
        self.assertEqual(self.calls, [])

    def test_second_run_refused_while_one_runs(self):
        import main

        async def two():
            async with main._update_pcb_lock:
                return json.loads(await main.update_pcb(None, project_path=self.prj))
        reply = asyncio.run(two())
        self.assertFalse(reply["success"])
        self.assertIn("another update_pcb is running", reply["error"])

    def test_execute_pressed_but_unread_is_unknown(self):
        async def pressed(outcome, *args, **kwargs):
            outcome.found = outcome.validated = outcome.execute_pressed = outcome.closed = True
            outcome.rows = [{"group": "Add Nets", "enabled": True, "change": "Add A To Board.PcbDoc", "message": ""}]
            outcome.problem = "executing the changes did not finish in time"
            await args[4].wait()
        reply = self.run_tool(fake_drive=pressed, execute=True)
        self.assertFalse(reply["success"])
        self.assertEqual(reply["executed"], "unknown")
        self.assertIn("did not finish in time", reply["executed_note"])

    def test_bridge_error_keeps_the_execute_state(self):
        async def pressed(outcome, *args, **kwargs):
            outcome.found = outcome.validated = outcome.execute_pressed = True
            await args[4].wait()
        reply = self.run_tool(fake_drive=pressed, execute=True, bridge_error="No response from Altium")
        self.assertFalse(reply["success"])
        self.assertEqual(reply["executed"], "unknown")
        self.assertIn("No response", reply["error"])

    def test_error_box_without_dialog_is_failure(self):
        async def failed(outcome, *args, **kwargs):
            outcome.messages.append("Error Access violation")
            outcome.error_messages.append("Error Access violation")
            await args[4].wait()
        reply = self.run_tool(fake_drive=failed)
        self.assertFalse(reply["success"])
        self.assertIn("Access violation", reply["error"])

    def test_bridge_sets_picked_up_apart_from_its_start_flag(self):
        # _execute_command_locked keeps a local "started" (did the bridge
        # start); the event it sets must be another name
        import inspect
        import main
        source = inspect.getsource(main.AltiumBridge._execute_command_locked)
        self.assertIn("picked_up: Optional[asyncio.Event] = None", source)
        self.assertNotRegex(source, r"\bpicked_up\s*=[^=]")
        self.assertIn("picked_up.set()", source)

    def test_empty_preview_still_asks_altium(self):
        async def message(outcome, *args, **kwargs):
            outcome.messages.append("Information No differences detected.")
            await args[4].wait()
        reply = self.run_tool(fake_drive=message)
        self.assertIn("run_update_pcb", self.calls)
        self.assertEqual(reply["preview"], {})
        self.assertFalse(reply["executed"])
        self.assertEqual(reply["altium_messages"], ["Information No differences detected."])

    def test_executed_changes_returned(self):
        async def executed(outcome, *args, **kwargs):
            outcome.found = outcome.validated = outcome.executed = outcome.closed = True
            outcome.rows = [{"group": "Add Nets", "enabled": True, "change": "Add A To Board.PcbDoc", "message": ""}]
            outcome.executed_changes = [{"group": "Add Nets", "change": "Add A To Board.PcbDoc"}]
            await args[4].wait()
        reply = self.run_tool(fake_drive=executed, execute=True)
        self.assertTrue(reply["success"])
        self.assertEqual(reply["executed_changes"], [{"group": "Add Nets", "change": "Add A To Board.PcbDoc"}])

    def test_blocked_execute_is_not_success(self):
        async def blocked(outcome, *args, **kwargs):
            outcome.found = outcome.validated = outcome.closed = True
            outcome.not_executed = ["group 'Add Nets' lists 2 change(s) but 1 were read"]
            await args[4].wait()
        reply = self.run_tool(fake_drive=blocked, execute=True)
        self.assertFalse(reply["success"])
        self.assertEqual(reply["not_executed"], ["group 'Add Nets' lists 2 change(s) but 1 were read"])


class PickedUpTest(unittest.TestCase):
    """The bridge's own request handling, not stubbed: picked_up is set once
    the bridge script has taken the request, not before."""

    def test_picked_up_set_when_the_request_is_taken(self):
        import main
        with tempfile.TemporaryDirectory() as d:
            req, resp, prog = Path(d) / "request.json", Path(d) / "response.json", Path(d) / "progress.txt"
            seen = []

            async def run():
                picked = asyncio.Event()

                async def bridge_script():
                    await asyncio.sleep(0.3)
                    seen.append(("before", picked.is_set()))
                    rid = json.loads(req.read_text())["request_id"]
                    req.unlink()
                    await asyncio.sleep(0.5)
                    seen.append(("after", picked.is_set()))
                    resp.write_text(json.dumps({"success": True, "request_id": rid, "result": "{}"}))

                async def launch():
                    asyncio.get_running_loop().create_task(bridge_script())
                    return True
                bridge = main.AltiumBridge.__new__(main.AltiumBridge)
                bridge.run_altium_script = launch
                bridge._altium_running = lambda: True
                return await bridge._execute_command_locked("run_update_pcb", {}, 30, picked_up=picked)
            with mock.patch.multiple(main, REQUEST_FILE=req, RESPONSE_FILE=resp, PROGRESS_FILE=prog,
                                     altium_limit_problem=lambda: None, _clear_script_stuck=lambda: None):
                response = asyncio.run(run())
        self.assertTrue(response["success"])
        self.assertEqual(seen, [("before", False), ("after", True)])


if __name__ == "__main__":
    unittest.main()
