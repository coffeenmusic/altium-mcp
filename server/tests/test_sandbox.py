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

    def test_template_declares_scratch_names(self):
        code = sandbox.strip_strings_and_comments(TEMPLATE)
        declared = set()
        for m in re.finditer(r"^\s*([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*:\s*[A-Za-z_]\w*\s*;", code, re.M):
            declared |= {n.strip().lower() for n in m.group(1).split(",")}
        missing = [n for n in sandbox.TEMPLATE_NAMES if n.lower() not in declared]
        self.assertEqual(missing, [])


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


class DialogTest(unittest.TestCase):
    def test_script_error_detection(self):
        err = sandbox.Dialog(1, "#32770", "X2", "Undeclared identifier: X at Line:5, Char:20. Continue execution?")
        self.assertTrue(err.is_script_error)
        self.assertFalse(sandbox.Dialog(2, "#32770", "X2", "Save changes?").is_script_error)
        self.assertFalse(sandbox.Dialog(3, "TMessageForm", "Error", "Continue execution?").is_script_error)


if __name__ == "__main__":
    unittest.main()
