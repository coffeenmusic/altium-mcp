"""Unit tests for bridge_bundle (no Altium needed).

Run from server/:  python -m unittest tests.test_bridge_bundle -v
"""
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import bridge_bundle  # noqa: E402

PROJECT = Path(__file__).resolve().parent.parent / "AltiumScript" / "Altium_API.PrjScr"

UNIT_A = """// unit a
const
    LIMIT = 3;

// Uses Helper from unit b
function Caller(X : Integer) : Integer;
var
    L : Integer;
begin
    L := Helper(X, LIMIT);
    Result := L;
end;
"""

UNIT_B = """var
    Count : Integer;

{ A header over two lines, with locals at column 0 }
function Helper(X : Integer;
                Y : Integer) : Integer;
var
    Local : Integer;
begin
    Local := X + Y; // not Caller(...)
    Result := Local;
end;
"""


def make_project(folder: Path, units: dict) -> Path:
    for name, text in units.items():
        (folder / name).write_text(text, encoding="utf-8")
    prj = folder / "Test.PrjScr"
    prj.write_text("\n".join(f"[Document{i}]\nDocumentPath={name}" for i, name in enumerate(units, 1)),
                   encoding="utf-8-sig")
    return prj


class SplitTest(unittest.TestCase):
    def test_globals_hoisted_locals_kept(self):
        decls, rest = bridge_bundle.split_unit(UNIT_B)
        self.assertEqual(decls, ["var\n    Count : Integer;"])
        routines = bridge_bundle.split_routines(rest)
        self.assertEqual([n for n, _ in routines], ["Helper"])
        self.assertIn("var\n    Local : Integer;", routines[0][1])
        self.assertTrue(routines[0][1].startswith("{ A header over two lines"))

    def test_code_outside_a_routine_is_refused(self):
        with self.assertRaises(ValueError):
            bridge_bundle.split_routines(["x := 1;"])


class OrderTest(unittest.TestCase):
    def test_callee_first_and_source_order_otherwise(self):
        routines = [("Caller", "function Caller;\nbegin\n  Helper;\nend;"),
                    ("Other", "procedure Other;\nbegin\nend;"),
                    ("Helper", "procedure Helper;\nbegin\n  S := 'Caller';\nend;")]
        self.assertEqual(len(bridge_bundle.forward_calls(routines)), 1)
        ordered = bridge_bundle.order_routines(routines)
        self.assertEqual([n for n, _ in ordered], ["Helper", "Caller", "Other"])
        self.assertEqual(bridge_bundle.forward_calls(ordered), [])

    def test_build_puts_declarations_first(self):
        with tempfile.TemporaryDirectory() as d:
            text = bridge_bundle.build(make_project(Path(d), {"a.pas": UNIT_A, "b.pas": UNIT_B}))
        first_routine = re.search(r"^(function|procedure) ", text, re.M).start()
        self.assertLess(text.index("LIMIT = 3;"), first_routine)
        self.assertLess(text.index("Count : Integer;"), first_routine)
        self.assertLess(text.index("function Helper"), text.index("function Caller"))


class BridgeProjectTest(unittest.TestCase):
    """The real bridge script, as Altium would get it."""

    def setUp(self):
        self.routines, self.decls = [], []
        for unit in bridge_bundle.project_units(PROJECT):
            decls, rest = bridge_bundle.split_unit(unit.read_text(encoding="utf-8-sig"))
            self.decls += decls
            self.routines += bridge_bundle.split_routines(rest)

    def test_every_routine_once_and_none_called_before_defined(self):
        ordered = bridge_bundle.order_routines(self.routines)
        self.assertEqual(sorted(n.lower() for n, _ in ordered), sorted(n.lower() for n, _ in self.routines))
        self.assertEqual(len({n.lower() for n, _ in ordered}), len(ordered))
        self.assertEqual(bridge_bundle.forward_calls(ordered), [])

    def test_globals_before_routines_and_no_local_hoisted(self):
        text = bridge_bundle.build(PROJECT)
        first_routine = re.search(r"^(function|procedure) ", text, re.M).start()
        for block in self.decls:
            self.assertLess(text.index(block), first_routine)
        # Run's helpers rely on these globals; a local block moved to the
        # top would instead shadow nothing and break its routine
        self.assertIn("REPLACEALL", "\n".join(self.decls))
        self.assertIn("RequestData", "\n".join(self.decls))
        self.assertNotIn("ParamValue", "\n".join(self.decls))


class BundleFileTest(unittest.TestCase):
    def test_named_after_content_written_once_and_pruned(self):
        with tempfile.TemporaryDirectory() as d:
            src, out = Path(d) / "src", Path(d) / "out"
            src.mkdir()
            prj = make_project(src, {"a.pas": UNIT_A, "b.pas": UNIT_B})
            first = bridge_bundle.bundle_file(prj, out, keep=2)
            self.assertEqual(bridge_bundle.bundle_file(prj, out, keep=2), first)
            self.assertTrue(first.read_bytes().startswith(b"\xef\xbb\xbf"))
            paths = [first]
            for n in range(3):
                time.sleep(0.02)
                (src / "a.pas").write_text(UNIT_A.replace("LIMIT = 3", f"LIMIT = {n + 4}"), encoding="utf-8")
                paths.append(bridge_bundle.bundle_file(prj, out, keep=2))
            self.assertEqual(len(set(paths)), 4)
            self.assertEqual(sorted(out.glob("Test_*.pas")), sorted(paths[-2:]))


if __name__ == "__main__":
    unittest.main()
