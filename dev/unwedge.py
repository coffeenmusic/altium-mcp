"""Clear a wedged Altium script executor by stopping the debugger (Ctrl+F3).

A DelphiScript runtime error leaves the script paused in the debugger. While
paused, every later RunScript silently does nothing. Altium's "Stop Debugging"
shortcut (Ctrl+F3) clears that state without restarting Altium, turning a
~90 second restart into a ~2 second recovery.

Usage:
    python dev/unwedge.py          # stop debugger + close leftover dialogs
"""
import ctypes
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture_window import find_windows, _title, _class


def close_dialogs():
    """Close modal error dialogs (they block key input to the editor)."""
    def is_dialog(h):
        cls = _class(h)
        return cls == "#32770" or (
            cls == "TMessageForm" and _title(h) in
            ("Error", "Warning", "Information", "Confirm"))

    hits = find_windows(is_dialog)
    for h in hits:
        ctypes.windll.user32.PostMessageW(h, 0x0010, 0, 0)
    return len(hits)


def stop_debugger():
    """Send Ctrl+F3 (Stop Debugging) to an Altium script-project window."""
    hits = find_windows(lambda h: "Altium Designer" in _title(h))
    # Ctrl+F3 only reaches the script editor, whose window title is the open
    # script file (e.g. "Sandbox.pas - Altium Designer") or the project
    def rank(h):
        t = _title(h)
        return (0 if ".pas" in t else 1 if ".PrjScr" in t else 2)
    hits.sort(key=rank)
    if not hits:
        return False, "no Altium window found"

    hwnd = hits[0]
    ps = f"""
Add-Type -AssemblyName System.Windows.Forms
$sig = @'
using System;
using System.Runtime.InteropServices;
public class FG {{
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
}}
'@
Add-Type -TypeDefinition $sig
[FG]::ShowWindow([IntPtr]{hwnd}, 9) | Out-Null   # SW_RESTORE
[FG]::SetForegroundWindow([IntPtr]{hwnd}) | Out-Null
Start-Sleep -Milliseconds 400
[System.Windows.Forms.SendKeys]::SendWait('^{{F3}}')
Start-Sleep -Milliseconds 400
Write-Output 'sent'
"""
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, timeout=60)
    ok = "sent" in r.stdout
    return ok, (r.stdout or r.stderr).strip()[:200]


def unwedge(verbose=True):
    n = close_dialogs()
    if verbose and n:
        print(f"closed {n} dialog(s)")
    ok, info = stop_debugger()
    if verbose:
        print(f"stop debugger (Ctrl+F3): {'sent' if ok else 'FAILED'} ({info})")
    close_dialogs()
    return ok


if __name__ == "__main__":
    unwedge()
