"""Unit tests for dblib (pure Python, no Altium needed).

Run from server/:  python -m unittest tests.test_dblib -v
"""
import os
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import dblib  # noqa: E402
import sch_edit  # noqa: E402

DBLIB = """[OutputDatabaseLinkFile]
Version=1.1
[DatabaseLinks]
ConnectionString=Provider=Microsoft.ACE.OLEDB.12.0;Data Source=x.xlsx
LibraryDatabasePath=Lib.xlsx
DatabasePathRelative=1
[Table1]
TableName=Resistor$
Enabled=True
[Table2]
TableName=README$
Enabled=False
[FieldMap1]
Options=FieldName=Resistor$.PartNr|TableNameOnly=Resistor$|FieldNameOnly=PartNr|FieldType=0|ParameterName=PartNr|VisibleOnAdd=False
[FieldMap2]
Options=FieldName=Resistor$.Library Ref|TableNameOnly=Resistor$|FieldNameOnly=Library Ref|FieldType=1|ParameterName=[Library Ref]|VisibleOnAdd=False
[FieldMap3]
Options=FieldName=Resistor$.Library Path|TableNameOnly=Resistor$|FieldNameOnly=Library Path|FieldType=1|ParameterName=[Library Path]|VisibleOnAdd=False
[FieldMap4]
Options=FieldName=Resistor$.Footprint Ref|TableNameOnly=Resistor$|FieldNameOnly=Footprint Ref|FieldType=1|ParameterName=[Footprint Ref]|VisibleOnAdd=False
[FieldMap5]
Options=FieldName=Resistor$.Description|TableNameOnly=Resistor$|FieldNameOnly=Description|FieldType=1|ParameterName=[Description]|VisibleOnAdd=False
[FieldMap6]
Options=FieldName=Resistor$.Value|TableNameOnly=Resistor$|FieldNameOnly=Value|FieldType=1|ParameterName=Value|VisibleOnAdd=True
"""

M = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
HEAD = ["PartNr", "Description", "Value", "Tolerance", "Library Ref", "Library Path", "Footprint Ref", "Mouser"]
ROW = ["R-1K", "Resistor 1k", "1k", None, "RES", "Resistor/R.SchLib", "R0603", ""]


def _xlsx(path, replace=None, sheet="Resistor"):
    row_values = [(replace or {}).get(v, v) for v in ROW]
    strings = HEAD + [v for v in row_values if v]
    cells = []
    for r, values in ((1, HEAD), (2, row_values)):
        row = []
        for i, v in enumerate(values):
            ref = f"{chr(65 + i)}{r}"
            if v is None:     # Tolerance: 0.005 shown as a percentage
                row.append(f'<c r="{ref}" s="1"><v>0.005</v></c>')
            elif v:
                row.append(f'<c r="{ref}" t="s"><v>{strings.index(v)}</v></c>')
        cells.append(f'<row r="{r}">{"".join(row)}</row>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook {M} {R}><sheets><sheet name="{sheet}" r:id="rId1"/>'
                                      f'</sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/sharedStrings.xml", f'<sst {M}>' + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>")
        z.writestr("xl/styles.xml", f'<styleSheet {M}><cellXfs><xf numFmtId="0"/><xf numFmtId="10"/></cellXfs>'
                                    '</styleSheet>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet {M}><sheetData>{"".join(cells)}</sheetData></worksheet>')


class DbLibTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.lib = os.path.join(self.dir, "Lib.DbLib")
        with open(self.lib, "w") as f:
            f.write(DBLIB)
        _xlsx(os.path.join(self.dir, "Lib.xlsx"))
        os.makedirs(os.path.join(self.dir, "Resistor"))
        open(os.path.join(self.dir, "Resistor", "R.SchLib"), "w").close()

    def test_read_dblib(self):
        lib = dblib.read_dblib(self.lib)
        self.assertEqual(lib["tables"], ["Resistor$"])
        self.assertEqual(lib["database"], os.path.join(self.dir, "Lib.xlsx"))
        self.assertTrue(lib["fields"]["Resistor$"][0]["key"])

    def test_place_operation(self):
        with mock.patch.object(dblib, "decimal_separator", return_value=","):
            op = dblib.place_operation({"op": "place_component", "dblib": self.lib, "part_number": "R-1K",
                                        "designator": "R9", "x": 100, "y": 200})
        self.assertEqual((op["symbol"], op["db_table"], op["db_key"]), ("RES", "Resistor$", "R-1K"))
        self.assertEqual(op["library"], os.path.join(self.dir, "Resistor", "R.SchLib"))
        self.assertEqual(op["db_description"], "Resistor 1k")
        self.assertEqual(op["db_footprints"], ["R0603"])
        self.assertEqual(op["comment"], "R-1K")
        params = {n: (v, vis) for n, v, vis in op["db_parameters"]}
        self.assertEqual(params["Value"], ("1k", True))
        self.assertEqual(params["Tolerance"], ("0,5%", False))   # percent format, decimal comma
        self.assertEqual(params["Mouser"], ("", False))
        self.assertNotIn("Library Ref", params)                  # Altium's own fields are not parameters
        self.assertNotIn("dblib", op)
        lines, error = sch_edit.encode_operations([op])
        self.assertEqual(error, "")
        f = lines[0].split("|")
        self.assertEqual(f[9:15], ["DB", "Lib.DbLib", "Resistor$", "R-1K", "Resistor 1k", str(len(params))])
        self.assertEqual(f[-2:], ["1", "R0603"])

    def test_errors(self):
        with self.assertRaisesRegex(dblib.DbLibError, "not a key"):
            dblib.place_operation({"dblib": self.lib, "part_number": "R-2K"})
        with self.assertRaisesRegex(dblib.DbLibError, "no enabled table"):
            dblib.place_operation({"dblib": self.lib, "part_number": "R-1K", "table": "Capacitor"})
        with self.assertRaisesRegex(dblib.DbLibError, "not found"):
            dblib.read_dblib(os.path.join(self.dir, "none.DbLib"))

    def _write_lib(self, text):
        with open(self.lib, "w") as f:
            f.write(text)

    def _params(self):
        op = dblib.place_operation({"dblib": self.lib, "part_number": "R-1K", "designator": "R9", "x": 0, "y": 0})
        return {n: (v, vis) for n, v, vis in op["db_parameters"]}

    def test_add_mode(self):
        # Value: do not add; Mouser (unmapped, blank): the library's "add only if not blank"
        self._write_lib(DBLIB.replace("ParameterName=Value|VisibleOnAdd=True",
                                      "ParameterName=Value|VisibleOnAdd=True|AddMode=1")
                        .replace("DatabasePathRelative=1", "DatabasePathRelative=1\nAddMode=3"))
        params = self._params()
        self.assertNotIn("Value", params)
        self.assertNotIn("Mouser", params)
        self.assertIn("Tolerance", params)
        self.assertIn("PartNr", params)

    def test_add_mode_field_overrides_default(self):
        self._write_lib(DBLIB.replace("FieldType=0|ParameterName=PartNr|VisibleOnAdd=False",
                                      "FieldType=0|ParameterName=PartNr|VisibleOnAdd=False|AddMode=2")
                        .replace("DatabasePathRelative=1", "DatabasePathRelative=1\nAddMode=1"))
        self.assertEqual(list(self._params()), ["PartNr"])

    def test_cell_with_a_pipe_is_named(self):
        _xlsx(os.path.join(self.dir, "Lib.xlsx"), {"Resistor 1k": "Resistor | 1k"})
        with self.assertRaisesRegex(dblib.DbLibError, r"cell B2 \(column 'Description'\)"):
            self._params()

    def test_broken_files(self):
        self._write_lib("AddMode=1 before any section\n[DatabaseLinks]\n")
        with self.assertRaisesRegex(dblib.DbLibError, "cannot read"):
            dblib.read_dblib(self.lib)
        with zipfile.ZipFile(os.path.join(self.dir, "Bad.xlsx"), "w") as z:
            z.writestr("xl/workbook.xml", "<workbook")
        with self.assertRaisesRegex(dblib.DbLibError, "cannot read sheet"):
            dblib.read_xlsx_sheet(os.path.join(self.dir, "Bad.xlsx"), "Resistor")

    def test_sheet_names(self):
        self.assertEqual(dblib.sheet_name("Resistor$"), "Resistor")
        self.assertEqual(dblib.sheet_name("'My Sheet$'"), "My Sheet")
        self.assertEqual(dblib.sheet_name("'Bob''s Parts$'"), "Bob's Parts")
        _xlsx(os.path.join(self.dir, "Lib.xlsx"), sheet="My Resistors")
        self._write_lib(DBLIB.replace("Resistor$", "'My Resistors$'"))
        op = dblib.place_operation({"dblib": self.lib, "part_number": "R-1K", "table": "My Resistors",
                                    "designator": "R9", "x": 0, "y": 0})
        self.assertEqual(op["db_table"], "'My Resistors$'")
        self.assertEqual(op["db_footprints"], ["R0603"])

    def test_sub_directories_only_when_asked(self):
        os.makedirs(os.path.join(self.dir, "deep", "er"))
        open(os.path.join(self.dir, "deep", "er", "X.SchLib"), "w").close()
        lib = dblib.read_dblib(self.lib)
        self.assertIsNone(dblib._find_file(lib, "X.SchLib"))
        lib["search_subdirectories"] = True
        self.assertEqual(dblib._find_file(lib, "X.SchLib"), os.path.join(self.dir, "deep", "er", "X.SchLib"))
        with mock.patch.object(dblib, "SEARCH_MAX_DEPTH", 2):
            self.assertIsNone(dblib._find_file(lib, "X.SchLib"))
        with mock.patch.object(dblib, "SEARCH_MAX_DIRS", 2):
            self.assertIsNone(dblib._find_file(lib, "X.SchLib"))


if __name__ == "__main__":
    unittest.main()
