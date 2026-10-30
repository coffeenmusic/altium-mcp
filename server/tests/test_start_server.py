"""Unit tests for start_server's venv pruning (no Altium needed).

Run from server/:  python -m unittest tests.test_start_server -v
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import start_server  # noqa: E402

DAY = 86400


class PruneTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.venvs = Path(self.tmp.name) / "venvs"
        self.venvs.mkdir()
        self.saved = {n: getattr(start_server, n) for n in ("VENVS_DIR", "VENV_DIR", "MARKER", "_venvs_in_use")}
        start_server.VENVS_DIR = self.venvs
        start_server.VENV_DIR = self.venvs / "current"
        start_server.MARKER = start_server.VENV_DIR / ".requirements-installed"
        self.in_use = set()
        start_server._venvs_in_use = lambda: self.in_use

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(start_server, name, value)
        self.tmp.cleanup()

    def venv(self, name, last_used_days=None):
        d = self.venvs / name
        (d / "Lib").mkdir(parents=True)
        (d / "Lib" / "module.py").write_text("x = 1")
        if last_used_days is not None:
            marker = d / ".requirements-installed"
            marker.write_text("reqs")
            t = time.time() - last_used_days * DAY
            os.utime(marker, (t, t))
        return d

    def test_keeps_current_recent_running_and_building(self):
        current = self.venv("current", last_used_days=100)
        recent = self.venv("recent", last_used_days=start_server.KEEP_UNUSED_DAYS - 1)
        running = self.venv("running", last_used_days=100)
        building = self.venv("building")
        (self.venvs / "building.lock").write_text(str(os.getpid()))
        self.in_use = {"running"}
        start_server._prune_old_venvs()
        for d in (current, recent, running, building):
            self.assertTrue((d / "Lib" / "module.py").exists(), d.name)

    def test_removes_old_unused_half_built_and_leftover_trash(self):
        old = self.venv("old", last_used_days=start_server.KEEP_UNUSED_DAYS + 1)
        half = self.venv("half")
        trash = self.venv("gone.trash")
        start_server._prune_old_venvs()
        for d in (old, half, trash):
            self.assertFalse(d.exists(), d.name)
        self.assertEqual(sorted(p.name for p in self.venvs.iterdir()), [])

    def test_nothing_pruned_when_processes_cannot_be_read(self):
        old = self.venv("old", last_used_days=100)
        self.in_use = None
        start_server._prune_old_venvs()
        self.assertTrue((old / "Lib" / "module.py").exists())

    def test_launch_marks_venv_used(self):
        current = self.venv("current", last_used_days=30)
        (current / "Scripts").mkdir()
        (current / "Scripts" / "python.exe").write_bytes(b"")
        start_server.MARKER.write_text(start_server._marker_payload(), encoding="utf-8")
        t = time.time() - 30 * DAY
        os.utime(start_server.MARKER, (t, t))
        saved = start_server.PYTHON_EXE
        start_server.PYTHON_EXE = current / "Scripts" / "python.exe"
        try:
            start_server.ensure_venv()
        finally:
            start_server.PYTHON_EXE = saved
        self.assertGreater(start_server.MARKER.stat().st_mtime, time.time() - 60)


@unittest.skipUnless(os.name == "nt", "process image paths are read with Windows APIs")
class InUseTest(unittest.TestCase):
    def test_finds_a_venv_a_process_runs_from(self):
        # A copied system executable stands in for a venv's python.exe. The
        # temp folder's path is often a short (8.3) one, as it can be in use.
        with tempfile.TemporaryDirectory() as tmp:
            saved = start_server.VENVS_DIR
            start_server.VENVS_DIR = Path(tmp)
            exe = Path(tmp) / "abc123" / "Scripts" / "ping.exe"
            exe.parent.mkdir(parents=True)
            shutil.copy(Path(os.environ["SystemRoot"]) / "System32" / "ping.exe", exe)
            proc = subprocess.Popen([str(exe), "-n", "30", "127.0.0.1"], stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                time.sleep(0.5)
                self.assertEqual(start_server._venvs_in_use(), {"abc123"})
            finally:
                proc.kill()
                proc.wait()
                start_server.VENVS_DIR = saved

    def test_venv_a_server_runs_from_survives_a_prune(self):
        # What happened: a build for another interpreter pruned the venv two
        # running servers used, leaving only the files they had locked
        with tempfile.TemporaryDirectory() as tmp:
            saved = {n: getattr(start_server, n) for n in ("VENVS_DIR", "VENV_DIR")}
            start_server.VENVS_DIR, start_server.VENV_DIR = Path(tmp), Path(tmp) / "new"
            old = Path(tmp) / "old"
            (old / "Lib" / "PIL").mkdir(parents=True)
            (old / "Lib" / "PIL" / "PngImagePlugin.py").write_text("x = 1")
            (old / ".requirements-installed").write_text("reqs")
            t = time.time() - 100 * DAY
            os.utime(old / ".requirements-installed", (t, t))
            exe = old / "Scripts" / "ping.exe"
            exe.parent.mkdir()
            shutil.copy(Path(os.environ["SystemRoot"]) / "System32" / "ping.exe", exe)
            proc = subprocess.Popen([str(exe), "-n", "30", "127.0.0.1"], stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                time.sleep(0.5)
                start_server._prune_old_venvs()
                self.assertTrue((old / "Lib" / "PIL" / "PngImagePlugin.py").exists())
                self.assertTrue((old / ".requirements-installed").exists())
            finally:
                proc.kill()
                proc.wait()
                for name, value in saved.items():
                    setattr(start_server, name, value)


if __name__ == "__main__":
    unittest.main()
