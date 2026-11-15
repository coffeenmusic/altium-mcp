"""Unit tests for hierarchy_check (pure Python, no Altium).

Run from server/:  python -m unittest tests.test_hierarchy_check -v
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import hierarchy_check as hc  # noqa: E402


class SheetBuilder:
    """FileHeader and Additional records of one sheet, in file units
    (10 mil), with OWNERINDEX numbering as Altium writes it."""

    def __init__(self):
        self.recs = [{"HEADER": "Protel for Windows - Schematic Capture Binary File Version 5.0"}]
        self.add = [{"HEADER": "Protel for Windows - Schematic Capture Binary File Version 5.0"}]

    def symbol(self, name, file, x, y, w=100, h=100, entries=(), attrs=None):
        """entries: (name, side, distance in 100 mil, harness, iotype);
        attrs: more fields of the sheet symbol record (SYMBOLTYPE, ...)"""
        idx = len(self.recs)
        self.recs.append({"RECORD": "15", "LOCATION.X": str(x), "LOCATION.Y": str(y),
                          "XSIZE": str(w), "YSIZE": str(h), **(attrs or {})})
        owner = str(idx - 1)
        self.recs.append({"RECORD": "32", "OWNERINDEX": owner, "TEXT": name})
        self.recs.append({"RECORD": "33", "OWNERINDEX": owner, "TEXT": file})
        for n, side, dist, harness, io in entries:
            r = {"RECORD": "16", "OWNERINDEX": owner, "NAME": n, "SIDE": str(side), "DISTANCEFROMTOP": str(dist)}
            if harness:
                r["HARNESSTYPE"] = harness
            if io:
                r["IOTYPE"] = str(io)
            self.recs.append(r)
        return self

    def no_erc(self, x, y, active=True):
        r = {"RECORD": "22", "LOCATION.X": str(x), "LOCATION.Y": str(y), "SUPPRESSALL": "T"}
        if not active:
            r["ISACTIVE"] = "F"
        self.recs.append(r)
        return self

    def port(self, name, x, y, width=50, harness="", io=0):
        r = {"RECORD": "18", "NAME": name, "LOCATION.X": str(x), "LOCATION.Y": str(y), "WIDTH": str(width)}
        if harness:
            r["HARNESSTYPE"] = harness
        if io:
            r["IOTYPE"] = str(io)
        self.recs.append(r)
        return self

    def wire(self, *pts, record="27"):
        r = {"RECORD": record, "LOCATIONCOUNT": str(len(pts))}
        for k, (x, y) in enumerate(pts, 1):
            r[f"X{k}"], r[f"Y{k}"] = str(x), str(y)
        (self.add if record == "218" else self.recs).append(r)
        return self

    def harness(self, *pts):
        return self.wire(*pts, record="218")

    def connector(self, htype, x, y, entries, w=50, h=100, pcp=50, side=0):
        idx = len(self.add)
        r = {"RECORD": "215", "LOCATION.X": str(x), "LOCATION.Y": str(y), "XSIZE": str(w), "YSIZE": str(h),
             "PRIMARYCONNECTIONPOSITION": str(pcp)}
        if side:
            r["HARNESSCONNECTORSIDE"] = str(side)
        self.add.append(r)
        owner = {"OWNERINDEX": str(idx - 1)} if idx - 1 else {}
        for k, n in enumerate(entries, 1):
            self.add.append({"RECORD": "216", **owner, "OWNERINDEXADDITIONALLIST": "T",
                             "DISTANCEFROMTOP": str(k), "NAME": n})
        self.add.append({"RECORD": "217", **owner, "OWNERINDEXADDITIONALLIST": "T", "TEXT": htype})
        return self


class Project:
    """A .PrjPcb in a temp folder and a reader serving built sheets."""

    def __init__(self, test, sheets, harness_files=None, encoding="utf-8-sig",
                 settings=("HierarchyMode=0", "AllowSheetEntryNetNames=0"), on_disk=None):
        """sheets: {path in the project: SheetBuilder}; a path may have
        folders (written with backslashes, as Altium does). on_disk: sheets
        that exist in the folder but are not in the project."""
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.sheets = {}
        lines = ["[Design]", *settings]
        for k, (name, b) in enumerate(sheets.items(), 1):
            lines += [f"[Document{k}]", f"DocumentPath={name}"]
            self._put(name, b)
        for name, b in (on_disk or {}).items():
            self._put(name, b)
        for name, text in (harness_files or {}).items():
            (self.dir / name).write_text(text, encoding="utf-8")
            lines += [f"[Document{len(lines)}]", f"DocumentPath={name}"]
        self.prj = self.dir / "Test.PrjPcb"
        self.prj.write_text("\r\n".join(lines) + "\r\n", encoding=encoding)

    def _key(self, path):
        return os.path.normpath(os.path.relpath(path, self.dir)).casefold()

    def _put(self, name, b):
        f = Path(os.path.normpath(self.dir / name))
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"")
        self.sheets[self._key(f)] = b

    def reader(self, path):
        b = self.sheets[self._key(path)]
        return hc.sheet_from_records(b.recs, b.add, path)

    def check(self, **kw):
        return hc.check_hierarchy(self.prj, reader=self.reader, **kw)


def types(res, severity=None):
    return sorted(i["type"] for i in res["issues"] if severity is None or i["severity"] == severity)


def wired_top(*entries, file="Child", y=300):
    """Top sheet with one symbol whose left entries each get a wire stub."""
    top = SheetBuilder().symbol("U1", file, 100, y, entries=entries)
    for n, side, dist, *_ in entries:
        top.wire((50, y - dist * 10), (100, y - dist * 10))
    return top


class EntriesAndPorts(unittest.TestCase):
    def test_matching_entries_and_ports_report_nothing(self):
        top = wired_top(("A", 0, 1, "", 2), ("B", 0, 2, "", 0))
        child = SheetBuilder().port("A", 100, 100, io=2).port("B", 100, 200)
        res = Project(self, {"Top.SchDoc": top, "Child.SchDoc": child}).check(min_severity="info")
        self.assertEqual(types(res), [])
        self.assertEqual(res["top_sheets"], ["Top.SchDoc"])

    def test_similar_names_are_paired_with_a_port_rename(self):
        top = wired_top(("SPI_CLK", 0, 1, "", 0))
        child = SheetBuilder().port("SPI-CLK", 100, 100)
        res = Project(self, {"Top.SchDoc": top, "Child.SchDoc": child}).check()
        (issue,) = res["issues"]
        self.assertEqual(issue["type"], "probable_pair")
        self.assertEqual(issue["suggest"], {"op": "rename_port", "sheet": "Child.SchDoc",
                                            "old": "SPI-CLK", "new": "SPI_CLK"})
        self.assertEqual(issue["alternative"]["op"], "rename_entry")
        self.assertEqual(issue["alternative"]["symbol_at"], [1000.0, 3000.0])

    def test_harness_entry_pairs_with_the_port_of_its_type(self):
        # the Ethernet case: entry "2" of type S15, port named after the type
        top = wired_top(("2", 0, 1, "S15", 0))
        child = SheetBuilder().port("S15", 100, 100, harness="S15").port("OTHER", 100, 200)
        child.connector("S15", 200, 150, ["TXEN", "TX0"])
        top.connector("S15", 0, 150, ["TXEN", "TX0"])
        res = Project(self, {"Top.SchDoc": top, "Child.SchDoc": child}).check()
        pair = [i for i in res["issues"] if i["type"] == "probable_pair"]
        self.assertEqual(len(pair), 1)
        self.assertEqual(pair[0]["reason"], "same harness type S15")
        self.assertEqual(pair[0]["port"], "S15")
        self.assertIn("port_without_entry", types(res))   # OTHER stays unmatched

    def test_child_used_twice_suggests_renaming_the_entry(self):
        top = SheetBuilder().symbol("U1", "Child", 100, 300, entries=[("SIGNAL_A", 0, 1, "", 0)]) \
                            .symbol("U2", "Child", 400, 300, entries=[("SIGNAL_A", 0, 1, "", 0)])
        child = SheetBuilder().port("SIGNAL-A", 100, 100)
        res = Project(self, {"Top.SchDoc": top, "Child.SchDoc": child}).check()
        pairs = [i for i in res["issues"] if i["type"] == "probable_pair"]
        self.assertEqual({i["suggest"]["op"] for i in pairs}, {"rename_entry"})
        self.assertEqual(sorted(tuple(i["suggest"]["symbol_at"]) for i in pairs),
                         [(1000.0, 3000.0), (4000.0, 3000.0)])

    def test_port_linked_by_another_instance_is_flagged(self):
        top = SheetBuilder().symbol("U1", "Child", 100, 300, entries=[("OUT", 0, 1, "", 0)]) \
                            .symbol("U2", "Child", 400, 300, entries=[])
        child = SheetBuilder().port("OUT", 100, 100)
        res = Project(self, {"Top.SchDoc": top, "Child.SchDoc": child}).check()
        (issue,) = [i for i in res["issues"] if i["type"] == "port_without_entry"]
        self.assertTrue(issue["linked_on_other_instance"])
        self.assertEqual(issue["symbol_at"], [4000.0, 3000.0])

    def test_unmatched_names_are_reported_on_both_sides(self):
        top = wired_top(("29", 0, 1, "", 0))
        child = SheetBuilder().port("PD_A", 100, 100)
        res = Project(self, {"Top.SchDoc": top, "Child.SchDoc": child}).check()
        self.assertEqual(types(res), ["entry_without_port", "port_without_entry"])

    def test_harness_type_mismatch(self):
        top = wired_top(("5", 0, 1, "S9", 0))
        child = SheetBuilder().port("5", 100, 100, harness="S2")
        top.connector("S9", 0, 150, ["X"])
        child.connector("S2", 200, 150, ["Y"])
        res = Project(self, {"Top.SchDoc": top, "Child.SchDoc": child}).check()
        (issue,) = [i for i in res["issues"] if i["type"] == "harness_type_mismatch"]
        self.assertEqual(issue["suggest"]["harness"], "S2")

    def test_io_types(self):
        top = wired_top(("A", 0, 1, "", 1), ("B", 0, 2, "", 0), ("H", 0, 3, "S1", 3))
        child = SheetBuilder().port("A", 100, 100, io=2).port("B", 100, 200, io=2) \
                              .port("H", 100, 300, harness="S1")
        top.connector("S1", 0, 150, ["X"])
        res = Project(self, {"Top.SchDoc": top, "Child.SchDoc": child}).check()
        found = {i["entry"]: i["type"] for i in res["issues"] if i["type"].startswith("io_")}
        # A: Output vs Input; B: unspecified vs Input; H: harness, no I/O type in Altium
        self.assertEqual(found, {"A": "io_mismatch", "B": "io_unspecified"})

    def test_unwired_entry(self):
        top = SheetBuilder().symbol("U1", "Child", 100, 300, entries=[("A", 0, 1, "", 0)])
        child = SheetBuilder().port("A", 100, 100)
        res = Project(self, {"Top.SchDoc": top, "Child.SchDoc": child}).check()
        (issue,) = res["issues"]
        self.assertEqual(issue["type"], "entry_unwired")
        self.assertEqual(issue["at"], [1000.0, 2900.0])

    def test_no_erc_marker_on_an_entry(self):
        top = SheetBuilder().symbol("U1", "Child", 100, 300, entries=[("A", 0, 1, "", 0), ("B", 0, 2, "", 0)])
        top.no_erc(100, 290)                 # on entry A
        top.no_erc(100, 280, active=False)   # on entry B, but switched off
        child = SheetBuilder().port("A", 100, 100).port("B", 100, 200)
        res = Project(self, {"Top.SchDoc": top, "Child.SchDoc": child}).check()
        self.assertEqual([(i["type"], i["entry"]) for i in res["issues"]], [("entry_unwired", "B")])

    def test_pair_by_similar_name_is_a_warning(self):
        top = wired_top(("SPI_CLOCK", 0, 1, "", 0), ("I2C_SDA", 0, 2, "", 0))
        child = SheetBuilder().port("SPI_CLK", 100, 100).port("i2c-sda", 100, 200)
        res = Project(self, {"Top.SchDoc": top, "Child.SchDoc": child}).check()
        found = {i["entry"]: (i["severity"], i["reason"]) for i in res["issues"]}
        self.assertEqual(found, {"SPI_CLOCK": ("warning", "similar name"),
                                 "I2C_SDA": ("error", "same name apart from case/separators")})

    def test_power_port_match_without_a_hierarchy_mode_key(self):
        top = wired_top(("VCC_CORE", 0, 1, "", 0))
        child = SheetBuilder()
        child.recs.append({"RECORD": "17", "TEXT": "VCC_CORE", "LOCATION.X": "100", "LOCATION.Y": "100"})
        p = Project(self, {"Top.SchDoc": top, "Child.SchDoc": child}, settings=())
        res = p.check(min_severity="info")
        self.assertEqual(types(res), ["entry_matches_power_port"])
        self.assertNotIn("HierarchyMode", res["settings"])

    def test_entry_named_like_a_power_port_is_info(self):
        top = wired_top(("VCC_CORE", 0, 1, "", 0))
        child = SheetBuilder()
        child.recs.append({"RECORD": "17", "TEXT": "VCC_CORE", "LOCATION.X": "100", "LOCATION.Y": "100"})
        res = Project(self, {"Top.SchDoc": top, "Child.SchDoc": child}).check(min_severity="info")
        self.assertEqual(types(res), ["entry_matches_power_port"])

    def test_pin_on_the_entry_counts_as_wired(self):
        top = SheetBuilder().symbol("U1", "Child", 100, 300, entries=[("A", 0, 1, "", 0)])
        # pin at 800,2900 pointing right (orientation 0), 200 mil long: hot end on the entry
        top.recs.append({"RECORD": "2", "LOCATION.X": "80", "LOCATION.Y": "290", "PINLENGTH": "20"})
        child = SheetBuilder().port("A", 100, 100)
        res = Project(self, {"Top.SchDoc": top, "Child.SchDoc": child}).check()
        self.assertEqual(types(res), [])

    def test_missing_child_sheet(self):
        top = SheetBuilder().symbol("U1", "Gone", 100, 300)
        res = Project(self, {"Top.SchDoc": top}).check()
        self.assertEqual(types(res), ["child_sheet_missing"])

    def test_managed_sheets_are_found_by_revision(self):
        # two revisions of one managed sheet, same file name, different ports
        def managed(rev):
            return {"SYMBOLTYPE": "Design Item", "DESIGNITEMID": "SCH-1-A." + rev, "REVISIONGUID": "GUID" + rev}
        top = SheetBuilder().symbol("Q1", "Osc.SchDoc", 100, 300, entries=[("CLK", 0, 1, "", 0)],
                                    attrs=managed("1"))
        top.symbol("Q2", "Osc.SchDoc", 300, 300, entries=[("CLK_OUT", 0, 1, "", 0)], attrs=managed("2"))
        top.wire((50, 290), (100, 290)).wire((250, 290), (300, 290))
        rev1 = SheetBuilder().port("CLK", 100, 100)
        rev2 = SheetBuilder().port("CLK_OUT", 100, 100)
        res = Project(self, {"Top.SchDoc": top,
                             r"Managed\Sheets\GUID1\Osc.SchDoc": rev1,
                             r"Managed\Sheets\GUID2\Osc.SchDoc": rev2}).check(min_severity="info")
        self.assertEqual(types(res), [])
        self.assertEqual(res["sheet_symbols"], 2)

    def test_managed_sheet_not_on_disk_is_info(self):
        top = wired_top(("CLK", 0, 1, "", 0), file="Osc.SchDoc")
        top.recs[1].update({"SYMBOLTYPE": "Design Item", "DESIGNITEMID": "SCH-1-A.1", "REVISIONGUID": "GONE"})
        res = Project(self, {"Top.SchDoc": top}).check(min_severity="info")
        (issue,) = res["issues"]
        self.assertEqual((issue["type"], issue["severity"]), ("device_sheet_not_local", "info"))
        self.assertEqual(issue["design_item"], "SCH-1-A.1")

    def test_same_file_name_in_two_folders(self):
        # A\Top uses A\Child (same folder); Top in the project folder cannot tell
        a_top = wired_top(("X", 0, 1, "", 0))
        root_top = wired_top(("X", 0, 1, "", 0))
        a_child = SheetBuilder().port("X", 100, 100)
        b_child = SheetBuilder().port("Y", 100, 100)
        res = Project(self, {r"A\Top.SchDoc": a_top, r"A\Child.SchDoc": a_child,
                             r"B\Child.SchDoc": b_child, "Root.SchDoc": root_top}).check(min_severity="info")
        issues = [(i["type"], i["sheet"]) for i in res["issues"] if i["severity"] != "info"]
        self.assertEqual(issues, [("child_sheet_ambiguous", "Root.SchDoc")])
        amb = next(i for i in res["issues"] if i["type"] == "child_sheet_ambiguous")
        self.assertEqual(amb["candidates"], [os.path.join("A", "Child.SchDoc"), os.path.join("B", "Child.SchDoc")])

    def test_non_ascii_names_in_a_utf8_project(self):
        top = wired_top(("A", 0, 1, "", 0), file="B3 µC")
        child = SheetBuilder().port("A", 100, 100)
        res = Project(self, {"Top.SchDoc": top, "B3 µC.SchDoc": child}).check(min_severity="info")
        self.assertEqual(types(res), [])


class Harnesses(unittest.TestCase):
    def test_conflicting_definitions(self):
        a = SheetBuilder().connector("S4", 100, 300, ["SCK", "MOSI", "CS"])
        b = SheetBuilder().connector("S4", 100, 300, ["MOSI", "SCK", "MISO"])
        res = Project(self, {"A.SchDoc": a, "B.SchDoc": b}).check()
        (issue,) = [i for i in res["issues"] if i["type"] == "harness_conflict"]
        only = {tuple(v["sheets"]): v["only_here"] for v in issue["variants"]}
        self.assertEqual(only, {("A.SchDoc",): ["CS"], ("B.SchDoc",): ["MISO"]})

    def test_same_entries_in_another_order_is_info(self):
        a = SheetBuilder().connector("S4", 100, 300, ["SCK", "MOSI"])
        b = SheetBuilder().connector("S4", 100, 300, ["MOSI", "SCK"])
        res = Project(self, {"A.SchDoc": a, "B.SchDoc": b}).check(min_severity="info")
        self.assertIn("harness_order", types(res, "info"))
        self.assertNotIn("harness_conflict", types(res))

    def test_undefined_harness_type(self):
        a = SheetBuilder().port("P", 100, 100, harness="S99")
        res = Project(self, {"A.SchDoc": a}).check()
        self.assertEqual(types(res), ["harness_type_undefined"])

    def test_signal_harness_from_a_connector_has_a_type(self):
        # connector at x 100..150, primary connection on its left edge 50 mil below the top
        a = SheetBuilder().connector("S1", 100, 300, ["A", "B"]).harness((60, 250), (100, 250)) \
                          .port("1", 10, 250, harness="S1")
        res = Project(self, {"A.SchDoc": a}, {"A.Harness": "S1=A,B\r\n"}).check(min_severity="info")
        self.assertEqual(types(res), [])

    def test_untyped_signal_harness_is_located(self):
        top = SheetBuilder().symbol("U1", "C1", 100, 700, entries=[("29", 1, 1, "", 0)]) \
                            .symbol("U2", "C2", 400, 700, entries=[("30", 0, 1, "", 0)])
        top.harness((200, 690), (300, 690)).harness((300, 690), (400, 690))
        top.harness((690, 660), (695, 655))
        c1, c2 = SheetBuilder().port("29", 0, 0), SheetBuilder().port("30", 0, 0)
        res = Project(self, {"Top.SchDoc": top, "C1.SchDoc": c1, "C2.SchDoc": c2}).check()
        untyped = [i for i in res["issues"] if i["type"] == "signal_harness_untyped"]
        self.assertEqual(len(untyped), 2)
        joined = next(i for i in untyped if i["reached"])
        self.assertEqual(joined["reached"], ["entry 29 of U1 (no type)", "entry 30 of U2 (no type)"])
        self.assertEqual(next(i for i in untyped if not i["reached"])["at"], [6900.0, 6600.0])

    def test_signal_harness_joining_two_types(self):
        a = SheetBuilder().connector("S1", 100, 300, ["A"]).connector("S2", 300, 300, ["B"], side=1)
        a.harness((100, 250), (350, 250))
        res = Project(self, {"A.SchDoc": a}).check()
        self.assertIn("signal_harness_type_conflict", types(res))

    def test_stale_harness_file(self):
        a = SheetBuilder().connector("S1", 100, 300, ["A", "B"]).connector("S2", 100, 600, ["C"])
        res = Project(self, {"A.SchDoc": a}, {"A.Harness": "S1=A,X\r\nS3=Q\r\n"}).check()
        (issue,) = [i for i in res["issues"] if i["type"] == "harness_file_stale"]
        self.assertEqual(issue["differences"], {"S1": {"file_only": ["X"], "sheet_only": ["B"]},
                                                "S2": "missing in file",
                                                "S3": "in file, no connector on the sheet"})

    def test_missing_harness_file(self):
        a = SheetBuilder().connector("S1", 100, 300, ["A"])
        res = Project(self, {"A.SchDoc": a}).check()
        self.assertEqual(types(res), ["harness_file_missing"])


class Settings(unittest.TestCase):
    def test_cp1252_project_and_severity_filter(self):
        top = SheetBuilder().symbol("U1", "Gone", 100, 300)
        p = Project(self, {"Top.SchDoc": top}, encoding="cp1252")
        res = p.check(min_severity="error")
        self.assertEqual(res["settings"]["HierarchyModeName"], "Automatic")
        self.assertEqual(res["issue_counts"], {"error": {"child_sheet_missing": 1}})


if __name__ == "__main__":
    unittest.main()
