"""Unit tests for screenshot_target (pure Python, no Altium needed).

Run from the repo root:  python -m unittest server.tests.test_screenshot_target -v
or from server/:          python -m unittest tests.test_screenshot_target -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import screenshot_target as st  # noqa: E402
from screenshot_target import Win  # noqa: E402


class ParseTest(unittest.TestCase):
    def test_empty_is_current(self):
        self.assertEqual(st.parse_target(None).kind, "current")
        self.assertEqual(st.parse_target("  ").kind, "current")

    def test_kinds(self):
        self.assertEqual(st.parse_target("dialog").kind, "dialog")
        self.assertEqual(st.parse_target("Current").kind, "current")
        t = st.parse_target("document")
        self.assertEqual((t.kind, t.name), ("document", ""))
        t = st.parse_target("document: PD_Mon.SchDoc ")
        self.assertEqual((t.kind, t.name), ("document", "PD_Mon.SchDoc"))
        t = st.parse_target(r"document:C:\Boards\Main\PCB1.PcbDoc")
        self.assertEqual(t.name, r"C:\Boards\Main\PCB1.PcbDoc")  # the drive colon stays
        t = st.parse_target("panel:Components")
        self.assertEqual((t.kind, t.name), ("panel", "Components"))

    def test_region(self):
        t = st.parse_target("region:930, 60, 980, 330")
        self.assertEqual(t.region, (930, 60, 1910, 390))
        self.assertEqual(t.describe(), "region:930,60,980,330")

    def test_errors(self):
        for bad in ("pcb", "window:1", "dialog:x", "current:x", "panel", "panel:",
                    "region:1,2,3", "region:a,b,c,d", "region:0,0,0,10"):
            with self.assertRaises(st.TargetError, msg=bad):
                st.parse_target(bad)

    def test_resolve(self):
        self.assertEqual(st.resolve(None, None).kind, "current")
        self.assertIsNone(st.resolve(None, "pcb"))           # old behaviour
        self.assertEqual(st.resolve("dialog", "pcb").kind, "dialog")  # target wins

    def test_zoom_to_alone_means_a_board(self):
        self.assertEqual(st.implied_view_type(None, None, ["U12"]), "pcb")
        self.assertEqual(st.implied_view_type(" ", "", ["U12"]), "pcb")
        self.assertIsNone(st.resolve(None, st.implied_view_type(None, None, ["U12"])))
        self.assertEqual(st.implied_view_type(None, "sch", ["U12"]), "sch")
        self.assertIsNone(st.implied_view_type("document", None, ["U12"]))
        self.assertIsNone(st.implied_view_type(None, None, None))  # nothing given stays 'current'
        self.assertIsNone(st.implied_view_type(None, None, []))


def win(cls, text="", rect=(0, 0, 100, 100), parent_cls="", hwnd=1):
    return Win(hwnd=hwnd, cls=cls, text=text, rect=rect, parent_cls=parent_cls)


# Child windows as Altium 26 shows them (from a live probe)
CHILDREN = [
    win("TdxTabSheet", "[24] PD_Mon.SchDoc", (933, 103, 1858, 987), "TGUIDocBar"),
    win("TViewComponent", "[24] PD_Mon.SchDoc", (933, 103, 1858, 987), "TdxTabSheet"),
    win("TSCHView_GraphicalForm", "SchView_Graphical_1", (933, 103, 1858, 987), "TViewComponent"),
    win("TGUIDocBarButton", "Home Page", (1321, 72, 1422, 102)),
    win("TViewComponent", "Projects", (11, 101, 925, 959), "TPanel"),
    win("TWorkspaceForm", "Projects", (11, 101, 925, 959), "TViewComponent"),
    win("TXPPanel", "Projects", (15, 75, 857, 97)),
    win("TViewComponent", "Messages", (11, 101, 925, 959), "TPanel"),
]


class PickTest(unittest.TestCase):
    def test_main_window_is_altium_s_own(self):
        browser = Win(hwnd=1, cls="", text="Altium Forum - Browser", rect=(0, 0, 3840, 2160), pid=50)
        frame = Win(hwnd=2, cls="", text="Main - Altium Designer", rect=(0, 0, 1900, 1000), pid=7)
        hint = Win(hwnd=3, cls="", text="Altium", rect=(0, 0, 100, 50), pid=7)
        self.assertEqual(st.pick_main_window([browser, hint, frame], {7}).hwnd, 2)
        self.assertIsNone(st.pick_main_window([browser], {7}))
        self.assertIsNone(st.pick_main_window([browser, frame], set()))

    def test_dialog_skips_frame_and_tooltips(self):
        tops = [win("tooltips_class32", "", (0, 0, 40, 20)),
                win("TDocumentForm", "Main - Altium Designer", (0, 0, 1900, 1000)),
                win("TApplication", ""),
                win("TEngineeringChangeOrderForm", "Engineering Change Order", (284, 201, 1636, 827), hwnd=7)]
        self.assertEqual(st.pick_dialog(tops).hwnd, 7)
        self.assertIsNone(st.pick_dialog(tops[:3]))

    def test_dialog_is_topmost(self):
        tops = [win("#32770", "Save As", (10, 10, 500, 400), hwnd=2),
                win("TReportPreview", "Report Preview", (0, 0, 800, 900), hwnd=3)]
        self.assertEqual(st.pick_dialog(tops).hwnd, 2)

    def test_panel(self):
        self.assertEqual(st.pick_panel(CHILDREN, "projects").rect, (11, 101, 925, 959))
        self.assertIsNone(st.pick_panel(CHILDREN, "Components"))
        # a document view is never a panel
        self.assertIsNone(st.pick_panel(CHILDREN, "[24] PD_Mon.SchDoc"))
        self.assertEqual(st.panel_names(CHILDREN), ["Projects", "Messages"])

    def test_document_view(self):
        self.assertEqual(st.pick_document_view(CHILDREN, "PD_Mon.SchDoc").cls, "TViewComponent")
        self.assertEqual(st.pick_document_view(CHILDREN, r"C:\x\pd_mon.schdoc").text, "[24] PD_Mon.SchDoc")
        self.assertIsNone(st.pick_document_view(CHILDREN, "Mon.SchDoc"))
        self.assertEqual(st.pick_document_view(CHILDREN, "").text, "[24] PD_Mon.SchDoc")
        pcb = [win("TViewComponent", "PCB1.PcbDoc", (933, 103, 1858, 987), "TdxTabSheet")]
        self.assertIsNotNone(st.pick_document_view(pcb, "PCB1.PcbDoc"))

    def test_is_blank(self):
        from PIL import Image
        self.assertTrue(st.is_blank(Image.new("RGB", (20, 10), (0, 0, 0))))
        img = Image.new("RGB", (20, 10), (0, 0, 0))
        img.putpixel((3, 4), (255, 255, 255))
        self.assertFalse(st.is_blank(img))

    def test_should_return_foreground(self):
        self.assertTrue(st.should_return_foreground(50, 7, {7}))
        self.assertFalse(st.should_return_foreground(7, 7, {7}))    # Altium was in front anyway
        self.assertFalse(st.should_return_foreground(50, 60, {7}))  # the user has moved on
        self.assertFalse(st.should_return_foreground(0, 7, {7}))    # nothing was in front
        self.assertFalse(st.should_return_foreground(50, 0, {7}))

    def test_focus_params(self):
        t = st.parse_target("document:C:/Boards/Main/PCB1.PcbDoc")
        self.assertEqual(st.focus_params(t, None, False),
                         {"path": r"C:\Boards\Main\PCB1.PcbDoc", "open_if_closed": "false"})
        t = st.parse_target("document:PD_Mon.SchDoc")
        self.assertEqual(st.focus_params(t, ("C46",), True),
                         {"path": "PD_Mon.SchDoc", "open_if_closed": "true", "designators": ["C46"]})
        self.assertEqual(st.focus_params(st.parse_target("document"), ["C46"], False)["path"], "")
        board = st.focus_params(st.parse_target("document"), "board", False)
        self.assertEqual(board["zoom_board"], "true")
        self.assertNotIn("designators", board)

    def test_view_document(self):
        self.assertEqual(st.view_document("[24] PD_Mon.SchDoc"), "PD_Mon.SchDoc")
        self.assertEqual(st.view_document("[24] PD_Mon.SchDoc *"), "PD_Mon.SchDoc")
        self.assertEqual(st.view_document("PCB1.PcbDoc"), "PCB1.PcbDoc")
        self.assertEqual(st.view_document(None), "")

    def test_captured_metadata(self):
        keys = ("captured_kind", "captured_document", "view_type")

        def got(*args):
            m = st.captured_metadata(*args)
            return tuple(m[k] for k in keys)
        # Altium was asked: its answer counts, reduced to the file name
        self.assertEqual(got(st.Target("document", "x"), r"C:\a\PCB1.PcbDoc", "PCB", "ignored"),
                         ("PCB", "PCB1.PcbDoc", "pcb"))
        # Not asked: the view's title tells
        self.assertEqual(got(st.Target("current"), None, "", "[24] PD_Mon.SchDoc"),
                         ("SCH", "PD_Mon.SchDoc", "sch"))
        self.assertEqual(got(st.Target("document"), None, "", "Lib.PcbLib"),
                         ("PCBLIB", "Lib.PcbLib", None))
        self.assertEqual(got(st.Target("current"), None, "", "Home Page"), (None, "Home Page", None))
        self.assertEqual(got(st.Target("current"), None, "", None), (None, None, None))
        for t in (st.Target("dialog"), st.Target("panel", "Projects"), st.Target("region", region=(0, 0, 1, 1))):
            self.assertEqual(got(t, None, "", None), (None, None, None))

    def test_region_in_window(self):
        window = (0, 0, 3440, 1400)  # a "current" capture of a maximised window
        self.assertEqual(st.region_in_window((10, 100, 520, 400), window), (10, 100, 520, 400))
        # Relative to the window, also on a second monitor
        self.assertEqual(st.region_in_window((0, 0, 100, 50), (3440, 0, 4976, 824)),
                         (3440, 0, 3540, 50))
        # The whole window is allowed
        self.assertEqual(st.region_in_window((0, 0, 1536, 824), (3440, 0, 4976, 824)),
                         (3440, 0, 4976, 824))

    def test_region_outside_window_refused(self):
        window = (3440, 0, 4976, 824)
        for region in ((0, 0, 1537, 10),       # one pixel too wide
                       (0, 800, 100, 825),     # below the window
                       (-1, 0, 10, 10),        # left of it
                       (1600, 0, 1700, 10),    # entirely outside
                       (-3440, 0, -3000, 10)): # on the other monitor
            with self.assertRaises(st.TargetError):
                st.region_in_window(region, window)

    def test_covering(self):
        rect = (0, 0, 3440, 1400)
        terminal = st.Win(1, "CASCADIA_HOSTING_WINDOW_CLASS", "Terminal", (0, 900, 1600, 1400))
        other_monitor = st.Win(2, "Chrome_WidgetWin_1", "Browser", (3440, 0, 4976, 824))
        touching = st.Win(3, "X", "Touching", (3440, 0, 3500, 10))  # shares only an edge
        self.assertEqual(st.covering(rect, [terminal, other_monitor, touching]), [terminal])
        self.assertEqual(st.covering(rect, [other_monitor, touching]), [])
        self.assertEqual(st.covering(rect, []), [])

    def test_covering_error_names_programs_only(self):
        msg = st.covering_error(["chrome.exe", "chrome.exe", "WindowsTerminal.exe"])
        self.assertIn("Nothing was captured", msg)
        self.assertIn("chrome.exe, WindowsTerminal.exe lies", msg)
        self.assertEqual(msg.count("chrome.exe"), 1)
        self.assertIn("another program", st.covering_error([""]))
        self.assertIn("and 2 more", st.covering_error([f"p{i}.exe" for i in range(5)]))

    def test_clip(self):
        self.assertEqual(st.clip((-2, 0, 1906, 1030), (0, 0, 1920, 1080)), (0, 0, 1906, 1030))
        self.assertIsNone(st.clip((2000, 0, 2100, 10), (0, 0, 1920, 1080)))


class AltiumWindowsTest(unittest.TestCase):
    # Altium's top-level windows after run_drc and with Project Options open,
    # topmost first, as the board edit session saw them
    FRAME = win("TDocumentForm", "Main - Altium Designer", (0, 0, 1900, 1000), hwnd=1)
    OPTIONS = win("TProjectPropertiesDialog", "Options for PCB Project Adapter.PrjPcb",
                  (400, 200, 1300, 900), hwnd=2)
    MESSAGES = win("TClientBarControl", "Messages", (900, 500, 1800, 950), hwnd=3)
    HINT = win("THintWindow", "", (10, 10, 120, 30), hwnd=4)
    VIEW = (300, 100, 1800, 950)    # a document view

    def windows(self, *tops, enabled=True):
        return st.altium_windows([*tops, win("TApplication", ""), self.FRAME], 1, enabled)

    def test_classification(self):
        ws = self.windows(self.OPTIONS, self.MESSAGES, self.HINT, enabled=False)
        self.assertEqual([(w["hwnd"], w["kind"], w["modal"]) for w in ws],
                         [(2, "dialog", True), (3, "panel", False)])
        self.assertEqual(ws[0]["title"], "Options for PCB Project Adapter.PrjPcb")
        self.assertFalse(self.windows(self.OPTIONS)[0]["modal"])   # the main frame takes input
        self.assertEqual(self.windows(), [])

    def test_dialog_over_the_view_refuses(self):
        error, panels = st.altium_windows_over(self.VIEW, self.windows(self.OPTIONS, self.MESSAGES, enabled=False))
        self.assertIn("Nothing was captured", error)
        self.assertIn("TProjectPropertiesDialog", error)
        self.assertIn('target="dialog"', error)
        self.assertEqual(panels, [])

    def test_floating_panel_over_the_view_warns(self):
        error, panels = st.altium_windows_over(self.VIEW, self.windows(self.MESSAGES))
        self.assertIsNone(error)
        self.assertEqual([p["hwnd"] for p in panels], [3])
        self.assertEqual(panels[0]["covers_percent"], 32)   # 900x450 of 1500x850
        self.assertIn("hide_floating_panels", st.panel_warning(panels[0]))

    def test_windows_beside_the_capture_are_ignored(self):
        beside = (0, 0, 300, 1000)   # only touches the dialog's and the panel's edges, or misses them
        self.assertEqual(st.altium_windows_over(beside, self.windows(self.MESSAGES)), (None, []))
        aside = win("TProjectPropertiesDialog", "Options", (1850, 0, 2400, 400), hwnd=5)
        self.assertEqual(st.altium_windows_over(self.VIEW, self.windows(aside, enabled=False)), (None, []))

    def test_dismissable_only_dialogs(self):
        ws = self.windows(self.OPTIONS, self.MESSAGES, enabled=False)
        self.assertEqual(st.dismissable(ws, 2)[0]["hwnd"], 2)
        self.assertEqual(st.dismissable(ws, "2")[0]["hwnd"], 2)
        self.assertEqual(st.dismissable(ws, "options for pcb project  adapter.prjpcb")[0]["hwnd"], 2)
        for never in (1, 3, "Messages", "Main - Altium Designer", 99):   # frame, panel, unknown
            target, why = st.dismissable(ws, never)
            self.assertIsNone(target)
            self.assertIn("Open dialogs: 2 'Options for PCB Project Adapter.PrjPcb'", why)
        self.assertIsNone(st.dismissable(ws, "\u00b2")[0])   # isdigit() but no int: no crash
        numbered = [dict(ws[0], title="2", hwnd=8)]
        self.assertEqual(st.dismissable(numbered, "2")[0]["hwnd"], 8)   # an all-digit title
        twins = [dict(ws[0]), dict(ws[0], hwnd=7)]
        self.assertIn("give the hwnd", st.dismissable(twins, "Options for PCB Project Adapter.PrjPcb")[1])
class SaveAndPointerTest(unittest.TestCase):
    def test_save_target_inside_the_profile_only(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as elsewhere:
            os.mkdir(os.path.join(home, "evidence"))
            ok, why = st.save_target(os.path.join(home, "evidence", "top.png"), [home])
            self.assertEqual((ok, why), (os.path.realpath(os.path.join(home, "evidence", "top.png")), ""))
            for bad in (os.path.join(elsewhere, "top.png"),                    # outside the profile
                        os.path.join(home, "evidence", "..", "..", "x.png"),   # climbs out of it
                        os.path.join(home, "missing", "top.png"),              # no such folder
                        os.path.join(home, "top.jpg"), "top.png", "", None):
                self.assertIsNone(st.save_target(bad, [home])[0], bad)
            self.assertIn("user profile", st.save_target(os.path.join(elsewhere, "a.png"), [home])[1])

    def test_park_point(self):
        screen = (0, 0, 3840, 1080)
        view = (300, 100, 1800, 950)
        self.assertIsNone(st.park_point([view], screen, (100, 100)))     # not over a view: left alone
        self.assertEqual(st.park_point([view], screen, (900, 500)), (299, 100))
        split = [view, (1800, 100, 3000, 950)]                           # two views side by side
        self.assertEqual(st.park_point(split, screen, (2000, 500)), (299, 100))
        full = (0, 0, 1920, 1080)                                         # filling the left monitor
        self.assertEqual(st.park_point([full], screen, (5, 5)), (1920, 0))
        self.assertIsNone(st.park_point([screen], screen, (5, 5)))       # nowhere off the views
        self.assertIsNone(st.park_point([], screen, (5, 5)))


if __name__ == "__main__":
    unittest.main()
