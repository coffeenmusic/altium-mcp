"""Unit tests for eco_dialog's row parsing (no Altium needed).

Run from server/:  python -m unittest tests.test_eco_dialog -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import eco_dialog  # noqa: E402

# As Altium AD25's Engineering Change Order dialog exposes them
ROWS = [
    "  Remove Pins From Nets(2)      ",
    " True Remove R2A-1 from 5V_6 In Board.PcbDoc   ",
    " True Remove R2A-2 from 5V In Board.PcbDoc   ",
    "  Change Net Names(1)      ",
    " True Modify NET_3 -> NET_4 In Board.PcbDoc   ",
    "  Add Component Classes(1)      ",
    " True Add U_Stack To Board.PcbDoc   Class already exists: U_Stack ",
    " False Remove R9 From Board.PcbDoc   ",
]


class ParseTest(unittest.TestCase):
    def test_rows(self):
        rows = eco_dialog.parse_rows(ROWS, "Board.PcbDoc")
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0], {"group": "Remove Pins From Nets", "enabled": True,
                                   "change": "Remove R2A-1 from 5V_6 In Board.PcbDoc", "message": ""})
        self.assertEqual(rows[2]["group"], "Change Net Names")
        self.assertEqual(rows[3]["message"], "Class already exists: U_Stack")
        self.assertFalse(rows[4]["enabled"])
        self.assertEqual([r["message"] for r in rows if r["message"]], ["Class already exists: U_Stack"])


if __name__ == "__main__":
    unittest.main()
