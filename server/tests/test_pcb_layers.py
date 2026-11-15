"""Layer spelling across JSON commands, library specs and native readback."""

import ast
import asyncio
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pcb_layers
import footprint_editor


class PcbLayersTest(unittest.TestCase):
    def test_shared_aliases_and_legacy_limits(self):
        cases = {"Multi-Layer": ("Multi Layer", "multi-layer", "eMultiLayer", "multilayer", " MULTI_LAYER "),
                 "Mechanical 13": ("Mechanical13", "eMechanical13", "Mechanical Layer 13", "mech-13"),
                 "Top Layer": ("eTopLayer", "top-layer"), "Top Solder Mask": ("eTopSolder",),
                 "Mid Layer 3": ("eMidLayer3",), "No Layer": ("eNoLayer", "No-Layer")}
        for expected, aliases in cases.items():
            for alias in aliases:
                with self.subTest(alias=alias):
                    self.assertEqual(pcb_layers.canonical_name(alias), expected)
                    if expected != "No Layer":
                        self.assertEqual(footprint_editor.layer_name(alias), expected)
        self.assertEqual(pcb_layers.canonical_name("Mechanical Layer 1024"), "Mechanical 1024")
        self.assertEqual(pcb_layers.canonical_name("Assembly custom name"), "Assembly custom name")
        for invalid in ("Mechanical0", "Mechanical1025", "a|b", "a\nb", ""):
            with self.assertRaises(ValueError):
                pcb_layers.canonical_name(invalid)
        with self.assertRaises(ValueError):
            footprint_editor.layer_name("Mechanical17")
        with self.assertRaises(ValueError):
            footprint_editor.layer_name("eNoLayer")

    def test_copy_normalizes_only_layer_fields_including_embedded_json(self):
        payload = dict(layer="Multi Layer", layers=["eMechanical13"], note="Multi Layer",
                       model_file=r"C:\Multi Layer\a.step", changes=dict(to_layer="eTopLayer"))
        copied = pcb_layers.normalize_fields(payload)
        self.assertEqual(copied['layer'], "Multi-Layer")
        self.assertEqual(copied['layers'], ["Mechanical 13"])
        self.assertEqual(copied['changes']['to_layer'], "Top Layer")
        self.assertEqual((payload['layer'], copied['note']), ("Multi Layer", "Multi Layer"))
        self.assertEqual(copied['model_file'], payload['model_file'])
        result = pcb_layers.normalize_fields(dict(result=json.dumps(payload)))
        self.assertEqual(json.loads(result['result'])['layer'], 'Multi-Layer')
        self.assertEqual(pcb_layers.normalize_fields(dict(layer="")), dict(layer=""))

    def test_real_bridge_boundary_canonicalizes_and_returns_accepted_layers(self):
        from typing import Optional, Callable, Awaitable
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'main.py').read_text(encoding='utf-8'))
        bridge_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'AltiumBridge')
        method = next(n for n in bridge_class.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'execute_command')
        method.decorator_list = []
        namespace = dict(pcb_layers=pcb_layers, asyncio=asyncio, Dict=dict, Any=object,
                         Optional=Optional, Callable=Callable, Awaitable=Awaitable,
                         RESPONSE_TIMEOUT=120, LOCK_TIMEOUT=10, sandbox=Mock(altium_hung=lambda: False),
                         _lift_failed_result=lambda r: r)
        exec(compile(ast.Module(body=[method], type_ignores=[]), 'bridge_method', 'exec'), namespace)
        async def exercise():
            owner = Mock()
            owner._command_lock = asyncio.Lock()
            owner.ensure_paths = AsyncMock(return_value=None)
            owner._exchange_lock.acquire = AsyncMock(return_value=True)
            owner._execute_command_locked = AsyncMock(return_value=dict(success=True, result=dict(
                layer='Multi Layer', not_found_layers=['Mechanical 15'])))
            result = await namespace['execute_command'](owner, 'test', dict(layers=['eMultiLayer', 'eMechanical15']))
            self.assertEqual(owner._execute_command_locked.call_args.args[1]['layers'], ['Multi-Layer', 'Mechanical 15'])
            self.assertEqual(result['result']['accepted_layers'], ['Multi-Layer'])
            self.assertEqual(result['result']['layer'], 'Multi-Layer')
            owner._exchange_lock.release.assert_called_once()
        asyncio.run(exercise())




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


if __name__ == "__main__":
    unittest.main()
