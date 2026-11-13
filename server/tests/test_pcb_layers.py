"""Unit tests for pcb_layers (pure Python, no Altium needed).

Run from server/:  python -m unittest tests.test_pcb_layers -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import pcb_layers  # noqa: E402


def layer(name, layer_id, shown=True):
    return {"name": name, "layer_id": str(layer_id), "is_displayed": shown}


# A four-layer board as get_pcb_layers reports it: inner layers renamed
LAYERS = {
    "copper_layers": [layer("Top Layer", 1), layer("Inner 1", 2), layer("Inner 2", 3), layer("Bottom Layer", 32)],
    "mechanical_layers": [layer("Mechanical 1", 16908289), layer("Board Outline", 16908290, False)],
    "special_layers": [layer("Top Overlay", 33), layer("Bottom Overlay", 34), layer("Top Solder Mask", 37),
                       layer("Bottom Solder Mask", 38), layer("Top Paste", 35), layer("Bottom Paste", 36),
                       layer("Drill Guide", 55, False), layer("Drill Drawing", 73, False),
                       layer("Multi Layer", 74), layer("Keep Out Layer", 56)],
}


class ResolveTest(unittest.TestCase):
    def test_altium_spellings(self):
        asked = ["Top Layer", "Mechanical 1", "Multi-Layer", "Top Solder", "Bottom Solder", "Keep-Out Layer",
                 "KeepOut", "MultiLayer", "top_overlay", "bottom paste"]
        found, renamed, unknown = pcb_layers.resolve(asked, LAYERS)
        self.assertEqual(unknown, [])
        self.assertEqual(found, ["Top Layer", "Mechanical 1", "Multi Layer", "Top Solder Mask",
                                 "Bottom Solder Mask", "Keep Out Layer", "Top Overlay", "Bottom Paste"])
        self.assertEqual(renamed["Multi-Layer"], "Multi Layer")
        self.assertNotIn("Top Layer", renamed)

    def test_renamed_copper_by_altium_name(self):
        found, renamed, _ = pcb_layers.resolve(["Mid Layer 1", "Mid-Layer 2", "inner 1"], LAYERS)
        self.assertEqual(found, ["Inner 1", "Inner 2"])
        self.assertEqual(renamed, {"Mid Layer 1": "Inner 1", "Mid-Layer 2": "Inner 2", "inner 1": "Inner 1"})

    def test_unknown_names_with_suggestions(self):
        found, _, unknown = pcb_layers.resolve(["Top Layer", "Botom Layer", "Mid Layer 3", "Silk"], LAYERS)
        self.assertEqual(found, ["Top Layer"])
        self.assertEqual([u["name"] for u in unknown], ["Botom Layer", "Mid Layer 3", "Silk"])
        self.assertIn("Bottom Layer", unknown[0]["suggestions"])

    def test_displayed(self):
        state = pcb_layers.displayed(LAYERS)
        self.assertEqual(len(state), 16)
        self.assertTrue(state["Multi Layer"])
        self.assertFalse(state["Board Outline"])
        self.assertEqual(pcb_layers.displayed([]), {})


if __name__ == "__main__":
    unittest.main()


class PlanTest(unittest.TestCase):
    def test_strict_unknown_name_changes_nothing(self):
        calls, out = pcb_layers.plan(LAYERS, ["Top Layer", "Multi-Layer", "Top Silk"], True, True)
        self.assertEqual(calls, [])
        self.assertFalse(out["success"])
        self.assertEqual([u["name"] for u in out["unresolved"]], ["Top Silk"])
        self.assertIn("Multi Layer", out["valid_names"])
        self.assertTrue(out["previous_state"]["Drill Guide"] is False)

    def test_exclusive_show_with_altium_spellings(self):
        calls, out = pcb_layers.plan(LAYERS, ["Top Layer", "Multi-Layer"], True, True)
        self.assertEqual(calls, [{"layer_names": ["Top Layer", "Multi Layer"], "visible": True, "exclusive": True}])
        self.assertEqual(out["resolved_names"], {"Multi-Layer": "Multi Layer"})
        self.assertEqual(len(out["previous_state"]), 16)

    def test_not_strict_sends_unknown_names_as_before(self):
        calls, out = pcb_layers.plan(LAYERS, ["Top Layer", "Top Silk"], True, True, strict=False)
        self.assertEqual(calls[0]["layer_names"], ["Top Layer", "Top Silk"])
        self.assertNotIn("success", out)

    def test_every_layer(self):
        calls, _ = pcb_layers.plan(LAYERS, ["*"], True)
        self.assertEqual(len(calls[0]["layer_names"]), 16)

    def test_restore_previous_state(self):
        before = pcb_layers.displayed(LAYERS)
        calls, _ = pcb_layers.plan(LAYERS, restore_state=before)
        self.assertEqual(calls[0], {"layer_names": [n for n, v in before.items() if v],
                                    "visible": True, "exclusive": False})
        self.assertEqual(calls[1], {"layer_names": ["Board Outline", "Drill Guide", "Drill Drawing"],
                                    "visible": False, "exclusive": False})
        # A layer gone since the snapshot is skipped; the rest is restored
        calls, out = pcb_layers.plan(LAYERS, restore_state={"Nope": True, "Top Layer": False})
        self.assertEqual(calls, [{"layer_names": ["Top Layer"], "visible": False, "exclusive": False}])
        self.assertEqual([u["name"] for u in out["unresolved"]], ["Nope"])
        self.assertNotIn("success", out)

    def test_exact_name_wins(self):
        layers = {"mechanical_layers": [layer("Mechanical 1", 16908289), layer("Mechanical1", 16908290)]}
        self.assertEqual(pcb_layers.resolve(["Mechanical1"], layers)[0], ["Mechanical1"])

    def test_bad_input(self):
        self.assertFalse(pcb_layers.plan(LAYERS, [])[1]["success"])
        self.assertFalse(pcb_layers.plan(LAYERS, restore_state=["Top Layer"])[1]["success"])
        self.assertFalse(pcb_layers.plan([], ["Top Layer"])[1]["success"])
        self.assertEqual(pcb_layers.plan(LAYERS, "Top Layer")[0][0]["layer_names"], ["Top Layer"])
