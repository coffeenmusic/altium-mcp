"""Client registration checks using isolated profiles; no Altium required."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
CODEX = shutil.which("codex")


class LauncherSetupTest(unittest.TestCase):
    def setUp(self):
        temp_root = REPO / ".altium-mcp" / "unit_tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temp_root)
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.profile = self.work / "codex profile"
        self.data = self.work / "server data"
        self.env = dict(os.environ, CODEX_HOME=str(self.profile),
                        ALTIUM_MCP_HOME=str(self.data), PYTHONIOENCODING="utf-8")

    def run_command(self, command):
        return subprocess.run(command, cwd=self.work, env=self.env, capture_output=True,
                              encoding="utf-8", errors="replace", timeout=30)

    def setup_command(self, script=None):
        return [sys.executable, str(script or REPO / "start_server.py"),
                "mcp", "setup", "--client", "codex"]

    def test_help_and_invalid_arguments_do_not_build_or_register(self):
        for args, code in [(["--help"], 0), (["mcp", "setup", "--help"], 0),
                           (["mcp"], 2), (["mcp", "setup"], 2),
                           (["mcp", "setup", "--client", "unknown"], 2),
                           (["--build", "mcp", "setup", "--client", "codex"], 2)]:
            with self.subTest(args=args):
                result = self.run_command([sys.executable, str(REPO / "start_server.py"), *args])
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertFalse(self.profile.exists())
                self.assertFalse(self.data.exists())

    def test_missing_cli_reports_manual_configuration(self):
        empty_path = self.work / "empty path"
        empty_path.mkdir()
        self.env["PATH"] = str(empty_path)
        result = self.run_command(self.setup_command())
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("Codex CLI was not found", result.stderr)
        self.assertIn("README.md", result.stderr)
        self.assertFalse(self.data.exists())
        self.assertFalse(self.profile.exists())

    def test_version_reports_disk_identity_without_starting_a_server(self):
        result = self.run_command([sys.executable, str(REPO / "start_server.py"), "--version"])
        self.assertEqual(result.returncode, 0, result.stderr)
        info = json.loads(result.stdout)
        self.assertEqual(info["installation_path"], str(REPO))
        self.assertEqual(info["server_path"], str(REPO / "server" / "main.py"))
        self.assertRegex(info["source_sha256"], r"^[0-9a-f]{64}$")
        self.assertNotEqual(info["server_version"], "unknown")
        self.assertNotIn("pid", info)
        self.assertFalse(self.data.exists())
        self.assertFalse(self.profile.exists())

    def test_cli_failure_is_returned_without_reporting_success(self):
        fake_path = self.work / "fake cli"
        fake_path.mkdir()
        fake = fake_path / ("codex.cmd" if os.name == "nt" else "codex")
        fake.write_text("@exit /b 23\n" if os.name == "nt" else "#!/bin/sh\nexit 23\n",
                        encoding="ascii")
        fake.chmod(0o755)
        self.env["PATH"] = str(fake_path)
        result = self.run_command(self.setup_command())
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertNotIn("Registered", result.stdout)
        self.assertFalse(self.data.exists())
        self.assertFalse(self.profile.exists())

    @unittest.skipUnless(CODEX, "Codex CLI is not installed")
    def test_real_cli_preserves_other_settings_and_updates_absolute_paths(self):
        self.profile.mkdir()
        config = self.profile / "config.toml"
        config.write_text('model = "setup-test-model"\n'
                          '[mcp_servers.keep]\ncommand = "keep-command"\n'
                          'args = ["keep-argument"]\n', encoding="utf-8")
        install = self.work / "toolkit \u0422\u0435\u0441\u0442"
        install.mkdir()
        script = install / "start_server.py"
        shutil.copyfile(REPO / "start_server.py", script)
        self.env["ALTIUM_MCP_HOME"] = "state \u0422\u0435\u0441\u0442"
        for target, override in [(script, True), (REPO / "start_server.py", False)]:
            if not override:
                self.env.pop("ALTIUM_MCP_HOME")
            result = self.run_command(self.setup_command(target))
            self.assertEqual(result.returncode, 0, result.stderr)
            result = self.run_command([CODEX, "mcp", "get", "altium-mcp", "--json"])
            self.assertEqual(result.returncode, 0, result.stderr)
            transport = json.loads(result.stdout)["transport"]
            self.assertEqual(transport["type"], "stdio")
            self.assertEqual(transport["command"], sys.executable)
            self.assertEqual(transport["args"], [str(target.resolve())])
            expected_env = {"ALTIUM_MCP_HOME": str(self.work / self.env["ALTIUM_MCP_HOME"])} if override else {}
            self.assertEqual(transport["env"] or {}, expected_env)
            result = self.run_command([CODEX, "mcp", "get", "keep", "--json"])
            self.assertEqual(result.returncode, 0, result.stderr)
            other = json.loads(result.stdout)["transport"]
            self.assertEqual(other["command"], "keep-command")
            self.assertEqual(other["args"], ["keep-argument"])
            text = config.read_text(encoding="utf-8")
            self.assertIn('model = "setup-test-model"', text)
            self.assertEqual(text.count("[mcp_servers.altium-mcp]"), 1)
            self.assertFalse((self.work / "state \u0422\u0435\u0441\u0442").exists())
            self.assertFalse(self.data.exists())


if __name__ == "__main__":
    unittest.main()
