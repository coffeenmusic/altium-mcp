"""Unit tests for screenshot_target (pure Python, no Altium needed).

Run from the repo root:  python -m unittest server.tests.test_screenshot_target -v
or from server/:          python -m unittest tests.test_screenshot_target -v
"""
import os
import sys
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

    def test_clip(self):
        self.assertEqual(st.clip((-2, 0, 1906, 1030), (0, 0, 1920, 1080)), (0, 0, 1906, 1030))
        self.assertIsNone(st.clip((2000, 0, 2100, 10), (0, 0, 1920, 1080)))


if __name__ == "__main__":
    unittest.main()
