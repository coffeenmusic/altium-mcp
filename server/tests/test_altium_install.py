"""Tests for finding Altium's X2.EXE without asking the user."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import altium_install  # noqa: E402


def _make_exe(folder):
    os.makedirs(folder, exist_ok=True)
    exe = os.path.join(folder, "X2.EXE")
    open(exe, "wb").close()
    return exe


class RegistryInstallsTest(unittest.TestCase):
    def test_newest_version_first_whatever_the_folder_name(self):
        builds = [
            {"ProgramsInstallPath": r"C:\Program Files\Altium\AD24", "Version": "24.10.1.45"},
            {"ProgramsInstallPath": r"D:\Program Files\Altium\AD25", "Version": "26.11.1.5"},
            {"ProgramsInstallPath": r"C:\Program Files\Altium\AD26", "Version": "26.2.0.7"},
        ]
        self.assertEqual(altium_install.registry_installs(builds), [
            r"D:\Program Files\Altium\AD25\X2.EXE",
            r"C:\Program Files\Altium\AD26\X2.EXE",
            r"C:\Program Files\Altium\AD24\X2.EXE",
        ])

    def test_uninstalled_and_pathless_builds_are_skipped(self):
        builds = [
            {"ProgramsInstallPath": r"C:\Old", "Version": "25.0.0.1", "Installed": "False"},
            {"Version": "27.0.0.1"},
            {"ProgramsInstallPath": r"C:\Kept", "Version": "24.0.0.1", "Installed": "True"},
        ]
        self.assertEqual(altium_install.registry_installs(builds), [r"C:\Kept\X2.EXE"])

    def test_reading_the_real_registry_does_not_fail(self):
        self.assertIsInstance(altium_install.registry_installs(), list)


class ScannedInstallsTest(unittest.TestCase):
    def test_highest_ad_number_first(self):
        with tempfile.TemporaryDirectory() as base:
            for name in ("AD9", "AD24", "AD25"):
                os.makedirs(os.path.join(base, name))
            found = altium_install.scanned_installs(base)
        self.assertEqual([os.path.basename(os.path.dirname(p)) for p in found],
                         ["AD25", "AD24", "AD9"])

    def test_missing_base_folder_gives_nothing(self):
        self.assertEqual(altium_install.scanned_installs(r"Z:\no\such\folder"), [])


class FindAltiumExeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _patch(self, registry=(), running=(), scanned=()):
        def source(paths):
            if isinstance(paths, Exception):
                def fail():
                    raise paths
                return fail
            return lambda: list(paths)
        return mock.patch.multiple(altium_install,
                                   registry_installs=source(registry),
                                   running_installs=source(running),
                                   scanned_installs=source(scanned))

    def test_registry_wins_over_running_and_scanned(self):
        reg = _make_exe(os.path.join(self.tmp.name, "reg"))
        run = _make_exe(os.path.join(self.tmp.name, "run"))
        with self._patch(registry=[reg], running=[run], scanned=[run]):
            self.assertEqual(altium_install.find_altium_exe(), reg)

    def test_paths_that_do_not_exist_are_passed_over(self):
        run = _make_exe(os.path.join(self.tmp.name, "run"))
        gone = os.path.join(self.tmp.name, "gone", "X2.EXE")
        with self._patch(registry=[gone], running=[run]):
            self.assertEqual(altium_install.find_altium_exe(), run)

    def test_a_failing_source_falls_through_to_the_next(self):
        scan = _make_exe(os.path.join(self.tmp.name, "scan"))
        with self._patch(running=OSError("snapshot failed"), scanned=[scan]):
            self.assertEqual(altium_install.find_altium_exe(), scan)

    def test_nothing_found_gives_empty_string(self):
        with self._patch():
            self.assertEqual(altium_install.find_altium_exe(), "")


if __name__ == "__main__":
    unittest.main()
