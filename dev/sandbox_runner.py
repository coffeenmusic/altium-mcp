"""Run a DelphiScript experiment in an isolated Altium sandbox project.

Usage:
    python dev/sandbox_runner.py <experiment.pas> [timeout_seconds]
    python dev/sandbox_runner.py -                 (body from stdin)

Why this exists: Altium has no headless test mode for DelphiScript. Its
failure modes are hostile to automation - a runtime error leaves the script
PAUSED IN THE DEBUGGER with no dialog, and every later RunScript silently
does nothing ("wedged executor"); compile errors show only a modal dialog.

How it works:
- The experiment body is injected between the BEGIN/END EXPERIMENT markers of
  dev/sandbox/Sandbox.pas, a STANDALONE script project. A broken experiment
  can never break the production Altium_API tooling.
- Altium recompiles scripts on every invocation, so no restart between runs.
- SandboxLog() flushes after every call: if the log stops mid-experiment, the
  statement after the last logged line is what crashed.
- Dialogs are read (not just dismissed) so compile/runtime error text is
  reported, and "another script is running" is detected explicitly.

Experiment body rules:
- Call SandboxLog('...') before each risky statement
- Assign findings to ResultText (a String) - it is written to sandbox_result.json
- Wrap independent probes in try/except so one failure doesn't hide the rest
- Pascal has no inline declarations: reuse the scratch variables declared in
  Sandbox.pas (S1..S3, I1..I3, B1, Obj1..Obj3, List1), or add more there
"""
import ctypes
import json
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SANDBOX_PAS = REPO / "dev" / "sandbox" / "Sandbox.pas"
SANDBOX_PRJ = REPO / "dev" / "sandbox" / "Sandbox.PrjScr"
EXCHANGE = Path("C:/Users/Public/altium_mcp")
SANDBOX_LOG = EXCHANGE / "sandbox_log.txt"
SANDBOX_RESULT = EXCHANGE / "sandbox_result.json"
BEGIN = "// === BEGIN EXPERIMENT"
END = "// === END EXPERIMENT"

config = json.load(open(REPO / "server" / "config.json"))

sys.path.insert(0, str(Path(__file__).resolve().parent))
from unwedge import unwedge  # noqa: E402


def _title(hwnd):
    user32 = ctypes.windll.user32
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def screenshot_dialog(hwnd, tag):
    """Render the dialog to PNG via PrintWindow.

    PrintWindow asks the window to paint itself into a DC, so it works even
    when the window is obscured or the desktop is not rendering (remote /
    disconnected sessions) - unlike screen-scraping, which returns blanks.
    """
    try:
        from capture_window import capture
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from capture_window import capture
    try:
        out = Path.cwd() / f"dialog_{tag}.png"
        path, _info = capture(hwnd, out)
        return path
    except Exception:
        return None


def read_dialog_text(hwnd):
    """Read a dialog's message text (UI Automation; Delphi dialogs are
    custom-drawn so child window titles are usually just class names)."""
    try:
        ps = (
            "Add-Type -AssemblyName UIAutomationClient;"
            "Add-Type -AssemblyName UIAutomationTypes;"
            f"$el=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]{hwnd});"
            "$all=$el.FindAll([System.Windows.Automation.TreeScope]::Descendants,"
            "[System.Windows.Automation.Condition]::TrueCondition);"
            "foreach($e in $all){if($e.Current.Name){Write-Output $e.Current.Name}}"
        )
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=25)
        texts = [l.strip() for l in r.stdout.splitlines()
                 if l.strip() and l.strip() not in ("OK", "Cancel")
                 and not l.strip().startswith(("XPPanels.", "TXP", "Panel"))]
        return " | ".join(texts[:6])
    except Exception as e:
        return f"(could not read: {e})"


def capture_script_editor(tag="script_editor"):
    """Capture Altium's document windows, including the script editor.

    When a script dies, the editor highlights the exact line execution stopped
    on - and unlike error dialogs, that highlight exists even for silent
    debugger pauses (which show no dialog at all). Altium hosts each document
    frame in its own top-level window, so all of them are captured; the one
    showing a .pas tab is the script editor.
    """
    try:
        from capture_window import capture, find_windows, _title
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from capture_window import capture, find_windows, _title

    hits = find_windows(lambda h: "Altium Designer" in _title(h))
    # Script-project frames first - most likely to host the editor
    hits.sort(key=lambda h: ".PrjScr" not in _title(h))
    out = []
    for i, h in enumerate(hits[:4]):
        path, _info = capture(h, Path.cwd() / f"{tag}_{i}.png")
        if path:
            out.append(path)
    return out


def find_dialogs():
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value == "#32770":
            found.append((hwnd, cls.value, _title(hwnd)))
        elif cls.value == "TMessageForm":
            t = _title(hwnd)
            if t in ("Error", "Warning", "Information", "Confirm"):
                found.append((hwnd, cls.value, t))
        return True

    user32.EnumWindows(cb, 0)
    return found


def handle_dialogs():
    """Read then close any modal dialogs; return [(cls, title, text)]."""
    out = []
    for idx, (hwnd, cls, title) in enumerate(find_dialogs()):
        text = read_dialog_text(hwnd)
        shot = screenshot_dialog(hwnd, f"{title}_{idx}")
        if shot:
            text = (text + f"  [screenshot: {shot}]") if text else f"[screenshot: {shot}]"
        out.append((cls, title, text))
        ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)
    return out


def inject(body: str):
    src = SANDBOX_PAS.read_text(encoding="utf-8")
    pre, rest = src.split(BEGIN, 1)
    marker_line, rest = rest.split("\n", 1)
    _, post = rest.split(END, 1)
    indented = "\n".join("        " + line if line.strip() else line
                         for line in body.strip("\n").splitlines())
    SANDBOX_PAS.write_text(
        pre + BEGIN + marker_line + "\n" + indented + "\n        " + END + post,
        encoding="utf-8")


def run(timeout=120, quiet=False):
    for f in (SANDBOX_LOG, SANDBOX_RESULT):
        if f.exists():
            f.unlink()

    cmd = (f'"{config["altium_exe_path"]}" -RScriptingSystem:RunScript('
           f'ProjectName="{SANDBOX_PRJ}"^|ProcName="Sandbox>Run")')
    subprocess.Popen(cmd, shell=True)

    start = time.time()
    dialogs = []
    while not SANDBOX_RESULT.exists() and time.time() - start < timeout:
        time.sleep(0.5)
        if time.time() - start > 6:
            d = handle_dialogs()
            for cls, title, text in d:
                dialogs.append((cls, title, text))
                print(f"  [DIALOG {cls} '{title}'] {text or '(no text)'}")
                if "another script" in (text or "").lower():
                    print("  >> A previous script is still running/paused in Altium.")
                    print("  >> Fix: Altium script editor -> Run -> Stop, or restart Altium.")
    time.sleep(0.3)

    if quiet:
        return SANDBOX_RESULT.exists()

    print("=" * 62)
    if SANDBOX_LOG.exists():
        lines = SANDBOX_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        print("STEP LOG:")
        for line in lines:
            print("  |", line)
        if SANDBOX_RESULT.exists():
            print("-" * 62)
            print("RESULT:", SANDBOX_RESULT.read_text(encoding="utf-8", errors="replace")[:3000])
            return True
        print("-" * 62)
        print(f">> Script STOPPED after: {lines[-1] if lines else '(nothing)'}")
        print(">> The statement AFTER that step is what crashed or paused.")
        print(">> Clearing the paused debugger (Ctrl+F3) so the next run works...")
        unwedge(verbose=False)
        for shot in capture_script_editor():
            print(f">> Altium window captured (look for the paused line): {shot}")
    else:
        print("STEP LOG: (missing - the script never started)")
        print(">> Usually a COMPILE error (see dialog text above), or a")
        print(">> previously paused script blocking execution.")
        for shot in capture_script_editor():
            print(f">> Altium window captured: {shot}")
    return False


PING_BODY = "SandboxLog('ping');\nResultText := '{\"ping\": true}';"


def altium_running():
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq X2.EXE"],
                       capture_output=True, text=True)
    return "X2.EXE" in r.stdout


def restart_altium(wait=180):
    """Force-restart Altium to clear a wedged script executor.

    WARNING: kills X2.EXE - any unsaved Altium work is lost. Only used when
    --auto-restart is passed explicitly.
    """
    print("  >> restarting Altium to clear the wedged executor...")
    subprocess.run(["taskkill", "/F", "/IM", "X2.EXE"], capture_output=True)
    time.sleep(5)
    subprocess.Popen(f'"{config["altium_exe_path"]}"', shell=True)
    # Altium is ready when a sandbox ping actually executes
    saved = SANDBOX_PAS.read_text(encoding="utf-8")
    inject(PING_BODY)
    start = time.time()
    try:
        while time.time() - start < wait:
            time.sleep(10)
            handle_dialogs()
            if run(timeout=45, quiet=True):
                print(f"  >> Altium ready after {int(time.time() - start)}s")
                return True
        print("  >> Altium did not become ready in time")
        return False
    finally:
        SANDBOX_PAS.write_text(saved, encoding="utf-8")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if not args:
        print(__doc__)
        sys.exit(1)
    body = sys.stdin.read() if args[0] == "-" else Path(args[0]).read_text(encoding="utf-8")
    timeout = int(args[1]) if len(args) > 1 else 120

    inject(body)
    print(f"injected {len(body.strip().splitlines())} lines -> running sandbox...")
    ok = run(timeout=timeout)

    if not ok and "--auto-restart" in flags:
        if not SANDBOX_LOG.exists():
            print("\n>> no step log: executor was likely already wedged - retrying after restart")
        else:
            print("\n>> experiment crashed the script (executor now wedged) - restarting to retry")
        if restart_altium():
            inject(body)
            print("re-running experiment on the fresh instance...")
            ok = run(timeout=timeout)
    elif not ok:
        print("\n>> Executor is now WEDGED. Re-run with --auto-restart, or")
        print(">> restart Altium manually before the next experiment.")

    sys.exit(0 if ok else 1)
