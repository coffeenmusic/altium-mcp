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

The driver starts once the bridge has picked up the request and runs until
the bridge call returns. It answers or closes every Altium dialog that opens
meanwhile (the Component Links question, message boxes such as "no
differences", anything else modal), with WM_CLOSE when a button does not
close it, so Altium is never left waiting on a dialog. Only Altium's
(X2.EXE) windows are looked at, and those open when it started are left
alone. The window calls go through Win32, which tests replace.
"""
import asyncio
import ctypes
import json
import ntpath
import re
import subprocess
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set, Tuple

FORM_CLASS = "TChangeManagementForm"
LINKS_CAPTION = "Component Links"
# Message boxes: Delphi's MessageDlg, Altium's themed task dialog, Windows'
MESSAGE_CLASSES = ("TMessageForm", "TThemedTaskDialog", "#32770")
BUTTON_CLASSES = ("TXPBitBtn", "TButton", "TBitBtn", "TVistaSpeedButton", "Button")
# Answers to a message box, the most cautious first: declining a question
# never applies anything the validation did not see
DISMISS_ORDER = ("Cancel", "No", "Close", "OK")
# Never closed as an unknown dialog: hints, tooltips, menus
IGNORED_CLASSES = ("THintWindow", "tooltips_class32", "#32768")
# A message box that reports a failure rather than informs
_ERROR_TEXT = re.compile(r"error|failed|exception|access violation|could not|cannot|unable", re.IGNORECASE)
WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON, WM_NULL = 0x0201, 0x0202, 0x0001, 0x0000
WM_CLOSE, WM_GETTEXT, SMTO_ABORTIFHUNG = 0x0010, 0x000D, 0x0002
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
    validation_problems: List[str] = field(default_factory=list)  # the change list did not check out
    not_executed: List[str] = field(default_factory=list)          # why Execute Changes was not pressed
    execute_pressed: bool = False   # Execute Changes was clicked (its outcome may still be unknown)
    executed: bool = False          # ...and Altium finished it and the list was read again
    executed_changes: List[dict] = field(default_factory=list)
    execute_errors: List[dict] = field(default_factory=list)
    closed: bool = False
    messages: List[str] = field(default_factory=list)  # message boxes answered or closed
    error_messages: List[str] = field(default_factory=list)  # those of them that report a failure
    component_links: str = ""  # how the Component Links question was answered
    problem: str = ""


class Win32:
    """The window calls the driver makes, on Altium's (X2.exe) windows."""

    def __init__(self):
        self.user32 = ctypes.windll.user32

    def windows(self) -> Set[int]:
        """Altium's visible top-level windows."""
        from sandbox import _process_name
        found = set()

        def cb(h, _):
            if self.user32.IsWindowVisible(h) and _process_name(h).lower() == "x2.exe":
                found.add(h)
            return True
        self.user32.EnumWindows(_proto(cb), 0)
        return found

    def cls(self, h) -> str:
        b = ctypes.create_unicode_buffer(256)
        self.user32.GetClassNameW(h, b, 256)
        return b.value

    def text(self, h) -> str:
        # With a timeout: a busy Altium must not hang the server
        b = ctypes.create_unicode_buffer(512)
        result = ctypes.c_size_t()
        if not self.user32.SendMessageTimeoutW(h, WM_GETTEXT, 512, b, SMTO_ABORTIFHUNG, 2000, ctypes.byref(result)):
            return ""
        return b.value

    def children(self, h) -> List[int]:
        kids = []
        self.user32.EnumChildWindows(h, _proto(lambda c, _: kids.append(c) or True), 0)
        return kids

    def alive(self, h) -> bool:
        return bool(self.user32.IsWindow(h) and self.user32.IsWindowVisible(h))

    def enabled(self, h) -> bool:
        return bool(self.user32.IsWindowEnabled(h))

    def click(self, h) -> None:
        r = wintypes.RECT()
        self.user32.GetClientRect(h, ctypes.byref(r))
        lp = ((r.bottom // 2) << 16) | (r.right // 2)
        self.user32.PostMessageW(h, WM_LBUTTONDOWN, MK_LBUTTON, lp)
        self.user32.PostMessageW(h, WM_LBUTTONUP, 0, lp)

    def close(self, h) -> None:
        self.user32.PostMessageW(h, WM_CLOSE, 0, 0)

    def wait_idle(self, h, seconds: float) -> bool:
        """True once the window's thread has handled what was posted to it.
        Blocks: run it in an executor."""
        result = ctypes.c_size_t()
        return bool(self.user32.SendMessageTimeoutW(h, WM_NULL, 0, 0, SMTO_ABORTIFHUNG,
                                                    max(1, int(seconds * 1000)), ctypes.byref(result)))

    def read_rows(self, tree: int) -> List[str]:
        """The change list's rows, through UI Automation. Blocks."""
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ROWS_SCRIPT % tree],
                             capture_output=True, text=True, timeout=120, creationflags=0x08000000)
        try:
            rows = json.loads(out.stdout or "[]")
        except ValueError:
            raise RuntimeError(f"could not read the change list: {out.stderr.strip()[:300]}")
        return [r for r in rows if isinstance(r, str)]


def open_windows(win: Optional[Win32] = None) -> Set[int]:
    """Altium's windows open now: the driver leaves these alone."""
    return (win or Win32()).windows()


def open_eco_dialogs(win: Optional[Win32] = None) -> List[int]:
    """Engineering Change Order dialogs open in Altium now."""
    win = win or Win32()
    return [h for h in win.windows() if win.cls(h) == FORM_CLASS]


def project_boards(prj_text: str, project_path: str) -> List[str]:
    """The boards a .PrjPcb lists (DocumentPath=...PcbDoc), as full paths;
    relative paths are relative to the project's folder."""
    folder = ntpath.dirname(project_path)
    return [ntpath.normpath(ntpath.join(folder, m.group(1).strip()))
            for m in re.finditer(r"^DocumentPath=(.+?)\s*$", prj_text, re.MULTILINE)
            if m.group(1).strip().lower().endswith(".pcbdoc")]


def board_in_project(board: str, prj_text: str, project_path: str) -> bool:
    """Whether board is one of the project's boards (case, separators and
    '..' ignored)."""
    want = ntpath.normcase(ntpath.normpath(board))
    return any(ntpath.normcase(b) == want for b in project_boards(prj_text, project_path))


_GROUP = re.compile(r"^\s*(.+?)\((\d+)\)\s*$")


def parse_list(rows: List[str], board_name: str) -> Tuple[List[dict], List[str]]:
    """The changes, each with its group, enabled flag, text and message (the
    text after the affected document's name), and what does not check out:
    a change row without the board's name (its message cannot be told from
    its text), a change outside a group, or a group whose count differs from
    the rows read under it (a collapsed or unread group). A row that does
    not start with True or False counts as enabled, so the removal checks
    do not miss it."""
    out, problems = [], []
    group = None
    declared: Dict[str, int] = {}
    read: Dict[str, int] = {}
    for raw in rows:
        line = raw.replace("\t", " ").strip()
        if not line:
            continue
        m = _GROUP.match(line)
        if m and not line.startswith(("True ", "False ")):
            group = m.group(1).strip()
            declared[group] = declared.get(group, 0) + int(m.group(2))
            read.setdefault(group, 0)
            continue
        flag, _, rest = line.partition(" ")
        if flag not in ("True", "False"):
            flag, rest = "", line
        change, message = rest, ""
        i = rest.lower().rfind(board_name.lower()) if board_name else -1
        if i >= 0:
            change, message = rest[:i + len(board_name)], rest[i + len(board_name):]
        else:
            problems.append(f"a change does not name the board {board_name}: {' '.join(line.split())[:200]}")
        if group is None:
            problems.append(f"a change outside any group: {' '.join(line.split())[:200]}")
        else:
            read[group] += 1
        out.append({"group": group or "", "enabled": flag != "False", "change": " ".join(change.split()),
                    "message": " ".join(message.split())})
    for name, count in declared.items():
        if read.get(name, 0) != count:
            problems.append(f"group '{name}' lists {count} change(s) but {read.get(name, 0)} were read")
    return out, problems


def parse_rows(rows: List[str], board_name: str) -> List[dict]:
    """The changes alone (see parse_list)."""
    return parse_list(rows, board_name)[0]


def is_component_removal(row: dict) -> bool:
    return row["group"].strip().lower() == "remove components"


def is_room_removal(row: dict) -> bool:
    return row["group"].strip().lower() == "remove rooms"


def removals(rows: List[dict], allow_component_removal: bool = False,
             allow_room_removal: bool = False) -> List[str]:
    """The removals in Altium's own change list that were not allowed."""
    out = []
    parts = [r for r in rows if r["enabled"] and is_component_removal(r)]
    if parts and not allow_component_removal:
        out.append(f"the update would remove {len(parts)} component(s) from the board "
                   "(allow_component_removal=true if that is intended)")
    rooms = [r for r in rows if r["enabled"] and is_room_removal(r)]
    if rooms and not allow_room_removal:
        out.append(f"the update would remove {len(rooms)} room(s) (allow_room_removal=true if that is intended)")
    return out


def execute_blockers(rows: List[dict], problems: List[str], allow_errors: bool,
                     allow_component_removal: bool, allow_room_removal: bool = False) -> List[str]:
    """Why Execute Changes must not be pressed; empty when it may be: a
    clean read of the list, no failed change (or allow_errors), and no
    component or room removal in it unless allowed."""
    out = list(problems)
    if not rows:
        out.append("the dialog lists no changes")
    failed = [r for r in rows if r["message"]]
    if failed and not allow_errors:
        out.append(f"{len(failed)} change(s) failed validation (allow_errors=true applies the rest)")
    return out + removals(rows, allow_component_removal, allow_room_removal)


class Driver:
    """One Import Changes run: answers what Altium opens until stopped."""

    def __init__(self, win, outcome: EcoOutcome, board_name: str, execute: bool, allow_errors: bool,
                 auto_links: bool, before: Set[int], allow_component_removal: bool = False,
                 allow_room_removal: bool = False, keep_alive: Optional[Callable[[], None]] = None,
                 work_timeout: float = 600, grace: float = 10, poll: float = 0.3, answer_wait: float = 2,
                 idle_slice: float = 5):
        self.win, self.outcome, self.board_name = win, outcome, board_name
        self.execute, self.allow_errors, self.auto_links = execute, allow_errors, auto_links
        self.allow_component_removal, self.allow_room_removal = allow_component_removal, allow_room_removal
        self.keep_alive = keep_alive                     # called while Altium works on the dialog
        self.before = set(before)
        self.work_timeout, self.grace, self.poll = work_timeout, grace, poll
        self.idle_slice = idle_slice                     # one wait for Altium's UI thread, at most
        self.answer_wait = answer_wait                   # a box's button gets this long to close it
        self.handled: Set[int] = set()
        self.first_seen: Dict[int, float] = {}
        self.closing: Dict[int, Tuple[float, bool]] = {}  # window -> (deadline, WM_CLOSE posted)
        self.blocked: List[str] = []                     # reasons found before the dialog opened

    def _now(self) -> float:
        return asyncio.get_running_loop().time()

    def _control(self, form: int, classes, text: str = "") -> Optional[int]:
        for c in self.win.children(form):
            if self.win.cls(c) in classes and (not text or self.win.text(c).replace("&", "") == text):
                return c
        return None

    def _message(self, h: int) -> str:
        texts = [self.win.text(h)]
        for c in self.win.children(h):
            if self.win.cls(c) not in BUTTON_CLASSES:
                texts.append(self.win.text(c))
        return " ".join(" ".join(t.split()) for t in texts if t and t.strip())

    def _alive_signal(self) -> None:
        if self.keep_alive is not None:
            try:
                self.keep_alive()
            except Exception:
                pass

    def _dismiss(self, h: int) -> None:
        """Answer a message box with its most cautious button; WM_CLOSE
        follows when that does not close it."""
        text = self._message(h) or self.win.cls(h)
        self.outcome.messages.append(text)
        if _ERROR_TEXT.search(text):
            self.outcome.error_messages.append(text)
        for label in DISMISS_ORDER:
            button = self._control(h, BUTTON_CLASSES, label)
            if button:
                self.win.click(button)
                self.closing[h] = (self._now() + self.answer_wait, False)
                return
        self.win.close(h)
        self.closing[h] = (self._now() + 2 * self.answer_wait, True)

    def _links(self, h: int) -> None:
        if self.auto_links:
            button = self._control(h, BUTTON_CLASSES, "Automatically Create Component Links")
        else:
            button = self._control(h, BUTTON_CLASSES, "Cancel")
        if button:
            self.outcome.component_links = "automatic (by designator)" if self.auto_links else "unique id only"
            self.win.click(button)
            self.closing[h] = (self._now() + self.answer_wait, False)
            return
        # Closing it is Cancel: unique ids alone
        self.outcome.component_links = "unique id only (the question was closed)"
        if self.auto_links:
            self.blocked.append("the Component Links question did not offer Automatically Create Component "
                                "Links; it was closed, which keeps unique-id matching")
        self.win.close(h)
        self.closing[h] = (self._now() + 2 * self.answer_wait, True)

    def _frame_disabled(self) -> bool:
        """Altium is showing something modal: a window open before the run
        (its main window) is disabled."""
        return any(self.win.alive(h) and not self.win.enabled(h) for h in self.before)

    def _follow_up(self) -> None:
        """WM_CLOSE for an answered box still open, and a problem for one
        that stays open after that."""
        now = self._now()
        for h, (deadline, posted) in list(self.closing.items()):
            if not self.win.alive(h):
                del self.closing[h]
            elif now >= deadline:
                if posted:
                    self.outcome.problem = self.outcome.problem or \
                        f"could not close Altium's window '{self._message(h) or self.win.cls(h)}'"
                    del self.closing[h]
                else:
                    self.win.close(h)
                    self.closing[h] = (now + 2 * self.answer_wait, True)

    def sweep(self, unknown: bool = True) -> Tuple[List[int], bool]:
        """Handle the new windows: (ECO dialogs found, whether a box was
        answered). unknown=False leaves unrecognised windows alone, as while
        Altium is validating or executing (a progress window must not be
        closed)."""
        forms, answered = [], False
        self._follow_up()
        self.handled = {h for h in self.handled if self.win.alive(h)}
        for h in sorted(self.win.windows() - self.before - self.handled):
            if not self.win.alive(h):
                continue
            cls = self.win.cls(h)
            if cls == FORM_CLASS:
                self.handled.add(h)
                forms.append(h)
            elif cls in MESSAGE_CLASSES and self.win.text(h) == LINKS_CAPTION:
                self.handled.add(h)
                self._links(h)
                answered = True
            elif cls in MESSAGE_CLASSES:
                self.handled.add(h)
                self._dismiss(h)
                answered = True
            elif unknown and cls not in IGNORED_CLASSES and "progress" not in cls.lower() \
                    and self.win.enabled(h) and self._frame_disabled():
                # Something else modal: closed once it has stayed a while
                first = self.first_seen.setdefault(h, self._now())
                if self._now() - first >= self.grace:
                    self.handled.add(h)
                    self._dismiss(h)
                    answered = True
        return forms, answered

    async def run(self, stop: asyncio.Event) -> None:
        """Until stop is set (the bridge call returned)."""
        while not stop.is_set():
            forms, _ = self.sweep()
            for form in forms:
                if self.outcome.found:
                    # A second ECO dialog in one run: not ours to apply
                    self.outcome.messages.append("a second Engineering Change Order dialog opened; it was closed")
                    await self._close_form(form, None)
                else:
                    await self._work(form)
            try:
                await asyncio.wait_for(stop.wait(), self.poll)
            except asyncio.TimeoutError:
                pass
        self._follow_up()

    async def _settle(self, form: int, what: str) -> bool:
        """Wait until Altium has handled a click, answering boxes it shows
        meanwhile. The wait goes in slices, each one a sign to the bridge
        (keep_alive) that Altium is still working, up to work_timeout."""
        loop = asyncio.get_running_loop()
        deadline = self._now() + self.work_timeout
        while True:
            self._alive_signal()
            left = deadline - self._now()
            if left <= 0:
                self.outcome.problem = f"{what} did not finish in time"
                return False
            if not self.win.alive(form):
                self.outcome.problem = f"the Engineering Change Order dialog went away during {what}"
                return False
            if not await loop.run_in_executor(None, self.win.wait_idle, form, min(left, self.idle_slice)):
                # Busy, or taken for hung while it works: wait on
                await asyncio.sleep(min(0.5, max(0.0, deadline - self._now())))
                continue
            _, answered = self.sweep(unknown=False)
            if not answered and not self.closing:
                return True
            await asyncio.sleep(0.5)

    async def _work(self, form: int) -> None:
        """Validate, read, execute when allowed, then close the dialog."""
        o = self.outcome
        o.found = True
        loop = asyncio.get_running_loop()
        close = None
        answered_before = len(o.messages)        # boxes answered before the dialog came
        try:
            await asyncio.sleep(0.5)
            close = self._control(form, BUTTON_CLASSES, "Close")
            tree = self._control(form, ("TdxTreeList",))
            validate = self._control(form, BUTTON_CLASSES, "Validate Changes")
            run = self._control(form, BUTTON_CLASSES, "Execute Changes")
            if not (tree and validate and run):
                o.problem = "the dialog does not have the expected controls"
                return
            if not await self._settle(form, "opening the dialog"):
                return
            self.win.click(validate)
            await asyncio.sleep(0.2)
            if not await self._settle(form, "validation"):
                return
            o.validated = True
            rows, problems = parse_list(await loop.run_in_executor(None, self.win.read_rows, tree),
                                        self.board_name)
            o.rows, o.validation_problems = rows, problems
            o.errors = [r for r in rows if r["message"]]
            if not self.execute:
                return
            blockers = self.blocked + execute_blockers(rows, problems, self.allow_errors,
                                                       self.allow_component_removal, self.allow_room_removal)
            # A box answered while the dialog opened or validated (Cancel
            # first) may have changed what the list means; a box that would
            # not close leaves Altium in an unknown state
            during = o.messages[answered_before:]
            if during:
                blockers.append(f"Altium showed {len(during)} message(s) while the changes were validated, "
                                f"answered or closed from here: " + " | ".join(during)[:400])
            if o.problem:
                blockers.append(o.problem)
            o.not_executed = blockers
            if o.not_executed:
                return
            self.win.click(run)
            o.execute_pressed = True
            await asyncio.sleep(0.2)
            if not await self._settle(form, "executing the changes"):
                return
            o.executed = True
            after = parse_rows(await loop.run_in_executor(None, self.win.read_rows, tree), self.board_name)
            o.execute_errors = [r for r in after if r["message"]]
            o.executed_changes = [{"group": r["group"], "change": r["change"]}
                                  for r in after if r["enabled"] and not r["message"]]
        except Exception as e:     # never leave the dialog open: the bridge waits on it
            o.problem = f"{type(e).__name__}: {e}"
        finally:
            await self._close_form(form, close)

    async def _close_form(self, form: int, close: Optional[int]) -> None:
        """Close with its Close button, else (or when that does not close
        it) with WM_CLOSE."""
        for attempt in ("button", "WM_CLOSE"):
            if not self.win.alive(form):
                break
            if attempt == "button":
                if close is None:
                    continue
                self.win.click(close)
            else:
                self.win.close(form)
            for _ in range(50):
                if not self.win.alive(form):
                    break
                self.sweep(unknown=False)
                await asyncio.sleep(0.2)
        self.outcome.closed = not self.win.alive(form)
        if not self.outcome.closed:
            self.outcome.problem = self.outcome.problem or "the Engineering Change Order dialog could not be closed"


async def drive(outcome: EcoOutcome, board_name: str, execute: bool, allow_errors: bool,
                auto_links: bool, stop: asyncio.Event, before: Set[int],
                allow_component_removal: bool = False, win=None, allow_room_removal: bool = False,
                keep_alive: Optional[Callable[[], None]] = None, **timing) -> None:
    """Drive one Import Changes run until stop is set: find the dialog,
    validate and read the changes, execute them when asked and the list
    checked out (see execute_blockers), close it, and answer or close every
    other dialog Altium opens meanwhile.

    When components on either side cannot be matched by unique id, Altium
    first asks how to link them (Component Links). Cancel goes on with the
    unique ids alone: unmatched parts are added and removed. auto_links
    answers Automatically Create Component Links instead, which pairs them
    by designator."""
    driver = Driver(win or Win32(), outcome, board_name, execute, allow_errors, auto_links, before,
                    allow_component_removal, allow_room_removal, keep_alive, **timing)
    await driver.run(stop)
