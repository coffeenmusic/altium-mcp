"""Unit tests for netlist_diff (pure Python, no Altium needed).

Run from server/:  python -m unittest tests.test_netlist_diff -v
"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import netlist_diff as nd  # noqa: E402

BEFORE = {"U1-1": "VCC", "U1-2": "GND", "U1-3": "NetU1_3", "R1-1": "NetU1_3", "R1-2": "SIG",
          "C1-1": "VCC", "C1-2": "GND", "J1-1": "SIG", "J1-2": ""}


class CompareTest(unittest.TestCase):
    def test_nothing_changed(self):
        self.assertEqual(nd.summary(nd.compare(BEFORE, dict(BEFORE))), {})

    def test_rename_moves_and_new_parts(self):
        after = dict(BEFORE)
        # the auto-named net only changes its name
        after["U1-3"] = after["R1-1"] = "NetR1_1"
        # C1-1 moves from VCC to SIG; J1-2 gets connected; R2 is new
        after["C1-1"] = "SIG"
        after["J1-2"] = "GND"
        after["R2-1"], after["R2-2"] = "SIG", "NEW"
        del after["C1-2"]
        d = nd.compare(BEFORE, after)
        self.assertEqual(d["nets_renamed"], [{"from": "NetU1_3", "to": "NetR1_1"}])
        self.assertEqual(d["pins_moved"], [{"pin": "C1-1", "from": "VCC", "to": "SIG"},
                                           {"pin": "J1-2", "from": None, "to": "GND"}])
        self.assertEqual(d["pins_added"], ["R2-1", "R2-2"])
        self.assertEqual(d["pins_removed"], ["C1-2"])
        self.assertEqual(d["nets_added"], ["NEW"])
        # VCC is left with U1-1 alone
        self.assertEqual(d["new_single_pin_nets"], ["NEW", "VCC"])
        changed = {c["net"]: c for c in d["nets_changed"]}
        self.assertEqual(changed["SIG"]["gained"], ["C1-1", "R2-1"])
        self.assertEqual(changed["VCC"]["lost"], ["C1-1"])
        self.assertEqual(changed["GND"], {"net": "GND", "pins": 2, "gained": ["J1-2"], "lost": ["C1-2"]})
        self.assertNotIn("NetR1_1", changed)

    def test_net_removed(self):
        after = {k: v for k, v in BEFORE.items()}
        after["R1-2"] = after["J1-1"] = ""
        d = nd.compare(BEFORE, after)
        self.assertEqual(d["nets_removed"], ["SIG"])
        self.assertEqual([m["pin"] for m in d["pins_moved"]], ["J1-1", "R1-2"])

    def test_from_source_and_snapshot(self):
        net = nd.from_source({"pins": ["U1|1|VCC", "U1|2|", "bad"]})
        self.assertEqual(net, {"U1-1": "VCC", "U1-2": ""})
        with tempfile.TemporaryDirectory() as d:
            p = nd.snapshot_path(Path(d), r"C:\x\My Board.PrjPcb", "before edit")
            self.assertTrue(p.name.startswith("My Board_"))
            self.assertTrue(p.name.endswith("__before_edit.json"))
            nd.save(p, r"C:\x\My Board.PrjPcb", net, "now")
            self.assertEqual(nd.load(p)["pins"], net)
            self.assertEqual(nd.load(p, "c:/X/sub/../My Board.PRJPCB")["pins"], net)
            self.assertIsNone(nd.load(Path(d) / "none.json"))

    def test_same_name_other_folder_keeps_its_own(self):
        folder = Path("snaps")
        a = nd.snapshot_path(folder, r"C:\work\Board.PrjPcb", "last")
        self.assertNotEqual(a, nd.snapshot_path(folder, r"C:\work\copy\Board.PrjPcb", "last"))
        # the same project written differently is the same snapshot
        self.assertEqual(a, nd.snapshot_path(folder, "c:/WORK/x/../Board.prjpcb", "last"))

    def test_snapshot_of_another_project_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            nd.save(p, r"C:\work\Board.PrjPcb", {"U1-1": "A"}, "now")
            with self.assertRaises(ValueError):
                nd.load(p, r"C:\work\copy\Board.PrjPcb")

    def test_malformed_snapshot_is_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            for text in ('{"pins": ["U1-1"]}', '{"pins": {"U1-1": 3}}', "[1, 2]", "not json",
                         '{"project_path": 5, "pins": {}}'):
                p.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError, msg=text):
                    nd.load(p)

    def test_prune_keeps_the_newest_per_project(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            prj, other = r"C:\work\Board.PrjPcb", r"C:\work\copy\Board.PrjPcb"
            for i in range(5):
                p = nd.snapshot_path(folder, prj, f"s{i}")
                nd.save(p, prj, {}, "now")
                os.utime(p, (1000 + i, 1000 + i))
            nd.save(nd.snapshot_path(folder, other, "s0"), other, {}, "now")
            self.assertEqual(sorted(nd.prune(folder, prj, keep=3)), ["s0", "s1"])
            left = sorted(p.name for p in folder.glob("*.json"))
            self.assertEqual(len(left), 4)
            self.assertTrue(nd.snapshot_path(folder, other, "s0").exists())
            self.assertEqual(nd.prune(folder, prj, keep=3), [])

    def test_match_nets_is_greedy(self):
        # both vanished nets overlap NEW; the first one takes it
        before = {"A": {"p1", "p2"}, "B": {"p3", "p4", "p5"}}
        after = {"NEW": {"p1", "p2", "p3", "p4", "p5"}}
        self.assertEqual(nd.match_nets(before, after), {"A": "NEW"})

    def test_limit(self):
        d = {"a": [1, 2, 3], "b": [1]}
        self.assertEqual(nd.limit(d, 2), ["a"])
        self.assertEqual(d, {"a": [1, 2], "b": [1]})



class ToolTest(unittest.TestCase):
    """compare_netlist in main.py, with the bridge faked."""

    def call(self, folder, project_dir, pins, **kwargs):
        import main
        prj = os.path.join(project_dir, "Board.PrjPcb")

        async def fake(command, params, timeout=None):
            return {"success": True, "result": json.dumps({"project_path": prj, "pins": pins})}
        with mock.patch.object(main.altium_bridge, "execute_command", fake), \
                mock.patch.object(main, "EXCHANGE_DIR", Path(folder)):
            return json.loads(asyncio.run(main.compare_netlist(None, project_path=prj, **kwargs)))

    def test_projects_of_the_same_name_do_not_share_a_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            for sub in ("a", "b"):
                os.makedirs(os.path.join(d, sub))
                open(os.path.join(d, sub, "Board.PrjPcb"), "w").close()
            first = self.call(d, os.path.join(d, "a"), ["U1|1|A"])
            self.assertIn("No snapshot", first["note"])
            # the copy in another folder has no snapshot yet
            other = self.call(d, os.path.join(d, "b"), ["U1|1|B"])
            self.assertIn("No snapshot", other["note"])
            again = self.call(d, os.path.join(d, "a"), ["U1|1|A"])
            self.assertEqual(again["summary"], {})

    def test_malformed_snapshot_returns_an_error(self):
        import main
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "Board.PrjPcb"), "w").close()
            prj = os.path.join(d, "Board.PrjPcb")
            snap = nd.snapshot_path(Path(d) / "netlists", prj, "last")
            snap.parent.mkdir()
            snap.write_text(json.dumps({"project_path": prj, "pins": ["U1-1"]}), encoding="utf-8")
            reply = self.call(d, d, ["U1|1|A"])
            self.assertFalse(reply["success"])
            self.assertIn("malformed", reply["error"])

    def test_old_snapshots_pruned(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "Board.PrjPcb"), "w").close()
            prj = os.path.join(d, "Board.PrjPcb")
            with mock.patch("netlist_diff.KEEP_PER_PROJECT", 2):
                for i, name in enumerate(("a", "b")):
                    self.call(d, d, ["U1|1|A"], against="", save_as=name)
                    os.utime(nd.snapshot_path(Path(d) / "netlists", prj, name), (1000 + i, 1000 + i))
                reply = self.call(d, d, ["U1|1|A"], against="", save_as="c")
            self.assertEqual(len(list((Path(d) / "netlists").glob("*.json"))), 2)
            self.assertEqual(reply["pruned"], ["a"])

    def test_not_a_project_refused(self):
        with tempfile.TemporaryDirectory() as d:
            import main
            reply = json.loads(asyncio.run(main.compare_netlist(None, project_path=os.path.join(d, "x.txt"))))
            self.assertFalse(reply["success"])


if __name__ == "__main__":
    unittest.main()
