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
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'main.py').read_text(encoding='utf-8'))
        bridge_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'AltiumBridge')
        method = next(n for n in bridge_class.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'execute_command')
        method.decorator_list = []
        namespace = dict(pcb_layers=pcb_layers, asyncio=asyncio, Dict=dict, Any=object,
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


if __name__ == '__main__':
    unittest.main()
