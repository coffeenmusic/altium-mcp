"""Unit tests for variants and file_locks (pure Python, no Altium needed).

Run from server/:  python -m unittest tests.test_variants -v
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import file_locks  # noqa: E402
import variants  # noqa: E402

# Laid out as Altium writes it: each variation's parameter rows right under it
PRJ = "\r\n".join([
    "[Design]",
    "CurrentVariant=Original",
    "",
    "[ProjectVariant1]",
    "UniqueId=aaaa",
    "Description=Original",
    "AllowFabrication=0",
    "ParameterCount=0",
    "VariationCount=2",
    r"Variation1=Designator=R1_CH1|UniqueId=\SYMA\COMPR|Kind=2|AlternatePart=X100|AltLibLink_Footprint=F",
    "ParamVariation1=ParameterName=Comment|VariantValue=X100",
    "ParamDesignator1=R1_CH1",
    "ParamVariation2=ParameterName=Value|VariantValue=100",
    "ParamDesignator2=R1_CH1",
    r"Variation2=Designator=C1|UniqueId=\COMPC|Kind=1|AlternatePart=",
    "ParamVariationCount=2",
    "",
    "[ProjectVariant2]",
    "UniqueId=bbbb",
    "Description=Flight",
    "AllowFabrication=0",
    "ParameterCount=0",
    "VariationCount=1",
    r"Variation1=Designator=C1|UniqueId=\COMPC|Kind=0|AlternatePart=10uF",
    "ParamVariation1=ParameterName=Comment|VariantValue=10uF",
    "ParamDesignator1=C1",
    "ParamVariationCount=1",
    "",
    "[Parameter1]",
    "Name=X",
    "",
])

TARGETS = variants.parse_targets([
    r"R1_CH1|R1|\SYMA\COMPR|Amp.SchDoc",
    r"R1_CH2|R1|\SYMB\COMPR|Amp.SchDoc",
    r"R2_CH1|R2|\SYMA\COMPR2|Amp.SchDoc",
    r"C1|C1|\COMPC|Power.SchDoc",
])


class ParseTest(unittest.TestCase):
    def test_variants_and_params(self):
        vs = variants.parse_variants(PRJ)
        self.assertEqual([v["name"] for v in vs], ["Original", "Flight"])
        r1 = vs[0]["variations"][0]
        self.assertEqual((r1["designator"], r1["kind"], r1["alternate_part"]), ("R1_CH1", "alternate", "X100"))
        self.assertEqual(r1["params"], {"Comment": "X100", "Value": "100"})
        self.assertEqual(vs[0]["variations"][1]["kind"], "not_fitted")
        self.assertEqual(vs[1]["variations"][0], {"designator": "C1", "unique_id": "\\COMPC", "kind": "none",
                                                  "alternate_part": "10uF", "params": {"Comment": "10uF"}})

    def test_rows_past_counts_ignored(self):
        text = PRJ.replace("ParamVariationCount=1\r\n",
                           "ParamVariationCount=1\r\nParamVariation2=ParameterName=Old|VariantValue=1\r\n"
                           "ParamDesignator2=C1\r\nVariation2=Designator=R9|UniqueId=\\X|Kind=1\r\n")
        self.assertEqual(variants.parse_variants(text)[1]["variations"][0]["params"], {"Comment": "10uF"})
        self.assertEqual(len(variants.parse_variants(text)[1]["variations"]), 1)

    def test_targets_and_channels(self):
        self.assertEqual(TARGETS[1]["channel"], "CH2")
        self.assertEqual(TARGETS[3]["channel"], "")
        self.assertEqual(variants.channel_of("Rtop1_U_CL2", "Rtop1"), "U_CL2")
        self.assertEqual(variants.channel_of("R1A", "R1"), "A")


class ResolveTest(unittest.TestCase):
    def test_physical_logical_and_channels(self):
        self.assertEqual([t["designator"] for t in variants.resolve(TARGETS, "r1_ch2")[0]], ["R1_CH2"])
        self.assertEqual([t["designator"] for t in variants.resolve(TARGETS, "R2")[0]], ["R2_CH1"])
        self.assertEqual([t["designator"] for t in variants.resolve(TARGETS, "R1", "ch2")[0]], ["R1_CH2"])
        self.assertEqual(len(variants.resolve(TARGETS, "R1", "*")[0]), 2)
        self.assertEqual(len(variants.resolve(TARGETS, "R1", "CH[12]")[0]), 2)

    def test_errors(self):
        hits, err = variants.resolve(TARGETS, "R1")
        self.assertEqual(hits, [])
        self.assertIn("2 channels (CH1, CH2)", err)
        self.assertIn("no channel CH9", variants.resolve(TARGETS, "R1", "CH9")[1])
        self.assertIn("no component R7", variants.resolve(TARGETS, "R7")[1])


class EncodeTest(unittest.TestCase):
    def encode(self, ops, default=""):
        return variants.encode_operations(variants.parse_variants(PRJ), TARGETS, ops, default)

    def test_kinds_and_params(self):
        lines, errors, plan = self.encode([
            {"op": "not_fitted", "designator": "R1", "channel": "CH2"},
            {"op": "set_param", "designator": "R2_CH1", "name": "Comment", "value": "4k7"},
            {"op": "fitted", "designator": "R1_CH1"},
            {"op": "fitted", "designator": "R2_CH1", "variant": "Flight"},
        ], "Original")
        self.assertEqual(errors, [])
        self.assertEqual(lines, ["KIND|Original|\\SYMB\\COMPR|R1_CH2|1",
                                 "PARAM|Original|\\SYMA\\COMPR2|R2_CH1|Comment|4k7",
                                 "DEL|Original|\\SYMA\\COMPR"])
        self.assertEqual(plan[2]["dropped"]["kind"], "alternate")
        self.assertIn("already fitted", plan[3]["note"])

    def test_variant_ops_and_default(self):
        lines, errors, _ = self.encode([
            {"op": "add_variant", "name": "Proto"},
            {"op": "not_fitted", "designator": "C1", "variant": "proto"},
            {"op": "rename_variant", "variant": "Proto", "name": "Proto 2"},
            {"op": "set_param", "designator": "C1", "variant": "Proto 2", "name": "Comment", "value": "1u"},
        ])
        self.assertEqual(errors, [])
        self.assertEqual(lines, ["NEW|Proto", "KIND|Proto|\\COMPC|C1|1", "RENAME|Proto|Proto 2",
                                 "PARAM|Proto 2|\\COMPC|C1|Comment|1u"])
        _, errors, _ = self.encode([{"op": "not_fitted", "designator": "C1"}])
        self.assertIn("give variant", errors[0])

    def test_remove_param_removes_only_that_parameter(self):
        lines, errors, _ = self.encode([
            {"op": "set_param", "designator": "C1", "name": "Note", "value": "x"},
            {"op": "remove_param", "designator": "C1", "name": "Comment"},
        ], "Flight")
        self.assertEqual(errors, [])
        self.assertEqual(lines[1:], ["UNPARAM|Flight|\\COMPC|Comment"])
        lines, _, _ = self.encode([{"op": "remove_param", "designator": "C1", "name": "Comment"}], "Flight")
        self.assertEqual(lines, ["DEL|Flight|\\COMPC"])        # nothing left: the variation goes

    def test_remove_param_on_an_alternate_part(self):
        lines, errors, plan = self.encode([{"op": "remove_param", "designator": "R1_CH1", "name": "Value"},
                                           {"op": "remove_param", "designator": "R1_CH1", "name": "Comment"}],
                                          "Original")
        self.assertEqual(errors, [])
        self.assertEqual(lines, ["UNPARAM|Original|\\SYMA\\COMPR|Value", "UNPARAM|Original|\\SYMA\\COMPR|Comment"])
        self.assertNotIn("note", plan[0])
        self.assertIn("alternate part", plan[1]["note"])

    def test_refusals(self):
        _, errors, _ = self.encode([{"op": "set_param", "designator": "C1", "name": "A|B", "value": "1"}], "Flight")
        self.assertIn("'|'", errors[0])
        _, errors, _ = self.encode([{"op": "add_variant", "name": "flight"}])
        self.assertIn("taken", errors[0])
        _, errors, _ = self.encode([{"op": "swap"}])
        self.assertIn("unknown op", errors[0])
        _, errors, _ = self.encode(["not_fitted", None])
        self.assertEqual(len(errors), 2)
        self.assertIn("dict", errors[0])

    def test_shared_designators(self):
        text = PRJ.replace("Variation2=Designator=C1|", "Variation2=Designator=R1_CH1|")
        self.assertEqual(variants.shared_designators(variants.parse_variants(text)), {"Original": ["R1_CH1"]})
        self.assertEqual(variants.shared_designators(variants.parse_variants(PRJ)), {})


class NormalizeTest(unittest.TestCase):
    # As Altium saves after the first variation was removed through the API:
    # keys updated where they stood, the old last rows left past the counts
    SHIFTED = "\r\n".join([
        "[ProjectVariant1]",
        "Description=Original",
        "VariationCount=2",
        r"Variation1=Designator=C1|UniqueId=\COMPC|Kind=1",
        "ParamVariation1=ParameterName=Comment|VariantValue=Y",
        "ParamDesignator1=R2",
        r"Variation2=Designator=R2|UniqueId=\COMPR2|Kind=2",
        "ParamVariation2=ParameterName=Value|VariantValue=2",
        "ParamDesignator2=R2",
        "ParamVariationCount=2",
        "ParamVariation3=ParameterName=Old|VariantValue=1",
        "ParamDesignator3=R2",
        r"Variation3=Designator=R9|UniqueId=\X|Kind=1",
        "",
        "[Other]",
        "A=1",
        "",
    ])

    def test_layout_problems_found_and_fixed(self):
        problems = variants.layout_problems(self.SHIFTED)
        self.assertEqual(problems, [{"section": "ProjectVariant1", "misplaced_rows": 1, "rows_past_counts": 3,
                                     "duplicate_designators": False}])
        fixed, report = variants.normalize(self.SHIFTED)
        self.assertTrue(report[0]["rewritten"])
        self.assertEqual(fixed, "\r\n".join([
            "[ProjectVariant1]",
            "Description=Original",
            "VariationCount=2",
            r"Variation1=Designator=C1|UniqueId=\COMPC|Kind=1",
            r"Variation2=Designator=R2|UniqueId=\COMPR2|Kind=2",
            "ParamVariation1=ParameterName=Comment|VariantValue=Y",
            "ParamDesignator1=R2",
            "ParamVariation2=ParameterName=Value|VariantValue=2",
            "ParamDesignator2=R2",
            "ParamVariationCount=2",
            "",
            "[Other]",
            "A=1",
            "",
        ]))
        self.assertEqual(variants.layout_problems(fixed), [])
        self.assertEqual(variants.parse_variants(fixed), variants.parse_variants(self.SHIFTED))

    def test_altium_layout_left_alone(self):
        self.assertEqual(variants.layout_problems(PRJ), [])
        self.assertEqual(variants.normalize(PRJ), (PRJ, []))

    def test_shared_designators_not_regrouped(self):
        text = self.SHIFTED.replace("Variation1=Designator=C1|", "Variation1=Designator=R2|")
        fixed, report = variants.normalize(text.replace("ParamDesignator1=R2", "ParamDesignator1=C1"))
        self.assertIn("left_alone", report[0])

    def test_shared_designators_with_rows_past_counts_left_alone(self):
        # Two R? variations, nothing misplaced, a stale row past the counts:
        # regrouping would hand every row to the first R? and the second
        # would lose its Comment
        text = "\r\n".join([
            "[ProjectVariant1]", "Description=Proto", "VariationCount=2",
            r"Variation1=Designator=R?|UniqueId=\A|Kind=0",
            "ParamVariation1=ParameterName=Value|VariantValue=1k", "ParamDesignator1=R?",
            r"Variation2=Designator=R?|UniqueId=\B|Kind=0",
            "ParamVariation2=ParameterName=Comment|VariantValue=X", "ParamDesignator2=R?",
            "ParamVariationCount=2", "ParamVariation3=ParameterName=Old|VariantValue=1", "ParamDesignator3=R?", ""])
        fixed, report = variants.normalize(text)
        self.assertEqual(fixed, text)
        self.assertIn("left_alone", report[0])

    def test_only_the_edited_variants(self):
        text = self.SHIFTED.replace("[Other]", "[ProjectVariant2]\r\nDescription=Flight\r\nVariationCount=1\r\n"
                                               "Variation1=Designator=C1|UniqueId=\\COMPC|Kind=1\r\n"
                                               "Variation2=Designator=R5|UniqueId=\\X|Kind=1\r\n\r\n[Other]")
        fixed, report = variants.normalize(text, ["Flight"])
        self.assertEqual([r["section"] for r in report], ["ProjectVariant2"])
        self.assertIn("Variation3=Designator=R9", fixed)          # Original untouched
        self.assertNotIn("Variation2=Designator=R5", fixed)
        self.assertEqual(variants.normalize(text, []), (text, []))

    def test_non_ascii_bytes_survive(self):
        # U+00C5 is C3 85 in UTF-8: read as latin-1 the 0x85 is U+0085, which
        # str.splitlines would treat as a line break
        raw = ("[Document1]\r\nDocumentPath=\u00c5rhus.SchDoc\r\n\r\n" + self.SHIFTED).encode("utf-8")
        text = raw.decode("latin-1")
        fixed, report = variants.normalize(text)
        self.assertTrue(report[0]["rewritten"])
        out = fixed.encode("latin-1")
        out.decode("utf-8")                                        # still valid UTF-8
        self.assertTrue(out.startswith("[Document1]\r\nDocumentPath=\u00c5rhus.SchDoc\r\n".encode("utf-8")))
        self.assertEqual(variants.split_lines("a\x85b\r\nc\n"), ["a\x85b", "c"])

    def test_edited_variants(self):
        lines = ["KIND|Original|\\U|R1|1", "NEW|Proto", "RENAME|Proto|Proto 2", "PARAM|flight|\\U|R1|A|1",
                 "RENAME|Original|Base"]
        self.assertEqual(variants.edited_variants(lines), ["Base", "Proto 2", "flight"])


class ReplaceFileTest(unittest.TestCase):
    def test_written_and_old_file_kept_when_the_write_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "p.PrjPcb")
            with open(path, "wb") as f:
                f.write(b"old")
            variants.replace_file(path, b"new")
            with open(path, "rb") as f:
                self.assertEqual(f.read(), b"new")
            with mock.patch.object(variants.os, "replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    variants.replace_file(path, b"newer")
            with open(path, "rb") as f:
                self.assertEqual(f.read(), b"new")
            self.assertEqual(os.listdir(tmp), ["p.PrjPcb"])


class DescribeTest(unittest.TestCase):
    def test_logical_channel_and_filters(self):
        out = variants.describe(variants.parse_variants(PRJ), TARGETS, designators=["R1"])
        self.assertEqual(out[0]["variations"], [{"designator": "R1_CH1", "kind": "alternate", "logical": "R1",
                                                 "channel": "CH1", "sheet": "Amp.SchDoc",
                                                 "alternate_part": "X100",
                                                 "params": {"Comment": "X100", "Value": "100"}}])
        self.assertEqual(out[1]["variations"], [])
        self.assertEqual([v["name"] for v in variants.describe(variants.parse_variants(PRJ), TARGETS, "flight")],
                         ["Flight"])


class DbLibSourceTest(unittest.TestCase):
    def write(self, folder, conn, extra=""):
        p = os.path.join(folder, "Lib.DbLib")
        with open(p, "w", encoding="utf-8") as f:
            f.write(f"[OutlookSettings]\nConnectionString={conn}\n{extra}")
        return p

    def test_absolute_relative_and_moved(self):
        with tempfile.TemporaryDirectory() as d:
            xl = os.path.join(d, "Parts.xlsx")
            open(xl, "w").close()
            conn = 'Provider=Microsoft.ACE.OLEDB.12.0;Data Source={};Extended Properties="Excel 12.0 Xml;HDR=Yes"'
            self.assertEqual(file_locks.dblib_source(self.write(d, conn.format(xl))), xl)
            self.assertEqual(file_locks.dblib_source(self.write(d, conn.format("Parts.xlsx"))), xl)
            moved = conn.format(r"Z:\gone\Parts.xlsx")
            self.assertEqual(file_locks.dblib_source(self.write(d, moved, "DatabasePathRelative=1\n")), xl)
            self.assertEqual(file_locks.dblib_source(self.write(d, moved)), r"Z:\gone\Parts.xlsx")
            self.assertIsNone(file_locks.dblib_source(self.write(d, "Provider=SQLOLEDB;Data Source=server1")))

    def test_lock_report_on_free_and_held_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "Parts.xlsx")
            open(p, "w").close()
            report = file_locks.lock_report(p)
            self.assertTrue(report["writable"])
            self.assertEqual(report["held_by"], [])
            with open(p, "r+"):
                held = file_locks.lock_report(p)
            self.assertFalse(held["writable"])
            self.assertTrue(any(h["pid"] == os.getpid() for h in held["held_by"]))
            self.assertEqual(held["reason"], "in_use")
            self.assertIn("advice", held)

    def test_read_only_told_apart(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "Parts.xlsx")
            open(p, "w").close()
            os.chmod(p, 0o444)
            try:
                report = file_locks.lock_report(p)
            finally:
                os.chmod(p, 0o666)
            self.assertFalse(report["writable"])
            self.assertEqual(report["reason"], "read_only")
            self.assertIn("read-only", report["advice"])


if __name__ == "__main__":
    unittest.main()
