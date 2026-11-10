"""How get_screenshot (view_type 'pcb'/'sch') finds Altium's main window and
which part of the window's pixels to keep.

Finding the window: by its process (X2.EXE), not by title alone. A browser tab
or an explorer window called "Altium ..." has the name in its title too, and
being maximised it was the largest such window and got captured.

The pixels: the capture copies the window's own surface (GetWindowDC). Altium
is system DPI aware, so on a monitor whose scale differs from the system's
Windows stretches that surface for display, and the surface is not the
window's size in physical pixels: at 125 % on a 100 % system it is 1552x840
for a 1940x1050 window, and the rest of the bitmap came back black. Reading
the window rectangle in the window's own DPI context gives the surface's
size. GetWindowRect also counts the invisible resize border (8 px around a
maximised window at 100 %), so the image is cut to the drawn frame
(DWMWA_EXTENDED_FRAME_BOUNDS, in physical pixels), scaled into the surface.

The choices are pure functions here, tested without windows.
"""
from typing import Dict, Iterable, Optional, Sequence, Tuple

Rect = Tuple[int, int, int, int]  # left, top, right, bottom


def _area(rect: Rect) -> int:
    l, t, r, b = rect
    return max(0, r - l) * max(0, b - t)


def pick_main_frame(windows: Sequence[Dict], altium_pids: Iterable[int]) -> Optional[Dict]:
    """Altium's main frame among top-level windows.

    windows: dicts with at least "title", "rect", "pid" and "iconic". Only
    windows of an Altium process with "Altium" in the title count; of those
    the largest that is not minimised (while Altium switches documents a
    small transient window can come first).
    """
    pids = set(altium_pids)
    own = [w for w in windows if w.get("pid") in pids and "Altium" in (w.get("title") or "")]
    if not own:
        return None
    shown = [w for w in own if not w.get("iconic")] or own
    return max(shown, key=lambda w: _area(w["rect"]))


def visible_crop(surface: Rect, physical: Rect, frame: Optional[Rect]) -> Rect:
    """The part of the window's surface that is drawn on screen.

    surface:  the window rectangle read in the window's own DPI context;
              its size is the size of the copied image.
    physical: the same window rectangle in physical pixels.
    frame:    the drawn frame (DWMWA_EXTENDED_FRAME_BOUNDS), physical pixels,
              or None when unknown.
    Returns (left, top, right, bottom) within the image.
    """
    w, h = surface[2] - surface[0], surface[3] - surface[1]
    full = (0, 0, w, h)
    pw, ph = physical[2] - physical[0], physical[3] - physical[1]
    if not frame or pw <= 0 or ph <= 0 or w <= 0 or h <= 0:
        return full
    sx, sy = w / pw, h / ph
    left = round((frame[0] - physical[0]) * sx)
    top = round((frame[1] - physical[1]) * sy)
    right = w - round((physical[2] - frame[2]) * sx)
    bottom = h - round((physical[3] - frame[3]) * sy)
    left, top = max(0, min(left, w)), max(0, min(top, h))
    right, bottom = max(0, min(right, w)), max(0, min(bottom, h))
    if right - left < 1 or bottom - top < 1:
        return full
    return (left, top, right, bottom)
