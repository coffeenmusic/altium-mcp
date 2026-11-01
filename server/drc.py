"""Running Altium's design rule check from the MCP server.

PCB:DesignRuleCheck opens the modal "Design Rule Checker" dialog and waits
for someone to press "Run Design Rule Check...". No script call runs the
check without it (IPCB_Board.RunBatchDesignRuleCheck exists but fails for
every argument list), so while the bridge command waits, drive_dialog
presses that button from outside Altium. Altium's TXPBitBtn ignores
BM_CLICK; posted mouse messages addressed to the button work.

Cancelling the dialog leaves no violations, exactly like a board that passes.
So a result only counts as a check that ran when the button was pressed and
the dialog closed, and the report Altium writes is read to confirm it.
"""
import asyncio
import ctypes
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from sandbox import _process_name, _window_text

DIALOG_CLASS = "TDesignRuleCheckForm"
RUN_BUTTON = "run design rule check"
WM_CLOSE, WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON = 0x0010, 0x0201, 0x0202, 0x0001


def find_dialog() -> Optional[int]:
    """The visible Design Rule Checker dialog of X2.EXE, if one is open."""
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def top(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            cls = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, cls, 64)
            if cls.value == DIALOG_CLASS and _process_name(hwnd).lower() == "x2.exe":
                found.append(hwnd)
        return True

    user32.EnumWindows(proto(top), 0)
    return found[0] if found else None


def _run_button(dialog: int) -> Optional[int]:
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def child(hwnd, _):
        if RUN_BUTTON in _window_text(hwnd).replace("&", "").lower():
            found.append(hwnd)
        return True

    user32.EnumChildWindows(dialog, proto(child), 0)
    return found[0] if found else None


def _is_open(hwnd: int) -> bool:
    user32 = ctypes.windll.user32
    return bool(user32.IsWindow(hwnd) and user32.IsWindowVisible(hwnd))


@dataclass
class DialogOutcome:
    pressed: bool = False      # the Run button was pressed and the dialog closed
    cancelled: bool = False    # the dialog would not run and was closed (no check)
    seen: bool = False
    notes: List[str] = field(default_factory=list)


async def drive_dialog(outcome: DialogOutcome, progress_file: Path, keep_alive: float = 5.0,
                       settle: float = 0.6, attempts: int = 3, press_wait: float = 5.0) -> None:
    """Press Run in the Design Rule Checker dialog when it opens, then keep the
    bridge's progress file fresh while the check runs, so a long check is not
    taken for a hung command. Runs until cancelled by the caller."""
    user32 = ctypes.windll.user32
    while not outcome.pressed and not outcome.cancelled:
        dialog = find_dialog()
        if dialog is None:
            await asyncio.sleep(0.2)
            continue
        outcome.seen = True
        await asyncio.sleep(settle)   # the buttons answer only once the form is up
        for n in range(attempts):
            button = _run_button(dialog)
            if button is None:
                outcome.notes.append("the dialog has no Run Design Rule Check button")
                break
            user32.PostMessageW(button, WM_LBUTTONDOWN, MK_LBUTTON, 0)
            user32.PostMessageW(button, WM_LBUTTONUP, 0, 0)
            for _ in range(max(1, int(press_wait / 0.25))):
                await asyncio.sleep(0.25)
                if not _is_open(dialog):
                    outcome.pressed = True
                    break
            if outcome.pressed:
                break
            outcome.notes.append(f"press {n + 1} did not close the dialog")
        if not outcome.pressed:
            # Never leave Altium blocked behind a modal dialog
            user32.PostMessageW(dialog, WM_CLOSE, 0, 0)
            outcome.cancelled = True
    while True:
        try:
            progress_file.touch()
        except OSError:
            pass
        await asyncio.sleep(keep_alive)


_RULE_RE = re.compile(r"^Processing Rule : (.*)$")
_COUNT_RE = re.compile(r"^Rule Violations :\s*(\d+)")
_TOTAL_RE = re.compile(r"^Violations Detected :\s*(\d+)")
_WAIVED_RE = re.compile(r"^Waived Violations :\s*(\d+)")


def parse_report(text: str) -> dict:
    """Per-rule violation counts and totals from a .drc report."""
    rules, current = [], None
    total = waived = None
    for line in text.splitlines():
        line = line.strip()
        m = _RULE_RE.match(line)
        if m:
            current = m.group(1).strip()
            continue
        m = _COUNT_RE.match(line)
        if m and current is not None:
            rules.append({"rule": current, "violations": int(m.group(1))})
            current = None
            continue
        m = _TOTAL_RE.match(line)
        if m:
            total = int(m.group(1))
        m = _WAIVED_RE.match(line)
        if m:
            waived = int(m.group(1))
    return {"violations": total, "waived": waived, "rules": rules}


def find_report(board_path: str, since: float) -> Optional[Path]:
    """The .drc report written for this board after `since`. Altium writes it
    beside the board, or into the project's output folder."""
    board = Path(board_path)
    if not board.parent.is_dir():
        return None
    candidates = []
    pattern = f"Design Rule Check - {board.stem}.drc"
    for folder in [board.parent] + [p for p in board.parent.iterdir() if p.is_dir()]:
        for p in list(folder.glob(pattern)) + list(folder.glob(f"*/{pattern}")):
            try:
                if p.stat().st_mtime >= since - 2:
                    candidates.append(p)
            except OSError:
                pass
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None
