"""Capture a window's pixels via PrintWindow (works when screen-scraping can't).

CopyFromScreen only captures what is actually rendered on the desktop, so it
returns blank images when the session is remote/disconnected or the window is
obscured. PrintWindow asks the window to render itself into a DC instead.

Usage:
    python dev/capture_window.py <hwnd> <output.png>
    python dev/capture_window.py --find "<title substring>" <output.png>
    python dev/capture_window.py --dialogs <output_prefix>   (all modal dialogs)
"""
import ctypes
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

PW_RENDERFULLCONTENT = 2


def _title(hwnd):
    user32 = ctypes.windll.user32
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _class(hwnd):
    buf = ctypes.create_unicode_buffer(64)
    ctypes.windll.user32.GetClassNameW(hwnd, buf, 64)
    return buf.value


def capture(hwnd, out_path, flags=PW_RENDERFULLCONTENT):
    """Render a window to PNG using PrintWindow via System.Drawing."""
    rect = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0:
        return None, f"window has no size ({w}x{h})"

    out_path = str(Path(out_path).resolve())
    ps = f"""
Add-Type -AssemblyName System.Drawing
$sig = @'
using System;
using System.Runtime.InteropServices;
public class PW {{
  [DllImport("user32.dll")]
  public static extern bool PrintWindow(IntPtr hwnd, IntPtr hdc, uint flags);
}}
'@
Add-Type -TypeDefinition $sig
$bmp = New-Object System.Drawing.Bitmap {w}, {h}
$g = [System.Drawing.Graphics]::FromImage($bmp)
$hdc = $g.GetHdc()
$ok = [PW]::PrintWindow([IntPtr]{hwnd}, $hdc, {flags})
$g.ReleaseHdc($hdc)
$g.Dispose()
$bmp.Save('{out_path}', [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
Write-Output ("printwindow=" + $ok)
"""
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, timeout=60)
    p = Path(out_path)
    if p.exists() and p.stat().st_size > 0:
        return out_path, r.stdout.strip()
    return None, (r.stderr or r.stdout).strip()[:300]


def find_windows(pred):
    user32 = ctypes.windll.user32
    hits = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, lparam):
        if user32.IsWindowVisible(hwnd) and pred(hwnd):
            hits.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return hits


def find_dialogs():
    def is_dialog(hwnd):
        cls = _class(hwnd)
        if cls == "#32770":
            return True
        return cls == "TMessageForm" and _title(hwnd) in (
            "Error", "Warning", "Information", "Confirm")

    return find_windows(is_dialog)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    if args[0] == "--find":
        needle = args[1].lower()
        hits = find_windows(lambda h: needle in _title(h).lower())
        if not hits:
            print("no matching window")
            sys.exit(1)
        path, info = capture(hits[0], args[2])
        print(f"{_title(hits[0])!r} -> {path} ({info})")
    elif args[0] == "--dialogs":
        prefix = args[1] if len(args) > 1 else "dialog"
        hits = find_dialogs()
        if not hits:
            print("no dialogs visible")
            sys.exit(1)
        for i, h in enumerate(hits):
            path, info = capture(h, f"{prefix}_{i}.png")
            print(f"[{_class(h)}] {_title(h)!r} -> {path} ({info})")
    else:
        path, info = capture(int(args[0]), args[1])
        print(f"{path} ({info})")
