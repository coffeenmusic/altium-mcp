"""Offline tests for script_bundle (no Altium needed).

Run: python -m unittest discover -s server/tests -p "test_script_bundle.py"
"""

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import script_bundle as sb  # noqa: E402


def defined_before_use(text):
    """Routines used before their definition in a bundle (should be none)."""
    clean = sb._NOISE.sub(lambda m: " " * len(m.group(0)), text)
    defs = {}
    for m in re.finditer(r"^(?:function|procedure)\s+(\w+)", text, re.M | re.I):
        defs.setdefault(m.group(1).lower(), m.start())
    return [n for n, pos in defs.items() if re.search(r"\b" + n + r"\b", clean[:pos], re.I)]


class BundleTests(unittest.TestCase):
    def write(self, folder, name, text):
        p = Path(folder) / name
        p.write_text(text.replace("\n", "\r\n"), encoding="latin-1")
        return p

    def test_callees_come_first_and_globals_lead(self):
        with tempfile.TemporaryDirectory() as d:
            a = self.write(d, "a.pas", "var\n    Counter: Integer;\n\n"
                                       "// Entry point\nprocedure Run;\nbegin\n    Helper('x');\nend;\n")
            b = self.write(d, "b.pas", "function Helper(S: String): String;\nbegin\n"
                                       "    Result := S; // Run is not called here\nend;\n")
            text = sb.bundle_text([a, b])
            self.assertLess(text.index("Counter: Integer"), text.index("function Helper"))
            self.assertLess(text.index("function Helper"), text.index("procedure Run"))
            self.assertIn("// Entry point", text)
            self.assertEqual(defined_before_use(text), [])

    def test_cycle_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            a = self.write(d, "a.pas", "procedure A;\nbegin\n    B;\nend;\n\nprocedure B;\nbegin\n    A;\nend;\n")
            with self.assertRaises(ValueError):
                sb.bundle_text([a])

    def test_real_project_bundles_in_order(self):
        units = sb.project_units(HERE.parent / "AltiumScript" / "Altium_API.PrjScr")
        self.assertGreater(len(units), 5)
        text = sb.bundle_text(units)
        self.assertEqual(defined_before_use(text), [])
        count = sum(len(re.findall(r"^(?:function|procedure)\s", p.read_text(encoding="latin-1"), re.M | re.I))
                    for p in units)
        self.assertEqual(len(re.findall(r"^(?:function|procedure)\s", text, re.M | re.I)), count)

    def test_rebuilds_when_a_unit_changes(self):
        with tempfile.TemporaryDirectory() as d:
            unit = self.write(d, "u.pas", "procedure Run;\nbegin\nend;\n")
            prj = self.write(d, "p.PrjScr", "[Document1]\nDocumentPath=u.pas\n")
            out = Path(d) / "bundle.pas"
            sb.ensure_bundle(prj, out)
            self.write(d, "u.pas", "procedure Run;\nbegin\n    ShowMessage('new');\nend;\n")
            later = out.stat().st_mtime + 5
            os.utime(unit, (later, later))
            sb.ensure_bundle(prj, out)
            self.assertIn("ShowMessage('new')", out.read_text(encoding="latin-1"))


if __name__ == "__main__":
    unittest.main()
