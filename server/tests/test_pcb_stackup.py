"""What GetPCBLayerStackup (AltiumScript/pcb_utils.pas) must read (no Altium needed).

The script runs only inside Altium, so these tests pin down the source: a
dielectric's type does not say whether it exists, the last copper layer has
none below it, and solder mask is part of the board's thickness.

Run from server/:  python -m unittest tests.test_pcb_stackup -v
"""
import re
import unittest
from pathlib import Path

PCB_UTILS = Path(__file__).resolve().parent.parent / "AltiumScript" / "pcb_utils.pas"


def routine(text, name):
    """One routine of a unit: its header line up to the column-0 'end;'."""
    text = text.replace("\r\n", "\n")
    match = re.search(rf"^function {name}\b.*?^end;", text, re.M | re.S)
    if match is None:
        raise AssertionError(f"{name} not found")
    return match.group(0)


class StackupSourceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = routine(PCB_UTILS.read_text(encoding="latin-1"), "GetPCBLayerStackup")

    def test_walks_the_stack_from_top_to_bottom(self):
        self.assertIn("TheLayerStack.FirstLayer", self.src)
        self.assertIn("TheLayerStack.NextLayer(LayerObject)", self.src)

    def test_type_does_not_decide_whether_a_dielectric_exists(self):
        # Rigid-flex stacks hold FR-4 and polyamide with type 0, eNoDielectric
        self.assertNotIn("eNoDielectric", self.src)

    def test_last_copper_layer_has_no_dielectric_below_it(self):
        # Its object can keep a stale height that is not in the stack
        self.assertIn("(NextLayerObject <> nil) and (DielObj.DielectricHeight > 0)", self.src)

    def test_solder_mask_is_reported_and_counted(self):
        self.assertIn("StackupSurfaceJSON(TheLayerStack.DielectricTop)", self.src)
        self.assertIn("StackupSurfaceJSON(TheLayerStack.DielectricBottom)", self.src)
        self.assertIn("'total_thickness_mils', TotalThickness + SurfaceThickness", self.src)
        self.assertIn("'thickness_without_soldermask_mils', TotalThickness", self.src)


if __name__ == "__main__":
    unittest.main()
