"""Unit tests for sandbox (pure Python, no Altium needed).

Run from the repo root:  python -m unittest server.tests.test_sandbox -v
or from server/:          python -m unittest tests.test_sandbox -v
"""
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import sandbox  # noqa: E402

TEMPLATE = (Path(__file__).resolve().parent.parent / "SandboxScript" / "Sandbox.pas").read_text(encoding="utf-8")


def build(script):
    return sandbox.build_script(TEMPLATE, script, r"C:\x\log.txt", r"C:\x\out.txt")


class VarBlockTest(unittest.TestCase):
    def test_no_var_block(self):
        sp = sandbox.split_var_block("S1 := 'a';\nI1 := 2;")
        self.assertEqual(sp.decls, [])
        self.assertEqual(sp.body, ["S1 := 'a';", "I1 := 2;"])
        self.assertEqual(sp.body_offset, 0)

    def test_var_block(self):
        sp = sandbox.split_var_block("var\n    Doc, Comp : IDispatch;\n    X : Integer;\nX := 1;")
        self.assertEqual(sp.decls, ["Doc, Comp : IDispatch;", "X : Integer;"])
        self.assertEqual(sp.decl_src, [2, 3])
        self.assertEqual(sp.body, ["X := 1;"])
        self.assertEqual(sp.body_offset, 3)
        self.assertEqual(sandbox.declared_names(sp.decls), ["Doc", "Comp", "X"])

    def test_var_on_first_line(self):
        sp = sandbox.split_var_block("var X : Integer;\nX := 1;")
        self.assertEqual(sp.decls, ["X : Integer;"])
        self.assertEqual(sp.body, ["X := 1;"])

    def test_variable_named_like_var_prefix(self):
        sp = sandbox.split_var_block("Variance := 1;")
        self.assertEqual(sp.decls, [])


class CheckTest(unittest.TestCase):
    def check(self, script):
        sp = sandbox.split_var_block(script)
        return sandbox.check_script(sp.body, sandbox.declared_names(sp.decls), sp.body_offset)

    def test_scratch_and_declared_names_pass(self):
        self.assertEqual(self.check("var\n  Doc : IDispatch;\nDoc := nil;\nS9 := 'x';\nfor I4 := 1 to 2 do F1 := 0.5;"), [])

    def test_undeclared_assignment(self):
        problems = self.check("S1 := 'a';\nS10 := 'b';")
        self.assertEqual(len(problems), 1)
        self.assertIn("line 2", problems[0])
        self.assertIn("'S10'", problems[0])

    def test_line_number_counts_var_block(self):
        problems = self.check("var\n  X : Integer;\nX := 1;\nY := 2;")
        self.assertIn("line 4", problems[0])

    def test_properties_indexes_strings_comments_ignored(self):
        script = ("Obj1.Location := Point(0, 0);\nList1[I1] := 'a';\n"
                  "S1 := 'Undeclared := 1';\n// Bogus := 1\n{ Other := 2 }\nS2 := 'it''s';")
        self.assertEqual(self.check(script), [])

    def test_case_insensitive(self):
        self.assertEqual(self.check("resulttext := 'ok';\nobj1 := nil;"), [])

    def test_with_statement_not_checked(self):
        # Inside a with block a plain name can be the object's property
        self.assertEqual(self.check("Obj1 := SchServer.GetCurrentSchDocument;\nwith Obj1 do\n  DocumentName := 'x';"), [])

    def test_template_declares_scratch_names(self):
        code = sandbox.strip_strings_and_comments(TEMPLATE)
        declared = set()
        for m in re.finditer(r"^\s*([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*:\s*[A-Za-z_]\w*\s*;", code, re.M):
            declared |= {n.strip().lower() for n in m.group(1).split(",")}
        missing = [n for n in sandbox.TEMPLATE_NAMES if n.lower() not in declared]
        self.assertEqual(missing, [])


class LintTest(unittest.TestCase):
    def lint(self, script):
        sp = sandbox.split_var_block(script)
        return sandbox.lint_script(sp.body, sp.body_offset, sp.decls)

    def test_clean_edit_passes(self):
        script = ("Obj1 := OpenSchDoc('C:\\x.SchDoc');\nSchServer.ProcessControl.PreProcess(Obj1, '');\n"
                  "Obj2.Corner := Point(MilsToCoord(100), MilsToCoord(100));\n"
                  "List1.Clear;\nList1.Add('a');\nSchServer.ProcessControl.PostProcess(Obj1, '');")
        self.assertEqual(self.lint(script), [])

    def test_known_crash_with_line_number(self):
        problems = self.lint("var\n  R : IDispatch;\nS1 := 'x';\nI1 := R.Corner.X;")
        self.assertEqual(len(problems), 1)
        self.assertIn("line 4", problems[0])
        self.assertIn("Corner", problems[0])

    def test_record_reads_through_typed_variables_allowed(self):
        # Read live on Altium 26.10 from a schematic rectangle and a PCB
        # component; the production units read them the same way
        script = ("var\n  Prim : ISch_GraphicalObject;\n  Comp : IPCB_Component;\n  R : TCoordRect;\n"
                  "I1 := Prim.Corner.X;\nR := Comp.BoundingRectangle;\nI2 := Comp.BoundingRectangle.Left;")
        self.assertEqual(self.lint(script), [])
        self.assertEqual(len(self.lint("I1 := Obj3.BoundingRectangle.Left;")), 1)

    def test_case_insensitive(self):
        self.assertEqual(len(self.lint("S1 := Obj1.getstate_descriptionstring;")), 1)
        self.assertEqual(len(self.lint("list1.free;")), 1)
        self.assertEqual(len(self.lint("Obj4 := TStringList.Create;")), 1)

    def test_strings_and_comments_ignored(self):
        script = "S1 := 'List1.Free';\n// Obj1.Tag := 1\n{ SCHM_PrimitiveRegistration }\nS2 := 'x.Tag';"
        self.assertEqual(self.lint(script), [])

    def test_robot_manager_registration_allowed(self):
        # The production units and the run_altium_script docstring use it to
        # report a new object; registering a placed net label works
        script = ("SchServer.ProcessControl.PreProcess(Obj1, '');\n"
                  "SchServer.RobotManager.SendMessage(Obj1.I_ObjectAddress, c_BroadCast, "
                  "SCHM_PrimitiveRegistration, Obj2.I_ObjectAddress);\n"
                  "SchServer.ProcessControl.PostProcess(Obj1, '');")
        self.assertEqual(self.lint(script), [])

    def test_unbalanced_preprocess(self):
        problems = self.lint("SchServer.ProcessControl.PreProcess(Obj1, '');\nObj1.GraphicallyInvalidate;")
        self.assertEqual(len(problems), 1)
        self.assertIn("PostProcess", problems[0])
        # PCBServer's take no parameters and are written without parentheses
        self.assertEqual(len(self.lint("PCBServer.PreProcess;\nI1 := 1;")), 1)
        self.assertEqual(self.lint("PCBServer.PreProcess;\nI1 := 1;\nPCBServer.PostProcess;"), [])

    def test_template_creates_and_frees_lists(self):
        code = sandbox.strip_strings_and_comments(TEMPLATE)
        run = code[code.index("procedure Run;"):]
        body = re.search(r"\btry\b", run).start()      # the script body runs inside this try
        for name in ("List1", "List2", "List3"):
            created = re.search(name + r"\s*:=\s*TStringList\.Create", run)
            self.assertIsNotNone(created, name)
            self.assertLess(created.start(), body, name)
            self.assertRegex(run, name + r"\.Free;")


class BuildTest(unittest.TestCase):
    def test_paths_and_markers_filled(self):
        b = build("ResultText := 'ok';")
        self.assertIn(r"LogPath := 'C:\x\log.txt';", b.text)
        self.assertIn(r"OutPath := 'C:\x\out.txt';", b.text)
        self.assertNotIn("%LOG_PATH%", b.text)
        self.assertNotIn("no script loaded", b.text)
        lines = b.text.splitlines()
        self.assertEqual(lines[b.body_first_line - 1], "        ResultText := 'ok';")
        self.assertIn("// === END EXPERIMENT", lines[b.body_first_line])

    def test_user_vars_inserted_in_run(self):
        b = build("var\n  Doc : IDispatch;\nDoc := nil;")
        lines = b.text.splitlines()
        self.assertEqual(lines[b.vars_first_line - 1], "    Doc : IDispatch;")
        run = b.text.index("procedure Run;")
        self.assertGreater(b.text.index("    Doc : IDispatch;"), run)
        self.assertLess(b.text.index("    Doc : IDispatch;"), b.text.index("begin", run))


class LoopGuardTest(unittest.TestCase):
    def test_while_and_repeat(self):
        code = "while I1 < 10 do\n  I1 := I1 + 1;\nrepeat\n  I2 := I2 + 1;\nuntil I2 > 5;"
        self.assertEqual(sandbox.guard_loops(code, 1),
                         "while SandboxTick(1) and (I1 < 10) do\n  I1 := I1 + 1;\nrepeat\n"
                         "  I2 := I2 + 1;\nuntil (not SandboxTick(5)) or (I2 > 5);")

    def test_line_numbers_follow_first_line_and_lines_are_kept(self):
        code = "S1 := 'a';\n\nwhile (A <> nil)\n  and (B = nil) do\n  A := nil;"
        out = sandbox.guard_loops(code, 4)
        self.assertIn("while SandboxTick(6) and ((A <> nil)\n  and (B = nil)) do", out)
        self.assertEqual(out.count("\n"), code.count("\n"))

    def test_nested_loops(self):
        # The script that froze Altium: an outer retry loop around an iterator walk
        code = ("while K = 1 do\nbegin\n  C := It.FirstSchObject;\n"
                "  while (C <> nil) and (Hit = nil) do\n    C := It.NextSchObject;\nend;")
        out = sandbox.guard_loops(code, 7)
        self.assertIn("while SandboxTick(7) and (K = 1) do", out)
        self.assertIn("while SandboxTick(10) and ((C <> nil) and (Hit = nil)) do", out)

    def test_until_ends_at_end_or_else_or_text_end(self):
        self.assertEqual(sandbox.guard_loops("repeat I1 := I1 + 1 until I1 = 3\nend", 1),
                         "repeat I1 := I1 + 1 until (not SandboxTick(1)) or (I1 = 3)\nend")
        self.assertEqual(sandbox.guard_loops("repeat I1 := 1 until F(I1, 2) > 0", 1),
                         "repeat I1 := 1 until (not SandboxTick(1)) or (F(I1, 2) > 0)")

    def test_strings_comments_properties_and_for_untouched(self):
        code = ("S1 := 'while x do';\n// while y do\n{ repeat until z; }\n"
                "Obj1.While := 1;\nfor I1 := 0 to 9 do S2 := '';")
        self.assertEqual(sandbox.guard_loops(code, 1), code)

    def test_keywords_case_insensitive(self):
        self.assertEqual(sandbox.guard_loops("WHILE B1 = 0 DO B1 := 1;", 1),
                         "WHILE SandboxTick(1) and (B1 = 0) DO B1 := 1;")

    def test_build_guards_body_and_fills_deadline(self):
        b = sandbox.build_script(TEMPLATE, "var\n  K : Integer;\nwhile K = 1 do K := 0;",
                                 r"C:\x\log.txt", r"C:\x\out.txt", guard_seconds=42)
        self.assertNotIn(sandbox.GUARD_TOKEN, b.text)
        self.assertIn("SbxGuardUntil := Now + 42 / 86400;", b.text)
        lines = b.text.splitlines()
        # Line 3 of the submitted script, at its usual place in the run file
        self.assertEqual(lines[b.body_first_line - 1], "        while SandboxTick(3) and (K = 1) do K := 0;")

    def test_template_defines_guard(self):
        self.assertIn("function SandboxTick(Line : Integer) : Boolean;", TEMPLATE)
        self.assertIn("'" + sandbox.GUARD_PREFIX, TEMPLATE)


class UndoGroupTest(unittest.TestCase):
    def test_pre_and_post_process_routed_through_template(self):
        code = ("SchServer.ProcessControl.PreProcess(Doc, '');\n"
                "PCBServer.PreProcess;\n"
                "PcbServer . PostProcess;\n"
                "schserver.processcontrol.postprocess(Doc, '');")
        self.assertEqual(sandbox.track_undo(code),
                         "SbxPreProcess(Doc, '');\nSbxPcbPreProcess;\nSbxPcbPostProcess;\nSbxPostProcess(Doc, '');")

    def test_strings_comments_and_other_names_untouched(self):
        code = ("S1 := 'SchServer.ProcessControl.PreProcess';\n// PCBServer.PostProcess\n"
                "MySchServer.ProcessControl.PreProcess(D);\nObj1.PCBServer.PreProcess;")
        self.assertEqual(sandbox.track_undo(code), code)

    def test_build_tracks_body_and_fills_undo_path(self):
        b = sandbox.build_script(TEMPLATE, "SchServer.ProcessControl.PreProcess(Obj1, '');",
                                 r"C:\x\log.txt", r"C:\x\out.txt", undo_path=r"C:\x\undo.txt")
        self.assertNotIn(sandbox.UNDO_TOKEN, b.text)
        self.assertIn(r"SbxUndoPath := 'C:\x\undo.txt';", b.text)
        self.assertEqual(b.text.splitlines()[b.body_first_line - 1], "        SbxPreProcess(Obj1, '');")

    def test_template_closes_groups_after_the_except_block(self):
        for name in ("procedure SbxPreProcess(", "procedure SbxPostProcess(", "procedure SbxPcbPreProcess;",
                     "procedure SbxPcbPostProcess;", "procedure SbxCloseUndo;"):
            self.assertIn(name, TEMPLATE)
        self.assertIn("'" + sandbox.UNDO_PREFIX, TEMPLATE)
        # after the except block, so a script that failed still has its groups closed
        run = TEMPLATE[TEMPLATE.index("procedure Run;"):]
        self.assertLess(run.index("ExceptionMessage"), run.index("SbxCloseUndo;"))
        self.assertLess(run.index("SbxCloseUndo;"), run.index("SandboxLog('sandbox end');"))

    def test_close_leftover_undo_builds_cleanup_from_list(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            sb = sandbox.Sandbox("x2.exe", Path("t.pas"), Path(d))
            files = sb.new_run_files()
            files.undo.write_text("SCH|C:\\a\\It's.SchDoc\nPCB|\n", encoding="utf-8")
            ran = []
            sb._run_until_done = lambda f, script, timeout: ran.append(script) or True
            self.assertEqual(sb.close_leftover_undo(files), ["SCH|C:\\a\\It's.SchDoc", "PCB|"])
            # newest first, the quote doubled
            self.assertEqual(ran[0].splitlines()[:2],
                             ["PCBServer.PostProcess;", "Obj1 := SchServer.GetSchDocumentByPath('C:\\a\\It''s.SchDoc');"])
            files.undo.write_text("", encoding="utf-8")
            self.assertEqual(sb.close_leftover_undo(files), [])


class MapLineTest(unittest.TestCase):
    def test_body_line(self):
        b = build("S1 := 'a';\nS2 := Undeclared;")
        msg = f"Undeclared identifier: Undeclared at Line:{b.body_first_line + 1}, Char:15. Continue execution?"
        self.assertEqual(sandbox.map_error_line(b, msg), {"where": "script", "line": 2, "char": 7})

    def test_body_line_after_var_block(self):
        b = build("var\n  X : Integer;\nX := 1;\nS1 := Bad;")
        msg = f"at Line:{b.body_first_line + 1}, Char:9."
        self.assertEqual(sandbox.map_error_line(b, msg)["line"], 4)

    def test_var_block_line(self):
        b = build("var\n  X : Integerr;\nX := 1;")
        msg = f"Undeclared identifier: Integerr at Line:{b.vars_first_line}, Char:9."
        self.assertEqual(sandbox.map_error_line(b, msg), {"where": "var block", "line": 2, "char": 5})

    def test_template_line_and_no_line(self):
        b = build("S1 := 'a';")
        self.assertEqual(sandbox.map_error_line(b, "at Line:3, Char:1")["where"], "sandbox template")
        self.assertIsNone(sandbox.map_error_line(b, "Access violation"))


class RunFilesTest(unittest.TestCase):
    def test_names_unique_across_sandbox_instances(self):
        # main.py builds a new Sandbox for every call; names must still differ
        # within the same second
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            a = sandbox.Sandbox("x2.exe", Path("t.pas"), Path(d)).new_run_files()
            b = sandbox.Sandbox("x2.exe", Path("t.pas"), Path(d)).new_run_files()
            self.assertNotEqual(a.script, b.script)

    def test_read_when_ready_retries_locked_file(self):
        calls = []

        def reader(path):
            calls.append(path)
            if len(calls) < 3:
                raise PermissionError("still open in Altium")
            return "ok"

        self.assertEqual(sandbox.read_when_ready(Path("r.txt"), reader, timeout=2), "ok")
        self.assertEqual(len(calls), 3)

    def test_read_when_ready_gives_up(self):
        def reader(path):
            raise PermissionError("locked")

        with self.assertRaises(PermissionError):
            sandbox.read_when_ready(Path("r.txt"), reader, timeout=0.3)


class DialogTest(unittest.TestCase):
    def test_script_error_detection(self):
        err = sandbox.Dialog(1, "#32770", "X2", "Undeclared identifier: X at Line:5, Char:20. Continue execution?")
        self.assertTrue(err.is_script_error)
        self.assertFalse(sandbox.Dialog(2, "#32770", "X2", "Save changes?").is_script_error)
        self.assertFalse(sandbox.Dialog(3, "TMessageForm", "Error", "Continue execution?").is_script_error)

    def test_error_boxes_only(self):
        # Recovery answers script errors and OK-only Error/Information boxes,
        # never a Confirm or Warning box or another #32770 dialog (the user's)
        self.assertTrue(sandbox.Dialog(1, "#32770", "X2", "Bad at Line:1, Char:2. Continue execution?").is_error_box)
        self.assertTrue(sandbox.Dialog(2, "TMessageForm", "Error").is_error_box)
        self.assertTrue(sandbox.Dialog(3, "TMessageForm", "Information").is_error_box)
        self.assertFalse(sandbox.Dialog(4, "TMessageForm", "Confirm").is_error_box)
        self.assertFalse(sandbox.Dialog(5, "TMessageForm", "Warning").is_error_box)
        self.assertFalse(sandbox.Dialog(6, "#32770", "Save As", "File name:").is_error_box)


if __name__ == "__main__":
    unittest.main()
