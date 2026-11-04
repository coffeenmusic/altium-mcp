"""Unit tests for pcb_query (pure Python, no Altium needed).

Run from server/:  python -m unittest tests.test_pcb_query -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from pcb_query import bare_numbers, check, with_units  # noqa: E402


class UnitsTest(unittest.TestCase):
    def test_values_are_written_in_both_units(self):
        mm, mil, found = with_units("IsVia And (HoleSize > 15mil) And (Size < 0.6mm)")
        self.assertEqual(mm, "IsVia And (HoleSize > 0.381) And (Size < 0.6)")
        self.assertEqual(mil, "IsVia And (HoleSize > 15) And (Size < 23.622047)")
        self.assertEqual(found, ["15mil", "0.6mm"])

    def test_names_and_bare_numbers_are_left_alone(self):
        e = "InNet('5V_MM') And InComponent('U12') And (Width > 6)"
        self.assertEqual(with_units(e)[:2], (e, e))
        self.assertEqual(bare_numbers(e), ["6"])
        self.assertEqual(bare_numbers("IsTrack And (Width > 6mil)"), [])

    def test_other_units(self):
        self.assertEqual(with_units("(Width > 1in)")[0], "(Width > 25.4)")
        self.assertEqual(with_units("(Width > 100um)")[1], "(Width > 3.937008)")


class CheckTest(unittest.TestCase):
    def test_rejected(self):
        self.assertIn("empty", check("  "))
        self.assertIn("quote", check('InNet("GND")'))
        self.assertIn("dot", check("IsVia And (HoleSize > 0,3)"))
        self.assertEqual(check("IsVia And InNet('GND')"), "")


if __name__ == "__main__":
    unittest.main()
