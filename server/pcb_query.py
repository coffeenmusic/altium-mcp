"""Running Altium query expressions (the PCB Filter panel's language).

Two traps of the engine, both met on a real board:
  * Bare numbers are read in the engine's own unit, which is not the board's
    display unit (a board shown in mil was queried in mm), and unit suffixes
    ("15mil") are a syntax error. So values are given here with a unit, and
    the expression is sent written both ways, in mm and in mil; the bridge
    finds the engine's unit and runs the matching one.
  * Decimals must use a dot, whatever the Windows locale.

A malformed expression makes Altium stop with a modal "... expected at Line:2
... Continue execution?" dialog. watch_errors answers it (continue, so the
query returns empty) and keeps the message to report instead.
"""
import asyncio
import ctypes
import re
from ctypes import wintypes
from typing import List, Tuple

from sandbox import _process_name

_VALUE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?|\.\d+)\s*(mils?|mm|um|µm|in|inch)\b", re.I)
_TO_MM = {"mil": 0.0254, "mils": 0.0254, "mm": 1.0, "um": 0.001, "µm": 0.001, "in": 25.4, "inch": 25.4}


def _number(v: float) -> str:
    return f"{v:.6f}".rstrip("0").rstrip(".") or "0"


def with_units(expression: str) -> Tuple[str, str, List[str]]:
    """The expression with every value that has a unit written in mm and in
    mil, and the values found (for the reply)."""
    found = []

    def convert(scale):
        def sub(m):
            mm = float(m.group(1)) * _TO_MM[m.group(2).lower()]
            found.append(m.group(0))
            return _number(mm / scale)
        return sub

    in_mm = _VALUE.sub(convert(1.0), expression)
    in_mil = _VALUE.sub(convert(0.0254), expression)
    return in_mm, in_mil, found[: len(found) // 2]


def bare_numbers(expression: str) -> List[str]:
    """Numbers without a unit that look like sizes (compared with < > =), so
    the reply can warn that the engine reads them in its own unit."""
    stripped = _VALUE.sub("", expression)
    return re.findall(r"[<>=]\s*(\d+(?:\.\d+)?)(?![\w.])", stripped)


def check(expression: str) -> str:
    """'' when the expression can be sent, else what is wrong."""
    if not expression.strip():
        return "the expression is empty"
    if any(c in expression for c in '"\r\n'):
        return 'the expression cannot contain " or a line break; quote names with \' as Altium does'
    if re.search(r"\d,\d", _VALUE.sub("", expression)):
        return "write decimals with a dot (0.25mm), not a comma"
    return ""


_proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _text(h) -> str:
    b = ctypes.create_unicode_buffer(512)
    ctypes.windll.user32.SendMessageW(h, 0x000D, 512, b)
    return b.value


def _cls(h) -> str:
    b = ctypes.create_unicode_buffer(64)
    ctypes.windll.user32.GetClassNameW(h, b, 64)
    return b.value


def _error_dialogs():
    user32 = ctypes.windll.user32
    found = []

    def cb(h, _):
        if user32.IsWindowVisible(h) and _cls(h) == "#32770" and _process_name(h).lower() == "x2.exe":
            kids = []
            user32.EnumChildWindows(h, _proto(lambda c, _: kids.append(c) or True), 0)
            message = next((_text(c) for c in kids
                            if _cls(c) == "Static" and "Continue execution" in _text(c)), "")
            ok = next((c for c in kids if _text(c) == "OK"), None)
            if message and ok:
                found.append((h, message, ok))
        return True
    user32.EnumWindows(_proto(cb), 0)
    return found


def _message_boxes(title: str):
    user32 = ctypes.windll.user32
    found = []

    def cb(h, _):
        if (user32.IsWindowVisible(h) and _cls(h) == "TMessageForm" and _text(h) == title
                and _process_name(h).lower() == "x2.exe"):
            found.append(h)
        return True
    user32.EnumWindows(_proto(cb), 0)
    return found


def _press_ok(box) -> None:
    """Altium's TXPBitBtn ignores BM_CLICK; posted mouse messages work."""
    user32 = ctypes.windll.user32
    kids = []
    user32.EnumChildWindows(box, _proto(lambda c, _: kids.append(c) or True), 0)
    ok = next((c for c in kids if _text(c) == "OK"), None)
    if ok is None:
        user32.PostMessageW(box, 0x0010, 0, 0)
        return
    r = wintypes.RECT()
    user32.GetClientRect(ok, ctypes.byref(r))
    lp = ((r.bottom // 2) << 16) | (r.right // 2)
    user32.PostMessageW(ok, 0x0201, 1, lp)
    user32.PostMessageW(ok, 0x0202, 0, lp)


async def watch_errors(errors: List[str], abort: asyncio.Event) -> None:
    """Answer Altium's script error dialogs while a query runs. Runs until
    cancelled by the caller.

    A syntax error stops the query once: continuing lets it return empty. An
    unknown name ("Undeclared identifier: InPad") fails again for every
    object tested, one dialog each, so a second dialog cancels the script and
    sets abort, and the caller stops waiting for it."""
    while True:
        # Altium's own "Error" box (comparing a text property with a number,
        # for one): its text is not readable, and closing it lets the query end
        for h in _message_boxes("Error"):
            errors.append("Altium reported an error while evaluating it (a property compared with a "
                          "value of the wrong type, e.g. Name = 1 instead of Name = '1'?)")
            _press_ok(h)
        for h, message, ok in _error_dialogs():
            errors.append(" ".join(message.split()).replace(" Continue execution?", ""))
            if len(errors) == 1:
                ctypes.windll.user32.SendMessageW(ok, 0x00F5, 0, 0)        # BM_CLICK: continue
            else:
                ctypes.windll.user32.PostMessageW(h, 0x0111, 2, 0)        # WM_COMMAND IDCANCEL
                abort.set()
        await asyncio.sleep(0.2)
