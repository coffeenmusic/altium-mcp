"""Driving Altium's Engineering Change Order dialog (Design > Import Changes /
Update PCB) from outside: read its changes, validate, execute or close.

The dialog is a Delphi form (TChangeManagementForm). Its buttons are window
controls, clicked with posted mouse messages as Altium's buttons need. The
list of changes is a DevExpress tree with no window text, but Windows UI
Automation exposes each row as a list item whose Value is the row's text:
'True Remove R2A From Board.PcbDoc   <message>'. A group row reads 'Remove
Components(6)'. After Validate Changes a change that fails carries its
message ('Class already exists: X'); a change that passes has none. UI
Automation is reached through PowerShell, so the server needs no COM
package.

Validation and execution run on Altium's UI thread: a WM_NULL sent with
SendMessageTimeout returns once the click has been handled, which is how
the driver knows the work is done.
"""
import asyncio
import ctypes
import json
import re
import subprocess
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import List, Optional

from sandbox import _process_name

FORM_CLASS = "TChangeManagementForm"
WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON, WM_NULL = 0x0201, 0x0202, 0x0001, 0x0000
_proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

ROWS_SCRIPT = r"""
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$e = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]%d)
$c = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::ListItem)
$out = @()
foreach ($it in $e.FindAll([System.Windows.Automation.TreeScope]::Descendants, $c)) {
  $out += [string]$it.GetCurrentPropertyValue([System.Windows.Automation.ValuePattern]::ValueProperty)
}
ConvertTo-Json -InputObject @($out) -Compress
"""


@dataclass
class EcoOutcome:
    found: bool = False
    rows: List[dict] = field(default_factory=list)
    errors: List[dict] = field(default_factory=list)
    validated: bool = False
    executed: bool = False
    execute_errors: List[dict] = field(default_factory=list)
    closed: bool = False
    message: str = ""          # a message box shown instead of the dialog
    component_links: str = ""  # how the Component Links question was answered
    problem: str = ""


def _cls(h) -> str:
    b = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetClassNameW(h, b, 256)
    return b.value


def _text(h) -> str:
    b = ctypes.create_unicode_buffer(512)
    ctypes.windll.user32.SendMessageW(h, 0x000D, 512, b)
    return b.value


def _altium_windows(cls: str) -> List[int]:
    user32 = ctypes.windll.user32
    found = []

    def cb(h, _):
        if user32.IsWindowVisible(h) and _cls(h) == cls and _process_name(h).lower() == "x2.exe":
            found.append(h)
        return True
    user32.EnumWindows(_proto(cb), 0)
    return found


def _children(h) -> List[int]:
    kids = []
    ctypes.windll.user32.EnumChildWindows(h, _proto(lambda c, _: kids.append(c) or True), 0)
    return kids


def _control(form: int, cls: str, text: str = "") -> Optional[int]:
    for c in _children(form):
        if _cls(c) == cls and (not text or _text(c).replace("&", "") == text):
            return c
    return None


def _click(hwnd: int) -> None:
    user32 = ctypes.windll.user32
    r = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    lp = ((r.bottom // 2) << 16) | min(8, r.right // 2) if _cls(hwnd) == "TXPCheckBox" else \
        ((r.bottom // 2) << 16) | (r.right // 2)
    user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lp)
    user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lp)


def _wait_idle(hwnd: int, seconds: float) -> bool:
    """True once the window's thread has handled what was posted to it."""
    result = ctypes.c_size_t()
    return bool(ctypes.windll.user32.SendMessageTimeoutW(hwnd, WM_NULL, 0, 0, 0x0002, int(seconds * 1000),
                                                         ctypes.byref(result)))


def read_rows(tree: int) -> List[str]:
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ROWS_SCRIPT % tree],
                         capture_output=True, text=True, timeout=120, creationflags=0x08000000)
    try:
        rows = json.loads(out.stdout or "[]")
    except ValueError:
        raise RuntimeError(f"could not read the change list: {out.stderr.strip()[:300]}")
    return [r for r in rows if isinstance(r, str)]


_GROUP = re.compile(r"^\s*(.+?)\((\d+)\)\s*$")


def parse_rows(rows: List[str], board_name: str) -> List[dict]:
    """The changes, each with its group, enabled flag, text and message (the
    text after the affected document's name)."""
    out, group = [], ""
    for raw in rows:
        line = raw.replace("\t", " ").strip()
        m = _GROUP.match(line)
        if m and not line.startswith(("True ", "False ")):
            group = m.group(1).strip()
            continue
        enabled, _, rest = line.partition(" ")
        change, message = rest, ""
        i = rest.lower().rfind(board_name.lower()) if board_name else -1
        if i >= 0:
            change, message = rest[:i + len(board_name)], rest[i + len(board_name):]
        out.append({"group": group, "enabled": enabled == "True", "change": " ".join(change.split()),
                    "message": " ".join(message.split())})
    return out


async def drive(outcome: EcoOutcome, board_name: str, execute: bool, allow_errors: bool,
                auto_links: bool = False, appear_timeout: float = 120, work_timeout: float = 600) -> None:
    """Find the dialog, read and validate the changes, execute them when
    asked (and when none failed, or allow_errors), then close it. Any
    failure still closes the dialog, so the bridge command returns.

    When components on either side cannot be matched by unique id, Altium
    first asks how to link them (Component Links). Cancel goes on with the
    unique ids alone: unmatched parts are added and removed. auto_links
    answers Automatically Create Component Links instead, which pairs them
    by designator."""
    form = None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + appear_timeout
    while loop.time() < deadline:
        forms = _altium_windows(FORM_CLASS)
        if forms:
            form = forms[0]
            break
        for ask in _altium_windows("TThemedTaskDialog"):
            if _text(ask) != "Component Links":
                continue
            button = _control(ask, "TVistaSpeedButton", "Automatically Create Component Links") if auto_links \
                else _control(ask, "TXPBitBtn", "Cancel")
            if button is None:
                outcome.problem = "the Component Links question does not have the expected buttons"
                ctypes.windll.user32.PostMessageW(ask, 0x0010, 0, 0)
                return
            outcome.component_links = "automatic (by designator)" if auto_links else "unique id only"
            _click(button)
            deadline = loop.time() + appear_timeout
        for box in _altium_windows("TMessageForm"):
            # 'no differences' and the like: report and answer it
            labels = [_text(c) for c in _children(box) if _cls(c) in ("TLabel", "TXPLabel", "TStaticText")]
            outcome.message = " ".join(t for t in labels if t) or _text(box)
            ok = _control(box, "TXPBitBtn", "OK") or _control(box, "TButton", "OK")
            if ok:
                _click(ok)
            else:
                ctypes.windll.user32.PostMessageW(box, 0x0010, 0, 0)
            return
        await asyncio.sleep(0.3)
    if form is None:
        outcome.problem = "the Engineering Change Order dialog did not open"
        return
    outcome.found = True
    close = _control(form, "TXPBitBtn", "Close")
    try:
        await asyncio.sleep(0.5)
        tree = _control(form, "TdxTreeList")
        validate = _control(form, "TXPBitBtn", "Validate Changes")
        run = _control(form, "TXPBitBtn", "Execute Changes")
        if not (tree and validate and run and close):
            outcome.problem = "the dialog does not have the expected controls"
            return
        _wait_idle(form, work_timeout)
        _click(validate)
        await asyncio.sleep(0.2)
        if not await loop.run_in_executor(None, _wait_idle, form, work_timeout):
            outcome.problem = "validation did not finish in time"
            return
        outcome.validated = True
        outcome.rows = parse_rows(await loop.run_in_executor(None, read_rows, tree), board_name)
        outcome.errors = [r for r in outcome.rows if r["message"]]
        if execute and outcome.rows and (allow_errors or not outcome.errors):
            _click(run)
            await asyncio.sleep(0.2)
            if not await loop.run_in_executor(None, _wait_idle, form, work_timeout):
                outcome.problem = "executing the changes did not finish in time"
                return
            outcome.executed = True
            after = parse_rows(await loop.run_in_executor(None, read_rows, tree), board_name)
            outcome.execute_errors = [r for r in after if r["message"]]
    except Exception as e:     # never leave the dialog open: the bridge waits on it
        outcome.problem = f"{type(e).__name__}: {e}"
    finally:
        if close and ctypes.windll.user32.IsWindow(form):
            _click(close)
            for _ in range(50):
                if not ctypes.windll.user32.IsWindow(form) or not ctypes.windll.user32.IsWindowVisible(form):
                    outcome.closed = True
                    break
                await asyncio.sleep(0.2)
