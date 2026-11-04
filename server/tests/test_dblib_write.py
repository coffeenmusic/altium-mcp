"""Unit tests for dblib_write (pure Python, no Altium needed).

Run from server/:  python -m unittest tests.test_dblib_write -v
"""
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import dblib  # noqa: E402
import dblib_write  # noqa: E402
from tests.test_dblib import DBLIB, _xlsx  # noqa: E402


class WriteTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.lib = os.path.join(self.dir, "Lib.DbLib")
        with open(self.lib, "w") as f:
            f.write(DBLIB)
        self.xlsx = os.path.join(self.dir, "Lib.xlsx")
        _xlsx(self.xlsx)
        self.backups = os.path.join(self.dir, "backups")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_add_rows(self):
        with mock.patch.object(dblib, "decimal_separator", return_value=","):
            r = dblib_write.add_rows(self.lib, "Resistor", [
                {"PartNr": "R-2K", "Value": "2k <&>", "Tolerance": "1%", "Library Ref": "RES",
                 "Library Path": "Resistor/R.SchLib"}], self.backups)
            self.assertEqual((r["added"], r["rows"], r["key"]), (["R-2K"], [3], "PartNr"))
            self.assertTrue(os.path.exists(r["backup"]))
            rows = dblib.read_xlsx_sheet(self.xlsx, "Resistor")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["PartNr"], "R-1K")
        self.assertEqual(rows[1]["Value"], "2k <&>")
        # a percent column of numbers gets a number, shown in the cell's format
        self.assertEqual(rows[1]["Tolerance"], "1%")
        with zipfile.ZipFile(self.xlsx) as z:
            sheet = z.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn('<c r="D3" s="1"><v>0.01</v></c>', sheet)
        self.assertEqual(dblib_write.text_in_numeric_columns(self.xlsx, "Resistor"), [])

    def test_refused(self):
        for rows, message in (([{"PartNr": "R-1K"}], "already"), ([{"Value": "1"}], "key"),
                              ([{"PartNr": "N", "Colour": "red"}], "no column"),
                              ([{"PartNr": "A"}, {"PartNr": "A"}], "already"), ([], "empty")):
            with self.assertRaisesRegex(dblib_write.WriteError, message):
                dblib_write.add_rows(self.lib, "Resistor", rows, self.backups)
        with self.assertRaisesRegex(dblib_write.WriteError, "no enabled table"):
            dblib_write.add_rows(self.lib, "Capacitor", [{"PartNr": "C"}], self.backups)
        self.assertEqual(len(dblib.read_xlsx_sheet(self.xlsx, "Resistor")), 1)

    def test_as_number(self):
        self.assertEqual([dblib_write.as_number(v) for v in ("10%", "0,5", "25", "25V", 3)], [0.1, 0.5, 25, "25V", 3])

    def test_text_in_numeric_column_found(self):
        # nine rows of numbers: the driver types the column from rows 2 to 9
        dblib_write.add_rows(self.lib, "Resistor", [{"PartNr": f"R-{k}", "Tolerance": 0.02} for k in range(9)],
                             self.backups)
        # a script that wrote the last tolerance as text: Altium reads it empty
        with zipfile.ZipFile(self.xlsx) as z:
            parts = {n: z.read(n) for n in z.namelist()}
        sheet = parts["xl/worksheets/sheet1.xml"].decode()
        sheet = sheet.replace('<c r="D11" s="1"><v>0.02</v></c>',
                              '<c r="D11" s="1" t="inlineStr"><is><t>0.02</t></is></c>')
        parts["xl/worksheets/sheet1.xml"] = sheet.encode()
        with zipfile.ZipFile(self.xlsx, "w") as z:
            for n, data in parts.items():
                z.writestr(n, data)
        self.assertEqual(dblib_write.text_in_numeric_columns(self.xlsx, "Resistor"), [{"row": 11, "column": "D"}])


if __name__ == "__main__":
    unittest.main()
