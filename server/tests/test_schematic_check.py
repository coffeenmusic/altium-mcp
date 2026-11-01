"""Unit tests for schdoc_reader and schematic_check (pure Python, no Altium).

Run from server/:  python -m unittest tests.test_schematic_check -v
"""
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import schematic_check as sc  # noqa: E402
from schdoc_reader import CompoundFile, coord, parse_records, read_schdoc  # noqa: E402

FREE, END = 0xFFFFFFFF, 0xFFFFFFFE


def compound_file(big: bytes, small: bytes) -> bytes:
    """A version 3 compound file holding stream "FileHeader" (>= 4096 bytes,
    in regular sectors) and stream "Small" (in the mini stream)."""
    S = 512
    pad = lambda b, n: b + b"\0" * (-len(b) % n)  # noqa: E731
    big_sectors = len(pad(big, S)) // S
    mini = pad(small, 64)
    mini_sectors = len(pad(mini, S)) // S
    first_big, minifat_sector = 2, 2 + big_sectors
    first_ministream = minifat_sector + 1

    fat = [FREE] * (S // 4)
    fat[0] = 0xFFFFFFFD          # the FAT sector itself
    fat[1] = END                 # directory
    for k in range(big_sectors):
        fat[first_big + k] = first_big + k + 1 if k < big_sectors - 1 else END
    fat[minifat_sector] = END
    for k in range(mini_sectors):
        fat[first_ministream + k] = first_ministream + k + 1 if k < mini_sectors - 1 else END
    minifat = [FREE] * (S // 4)
    n_mini = len(mini) // 64
    for k in range(n_mini):
        minifat[k] = k + 1 if k < n_mini - 1 else END

    def entry(name, obj_type, start, size):
        raw = name.encode("utf-16-le") + b"\0\0"
        e = raw.ljust(64, b"\0") + struct.pack("<HBB", len(raw), obj_type, 1)
        e += struct.pack("<III", FREE, FREE, FREE) + b"\0" * 36
        return e + struct.pack("<IQ", start, size)

    directory = entry("Root Entry", 5, first_ministream, len(mini)) + entry("FileHeader", 2, first_big, len(big)) \
        + entry("Small", 2, 0, len(small)) + entry("", 0, FREE, 0)

    header = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 16
    header += struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6) + b"\0" * 6
    header += struct.pack("<IIIIIIIII", 0, 1, 1, 0, 4096, minifat_sector, 1, END, 0)
    header += struct.pack("<109I", 0, *([FREE] * 108))
    return header + struct.pack(f"<{S // 4}I", *fat) + directory + pad(big, S) \
        + struct.pack(f"<{S // 4}I", *minifat) + pad(mini, S)


def records_stream(records) -> bytes:
    out = b""
    for r in records:
        body = ("|" + "|".join(f"{k}={v}" for k, v in r.items())).encode("cp1252") + b"\0"
        out += struct.pack("<I", len(body)) + body
    return out


def at(x, y, prefix="LOCATION."):
    """Mils to record fields (10-mil units plus a fraction)."""
    out = {}
    for axis, v in (("X", x), ("Y", y)):
        whole = int(v // 10)
        out[f"{prefix}{axis}"] = whole
        frac = round((v / 10 - whole) * 100000)
        if frac:
            out[f"{prefix}{axis}_FRAC"] = frac
    return out


def wire(*pts):
    r = {"RECORD": 27, "LOCATIONCOUNT": len(pts)}
    for k, (x, y) in enumerate(pts, 1):
        r[f"X{k}"], r[f"Y{k}"] = int(x // 10), int(y // 10)
    return r


def label(text, x, y, just=0, orient=0):
    return {"RECORD": 25, **at(x, y), "TEXT": text, "FONTID": 1, "JUSTIFICATION": just, "ORIENTATION": orient}


def port(x, y, orient):
    return {"RECORD": 17, **at(x, y), "STYLE": 2, "ORIENTATION": orient, "TEXT": "P", "FONTID": 1}


def scene():
    """A 100 mil grid sheet with one of each problem.

    C1 (record 2) is a vertical cap whose top pin lands ON a rail, with its
    designator drawn across that rail.
    """
    recs = [
        {"HEADER": "Protel for Windows - Schematic Capture Binary File Version 5.0"},
        {"RECORD": 31, "FONTIDCOUNT": 1, "SIZE1": 10, "FONTNAME1": "Times New Roman", "SNAPGRIDSIZE": 10},
        {"RECORD": 1, "LIBREFERENCE": "CAP", **at(1000, 850), "CURRENTPARTID": 1},
        # children of record index 2 have OWNERINDEX 1
        {"RECORD": 2, "OWNERINDEX": 1, "OWNERPARTID": 1, "PINCONGLOMERATE": 1, "PINLENGTH": 10,
         **at(1000, 900), "DESIGNATOR": "1"},
        {"RECORD": 2, "OWNERINDEX": 1, "OWNERPARTID": 1, "PINCONGLOMERATE": 3, "PINLENGTH": 10,
         **at(1000, 800), "DESIGNATOR": "2"},
        {"RECORD": 14, "OWNERINDEX": 1, "OWNERPARTID": 1, **at(960, 800), **at(1040, 900, "CORNER.")},
        {"RECORD": 34, "OWNERINDEX": 1, **at(1050, 960), "TEXT": "C1", "NAME": "Designator", "FONTID": 1},
        {"RECORD": 41, "OWNERINDEX": 1, **at(1050, 720), "TEXT": "100nF", "NAME": "Value", "FONTID": 1},
        # rail across C1's top pin, held at both ends by ports
        wire((500, 1000), (1500, 1000)), port(500, 1000, 2), port(1500, 1000, 0),
        # GND stub below C1
        wire((1000, 700), (1000, 600)), port(1000, 600, 3),
        # a label 500 mil short of its wire's free end at x=3000
        wire((3000, 500), (4000, 500)), port(4000, 500, 0), label("FAR", 3500, 500),
        # a bottom-left label at a right-hand free end: text hangs off the wire
        wire((5000, 500), (6000, 500)), port(5000, 500, 2), label("HANG", 6000, 500, just=0),
        # the same end with a middle-left label reading outward: accepted
        wire((5000, 800), (6000, 800)), port(5000, 800, 2), label("CAP", 6000, 800, just=3),
        # right-justified at the right-hand end, running back along the wire
        wire((5000, 1100), (6000, 1100)), port(5000, 1100, 2), label("GOOD", 6000, 1100, just=2),
        # a wire end that goes nowhere
        wire((8000, 500), (8500, 500)), port(8000, 500, 2),
    ]
    return recs


class ReaderTest(unittest.TestCase):
    def test_reads_regular_and_mini_streams(self):
        big = records_stream(scene()) + b"\0" * 4096
        small = b"hello mini stream"
        cf = CompoundFile(compound_file(big, small))
        self.assertEqual(cf.stream("FileHeader"), big)
        self.assertEqual(cf.stream("Small"), small)

    def test_read_schdoc_and_owner_numbering(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.SchDoc")
            with open(path, "wb") as f:
                f.write(compound_file(records_stream(scene()) + b"\0" * 4096, b"x"))
            recs = [r for r in read_schdoc(path) if r]
        self.assertEqual(recs[2]["RECORD"], "1")
        self.assertEqual(recs[3]["OWNERINDEX"], "1")   # record index 2 - 1

    def test_binary_records_keep_their_index(self):
        stream = records_stream([{"RECORD": 1}]) + struct.pack("<I", (1 << 24) | 3) + b"abc" \
            + records_stream([{"RECORD": 2}])
        recs = parse_records(stream)
        self.assertEqual([r and r["RECORD"] for r in recs], ["1", None, "2"])

    def test_coord_keeps_metric_fractions(self):
        self.assertAlmostEqual(coord({"X": "39", "X_FRAC": "37008"}, "X"), 393.70, places=2)
        self.assertEqual(coord({}, "X"), 0.0)


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.sheet = sc.Sheet(parse_records(records_stream(scene())))
        self.issues = sc.Checker(self.sheet).run()

    def of(self, kind):
        return [i for i in self.issues if i["type"] == kind]

    def test_pin_and_hot_end(self):
        c1 = next(iter(self.sheet.components.values()))
        self.assertEqual(c1["designator"], "C1")
        self.assertEqual(sorted(p["hot"] for p in c1["pins"]), [(1000, 700), (1000, 1000)])

    def test_cap_on_its_rail_is_a_pin_tap(self):
        taps = self.of("pin_tap")
        self.assertEqual([(t["designator"], t["pin"]) for t in taps], [("C1", "1")])
        self.assertEqual(taps[0]["suggest"]["junction_at"], [1000, 1200])

    def test_label_short_of_free_end(self):
        far = self.of("label_not_at_end")
        self.assertEqual([i["label"] for i in far], ["FAR"])
        self.assertEqual(far[0]["suggest"], {"x": 3000, "y": 500, "orientation": 0, "justification": 0})

    def test_label_hanging_off_the_end_but_not_an_end_cap(self):
        hang = self.of("label_justification")
        self.assertEqual([i["label"] for i in hang], ["HANG"])
        self.assertEqual(hang[0]["suggest"]["justification"], 2)

    def test_dangling_wire(self):
        self.assertEqual([i["at"] for i in self.of("dangling_wire")], [[8500, 500]])

    def test_designator_across_rail_gets_a_clear_suggestion(self):
        overlaps = [i for i in self.of("text_overlap") if i.get("designator") == "C1"]
        self.assertTrue(overlaps)
        sug = overlaps[0]["suggest"]
        self.assertTrue(sug["clear"])
        rail = ((500, 1000), (1500, 1000))
        c1 = next(iter(self.sheet.components.values()))
        for t in sug["texts"]:
            size = next(x["size"] for x in c1["texts"] if x["name"] == t["parameter"])
            box = sc.text_box(t["x"], t["y"], *size, 0, t["justification"])
            self.assertFalse(box.crossed_by(rail), t)
            self.assertFalse(box.overlaps(c1["body"]), t)

    def test_no_other_issues(self):
        self.assertEqual(sorted({i["type"] for i in self.issues}),
                         ["dangling_wire", "label_justification", "label_not_at_end", "pin_tap", "text_overlap"])


class SheetSymbolTest(unittest.TestCase):
    def check(self, distance, file_y):
        recs = [
            {"HEADER": "x"},
            {"RECORD": 31, "FONTIDCOUNT": 1, "SIZE1": 10, "FONTNAME1": "Times New Roman", "SNAPGRIDSIZE": 10},
            {"RECORD": 15, **at(1000, 2000), "XSIZE": 100, "YSIZE": 80},
            # DistanceFromTop in 100 mil units
            {"RECORD": 16, "OWNERINDEX": 1, "SIDE": 0, "DISTANCEFROMTOP": distance, "NAME": "A"},
            {"RECORD": 16, "OWNERINDEX": 1, "SIDE": 1, "DISTANCEFROMTOP": distance + 1, "NAME": "B"},
            {"RECORD": 32, "OWNERINDEX": 1, **at(1000, 2100), "TEXT": "U_SUB", "FONTID": 1},
            {"RECORD": 33, "OWNERINDEX": 1, **at(1000, file_y), "TEXT": "Sub.SchDoc", "FONTID": 1},
        ]
        return sc.Checker(sc.Sheet(parse_records(records_stream(recs)))).run()

    def test_altium_default_layout_passes(self):
        self.assertEqual(self.check(1, 2000), [])

    def test_deep_first_entry_and_file_name_below(self):
        issues = {i["type"]: i for i in self.check(6, 1100)}
        self.assertEqual(sorted(issues), ["sheet_entry_margin", "sheet_symbol_text"])
        self.assertEqual(issues["sheet_entry_margin"]["suggest"],
                         {"first_entry_from_top": 100, "move_entries_up": 500})
        self.assertEqual(issues["sheet_symbol_text"]["suggest"]["file_name"], {"x": 1000, "y": 2000, "justification": 0})


class HarnessTest(unittest.TestCase):
    def test_wires_on_a_harness_connector_and_a_signal_harness_are_not_free(self):
        recs = [
            {"HEADER": "x"},
            {"RECORD": 31, "FONTIDCOUNT": 1, "SIZE1": 10, "FONTNAME1": "Times New Roman", "SNAPGRIDSIZE": 10},
            # from an entry on the connector's right edge (x=3000) to a label at the free end
            wire((3000, 2000), (3600, 2000)), label("SIG", 3600, 2000, just=2),
            # from a signal harness to a label at the free end
            wire((1500, 1500), (2000, 1500)), label("SIG2", 1500, 1500),
        ]
        # The connector, its entry and the harness are stored in the
        # "Additional" stream, numbered on their own
        additional = parse_records(records_stream([
            {"HEADER": "x"},
            {"RECORD": 215, **at(2300, 2300), "XSIZE": 70, "YSIZE": 60},
            {"RECORD": 216, "OWNERINDEXADDITIONALLIST": "T", "SIDE": 1, "DISTANCEFROMTOP": 3, "NAME": "SIG"},
            {"RECORD": 218, "LOCATIONCOUNT": 2, "X1": 200, "Y1": 160, "X2": 200, "Y2": 140},
        ]))
        sheet = parse_records(records_stream(recs))
        self.assertIn("dangling_wire", {i["type"] for i in sc.Checker(sc.Sheet(sheet)).run()})
        self.assertEqual(sc.Checker(sc.Sheet(sheet, additional)).run(), [])


class TextBoxTest(unittest.TestCase):
    def test_justification_and_rotation(self):
        b = sc.text_box(0, 0, 100, 10, 0, 0)
        self.assertEqual(b.as_list(), [0, 0, 100, 10])
        self.assertEqual(sc.text_box(0, 0, 100, 10, 0, 2).as_list(), [-100, 0, 0, 10])
        self.assertEqual(sc.text_box(0, 0, 100, 10, 0, 5).as_list(), [-100, -5, 0, 5])
        self.assertEqual(sc.text_box(0, 0, 100, 10, 1, 0).as_list(), [-10, 0, 0, 100])


def exact_wire(*pts):
    """A wire keeping fractions of a mil, as metric sheets store them."""
    r = {"RECORD": 27, "LOCATIONCOUNT": len(pts)}
    for k, (x, y) in enumerate(pts, 1):
        for key, v in at(x, y, "").items():
            r[key[0] + str(k) + key[1:]] = v
    return r


class SlantedWireTest(unittest.TestCase):
    def test_wire_to_an_off_grid_pin_is_slanted(self):
        # U1's pin ends 0.0866 mil (2.2 um) above the grid, as on a metric
        # symbol whose pins were rounded
        hot_y = 1000.0866
        recs = [
            {"HEADER": "x"},
            {"RECORD": 31, "FONTIDCOUNT": 1, "SIZE1": 10, "FONTNAME1": "Times New Roman", "SNAPGRIDSIZE": 10},
            {"RECORD": 1, "LIBREFERENCE": "CONN", **at(2000, 1000), "CURRENTPARTID": 1},
            {"RECORD": 2, "OWNERINDEX": 1, "OWNERPARTID": 1, "PINCONGLOMERATE": 2, "PINLENGTH": 20,
             **at(2000, hot_y), "DESIGNATOR": "1"},
            {"RECORD": 34, "OWNERINDEX": 1, **at(2100, 1300), "TEXT": "J1", "NAME": "Designator", "FONTID": 1},
            exact_wire((1800, hot_y), (1000, 1000)), label("A", 1000, 1000),
            # a deliberate diagonal and a wire a fraction of a micron out of level: not reported
            exact_wire((3000, 3000), (3200, 3200)), label("B", 3000, 3000), label("C", 3200, 3200),
            exact_wire((3000, 2000), (4000, 2000.001)), label("D", 3000, 2000), label("E", 4000, 2000.001),
        ]
        issues = [i for i in sc.Checker(sc.Sheet(parse_records(records_stream(recs)))).run()
                  if i["type"] == "slanted_wire"]
        self.assertEqual(len(issues), 1)
        self.assertEqual((issues[0]["designator"], issues[0]["pin"]), ("J1", "1"))
        # keeps the pin end and moves the far end level with it, unrounded
        self.assertEqual(issues[0]["suggest"], {"move_end": [1000, 1000], "to": [1000, hot_y]})


class ProjectTest(unittest.TestCase):
    def test_project_sheets(self):
        with tempfile.TemporaryDirectory() as tmp:
            prj = os.path.join(tmp, "a.PrjPcb")
            with open(prj, "w") as f:
                f.write("[Document1]\nDocumentPath=Sub\\A.SchDoc\n[Document2]\nDocumentPath=B.PcbDoc\n")
            got = sc.project_sheets(prj)
        self.assertEqual([p.name for p in got], ["A.SchDoc"])


if __name__ == "__main__":
    unittest.main()
