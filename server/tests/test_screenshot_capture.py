"""Desktop compositor capture coordinates and GDI cleanup contracts."""

import ast
from pathlib import Path
import unittest
from unittest.mock import Mock


class ScreenshotCaptureTest(unittest.TestCase):
    def stable_capture(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8"))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_grab_stable_window_image")
        grab = Mock(return_value="image")
        scope = {"_grab_window_image": grab}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "capture", "exec"), scope)
        return scope["_grab_stable_window_image"], grab

    def test_foreground_loss_refuses_wrong_app_images(self):
        capture, grab = self.stable_capture()
        with self.assertRaisesRegex(RuntimeError, "before capture"):
            capture(10, 100, 100, Mock(return_value=False))
        grab.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, "during capture"):
            capture(10, 100, 100, Mock(side_effect=[True, False]))
        self.assertEqual(capture(10, 100, 100, Mock(return_value=True)), "image")

    def load(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8"))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_grab_window_image")
        gui, ui, image = Mock(), Mock(), Mock()
        gui.GetWindowRect.return_value = (-1200, 40, 3, 1768)
        gui.GetDC.return_value = 42
        bitmap = ui.CreateBitmap.return_value
        bitmap.GetInfo.return_value = {"bmWidth": 1203, "bmHeight": 1728}
        bitmap.GetBitmapBits.return_value = b"pixels"
        scope = {"win32gui": gui, "win32ui": ui, "Image": image, "win32con": Mock(SRCCOPY=13369376)}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "capture", "exec"), scope)
        return scope["_grab_window_image"], gui, ui, image

    def test_captures_composed_desktop_at_physical_window_coordinates(self):
        capture, gui, ui, image = self.load()
        result = capture(10, 1203, 1728)
        self.assertIs(result, image.frombuffer.return_value)
        gui.GetDC.assert_called_once_with(0)
        gui.GetWindowDC.assert_not_called()
        source = ui.CreateDCFromHandle.return_value
        source.CreateCompatibleDC.return_value.BitBlt.assert_called_once_with(
            (0, 0), (1203, 1728), source, (-1200, 40), 13369376)
        gui.ReleaseDC.assert_called_once_with(0, 42)
        source.CreateCompatibleDC.return_value.DeleteDC.assert_called_once()
        gui.DeleteObject.assert_called_once()

    def test_failed_copy_releases_borrowed_desktop_dc(self):
        capture, gui, ui, _ = self.load()
        ui.CreateDCFromHandle.return_value.CreateCompatibleDC.return_value.BitBlt.side_effect = OSError("copy failed")
        with self.assertRaises(OSError):
            capture(10, 1203, 1728)
        gui.ReleaseDC.assert_called_once_with(0, 42)
        gui.DeleteObject.assert_called_once()


if __name__ == "__main__":
    unittest.main()
