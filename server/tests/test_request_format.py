"""The request layout the bridge script's array reader relies on (no Altium needed).

The server writes request.json with json.dump(indent=2) and
ReadJSONStringArray (AltiumScript/json_utils.pas) reads string arrays from
it line by line. read_string_array below follows that function step by
step, so these tests pin down the layout it expects: if the way requests
are written changes, they fail here instead of in Altium.

Run from server/:  python -m unittest tests.test_request_format -v
"""
import json
import re
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "AltiumScript"


def json_unescape(s):
    """JSONUnescape in json_utils.pas."""
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i < len(s) - 1:
            i += 1
            c = s[i]
            if c == "n":
                out.append("\n")
            elif c == "r":
                out.append("\r")
            elif c == "t":
                out.append("\t")
            elif c == "u" and i + 4 < len(s):
                out.append(chr(int(s[i + 1:i + 5], 16)))
                i += 4
            else:
                out.append(c)
        else:
            out.append(c)
        i += 1
    return "".join(out)


def read_string_array(lines, key_line):
    """ReadJSONStringArray in json_utils.pas: (elements, index of closing line)."""
    items = []
    if "[]" in lines[key_line]:
        return items, key_line
    i = key_line + 1
    while i < len(lines):
        line = lines[i].strip()
        if line in ("]", "],"):
            return items, i
        if line.endswith(","):
            line = line[:-1]
        if len(line) >= 2 and line[0] == '"' and line[-1] == '"':
            line = line[1:-1]
        if line:
            items.append(json_unescape(line))
        i += 1
    return items, i


def request_lines(**params):
    """The request exactly as AltiumBridge writes it."""
    return json.dumps({"command": "x", "request_id": "abc", **params}, indent=2).splitlines()


def read(lines, key):
    key_line = next(i for i, ln in enumerate(lines) if f'"{key}"' in ln)
    return read_string_array(lines, key_line)


class StringArrayTest(unittest.TestCase):
    def test_values_arrive_as_sent(self):
        values = ["R\\E\\S\\E\\T\\", "Docs [rev B]", "A, B", 'say "hi"', "10µF", "D[0]", "a]", "]"]
        lines = request_lines(net_names=values, after="x")
        items, end = read(lines, "net_names")
        self.assertEqual(items, values)
        self.assertIn('"after"', lines[end + 1])

    def test_empty_list_reads_nothing_and_stops_at_its_key(self):
        lines = request_lines(designators=[], max_pads_per_net=40)
        items, end = read(lines, "designators")
        self.assertEqual(items, [])
        self.assertIn('"designators": []', lines[end])

    def test_last_parameter(self):
        items, end = read(request_lines(class_name="C", net_names=["GND", "VCC"]), "net_names")
        self.assertEqual(items, ["GND", "VCC"])

    def test_pipe_strings_keep_their_commas(self):
        pins = ["1|IN+|input|0|100|180", "label|1|0|0|Gain, 10x"]
        self.assertEqual(read(request_lines(pins=pins), "pins")[0], pins)


class BridgeSourceTest(unittest.TestCase):
    def test_no_hand_written_array_loops_left(self):
        # Every array reader goes through ReadJSONStringArray; the old loops
        # stopped at the first line containing ']' and kept JSON escapes
        src = (SCRIPT_DIR / "Altium_API.pas").read_text(encoding="utf-8")
        self.assertNotIn("Pos(']', RequestData[i]) = 0", src)
        self.assertGreaterEqual(len(re.findall(r"ReadJSONStringArray\(RequestData, i, \w+\)", src)), 15)

    def test_reader_defined(self):
        src = (SCRIPT_DIR / "json_utils.pas").read_text(encoding="utf-8")
        self.assertIn("function ReadJSONStringArray(RequestData: TStringList; KeyLine: Integer; "
                      "List: TStringList): Integer;", src)


if __name__ == "__main__":
    unittest.main()
