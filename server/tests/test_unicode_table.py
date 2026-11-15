"""Generated UTF-16 lookup resource used by the native JSON decoder."""

from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from altium_files import ensure_unicode_table


class UnicodeTableTest(unittest.TestCase):
    def test_all_code_units_survive_and_existing_table_is_not_rewritten(self):
        root = Path(__file__).resolve().parents[2] / ".altium-mcp" / "unit_tests"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as folder:
            path = ensure_unicode_table(folder)
            before = path.stat().st_mtime_ns
            data = path.read_bytes()
            self.assertEqual(data[:2], b"\xff\xfe")
            self.assertEqual(struct.unpack("<65280H", data[2:]), tuple(range(256, 65536)))
            text = data[2:].decode("utf-16-le", errors="surrogatepass")
            for code in (256, 937, 1046, 55357, 56832, 65535):
                offset = 2 + (code - 256) * 2
                unit = data[offset:offset + 2].decode("utf-16-le", errors="surrogatepass")
                self.assertEqual(ord(unit), code)
            self.assertNotIn("\n", text)
            self.assertNotIn("\r", text)
            self.assertEqual(ensure_unicode_table(folder), path)
            self.assertEqual(path.stat().st_mtime_ns, before)
            path.write_bytes(b"invalid")
            ensure_unicode_table(folder)
            self.assertEqual(path.read_bytes(), data)


if __name__ == "__main__":
    unittest.main()
