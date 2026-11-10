"""Unit tests for window_capture (pure Python, no windows).

Run from server/:  python -m unittest tests.test_window_capture -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import window_capture as wc  # noqa: E402

ALTIUM, BROWSER = 20, 30


def win(title, rect, pid=ALTIUM, iconic=False):
    return {"title": title, "rect": rect, "pid": pid, "iconic": iconic}


class PickMainFrame(unittest.TestCase):
    def test_a_browser_tab_named_altium_is_not_taken(self):
        browser = win("Altium Designer docs - Google Chrome", (0, 0, 3440, 1400), pid=BROWSER)
        frame = win("Main_Board.PrjPcb - Altium Designer (26.11.1)", (3440, 0, 5360, 1030))
        self.assertIs(wc.pick_main_frame([browser, frame], {ALTIUM}), frame)

    def test_largest_shown_window_of_altium(self):
        hint = win("Altium Designer", (10, 10, 200, 60))
        frame = win("Main_Board.PrjPcb - Altium Designer", (0, 0, 1920, 1030))
        self.assertIs(wc.pick_main_frame([hint, frame], {ALTIUM}), frame)

    def test_minimised_frame_only_when_nothing_else(self):
        frame = win("Main_Board.PrjPcb - Altium Designer", (0, 0, 1920, 1030), iconic=True)
        hint = win("Altium Designer", (10, 10, 200, 60))
        self.assertIs(wc.pick_main_frame([frame, hint], {ALTIUM}), hint)
        self.assertIs(wc.pick_main_frame([frame], {ALTIUM}), frame)

    def test_no_altium_process(self):
        browser = win("Altium Designer docs - Google Chrome", (0, 0, 3440, 1400), pid=BROWSER)
        self.assertIsNone(wc.pick_main_frame([browser], set()))
        self.assertIsNone(wc.pick_main_frame([], {ALTIUM}))


class VisibleCrop(unittest.TestCase):
    def test_maximised_at_100_percent_loses_the_invisible_border(self):
        # GetWindowRect (-8,-8,3448,1408), drawn frame (0,0,3440,1400)
        rect = (-8, -8, 3448, 1408)
        self.assertEqual(wc.visible_crop(rect, rect, (0, 0, 3440, 1400)), (8, 8, 3448, 1408))

    def test_system_aware_window_on_a_125_percent_monitor(self):
        # Altium (system aware, 96 dpi) on a 125 % monitor: its surface is
        # 1552x840, the window is 1940x1050 physical with a 10 px border
        surface = (2752, -6, 4304, 834)          # 1552 x 840
        physical = (3430, -10, 5370, 1040)       # 1940 x 1050
        frame = (3440, 0, 5360, 1030)            # drawn: 1920 x 1030
        l, t, r, b = wc.visible_crop(surface, physical, frame)
        self.assertEqual((l, t), (8, 8))
        self.assertEqual((r - l, b - t), (1536, 824))   # 1920x1030 / 1.25

    def test_without_a_frame_the_whole_surface(self):
        rect = (0, 0, 800, 600)
        self.assertEqual(wc.visible_crop(rect, rect, None), (0, 0, 800, 600))

    def test_a_frame_outside_the_window_keeps_everything(self):
        rect = (0, 0, 800, 600)
        self.assertEqual(wc.visible_crop(rect, rect, (900, 0, 1000, 100)), (0, 0, 800, 600))


if __name__ == "__main__":
    unittest.main()
