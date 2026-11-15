"""Unit tests for altium_files (pure Python, no Altium needed).

Run from the repo root:  python -m unittest server.tests.test_altium_files -v
or from server/:          python -m unittest tests.test_altium_files -v
"""
import codecs
import ctypes
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from altium_files import long_path, read_altium_text, spec_with_long_library, write_altium_text  # noqa: E402

RESPONSE = '{\r\n  "success": true,\r\n  "result": "10µF"\r\n}\r\n'


class AltiumFilesTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)

    def tearDown(self):
        os.remove(self.path)

    def write_bytes(self, data):
        with open(self.path, "wb") as f:
            f.write(data)

    def test_utf8_with_bom(self):
        # What SaveToFile writes when the ANSI code page is UTF-8 (issue #20)
        self.write_bytes(codecs.BOM_UTF8 + RESPONSE.encode("utf-8"))
        text = read_altium_text(self.path)
        self.assertEqual(json.loads(text)["result"], "10µF")

    def test_no_bom(self):
        # What SaveToFile writes on every other machine: the ANSI code page
        self.write_bytes(RESPONSE.replace("µ", "u").encode("ascii"))
        self.assertEqual(json.loads(read_altium_text(self.path))["result"], "10uF")

    @unittest.skipUnless(ctypes.windll.kernel32.GetACP() == 1252, "needs ANSI code page 1252")
    def test_cp1252_is_not_read_as_utf8(self):
        # 0xB5 alone is invalid UTF-8; reading it as UTF-8 garbles the text
        self.write_bytes(RESPONSE.encode("cp1252"))
        self.assertEqual(json.loads(read_altium_text(self.path))["result"], "10µF")

    def test_line_endings_normalized(self):
        self.write_bytes(b"a\r\nb\r\n")
        self.assertEqual(read_altium_text(self.path), "a\nb\n")

    def test_write_has_bom_and_round_trips(self):
        write_altium_text(self.path, "PART|R1|10µF Ω\n")
        with open(self.path, "rb") as f:
            self.assertTrue(f.read().startswith(codecs.BOM_UTF8))
        self.assertEqual(read_altium_text(self.path), "PART|R1|10µF Ω\n")


class LongPathTest(unittest.TestCase):
    def test_short_names_are_expanded(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "A long board name.PcbDoc")
            open(path, "w").close()
            buf = ctypes.create_unicode_buffer(32768)
            ctypes.windll.kernel32.GetShortPathNameW(path, buf, len(buf))
            if "~" not in os.path.basename(buf.value):
                self.skipTest("8.3 names are off on this volume")
            self.assertEqual(os.path.normcase(long_path(buf.value)), os.path.normcase(os.path.realpath(path)))

    def test_forward_slashes_become_backslashes(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "board.PcbDoc")
            open(path, "w").close()
            got = long_path(path.replace("\\", "/"))
            self.assertNotIn("/", got)
            self.assertEqual(os.path.normcase(got), os.path.normcase(os.path.realpath(path)))
        self.assertEqual(long_path("C:/no such folder/x.PcbDoc"), "C:\\no such folder\\x.PcbDoc")

    def test_missing_and_empty_paths_unchanged(self):
        missing = os.path.join(tempfile.gettempdir(), "NOSUCH~1", "x.PcbDoc")
        self.assertEqual(long_path(missing), missing)
        self.assertEqual(long_path(""), "")


class SpecLibraryTest(unittest.TestCase):
    def test_short_library_path_is_expanded(self):
        with tempfile.TemporaryDirectory() as d:
            lib = os.path.join(d, "A long library name.SchLib")
            open(lib, "w").close()
            buf = ctypes.create_unicode_buffer(32768)
            ctypes.windll.kernel32.GetShortPathNameW(lib, buf, len(buf))
            if "~" not in os.path.basename(buf.value):
                self.skipTest("8.3 names are off on this volume")
            spec = f"library| {buf.value} \nSYMBOL|X|10µF|1\nSYMPARAM|LIBRARY|a/b|1\nEND\n"
            got = spec_with_long_library(spec).split("\n")
            self.assertEqual(os.path.normcase(got[0].split("|")[1]), os.path.normcase(os.path.realpath(lib)))
            self.assertEqual(got[0].split("|")[0], "library")
            self.assertEqual(got[1:], spec.split("\n")[1:])  # other records untouched

    def test_forward_slashes(self):
        got = spec_with_long_library("LIBRARY|C:/no such folder/x.SchLib\nEND\n")
        self.assertEqual(got, "LIBRARY|C:\\no such folder\\x.SchLib\nEND\n")

    def test_long_path_or_no_library_unchanged(self):
        self.assertIsNone(spec_with_long_library("LIBRARY|C:\\no such folder\\x.SchLib\nEND\n"))
        self.assertIsNone(spec_with_long_library("SYMBOL|X|desc|1\nEND\n"))
        self.assertIsNone(spec_with_long_library("LIBRARY|\nEND\n"))


if __name__ == "__main__":
    unittest.main()
