"""Background launcher checks; no Altium or package installation required."""

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("altium_launcher_background", REPO / "start_server.py")
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


class LauncherBackgroundTest(unittest.TestCase):
    def test_build_commands_do_not_create_windows_consoles(self):
        with tempfile.TemporaryDirectory() as work:
            with patch.object(launcher, "VENV_DIR", Path(work) / "new-venv"), \
                    patch.object(launcher, "MARKER", Mock()), \
                    patch.object(launcher, "_locked_text", return_value=""), \
                    patch.object(launcher, "_prune_old_venvs"), \
                    patch.object(launcher.subprocess, "check_call") as run:
                launcher._build_venv()
        self.assertEqual(run.call_count, 2)
        expected = launcher._background_subprocess_options()
        for call in run.call_args_list:
            self.assertEqual(call.kwargs, expected)
        if os.name == "nt":
            self.assertEqual(expected["creationflags"], subprocess.CREATE_NO_WINDOW)

    def test_server_preserves_stdio_and_runs_without_a_console(self):
        with patch.object(launcher, "ensure_venv", return_value="venv-python"), \
                patch.object(launcher.subprocess, "call", return_value=0) as run:
            self.assertEqual(launcher.main([]), 0)
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["venv-python", str(REPO / "server" / "main.py")])
        self.assertEqual(kwargs["env"]["ALTIUM_MCP_HOME"], str(launcher.DATA_DIR))
        self.assertIs(kwargs["stdin"], sys.stdin)
        self.assertIs(kwargs["stdout"], sys.stdout)
        self.assertIs(kwargs["stderr"], sys.stderr)
        for name, value in launcher._background_subprocess_options().items():
            self.assertEqual(kwargs[name], value)

    def test_real_launcher_round_trips_stdio_to_server(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            (root / "server").mkdir()
            (root / "server" / "main.py").write_text(
                "import sys\n"
                "print('server-stderr', file=sys.stderr, flush=True)\n"
                "print(sys.stdin.readline().strip(), flush=True)\n",
                encoding="utf-8")
            harness = (
                "import importlib.util,sys; from pathlib import Path; "
                f"s=importlib.util.spec_from_file_location('launcher',{str(REPO / 'start_server.py')!r}); "
                "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
                f"m.SCRIPT_DIR=Path({str(root)!r}); "
                "m.ensure_venv=lambda:sys.executable; sys.exit(m.main([]))")
            result = subprocess.run(
                [sys.executable, "-B", "-c", harness],
                input="mcp-stdio-probe\n", capture_output=True, text=True,
                timeout=15, **launcher._background_subprocess_options())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "mcp-stdio-probe")
        self.assertEqual(result.stderr.strip(), "server-stderr")

    @unittest.skipUnless(os.name == "nt", "Windows console behavior")
    def test_real_child_process_has_no_console(self):
        result = subprocess.run(
            [sys.executable, "-c",
             "import ctypes; print(ctypes.windll.kernel32.GetConsoleWindow())"],
            capture_output=True, text=True, timeout=15,
            **launcher._background_subprocess_options())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "0")


if __name__ == "__main__":
    unittest.main()
