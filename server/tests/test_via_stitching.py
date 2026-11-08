"""Unit tests for via_stitching (pure Python, no Altium needed).

Run from server/:  python -m unittest tests.test_via_stitching -v
"""
import asyncio
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import via_stitching as vs  # noqa: E402

STITCH = (b"|SELECTION=FALSE|UNIONTYPE=2|FUNIONINDEX=1048578|X1=0mil|STEP=118.1102mil|STAGGER=TRUE"
          b"|NETNAME=GND|VIAPADCLEARANCE=0.3mm|PULLBACK=0mil|DIAMETER=35.4331mil|HOLESIZE=17.7165mil"
          b"|ISCONSTRAINAREA=FALSE|KIND0=0\x00")
SHIELD = (b"|UNIONTYPE=6|FUNIONINDEX=2097156|STEP=47.2441mil|NETNAME=CLK|VIANETNAME=GND"
          b"|DISTANCE=59.0551mil|ROWCOUNT=2|ADDCOPPER=FALSE|ADDPOLYCUTOUT=TRUE\x00")


def _record(text: bytes) -> bytes:
    """A stream record as Altium writes it: a binary length, then the text."""
    return len(text).to_bytes(4, "little") + text


class SettingsTest(unittest.TestCase):
    def test_checks(self):
        self.assertEqual(vs.check_settings({"grid_mils": 100, "via_diameter_mils": 20, "via_hole_mils": 10}), "")
        self.assertIn("positive", vs.check_settings({"grid_mils": 0}))
        self.assertIn("smaller", vs.check_settings({"via_diameter_mils": 10, "via_hole_mils": 10}))
        self.assertIn("number", vs.check_settings({"pullback_mils": "5"}))
        self.assertIn("rows", vs.check_shield_settings({}, 0))
        self.assertEqual(vs.check_shield_settings({"via_spacing_mils": 40, "distance_mils": 0}, 2), "")

    def test_values_go_to_the_dialog_in_mils(self):
        self.assertEqual(vs.mil_text(11.81102), "11.811mil")
        self.assertEqual(vs.mil_text(100), "100mil")
        self.assertEqual(vs.mils_of("0.254mm"), 10.0)
        self.assertEqual(vs.mils_of("100mil"), 100.0)
        self.assertIsNone(vs.mils_of("N/A"))

    def test_union_index_zero_is_not_a_group(self):
        # 0 is every via in no group: removing it would remove them all
        self.assertTrue(vs.valid_union_index(1048578))
        for bad in (0, -3, None, "5", True, 2.0):
            self.assertFalse(vs.valid_union_index(bad), bad)


class StoredGroupsTest(unittest.TestCase):
    def test_stitching_record(self):
        g = vs.stored_groups(b"junk" + _record(STITCH) + _record(SHIELD))
        self.assertEqual(list(g), [1048578])
        self.assertEqual(g[1048578], {"net": "GND", "grid_mils": 118.1102, "stagger": True,
                                      "via_pad_clearance_mils": 11.811, "pullback_mils": 0.0,
                                      "via_diameter_mils": 35.4331, "via_hole_mils": 17.7165,
                                      "constrained_to_area": False})

    def test_field_order_does_not_matter(self):
        record = b"|FUNIONINDEX=7|NETNAME=VCC|STEP=50mil|UNIONTYPE=2\x00"
        self.assertEqual(vs.stored_groups(_record(record))[7]["net"], "VCC")

    def test_shielding_record(self):
        g = vs.stored_shielding(_record(STITCH) + _record(SHIELD))
        self.assertEqual(g[2097156]["shielded_net"], "CLK")
        self.assertEqual(g[2097156]["rows"], 2)
        self.assertTrue(g[2097156]["add_cutout"])
        self.assertFalse(g[2097156]["add_copper"])

    def test_stored_net_must_match_the_board(self):
        stored = vs.stored_groups(_record(STITCH))[1048578]
        self.assertEqual(vs.stored_net_problem({"union_index": 1048578, "net": "gnd"}, stored), "")
        self.assertIn("Save the board", vs.stored_net_problem({"union_index": 1048578, "net": "VCC"}, stored))


class StoredFileTest(unittest.TestCase):
    """Groups are read from the saved .PcbDoc's streams, not its raw bytes."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "board.PcbDoc")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, storages):
        """A compound file whose streams are written in turns, a sector at a
        time, so each stream's sectors are spread through the file."""
        import pythoncom
        from win32com import storagecon as sc
        mode = sc.STGM_CREATE | sc.STGM_READWRITE | sc.STGM_SHARE_EXCLUSIVE
        root = pythoncom.StgCreateDocfile(self.path, mode, 0)
        streams = []
        for name, data in storages.items():
            st = root.CreateStorage(name, mode, 0, 0)
            streams.append((st, st.CreateStream("Data", mode, 0, 0), data))
        for start in range(0, max(len(d) for _, _, d in streams), 512):
            for _, stream, data in streams:
                if start < len(data):
                    stream.Write(data[start:start + 512])
        for st, stream, _ in streams:
            stream.Commit(0)
            st.Commit(0)
        root.Commit(0)
        streams = root = st = stream = None

    def test_record_split_over_sectors(self):
        # Large enough that the streams use regular 512-byte sectors, with the
        # stitching record crossing a sector boundary
        padding = _record(b"|UNIONTYPE=9|FUNIONINDEX=1|PAD=" + b"x" * 5000 + b"\x00")
        unions = padding + _record(STITCH) + _record(SHIELD) + padding
        tracks = b"\x01" * len(unions)
        self._write({"Tracks6": tracks, "SmartUnions": unions})
        raw = Path(self.path).read_bytes()
        self.assertNotIn(STITCH[:-1], raw)   # the record is not in one piece in the file
        stitching, shielding = vs.read_stored(self.path)
        self.assertEqual(stitching[1048578]["grid_mils"], 118.1102)
        self.assertEqual(stitching[1048578]["net"], "GND")
        self.assertEqual(shielding[2097156]["via_net"], "GND")

    def test_unions_found_in_any_storage(self):
        self._write({"Other6": _record(STITCH)})
        self.assertIn(1048578, vs.read_stored(self.path)[0])

    def test_unreadable_file(self):
        self.assertEqual(vs.read_stored(os.path.join(self.dir, "missing.PcbDoc")), ({}, {}))
        self.assertEqual(vs.read_stored(""), ({}, {}))


class StaggerTest(unittest.TestCase):
    def test_measured_from_rows(self):
        straight = [(y * 150.0, 31.5) for y in range(5)]
        stagger = [(y * 150.0, 31.5 + (75 if y % 2 else 0)) for y in range(5)]
        self.assertFalse(vs.staggered(straight, 150))
        self.assertTrue(vs.staggered(stagger, 150))
        # a row that starts further in (copper missing at its left) is still a grid row
        self.assertFalse(vs.staggered([(0, 31.5), (150, 331.5), (300, 31.5)], 150))
        self.assertIsNone(vs.staggered([(0, 0)], 150))


class TickTest(unittest.TestCase):
    """A box is read from two pictures, before and after one click."""

    @staticmethod
    def _box(face, border, tick=None, fill=None):
        from PIL import Image, ImageDraw
        img = Image.new("L", (200, 32), face)
        draw = ImageDraw.Draw(img)
        draw.rectangle((2, 2, 29, 29), outline=border, fill=fill)
        if tick is not None:
            draw.line((9, 16, 14, 22, 23, 9), fill=tick, width=2)
        return img

    def test_dark_theme(self):
        empty, ticked = self._box(60, 200), self._box(60, 200, tick=255)
        self.assertTrue(vs.tick_from_pair(ticked, empty))
        self.assertFalse(vs.tick_from_pair(empty, ticked))

    def test_light_theme(self):
        # a dark tick on a white box: what a light-pixel test reads wrongly
        empty, ticked = self._box(240, 120, fill=255), self._box(240, 120, tick=20, fill=255)
        self.assertTrue(vs.tick_from_pair(ticked, empty))
        self.assertFalse(vs.tick_from_pair(empty, ticked))

    def test_filled_when_ticked(self):
        empty, ticked = self._box(240, 120, fill=255), self._box(240, 90, tick=255, fill=90)
        self.assertTrue(vs.tick_from_pair(ticked, empty))

    def _set(self, pictures, want):
        clicks = []

        async def picture(hwnd):
            return pictures.pop(0)
        with mock.patch.object(vs, "_settled_image", picture), \
                mock.patch.object(vs, "_click", lambda *a, **k: clicks.append(a[0])):
            return asyncio.run(vs.set_checkbox(5, want)), len(clicks)

    def test_set_checkbox(self):
        empty, ticked = self._box(240, 120, fill=255), self._box(240, 120, tick=20, fill=255)
        self.assertEqual(self._set([ticked, empty], False), (False, 1))
        # the click went the wrong way: clicked back, and it must look as before
        self.assertEqual(self._set([empty, ticked, empty], False), (False, 2))
        self.assertEqual(self._set([empty, ticked, ticked], False), (None, 2))
        self.assertEqual(self._set([empty, empty], False), (None, 1))

    def test_unchanged_is_unknown(self):
        # the click did not take: nothing can be told
        self.assertIsNone(vs.tick_from_pair(self._box(60, 200), self._box(60, 200)))
        lit = self._box(60, 255)   # only the border lit under the mouse
        self.assertIsNone(vs.tick_from_pair(lit, self._box(60, 200)))


class MessageTest(unittest.TestCase):
    def test_only_known_boxes_are_answered(self):
        self.assertEqual(vs.message_answer("Information", ["OK"], None), "OK")
        self.assertEqual(vs.message_answer("Whatever", ["&OK"], True), "OK")
        self.assertEqual(vs.message_answer("Confirm", ["&Yes", "&No"], True), "Yes")
        self.assertEqual(vs.message_answer("Confirm", ["&Yes", "&No"], False), "No")
        # a question where none is expected, or one not recognised: no answer (closed)
        self.assertIsNone(vs.message_answer("Confirm", ["&Yes", "&No"], None))
        self.assertIsNone(vs.message_answer("Warning", ["&Yes", "&No"], True))
        self.assertIsNone(vs.message_answer("Confirm", ["&Yes", "&No", "Cancel"], True))
        self.assertIsNone(vs.message_answer("Error", [], None))

    def test_foreground_goes_back_only_from_altium(self):
        self.assertTrue(vs.should_hand_back("claude.exe", "X2.EXE"))
        self.assertFalse(vs.should_hand_back("X2.EXE", "X2.EXE"))     # Altium was in front before
        self.assertFalse(vs.should_hand_back("claude.exe", "code.exe"))  # the user moved on
        self.assertFalse(vs.should_hand_back("", "X2.EXE"))


class DriverTest(unittest.TestCase):
    """The drivers never press OK blind and never leave the dialog open."""

    def setUp(self):
        self.closed = []
        self.pressed = []
        patches = [
            mock.patch.object(vs, "_post_close", side_effect=self.closed.append),
            mock.patch.object(vs, "answer_messages", lambda *a, **k: None),
            mock.patch.object(vs, "open_message_boxes", lambda: set()),
            mock.patch.object(vs, "bring_to_front", lambda h: True),
            mock.patch.object(vs, "_is_open", lambda h: h not in self.closed),
            mock.patch.object(vs, "_text", lambda h: ""),
            mock.patch.object(vs, "_click", lambda *a, **k: self.pressed.append(a[0])),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.progress = Path(tempfile.mkdtemp()) / "progress.txt"

    def _dialog(self, controls):
        """Dialog 99 is open with these controls."""
        for p in (mock.patch.object(vs, "_top_windows", lambda cls: [99] if cls == vs.DIALOG_CLASS else []),
                  mock.patch.object(vs, "_controls", lambda h: controls)):
            p.start()
            self.addCleanup(p.stop)

    async def _run(self, coro, seconds=0.5):
        task = asyncio.ensure_future(coro)
        await asyncio.sleep(seconds)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def test_dialog_wait_is_limited(self):
        outcome = vs.StitchOutcome()
        with mock.patch.object(vs, "_top_windows", lambda cls: []):
            asyncio.run(self._run(vs.drive_dialog(outcome, "GND", {}, None, self.progress,
                                                  started=lambda: True, appear_wait=0.1), 0.6))
        self.assertFalse(outcome.seen)
        self.assertIn("did not open", " ".join(outcome.notes))

    def test_dialog_wait_has_an_overall_limit(self):
        # the bridge never picks the command up: the overall limit still ends the wait
        async def run():
            return await vs._wait_for_dialog("X", vs.StitchOutcome(), set(), lambda: False, 0.1, max_wait=0.5)
        with mock.patch.object(vs, "_top_windows", lambda cls: []):
            self.assertIsNone(asyncio.run(asyncio.wait_for(run(), 2)))

    def test_late_dialog_is_closed(self):
        outcome = vs.StitchOutcome()
        calls = {"n": 0}

        def windows(cls):
            calls["n"] += 1
            return [77] if cls == vs.DIALOG_CLASS and calls["n"] > 3 else []
        with mock.patch.object(vs, "_top_windows", windows):
            asyncio.run(self._run(vs.drive_dialog(outcome, "GND", {}, None, self.progress,
                                                  started=lambda: True, appear_wait=0.1), 1.5))
        self.assertIn(77, self.closed)
        self.assertTrue(outcome.cancelled)

    def test_constrain_area_unknown_is_not_confirmed(self):
        outcome = vs.StitchOutcome()
        controls = [vs.Control(1, "TXPComboBox", "GND", "Properties", 0, 0),
                    vs.Control(2, "TXPCheckBox", "Constrain Area", "", 0, 0),
                    vs.Control(3, "TXPBitBtn", "OK", "", 0, 0)]

        async def unknown(hwnd, want):
            return None
        self._dialog(controls)
        with mock.patch.object(vs, "set_checkbox", unknown):
            asyncio.run(self._run(vs.drive_dialog(outcome, "GND", {}, None, self.progress, settle=0)))
        self.assertNotIn(3, self.pressed)
        self.assertIn(99, self.closed)
        self.assertFalse(outcome.pressed)
        self.assertIn("Constrain Area", " ".join(outcome.notes))

    def test_missing_constrain_area_is_not_confirmed(self):
        outcome = vs.StitchOutcome()
        controls = [vs.Control(1, "TXPComboBox", "GND", "Properties", 0, 0),
                    vs.Control(3, "TXPBitBtn", "OK", "", 0, 0)]
        self._dialog(controls)
        asyncio.run(self._run(vs.drive_dialog(outcome, "GND", {}, None, self.progress, settle=0)))
        self.assertNotIn(3, self.pressed)
        self.assertIn(99, self.closed)

    def test_cancelled_driver_closes_the_dialog(self):
        outcome = vs.StitchOutcome()
        controls = [vs.Control(1, "TXPComboBox", "GND", "Properties", 0, 0),
                    vs.Control(2, "TXPCheckBox", "Constrain Area", "", 0, 0)]

        async def slow(hwnd, want):
            await asyncio.sleep(10)
        self._dialog(controls)
        with mock.patch.object(vs, "set_checkbox", slow):
            asyncio.run(self._run(vs.drive_dialog(outcome, "GND", {}, None, self.progress, settle=0), 0.3))
        self.assertEqual(self.closed, [99])
        self.assertTrue(outcome.cancelled)


if __name__ == "__main__":
    unittest.main()
