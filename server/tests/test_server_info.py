"""Build identity and live MCP diagnostics, without importing the Altium bridge."""

import ast
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest

from server.server_info import capture_runtime_info, get_build_info

try:
    import anyio
    from mcp import ClientSession
    from mcp.server.fastmcp import FastMCP, Context
    from mcp.shared.memory import create_client_server_memory_streams
    from mcp.types import ToolAnnotations
except ImportError:
    FastMCP = None


REPO = Path(__file__).resolve().parents[2]


class ServerInfoTest(unittest.TestCase):
    def setUp(self):
        temp_root = REPO / ".altium-mcp" / "unit_tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temp_root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "server" / "AltiumScript").mkdir(parents=True)
        self.metadata = self.root / "pyproject.toml"
        self.metadata.write_text('[project]\nversion = "1.2.3"\n'
                                 '[tool.other]\nversion = "9.9.9"\n', encoding="utf-8")
        self.main_file = self.root / "server" / "main.py"
        self.main_file.write_text("# Initial runtime source\n", encoding="utf-8")

    def test_version_comes_from_project_metadata(self):
        self.assertEqual(get_build_info(self.root)["server_version"], "1.2.3")
        self.metadata.write_text('[tool.other]\nversion = "9.9.9"\n', encoding="utf-8")
        self.assertEqual(get_build_info(self.root)["server_version"], "unknown")
        self.metadata.unlink()
        self.assertEqual(get_build_info(self.root)["server_version"], "unknown")

    def test_fingerprint_tracks_runtime_files_and_ignores_nonruntime_files(self):
        original = get_build_info(self.root)["source_sha256"]
        (self.root / "README.md").write_text("Updated documentation", encoding="utf-8")
        (self.root / "server" / "altium_mcp.log").write_text("New log entry", encoding="utf-8")
        (self.root / "server" / "config.json").write_text("{}", encoding="utf-8")
        (self.root / "server" / "tests").mkdir()
        (self.root / "server" / "tests" / "test_example.py").write_text("pass", encoding="utf-8")
        self.assertEqual(get_build_info(self.root)["source_sha256"], original)
        self.main_file.write_text("# Updated runtime source\n", encoding="utf-8")
        updated = get_build_info(self.root)["source_sha256"]
        self.assertNotEqual(updated, original)
        (self.root / "server" / "AltiumScript" / "test.pas").write_text("begin end;", encoding="utf-8")
        self.assertNotEqual(get_build_info(self.root)["source_sha256"], updated)
        self.assertEqual(get_build_info(self.root)["server_version"], "1.2.3")

    def load_status(self, protocol=False):
        # Extract the real diagnostic setup/tool, avoiding Windows bridge side effects.
        tree = ast.parse((REPO / "server" / "main.py").read_text(encoding="utf-8"))
        selected = []
        for node in tree.body:
            if isinstance(node, ast.Assign):
                target = node.targets[0]
                if isinstance(target, ast.Name) and target.id in (
                    "SERVER_INFO", "SERVER_STARTED_MONOTONIC", "mcp", "READ_ONLY"
                ):
                    if protocol or target.id != "mcp":
                        selected.append(node)
                elif protocol and isinstance(target, ast.Attribute) and target.attr == "version":
                    selected.append(node)
            elif protocol and isinstance(node, ast.FunctionDef) and node.name == "_tool":
                selected.append(node)
            elif isinstance(node, ast.AsyncFunctionDef) and node.name == "get_server_status":
                if not protocol:
                    node.decorator_list = []
                selected.append(node)
        scope = {"capture_runtime_info": lambda: capture_runtime_info(self.root),
                 "time": time, "json": json, "os": os, "AGENT_INSTRUCTIONS": "",
                 "FastMCP": FastMCP, "Context": Context if FastMCP else object,
                 "ToolAnnotations": ToolAnnotations if FastMCP else None,
                 "altium_bridge": SimpleNamespace(config=SimpleNamespace(
                     altium_exe_path=str(self.root / "missing.exe"), script_path=str(self.metadata),
                     discover_altium=lambda: False))}
        exec(compile(ast.Module(body=selected, type_ignores=[]), "server_status", "exec"), scope)
        return scope

    def test_status_keeps_startup_identity_after_files_change(self):
        import asyncio
        scope = self.load_status()
        old_info = dict(scope["SERVER_INFO"])
        self.metadata.write_text('[project]\nversion = "2.0.0"\n', encoding="utf-8")
        live = json.loads(asyncio.run(scope["get_server_status"](None)))
        self.assertEqual(live["server"], "Running")
        self.assertEqual(live["server_version"], "1.2.3")
        self.assertEqual(live["source_sha256"], old_info["source_sha256"])
        self.assertNotEqual(live["source_sha256"], get_build_info(self.root)["source_sha256"])
        self.assertEqual(live["pid"], os.getpid())
        self.assertEqual(live["python_executable"], sys.executable)
        self.assertIsNotNone(datetime.fromisoformat(live["started_at"]).tzinfo)
        self.assertGreaterEqual(live["uptime_seconds"], 0)
        self.assertFalse(live["altium_found"])
        self.assertTrue(live["script_found"])
        self.assertEqual(scope["SERVER_INFO"], old_info)

    @unittest.skipUnless(FastMCP, "MCP SDK is not installed")
    def test_protocol_handshake_and_status_tool_report_project_version(self):
        scope = self.load_status(protocol=True)

        async def check():
            server = scope["mcp"]._mcp_server
            async with create_client_server_memory_streams() as (client_streams, server_streams):
                async with anyio.create_task_group() as tasks:
                    tasks.start_soon(server.run, *server_streams, server.create_initialization_options())
                    async with ClientSession(*client_streams) as client:
                        initialized = await client.initialize()
                        self.assertEqual(initialized.serverInfo.name, "AltiumMCP")
                        self.assertEqual(initialized.serverInfo.version, "1.2.3")
                        tools = await client.list_tools()
                        status_tool = next(tool for tool in tools.tools if tool.name == "get_server_status")
                        self.assertFalse(status_tool.inputSchema.get("required"))
                        result = await client.call_tool("get_server_status", {})
                        self.assertFalse(result.isError)
                        live = json.loads(result.content[0].text)
                        self.assertEqual(live["server_version"], initialized.serverInfo.version)
                        self.assertEqual(live["source_sha256"], scope["SERVER_INFO"]["source_sha256"])
                    tasks.cancel_scope.cancel()

        async def bounded_check():
            with anyio.fail_after(10):
                await check()

        anyio.run(bounded_check)


if __name__ == "__main__":
    unittest.main()
