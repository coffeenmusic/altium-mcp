"""Cross-branch regressions for the November 2026 upstream integration."""

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Dict, Optional
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "server"))
import pcb_layers
import screenshot_target
import symbol_metadata
from altium_files import long_path, write_altium_text
from server.tests.test_symbol_metadata import DESCRIPTION, PARAMETERS


class UpstreamSyncTests(unittest.TestCase):
    def test_visibility_resolves_fork_aliases_against_custom_board_names(self):
        layers = {"copper_layers": [{"name": "Inner Power", "layer_id": "3", "is_displayed": True}],
                  "special_layers": [{"name": "Multi Layer", "layer_id": "74", "is_displayed": True}],
                  "mechanical_layers": [{"name": "3D Body", "layer_id": "v7-model-layer",
                                         "mechanical_number": "13", "is_displayed": False}]}
        names, renamed, missing = pcb_layers.resolve(["eMidLayer2", "eMultiLayer", "mech-13", "eMechanical13"], layers)
        self.assertEqual(names, ["Inner Power", "Multi Layer", "3D Body"])
        self.assertEqual(renamed["eMechanical13"], "3D Body")
        self.assertFalse(missing)
        self.assertEqual(pcb_layers.accepted_names(["eMechanical13", "eMultiLayer"],
            ["3D Body", "Multi-Layer"], renamed), ["Mechanical 13", "Multi-Layer"])
        self.assertEqual(pcb_layers.accepted_names(["3d body"], ["3D Body"], {"3d body": "3D Body"}), ["3D Body"])
        calls, result = pcb_layers.plan(layers, ["Top Layer", "eMechanical13"])
        self.assertEqual(calls, [])
        self.assertFalse(result["success"])
        layers["special_layers"].append({"name": "eMechanical13", "layer_id": "unrelated", "is_displayed": True})
        self.assertEqual(pcb_layers.resolve(["eMechanical13"], layers)[0], ["eMechanical13"])

    def test_bridge_watcher_keeps_normalization_and_unstarted_status(self):
        tree = ast.parse((REPO / "server/main.py").read_text(encoding="utf-8"))
        bridge = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "AltiumBridge")
        method = next(n for n in bridge.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "execute_command")
        sandbox = SimpleNamespace(altium_hung=lambda: False)
        scope = dict(Dict=Dict, Any=Any, Optional=Optional, Callable=Callable, Awaitable=Awaitable,
                     asyncio=asyncio, pcb_layers=pcb_layers, RESPONSE_TIMEOUT=120, LOCK_TIMEOUT=10,
                     ALTIUM_HUNG_ERROR="hung", sandbox=sandbox, _lift_failed_result=lambda response: response)
        exec(compile(ast.Module(body=[method], type_ignores=[]), "bridge", "exec"), scope)

        async def exercise():
            exchange = SimpleNamespace(acquire=AsyncMock(return_value=True), release=Mock())
            owner = SimpleNamespace(_command_lock=asyncio.Lock(), _exchange_lock=exchange,
                ensure_paths=AsyncMock(return_value=None), _execute_command_locked=AsyncMock(),
                _execute_watched=AsyncMock(return_value={"success": True, "result": {"layer": "Multi Layer"}}))
            watcher = AsyncMock()
            result = await scope["execute_command"](owner, "query", {"layers": ["eMultiLayer"]}, watcher=watcher)
            owner._execute_watched.assert_awaited_once_with("query", {"layers": ["Multi-Layer"]}, 120, watcher)
            owner._execute_command_locked.assert_not_awaited()
            self.assertEqual(result["result"]["accepted_layers"], ["Multi-Layer"])
            owner._execute_command_locked.return_value = {"success": True, "result": {}}
            await scope["execute_command"](owner, "set_pcb_layer_visibility", {"layer_names": ["eMechanical13"]})
            owner._execute_command_locked.assert_awaited_once_with("set_pcb_layer_visibility", {"layer_names": ["eMechanical13"]}, 120)
            sandbox.altium_hung = lambda: True
            result = await scope["execute_command"](owner, "query", {})
            self.assertFalse(result["command_started"])
            self.assertEqual(exchange.release.call_count, 3)

        asyncio.run(exercise())

    def test_screenshot_keeps_library_positional_and_new_target_forms(self):
        tree = ast.parse((REPO / "server/main.py").read_text(encoding="utf-8"))
        node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "get_screenshot")
        node.decorator_list = []
        legacy, target = AsyncMock(return_value="legacy"), AsyncMock(return_value="target")
        scope = dict(Context=object, screenshot_target=screenshot_target, json=json,
                     _view_type_screenshot=legacy, _target_screenshot=target)
        exec(compile(ast.Module(body=[node], type_ignores=[]), "screenshot", "exec"), scope)
        self.assertEqual(asyncio.run(scope["get_screenshot"](None, "schlib")), "legacy")
        legacy.assert_awaited_once_with("schlib", None)
        for options in ({"target": "document:Symbols.SchLib"}, {}):
            if options:
                result = asyncio.run(scope["get_screenshot"](None, **options))
            else:
                result = asyncio.run(scope["get_screenshot"](None, "document:Symbols.SchLib"))
            self.assertEqual(result, "target")
            self.assertEqual(target.call_args.args[0].name, "Symbols.SchLib")

    def test_batch_preserves_pin_functions_and_canonical_library_paths(self):
        root = REPO / ".altium-mcp/unit_tests"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temp:
            library, spec = Path(temp) / "Symbols.SchLib", Path(temp) / "symbols.txt"
            library.touch()
            source = f"LIBRARY|{str(library).replace(chr(92), '/')}\nSYMBOL|NEW|{DESCRIPTION}|1\n"
            source += "\n".join(f"PARAM|{name}|{value}" for name, value in PARAMETERS.items())
            source += "\nPINFUNC|1|ETH_MDIO\nEDITSYMBOL|EXISTING\nPINFUNC|2|USART_TX\n"
            write_altium_text(spec, source)
            original = spec.read_bytes()
            prepared, symbols = symbol_metadata.prepare_batch(str(spec))
            self.assertTrue(prepared.startswith("LIBRARY|" + long_path(library) + "\n"))
            self.assertIn("PINFUNC|1|ETH_MDIO", prepared)
            self.assertIn("PINFUNC|2|USART_TX", prepared)
            self.assertEqual(len(symbols), 1)
            self.assertEqual(spec.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
