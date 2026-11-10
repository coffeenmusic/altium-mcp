"""Unit tests for sandbox (pure Python, no Altium needed).

Run from the repo root:  python -m unittest server.tests.test_sandbox -v
or from server/:          python -m unittest tests.test_sandbox -v
"""
import hashlib
import os
import re
import sys
import time
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

    def test_comments_before_and_inside_the_var_block(self):
        script = ("// read the ports\n{ second note }\nvar\n    X : Integer;\n"
                  "    // the document\n    Doc : IDispatch;\nX := 1;")
        sp = sandbox.split_var_block(script)
        self.assertEqual(sp.decls, ["X : Integer;", "Doc : IDispatch;"])
        self.assertEqual(sp.decl_src, [4, 6])
        self.assertEqual(sp.body, ["X := 1;"])
        self.assertEqual(sp.body_offset, 6)
        # and the declared name is no longer refused
        b = build("// note\nvar\n    X : Integer;\nX := 1;\nResultText := IntToStr(X);")
        self.assertIn("    X : Integer;", b.text)

    def test_comment_without_var_block_stays_in_the_body(self):
        sp = sandbox.split_var_block("// just a note\nI1 := 1;")
        self.assertEqual(sp.decls, [])
        self.assertEqual(sp.body, ["// just a note", "I1 := 1;"])
        self.assertEqual(sp.body_offset, 0)


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

    def test_record_reads_through_routine_parameters_and_locals(self):
        script = ("function Left(Prim : IDispatch; N : Integer) : Integer;\n"   # line 1
                  "var\n    A, B : IDispatch;\n    C : IPCB_Component;\n"
                  "begin\n"
                  "    Result := Prim.BoundingRectangle.Left;\n"                # line 6
                  "    I1 := B.Corner.X;\n"                                     # line 7
                  "    I2 := C.BoundingRectangle.Left;\n"
                  "end;\n"
                  "function Get : IDispatch;\n"                                # line 10
                  "begin\n    Result := nil;\n    I3 := Result.Corner.X;\nend;\n"  # line 13
                  "procedure Other(X : IPCB_Component);\n"
                  "begin\n    I4 := X.BoundingRectangle.Left;\n    I5 := Prim.Corner.X;\nend;\n"
                  "I6 := Left(nil, 1);\nI7 := A.Corner.X;")
        sp = sandbox.split_var_block(script)
        problems = sandbox.lint_script(sp.routines + sp.body, sp.routines_offset, sp.decls,
                                       sandbox.routines(sp))
        self.assertEqual([p.split(":")[0] for p in problems], ["line 6", "line 7", "line 13"])
        self.assertIn("(Prim)", problems[0])
        self.assertIn("(B)", problems[1])
        # A routine's own names do not reach other routines or the statements
        # (Prim in Other and A in the statements are some other, undeclared name)
        self.assertEqual(sandbox.routines(sp)[0].untyped, ["Prim", "A", "B"])
        self.assertEqual(sandbox.routines(sp)[1].untyped, ["Result"])
        self.assertEqual(sandbox.routines(sp)[2].untyped, [])

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

    def test_user_vars_are_globals(self):
        # Global, so the script's own functions and procedures can use them
        b = build("var\n  Doc : IDispatch;\nDoc := nil;")
        lines = b.text.splitlines()
        self.assertEqual(lines[b.vars_first_line - 1], "    Doc : IDispatch;")
        self.assertLess(b.text.index("    Doc : IDispatch;"), b.text.index("procedure SandboxLog"))

    def test_body_is_its_own_procedure(self):
        # An Exit in the script ends SbxBody; Run still writes the result
        b = build("Exit;")
        body = b.text.index("procedure SbxBody;")
        run = b.text.index("procedure Run;")
        # (the template's own routines use Exit too, further indented)
        self.assertLess(body, b.text.index("\n        Exit;"))
        self.assertLess(b.text.index("\n        Exit;"), run)
        tail = b.text[run:]
        self.assertLess(tail.index("SbxBody;"), tail.index("OutLines.SaveToFile(OutPath);"))

class RoutineTest(unittest.TestCase):
    SCRIPT = ("var\n    N : Integer;\n"
              "function Twice(X : Integer) : Integer;\nvar\n    Y : Integer;\nbegin\n"
              "    Y := X * 2;\n    if Y > 0 then\n    begin\n        Result := Y;\n    end;\nend;\n"
              "\n"
              "procedure Note(S : String);\nbegin\n    try\n        SandboxLog(S);\n    finally\n"
              "        N := N + 1;\n    end;\nend;\n"
              "N := Twice(2);\nNote('x');\nResultText := IntToStr(N);")

    def test_split(self):
        sp = sandbox.split_var_block(self.SCRIPT)
        self.assertEqual(sp.decls, ["N : Integer;"])
        self.assertEqual(sp.routines[0], "function Twice(X : Integer) : Integer;")
        self.assertEqual(sp.routines[-1], "end;")
        self.assertEqual(sp.routines_offset, 2)
        self.assertEqual(sp.body[0], "N := Twice(2);")
        self.assertEqual(sp.body_offset, 21)
        rs = sandbox.routines(sp)
        self.assertEqual([r.name for r in rs], ["Twice", "Note"])
        self.assertEqual(rs[0].names, ["X", "Y"])
        self.assertEqual([r.first_line for r in rs], [3, 14])

    def test_routines_without_var_block_and_none_at_all(self):
        sp = sandbox.split_var_block("procedure P;\nbegin\n    S1 := 'end';\nend;\nP;")
        self.assertEqual(len(sp.routines), 4)
        self.assertEqual(sp.body, ["P;"])
        sp = sandbox.split_var_block("var\n  X : Integer;\n\nX := 1;")
        self.assertEqual(sp.routines, [])
        self.assertEqual(sp.body, ["X := 1;"])
        self.assertEqual(sp.body_offset, 3)

    def test_checks_know_params_locals_and_result(self):
        sp = sandbox.split_var_block(self.SCRIPT)
        self.assertEqual(sandbox.check_routines(sp), [])
        self.assertEqual(sandbox.check_declarations(sp), [])
        sp = sandbox.split_var_block("function F(A : Integer) : Integer;\nbegin\n    Z := A;\nend;\nI1 := F(1);")
        problems = sandbox.check_routines(sp)
        self.assertEqual(len(problems), 1)
        self.assertIn("line 3", problems[0])
        self.assertIn("'Z'", problems[0])

    def test_built_in_order_with_lines_mapped(self):
        b = build(self.SCRIPT)
        t = b.text
        self.assertLess(t.index("    N : Integer;"), t.index("procedure SandboxLog"))
        self.assertLess(t.index("procedure SaveDoc"), t.index("function Twice"))
        self.assertLess(t.index("function Twice"), t.index("procedure SbxBody"))
        self.assertLess(t.index("procedure SbxBody"), t.index("N := Twice(2);"))
        self.assertLess(t.index("N := Twice(2);"), t.index("procedure Run;"))
        lines = t.splitlines()
        self.assertEqual(lines[b.routines_first_line - 1], "function Twice(X : Integer) : Integer;")
        msg = f"Undeclared identifier at Line:{b.routines_first_line + 4}, Char:5"
        self.assertEqual(sandbox.map_error_line(b, msg), {"where": "script", "line": 7, "char": 5})
        msg = f"x at Line:{b.body_first_line + 1}, Char:9"
        self.assertEqual(sandbox.map_error_line(b, msg), {"where": "script", "line": 23, "char": 1})

    def test_loops_in_routines_guarded(self):
        b = build("procedure P;\nbegin\n    while I1 < 3 do I1 := I1 + 1;\nend;\nP;")
        self.assertIn("while SandboxTick(3) and (I1 < 3) do", b.text)


class DeclarationTest(unittest.TestCase):
    def test_reserved_words(self):
        sp = sandbox.split_var_block("var\n    Label, X : Integer;\n    Object : IDispatch;\nX := 1;")
        problems = sandbox.check_declarations(sp)
        self.assertEqual(len(problems), 2)
        self.assertIn("line 2", problems[0])
        self.assertIn("'Label' is a reserved word", problems[0])
        self.assertIn("line 3", problems[1])
        sp = sandbox.split_var_block("procedure P(Unit : Integer);\nbegin\nend;\nP(1);")
        self.assertIn("'Unit' is a reserved word", sandbox.check_declarations(sp)[0])

    def test_directives_that_broke_builds(self):
        for word in ("Stored", "Index", "Default", "Read", "Write", "Message", "At", "On", "Absolute"):
            sp = sandbox.split_var_block(f"var\n    {word} : Integer;\nS1 := 'x';")
            problems = sandbox.check_declarations(sp)
            self.assertEqual(len(problems), 1, word)
            self.assertIn(f"'{word}' is a reserved word", problems[0])
        sp = sandbox.split_var_block("function F(Index : Integer) : Integer;\nvar\n    Read : Integer;\n"
                                     "begin\n    Result := 1;\nend;\nI1 := F(1);")
        self.assertEqual(len(sandbox.check_declarations(sp)), 2)

    def test_skip_lint_skips_reserved_words_only(self):
        sp = sandbox.split_var_block("var\n    Label, S1 : Integer;\nprocedure Read(Unit : Integer);\n"
                                     "begin\nend;\nRead(1);")
        self.assertEqual(len(sandbox.check_declarations(sp)), 4)
        # A sandbox name declared again never compiles, so it stays refused
        problems = sandbox.check_declarations(sp, reserved_words=False)
        self.assertEqual(len(problems), 1)
        self.assertIn("'S1' is already declared", problems[0])

    def test_names_the_template_declares(self):
        sp = sandbox.split_var_block("var\n    S1 : Integer;\nfunction MM(V : Double) : Integer;\nbegin\nend;\nI1 := 1;")
        problems = sandbox.check_declarations(sp)
        self.assertEqual(len(problems), 2)
        self.assertIn("'S1' is already declared", problems[0])
        self.assertIn("'MM' is already a sandbox name", problems[1])

    def test_template_globals_listed(self):
        code = sandbox.strip_strings_and_comments(TEMPLATE)
        routines = re.findall(r"^\s*(?:procedure|function)\s+([A-Za-z_]\w*)", code, re.M | re.I)
        self.assertEqual([n for n in routines if n not in sandbox.TEMPLATE_GLOBALS], [])
        consts = re.findall(r"^\s*([A-Za-z_]\w*)\s*=", code, re.M)
        self.assertEqual([n for n in consts if n not in sandbox.TEMPLATE_GLOBALS], [])
        globals_block = code[:code.index("procedure SandboxLog")]
        for m in re.finditer(r"^\s*([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*:\s*[A-Za-z_]\w*\s*;", globals_block, re.M):
            for n in m.group(1).split(","):
                self.assertIn(n.strip(), sandbox.TEMPLATE_NAMES + sandbox.TEMPLATE_GLOBALS)


class PrecedenceTest(unittest.TestCase):
    def problems(self, code):
        return sandbox.precedence_problems(sandbox.strip_strings_and_comments(code))

    def test_comparison_next_to_and_or(self):
        p = self.problems("I1 := 1;\nif I1 = 1 and I2 = 2 then S1 := 'a';")
        self.assertEqual(len(p), 1)
        self.assertIn("line 2", p[0])
        self.assertEqual(len(self.problems("while (I1 < 3) and\n  I2 > 0 do I1 := I1 + 1;")), 1)
        self.assertEqual(len(self.problems("B1 := X = 1 or Y;")), 1)
        self.assertEqual(len(self.problems("if F(A = 1 or B) then ;")), 1)
        # Within one argument the comma does not hide it
        self.assertEqual(len(self.problems("I1 := Max(I2, A = 1 or B);")), 1)
        self.assertEqual(len(self.problems("I1 := Max(A = 1 or B, I2);")), 1)

    def test_parenthesized_and_plain_pass(self):
        for ok in ("if (I1 = 1) and (I2 = 2) then S1 := 'a';",
                   "if A and B then S1 := 'x' else if C = 2 then S1 := 'y';",
                   "S1 := 'a = 1 and b';\nI1 := I2 and 3;\nif I1 = 1 then I2 := 3 and I1;",
                   "repeat I1 := I1 + 1 until (I1 = 3) or (I2 > 4);",
                   "Ok := Pos('x', S1) > 0;\nX := A[I1 = 1];",
                   "function F(A : Integer = 4; B : Boolean = False) : Integer;",
                   # Commas separate arguments: a comparison in one, or in another
                   "I1 := Max(A = 1, B or C);",
                   "S1 := Copy(S2, I1 = 1, I2 and 3);"):
            self.assertEqual(self.problems(ok), [], ok)

    def test_not_before_comparison(self):
        p = self.problems("if not I1 = 1 then S1 := 'a';")
        self.assertEqual(len(p), 1)
        self.assertIn("not I1 =", p[0])
        self.assertEqual(len(self.problems("if not Obj1.Selection(1) <> 0 then ;")), 1)
        self.assertEqual(self.problems("if not (I1 = 1) then ;\nif not B1 then ;\nif I1 <> 1 then ;"), [])

    def test_reported_by_lint(self):
        sp = sandbox.split_var_block("var\n  X : Integer;\nif X = 1 and X = 2 then X := 3;")
        p = sandbox.lint_script(sp.body, sp.body_offset, sp.decls)
        self.assertEqual(len(p), 1)
        self.assertIn("line 3", p[0])


class SnapshotTest(unittest.TestCase):
    def test_doc_paths_from_string_literals(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            a = Path(d) / "A.SchDoc"
            b = Path(d) / "it's.PcbDoc"
            a.write_text("x")
            b.write_text("y")
            q = str(b).replace("'", "''")
            script = (f"Obj1 := OpenSchDoc('{a}');\nSaveDoc('{str(a).upper()}');\n"
                      f"S1 := '{q}';\nS2 := '{d}\\missing.SchDoc';\nS3 := 'Rel.SchDoc';")
            self.assertEqual(sandbox.doc_paths(script), [str(a), str(b)])

    def test_take_snapshot_and_doc_states(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            a = Path(d) / "A.SchDoc"
            a.write_bytes(b"abc")
            entries = sandbox.take_snapshot(Path(d) / "snap", [str(a)])
            self.assertEqual(entries, [{"path": str(a), "copy": "0_A.SchDoc", "bytes": 3,
                                        "sha256": hashlib.sha256(b"abc").hexdigest()}])
            self.assertEqual((Path(d) / "snap" / "0_A.SchDoc").read_bytes(), b"abc")
            self.assertEqual(json.loads((Path(d) / "snap" / "manifest.json").read_text()), entries)
        states = sandbox.doc_states(["sandbox start", "DOC STATE: unsaved|C:\\X\\A.SchDoc",
                                     "DOC STATE: closed|C:\\X\\B.PcbDoc", "DOC AFTER: open|C:\\X\\B.PcbDoc"])
        self.assertEqual(states, {"c:\\x\\a.schdoc": "unsaved", "c:\\x\\b.pcbdoc": "closed"})
        after = sandbox.doc_states(["DOC AFTER: open|C:\\X\\B.PcbDoc"], sandbox.DOC_AFTER_PREFIX)
        self.assertEqual(after, {"c:\\x\\b.pcbdoc": "open"})

    def test_rollback_refused_after_a_later_save(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "A.SchDoc", Path(d) / "B.PcbDoc"
            a.write_bytes(b"before")
            b.write_bytes(b"pcb")
            snap = Path(d) / "snap"
            sandbox.take_snapshot(snap, [str(a), str(b)])
            # The end of the run was never recorded: nothing is safe to restore
            self.assertEqual(sandbox.changed_since_run(sandbox.read_manifest(snap)), [str(a), str(b)])
            a.write_bytes(b"script saved this")
            entries = sandbox.record_run_end(snap, [f"DOC STATE: open|{a}", f"DOC AFTER: unsaved|{a}"])
            self.assertEqual(entries[0]["state_before"], "open")
            self.assertEqual(entries[0]["state_after"], "unsaved")
            self.assertIsNone(entries[1]["state_before"])
            self.assertEqual(sandbox.read_manifest(snap), entries)
            self.assertEqual(sandbox.changed_since_run(entries), [])
            # The user saves later work: rolling back would lose it
            b.write_bytes(b"user saved this")
            self.assertEqual(sandbox.changed_since_run(entries), [str(b)])
            b.unlink()
            self.assertEqual(sandbox.changed_since_run(entries), [str(b)])
        self.assertIsNone(sandbox.read_manifest(Path(d) / "gone"))

    def test_unique_paths_normalised(self):
        norm = lambda p: p.replace("/", "\\")
        self.assertEqual(sandbox.unique_paths(["C:/p/A.SchDoc", "c:\\P\\a.schdoc", "C:\\p\\B.PcbDoc"], norm),
                         ["C:\\p\\A.SchDoc", "C:\\p\\B.PcbDoc"])

    def test_nothing_snapshotted_note(self):
        self.assertIn("GetCurrentPCBBoard", sandbox.nothing_snapshotted_note(False))
        self.assertIn("snapshot=[] was passed", sandbox.nothing_snapshotted_note(True))

    def test_doc_states_logged_before_and_after_the_script(self):
        b = sandbox.build_script(TEMPLATE, "S1 := 'x';", "l", "o", snapshot_docs=["C:\\it's\\A.SchDoc"])
        run = b.text[b.text.index("procedure Run;"):]
        self.assertIn("SbxNoteDoc('C:\\it''s\\A.SchDoc');", run)
        self.assertLess(run.index("SbxNoteDoc("), run.index("SbxBody;"))
        self.assertLess(run.index("SbxSnapDocs := TStringList.Create;"), run.index("SbxNoteDoc("))
        self.assertLess(run.index("SbxBody;"), run.index("SbxNoteDocsAfter;"))
        self.assertLess(run.index("SbxNoteDocsAfter;"), run.index("SbxSnapDocs.Free;"))

    def test_norm_path(self):
        self.assertEqual(sandbox.norm_path(" C:/P//Sub\\A.SchDoc "), "c:\\p\\sub\\a.schdoc")
        self.assertEqual(sandbox.norm_path("\\\\Srv\\Share\\\\A.SchDoc"), "\\\\srv\\share\\a.schdoc")

    def test_template_finds_open_documents_by_any_spelling(self):
        # Client.IsDocumentOpen and GetDocumentByPath miss a path spelled
        # differently from Altium's; the template compares normalised paths
        code = sandbox.strip_strings_and_comments(TEMPLATE)
        self.assertNotIn("IsDocumentOpen", code)
        for name in ("SbxDocState", "SbxCloseDoc", "SbxProjectUnsaved"):
            start = code.index(f"function {name}(")
            body = code[start:code.index("\nend;", start)]
            self.assertIn("SbxFindDoc(", body, name)
            self.assertNotIn("GetDocumentByPath", body, name)

    def test_template_reads_modified_through_booltostr(self):
        # Modified read directly in a condition is always False in a script
        code = sandbox.strip_strings_and_comments(TEMPLATE)
        self.assertEqual(re.findall(r"\.Modified\b(?!\s*,\s*True\))", code), [])
        self.assertIn("D.SetModified(False);", code)

    def test_close_state_and_reopen_scripts(self):
        paths = ["C:\\p\\A.SchDoc", "C:\\p\\P.PrjPcb", "C:\\p\\B.PcbDoc"]
        close = sandbox.close_script(paths)
        # Documents before the project, which would close them with it
        self.assertEqual(re.findall(r"SbxCloseDoc\('([^']+)'\)", close),
                         ["C:\\p\\A.SchDoc", "C:\\p\\B.PcbDoc", "C:\\p\\P.PrjPcb"])
        self.assertEqual(sandbox.check_script(close.split("\n"), []), [])
        state = sandbox.state_script(paths)
        self.assertEqual(state.count("SbxDocState("), 3)
        self.assertEqual(state.count("SbxProjectUnsaved("), 1)
        self.assertIn("SbxProjectUnsaved('C:\\p\\P.PrjPcb', '|C:\\P\\A.SCHDOC||C:\\P\\P.PRJPCB||C:\\P\\B.PCBDOC|')",
                      state)
        # The skip list is compared with SbxNormPath's form of each path
        self.assertIn("'|C:\\P\\A.SCHDOC||\\\\SRV\\P\\B.PCBDOC||C:\\P\\P.PRJPCB|'",
                      sandbox.state_script(["C:/p//A.SchDoc", "\\\\srv\\p\\\\B.PcbDoc", "C:\\p\\P.PrjPcb"]))
        self.assertEqual(sandbox.check_script(state.split("\n"), []), [])
        now, children = sandbox.parse_states(
            "unsaved|C:\\p\\A.SchDoc\nopen|C:\\p\\P.PrjPcb\nchildren|C:\\p\\P.PrjPcb|C:\\p\\C.SchDoc*C:\\p\\D.SchDoc*\n"
            "closed|C:\\p\\B.PcbDoc\n")
        self.assertEqual(now, {"c:\\p\\a.schdoc": "unsaved", "c:\\p\\p.prjpcb": "open", "c:\\p\\b.pcbdoc": "closed"})
        self.assertEqual(children, {"c:\\p\\p.prjpcb": ["C:\\p\\C.SchDoc", "C:\\p\\D.SchDoc"]})
        self.assertEqual(sandbox.parse_states("children|C:\\p\\P.PrjPcb|\n")[1], {"c:\\p\\p.prjpcb": []})
        reopen = sandbox.reopen_script(paths)
        self.assertIn("Client.OpenDocument('SCH', 'C:\\p\\A.SchDoc');", reopen)
        self.assertIn("GetWorkspace.DM_OpenProject('C:\\p\\P.PrjPcb', True);", reopen)
        self.assertIn("Client.OpenDocument('PCB', 'C:\\p\\B.PcbDoc');", reopen)

    def test_snapshot_dir_by_id(self):
        sb = sandbox.Sandbox("x", Path("t.pas"), Path("runs"), snapshots_dir=Path("snaps"))
        self.assertEqual(sb.snapshot_dir("Sbx_1"), Path("snaps") / "Sbx_1")
        self.assertIsNone(sb.snapshot_dir("..\\Sbx_1"))
        self.assertIsNone(sb.snapshot_dir(""))
        self.assertIsNone(sandbox.Sandbox("x", Path("t.pas"), Path("runs")).snapshot_dir("Sbx_1"))

    def test_prune_keeps_script_runs_apart_from_helpers(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            runs, snaps = Path(d) / "runs", Path(d) / "snaps"
            sb = sandbox.Sandbox("x", Path(d) / "t.pas", runs, keep_runs=2, snapshots_dir=snaps)
            runs.mkdir()
            t = time.time() - 100
            for n in range(3):
                for tag in ("Sbx", "Helper", "Probe"):
                    p = runs / f"{tag}_{n}.pas"
                    p.write_text("x")
                    os.utime(p, (t + n, t + n))
                (snaps / f"Sbx_{n}").mkdir(parents=True)
                os.utime(snaps / f"Sbx_{n}", (t + n, t + n))
            (runs / "Sbx_0_snapshot").mkdir()        # the old place, readable by every account
            sb.prune()
            self.assertEqual(sorted(p.stem for p in runs.glob("Sbx_*.pas")), ["Sbx_1", "Sbx_2"])
            # Five helper and probe runs after them did not push the script runs out
            self.assertEqual(len([p for p in runs.glob("*.pas") if not p.stem.startswith("Sbx")]), 2)
            self.assertEqual(sorted(p.name for p in snaps.iterdir()), ["Sbx_1", "Sbx_2"])
            self.assertFalse((runs / "Sbx_0_snapshot").exists())


class HelperDialogTest(unittest.TestCase):
    def test_only_new_dialogs_closed_and_listed_once(self):
        import tempfile
        from unittest import mock
        users = sandbox.Dialog(1, "TMessageForm", "Confirm")
        prompt = sandbox.Dialog(2, "TMessageForm", "Confirm")
        error = sandbox.Dialog(3, "TMessageForm", "Error")
        calls = {"n": 0}

        def dialogs():
            calls["n"] += 1
            return [users] if calls["n"] == 1 else [users, prompt, error]

        answered = []
        with tempfile.TemporaryDirectory() as d:
            sb = sandbox.Sandbox("x", Path(d) / "t.pas", Path(d))
            files = sb.new_run_files("Helper")
            with mock.patch.object(sandbox, "altium_dialogs", dialogs), \
                    mock.patch.object(sandbox, "answer_dialog", lambda dlg: answered.append(dlg.hwnd)), \
                    mock.patch.object(sandbox.Sandbox, "write", lambda self, f, script: None), \
                    mock.patch.object(sandbox.Sandbox, "launch", lambda self, f: None), \
                    mock.patch.object(sandbox.time, "sleep", lambda s: None):
                closed = []
                self.assertFalse(sb._run_until_done(files, "x", 0.3, closed))
                self.assertEqual(closed, [{"title": "Confirm", "picture": None}])
                self.assertNotIn(1, answered)
                self.assertIn(2, answered)
                self.assertIn(3, answered)
                # Without the list, only error boxes are answered
                answered.clear()
                calls["n"] = 0
                sb._run_until_done(files, "x", 0.3)
                self.assertEqual(set(answered), {3})


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

    def test_result_is_written_in_a_finally_block(self):
        # Exit in the script ends Run; only a finally block still runs then,
        # so cleanup and the result file must be inside one
        run = TEMPLATE[TEMPLATE.index("procedure Run;"):]
        body = run.index(sandbox.BODY_BEGIN)
        except_at = run.index("\n    except", body)
        finally_at = run.index("finally", except_at)
        for step in ("List1.Free;", "SbxCloseUndo;", "OutLines.SaveToFile(OutPath);"):
            self.assertLess(finally_at, run.index(step), step)
        # the finally belongs to a try opened before the script body
        self.assertRegex(run[:body], r"\btry\s+try\b")

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



class OneLineVarBlockTest(unittest.TestCase):
    def test_several_declarations_on_one_line(self):
        split = sandbox.split_var_block("var D: IServerDocument; P, S: String;\nS := P;\nResultText := S;")
        self.assertEqual(split.decls, ["D: IServerDocument;", "P, S: String;"])
        self.assertEqual(sandbox.declared_names(split.decls), ["D", "P", "S"])
        self.assertEqual(split.body, ["S := P;", "ResultText := S;"])


class RollbackToolTest(unittest.TestCase):
    """main.rollback_altium_script with Altium faked."""

    def setUp(self):
        import asyncio
        import json
        import tempfile
        from unittest import mock
        import main
        self.main, self.json, self.mock, self.asyncio = main, json, mock, asyncio
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.doc = self.dir / "A.SchDoc"
        self.doc.write_bytes(b"before")
        patch = mock.patch.object(main, "SANDBOX_SNAPSHOTS", self.dir / "snaps")
        patch.start()
        self.addCleanup(patch.stop)
        self.sb = main._sandbox()
        sandbox.take_snapshot(self.sb.snapshot_dir("Sbx_1"), [str(self.doc)])
        # The script edits the document and leaves it unsaved
        self.doc.write_bytes(b"script saved this")
        sandbox.record_run_end(self.sb.snapshot_dir("Sbx_1"),
                               [f"DOC STATE: open|{self.doc}", f"DOC AFTER: unsaved|{self.doc}"])

    def rollback(self, running, replies=(), **kw):
        mock, main = self.mock, self.main
        calls = []

        def run_quick(_self, script, timeout=30.0, closed_dialogs=None):
            calls.append(script)
            return replies[len(calls) - 1]

        async def ensure_paths(need_script=True):
            return None
        with mock.patch.object(type(main.altium_bridge), "_altium_running", staticmethod(lambda: running)), \
                mock.patch.object(main.altium_bridge, "ensure_paths", ensure_paths), \
                mock.patch.object(main, "altium_limit_problem", lambda: None), \
                mock.patch.object(sandbox, "altium_hung", lambda: False), \
                mock.patch.object(sandbox, "altium_dialogs", lambda: []), \
                mock.patch.object(sandbox.Sandbox, "run_quick", run_quick), \
                mock.patch.object(sandbox.Sandbox, "prune", lambda _self: None):
            result = self.asyncio.run(main.rollback_altium_script(None, "Sbx_1", **kw))
        return self.json.loads(result), calls

    def test_altium_not_running_restores_without_starting_it(self):
        result, calls = self.rollback(running=False)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["restored"], [str(self.doc)])
        self.assertEqual(calls, [])
        self.assertEqual(self.doc.read_bytes(), b"before")

    def test_altium_not_running_still_checks_the_disk(self):
        self.doc.write_bytes(b"user saved this")
        result, calls = self.rollback(running=False)
        self.assertFalse(result["success"])
        self.assertEqual(result["changed_since_run"], [str(self.doc)])
        self.assertEqual(self.doc.read_bytes(), b"user saved this")

    def test_unsaved_at_rollback_refused_even_if_the_run_left_it_so(self):
        result, calls = self.rollback(running=True, replies=[f"unsaved|{self.doc}\n"])
        self.assertFalse(result["success"])
        self.assertEqual(result["unsaved_now"], [str(self.doc)])
        self.assertEqual(result["left_unsaved_by_run"], [str(self.doc)])
        self.assertEqual(len(calls), 1)          # only asked for the state; nothing closed
        self.assertEqual(self.doc.read_bytes(), b"script saved this")

    def test_unsaved_restored_with_discard_unsaved(self):
        result, calls = self.rollback(running=True, discard_unsaved=True,
                                      replies=[f"unsaved|{self.doc}\n", f"open|{self.doc}\n", "reopened"])
        self.assertTrue(result["success"], result)
        self.assertEqual(result["reopened"], [str(self.doc)])
        self.assertIn("SbxCloseDoc(", calls[1])
        self.assertEqual(self.doc.read_bytes(), b"before")


if __name__ == "__main__":
    unittest.main()
