"""Via stitching with Altium's own "Add Stitching to Net".

PCB:DoViaStitching (Mode=Flood) opens a modal dialog and stitches when OK is
pressed. It ignores process parameters, and a stitching group's settings are
not reachable from a script, so while the bridge command waits, drive_dialog
fills the dialog in and presses OK from outside Altium, as run_drc does for
the Design Rule Checker:

  * The net comes from the selection: the bridge selects one primitive of the
    net before it opens the dialog. drive_dialog checks the net box shows it.
  * Grid, via-pad clearance, pullback, hole and diameter are plain edit
    controls set with WM_SETTEXT, in mils.
  * The checkboxes are Altium's own controls that answer no state query.
    One is read by clicking it once and comparing how it is drawn before and
    after: the picture with the tick has more pixels off the box's own
    background, whatever the theme's colours. They draw and take clicks
    reliably only while the dialog is the active window, so the dialog is
    brought to the front first; the window that was in front gets the
    foreground back afterwards. The result's stagger is measured from the
    vias placed.

A dialog left open blocks every later bridge command, so each driver waits
for its dialog only so long, closes it when it gives up or is cancelled, and
answers only the message boxes it recognises; any other box is closed
without an answer, which is Cancel or No.

A group's stored settings (grid, stagger, clearances) are read from the saved
.PcbDoc, where each group is a union record with UNIONTYPE=2.
"""
import asyncio
import ctypes
import re
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from sandbox import _process_name

DIALOG_CLASS = "TViaStitching_FloodOptionsForm"
MESSAGE_CLASSES = ("TMessageForm", "#32770")
BUTTON_CLASSES = ("TXPBitBtn", "TButton", "TBitBtn", "Button")
WM_SETTEXT, WM_GETTEXT, WM_CLOSE = 0x000C, 0x000D, 0x0010
WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON = 0x0201, 0x0202, 0x0001

# Seconds from the bridge picking the command up to the dialog showing
APPEAR_WAIT = 20.0
# Seconds Altium may take to start and pick a command up (the bridge's own
# limit for a cold start)
ALTIUM_START_WAIT = 240.0
# Seconds after OK during which the bridge is told the command is still busy
BUSY_LIMIT = 600.0

# The settings the dialog takes, in the order they are filled in
SETTINGS = ("grid_mils", "via_diameter_mils", "via_hole_mils", "via_pad_clearance_mils", "pullback_mils")

_proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def check_settings(settings: Dict[str, Optional[float]]) -> str:
    """'' when the settings can go to Altium, else what is wrong."""
    for key in SETTINGS:
        v = settings.get(key)
        if v is None:
            continue
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            return f"{key} must be a number"
        if v < 0 or (v == 0 and key in ("grid_mils", "via_diameter_mils", "via_hole_mils")):
            return f"{key} must be positive"
    d, h = settings.get("via_diameter_mils"), settings.get("via_hole_mils")
    if d is not None and h is not None and h >= d:
        return "via_hole_mils must be smaller than via_diameter_mils"
    return ""


def valid_union_index(value) -> bool:
    """Whether value names a via group. Ungrouped vias have union index 0, so
    removing "group 0" would remove every via that is not in a group."""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def mil_text(v: float) -> str:
    return f"{round(float(v), 4):g}mil"


# --- stored settings -------------------------------------------------------

_UNIT = re.compile(r"^(-?[\d.]+)(mil|mm)?$")


def _mils(text: Optional[str]) -> Optional[float]:
    m = _UNIT.match((text or "").strip())
    if not m:
        return None
    v = float(m.group(1))
    return round(v / 0.0254 if m.group(2) == "mm" else v, 4)


def mils_of(text: str) -> Optional[float]:
    """A dialog value such as '0.254mm' or '100mil' in mils."""
    return _mils(text)


def _union_records(data: bytes, union_type: str) -> Dict[int, Dict[str, str]]:
    """{union index: fields} of the union records of one type in a stream.
    Each record is a null-terminated text of |KEY=VALUE pairs after a binary
    length, so the text before its first '|' is not a field."""
    out = {}
    for chunk in data.split(b"\x00"):
        if b"UNIONTYPE=" not in chunk:
            continue
        fields = {}
        for kv in chunk.decode("latin-1").split("|")[1:]:
            k, sep, v = kv.partition("=")
            if sep:
                fields.setdefault(k.strip().upper(), v)
        if fields.get("UNIONTYPE") == union_type and fields.get("FUNIONINDEX", "").isdigit():
            out[int(fields["FUNIONINDEX"])] = fields
    return out


def stored_groups(data: bytes) -> Dict[int, dict]:
    """The stitching groups (UNIONTYPE=2) in a .PcbDoc stream, by union index."""
    out = {}
    for index, fields in _union_records(data, "2").items():
        out[index] = {
            "net": fields.get("NETNAME", ""),
            "grid_mils": _mils(fields.get("STEP")),
            "stagger": fields.get("STAGGER", "").upper() == "TRUE",
            "via_pad_clearance_mils": _mils(fields.get("VIAPADCLEARANCE")),
            "pullback_mils": _mils(fields.get("PULLBACK")),
            "via_diameter_mils": _mils(fields.get("DIAMETER")),
            "via_hole_mils": _mils(fields.get("HOLESIZE")),
            "constrained_to_area": fields.get("ISCONSTRAINAREA", "").upper() == "TRUE",
        }
    return out


def stored_shielding(data: bytes) -> Dict[int, dict]:
    """The shielding groups (UNIONTYPE=6) in a .PcbDoc stream, by union index."""
    out = {}
    for index, fields in _union_records(data, "6").items():
        out[index] = {
            "shielded_net": fields.get("NETNAME", ""),
            "via_net": fields.get("VIANETNAME", ""),
            "via_spacing_mils": _mils(fields.get("STEP")),
            "distance_mils": _mils(fields.get("DISTANCE")),
            "rows": int(fields["ROWCOUNT"]) if fields.get("ROWCOUNT", "").isdigit() else None,
            "add_copper": fields.get("ADDCOPPER", "").upper() == "TRUE",
            "add_cutout": fields.get("ADDPOLYCUTOUT", "").upper() == "TRUE",
        }
    return out


def read_stored(board_path: str) -> Tuple[Dict[int, dict], Dict[int, dict]]:
    """(stitching groups, shielding groups) saved in a .PcbDoc. The unions
    live in their own storage; when none is found there, every storage's
    Data stream is searched."""
    import pcblib_file

    if not board_path:
        return {}, {}

    def parse(streams):
        stitching, shielding = {}, {}
        for data in streams.values():
            stitching.update(stored_groups(data))
            shielding.update(stored_shielding(data))
        return stitching, shielding

    stitching, shielding = parse(pcblib_file.storage_streams(board_path, "Data", lambda n: "union" in n.lower()))
    if not stitching and not shielding:
        stitching, shielding = parse(pcblib_file.storage_streams(board_path, "Data"))
    return stitching, shielding


def stored_net_problem(group: dict, stored: dict) -> str:
    """'' when the saved group is on the net the board shows for it, else why
    the saved settings cannot be trusted for it."""
    board_net, saved_net = group.get("net", ""), stored.get("net", "")
    if saved_net.upper() == str(board_net).upper():
        return ""
    return (f"Group {group.get('union_index')} is on net {board_net!r} on the board but on "
            f"{saved_net!r} in the saved file, so its saved settings belong to something else. "
            "Save the board and try again.")


# --- windows -----------------------------------------------------------------

def _cls(h) -> str:
    b = ctypes.create_unicode_buffer(128)
    ctypes.windll.user32.GetClassNameW(h, b, 128)
    return b.value


def _text(h) -> str:
    b = ctypes.create_unicode_buffer(512)
    ctypes.windll.user32.SendMessageW(h, WM_GETTEXT, 512, b)
    return b.value


def _is_open(hwnd: int) -> bool:
    user32 = ctypes.windll.user32
    return bool(user32.IsWindow(hwnd) and user32.IsWindowVisible(hwnd))


def _top_windows(classes) -> List[int]:
    """Visible top-level windows of Altium (X2.EXE) of the given classes."""
    user32 = ctypes.windll.user32
    if isinstance(classes, str):
        classes = (classes,)
    found = []

    def cb(h, _):
        if user32.IsWindowVisible(h) and _cls(h) in classes and _process_name(h).lower() == "x2.exe":
            found.append(h)
        return True
    user32.EnumWindows(_proto(cb), 0)
    return found


def _post_close(hwnd: int) -> None:
    """Close a window as its close box would: a dialog or message box closed
    this way is cancelled, never confirmed."""
    ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def close_dialogs(cls: str) -> int:
    """Close every open Altium dialog of a class without confirming it."""
    found = _top_windows(cls)
    for h in found:
        _post_close(h)
    return len(found)


@dataclass
class Control:
    hwnd: int
    cls: str
    text: str
    group: str
    x: int
    y: int


def _controls(dialog: int) -> List[Control]:
    user32 = ctypes.windll.user32
    out = []

    def group_of(h):
        p = user32.GetParent(h)
        while p and p != dialog:
            if _cls(p) == "TXPGroupBox":
                return _text(p).strip()
            p = user32.GetParent(p)
        return ""

    def cb(h, _):
        if user32.IsWindowVisible(h):
            r = wintypes.RECT()
            user32.GetWindowRect(h, ctypes.byref(r))
            out.append(Control(h, _cls(h), _text(h), group_of(h), r.left, r.top))
        return True
    user32.EnumChildWindows(dialog, _proto(cb), 0)
    return out


def _click(hwnd: int, at_left: bool = False) -> None:
    user32 = ctypes.windll.user32
    r = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    x = min(8, r.right // 2) if at_left else r.right // 2
    lp = ((r.bottom // 2) << 16) | x
    user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lp)
    user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lp)


def bring_to_front(hwnd: int) -> bool:
    """Make the window the active one. Windows lets only the foreground
    thread change the foreground, so attach to its input for the call."""
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    fg = user32.GetForegroundWindow()
    if fg == hwnd:
        return True
    theirs = user32.GetWindowThreadProcessId(fg, None)
    mine = kernel32.GetCurrentThreadId()
    attached = bool(theirs and theirs != mine and user32.AttachThreadInput(mine, theirs, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(mine, theirs, False)
    return user32.GetForegroundWindow() == hwnd


def foreground_window() -> int:
    return ctypes.windll.user32.GetForegroundWindow() or 0


def should_hand_back(previous_process: str, front_process: str) -> bool:
    """Whether the window that was in front before a dialog was driven gets
    the foreground back: only when it was not Altium's and Altium is still
    in front (not when the user has moved on by themselves)."""
    return bool(previous_process) and previous_process.lower() != "x2.exe" and front_process.lower() == "x2.exe"


def hand_foreground_back(previous: int) -> bool:
    """Give the foreground back to the window that had it before the dialog
    was brought to the front, as get_screenshot does."""
    user32 = ctypes.windll.user32
    if not previous or not user32.IsWindow(previous):
        return False
    front = user32.GetForegroundWindow()
    if not should_hand_back(_process_name(previous), _process_name(front) if front else ""):
        return False
    return bring_to_front(previous)


# --- checkboxes --------------------------------------------------------------

def _snapshot(hwnd: int):
    """The control as drawn, whether or not it is covered on screen."""
    from PIL import Image
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return None
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mem, bmp)
    try:
        user32.PrintWindow(hwnd, mem, 0)

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                        ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]
        bi = BITMAPINFOHEADER()
        bi.biSize, bi.biWidth, bi.biHeight = ctypes.sizeof(bi), w, -h
        bi.biPlanes, bi.biBitCount = 1, 32
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
        return Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", 0, 1).convert("L")
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(hwnd, hdc)


def tick_ink(image) -> int:
    """How many pixels inside a checkbox's square (at the control's left,
    inside the border, which lights up under the mouse) stand out from the
    square's own background. An empty square is one flat colour; a tick
    stands out from it, light on dark or dark on light alike."""
    h = image.height
    inner = image.crop((4, 4, max(5, h - 4), max(5, h - 4)))
    pixels = sorted(inner.tobytes())
    background = pixels[len(pixels) // 2]
    return sum(1 for p in pixels if abs(p - background) > 60)


def tick_from_pair(before, after, min_diff: int = 4) -> Optional[bool]:
    """Whether a checkbox was ticked in `before`, from pictures of the same
    box before and after one click: the ticked picture has the more ink.
    None when the two cannot be told apart (the click did not take, or the
    box draws no tick that stands out)."""
    a, b = tick_ink(before), tick_ink(after)
    if abs(a - b) < min_diff:
        return None
    return a > b


async def _settled_image(hwnd: int, tries: int = 12, agree: int = 3):
    """The control's picture once it is the same several times running: a
    box caught while the dialog repaints is drawn half."""
    last, run = None, 0
    for _ in range(tries):
        img = _snapshot(hwnd)
        data = img.tobytes() if img is not None else None
        if data is not None and data == last:
            run += 1
            if run >= agree:
                return img
        else:
            last, run = data, 1 if data is not None else 0
        await asyncio.sleep(0.25)
    return None


async def set_checkbox(hwnd: int, want: bool) -> Optional[bool]:
    """Set one of Altium's checkboxes and return its state, or None when it
    cannot be told. It is clicked once and the two pictures compared; if
    that was the wrong way it is clicked back and must look as before."""
    before = await _settled_image(hwnd)
    if before is None:
        return None
    _click(hwnd, at_left=True)
    await asyncio.sleep(0.4)
    after = await _settled_image(hwnd)
    if after is None:
        return None
    was = tick_from_pair(before, after)
    if was is None:
        return None
    if (not was) == want:
        return want
    _click(hwnd, at_left=True)
    await asyncio.sleep(0.4)
    final = await _settled_image(hwnd)
    if final is None or tick_from_pair(final, after) != was:
        return None
    return was


def staggered(rows, grid_mils: float) -> Optional[bool]:
    """Whether stitching rows [(y, first x), ...] are staggered: alternate
    rows offset by half the grid. None when it cannot be told."""
    rows = sorted(rows)
    if len(rows) < 2 or not grid_mils:
        return None
    offsets = []
    for (y1, x1), (y2, x2) in zip(rows, rows[1:]):
        if abs((y2 - y1) - grid_mils) > 0.5:
            continue    # not neighbouring rows
        d = (x2 - x1) % grid_mils
        offsets.append(min(d, grid_mils - d))
    if not offsets:
        return None
    half = sum(1 for d in offsets if abs(d - grid_mils / 2) < 0.5)
    return half > len(offsets) / 2


# --- message boxes -----------------------------------------------------------

def message_answer(title: str, buttons, confirm: Optional[bool]) -> Optional[str]:
    """The button to press on an Altium message box, or None to close it
    without an answer (Cancel or No). A box with only OK just reports
    something; the one question recognised is the Yes/No Confirm Add
    Shielding to Net asks when the net is already shielded, answered only
    where confirm says how (None: no question is expected here)."""
    names = {b.replace("&", "").strip() for b in buttons}
    if names == {"OK"}:
        return "OK"
    if confirm is not None and title == "Confirm" and names == {"Yes", "No"}:
        return "Yes" if confirm else "No"
    return None


def _buttons(box: int) -> Dict[str, int]:
    return {c.text.replace("&", "").strip(): c.hwnd for c in _controls(box) if c.cls in BUTTON_CLASSES}


def answer_messages(outcome, ignore: Set[int], confirm: Optional[bool] = None) -> None:
    """Answer or close the Altium message boxes that opened since the driver
    started (those in ignore were already open). A box still open after its
    answer is closed."""
    for box in _top_windows(MESSAGE_CLASSES):
        if box in ignore:
            continue
        if box in outcome.handled:
            _post_close(box)
            continue
        outcome.handled.add(box)
        title = _text(box)
        outcome.messages.append(title)
        buttons = _buttons(box)
        answer = message_answer(title, buttons, confirm)
        if answer is not None and answer in buttons:
            _click(buttons[answer])
            if answer != "OK":
                outcome.notes.append(f"answered {answer} to Altium's {title!r} box")
        else:
            _post_close(box)
            outcome.notes.append(f"closed Altium's {title!r} box ({'/'.join(sorted(buttons)) or 'no buttons'}) "
                                 "without answering it")


@dataclass
class StitchOutcome:
    pressed: bool = False       # OK was pressed and the dialog closed
    cancelled: bool = False     # the dialog was closed without stitching
    seen: bool = False
    net_shown: str = ""
    layer_pair: str = ""
    applied: Dict[str, str] = field(default_factory=dict)
    messages: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    handled: Set[int] = field(default_factory=set)


def open_message_boxes() -> Set[int]:
    return set(_top_windows(MESSAGE_CLASSES))


async def _wait_for_dialog(cls: str, outcome, ignore: Set[int], started: Optional[Callable[[], bool]],
                           appear_wait: float, max_wait: Optional[float] = None) -> Optional[int]:
    """The dialog once it opens, or None after appear_wait seconds counted
    from the bridge picking the command up (started), and in any case after
    max_wait seconds (by default appear_wait plus the time Altium may take
    to start). Message boxes that open meanwhile are answered or closed."""
    begin, since = time.monotonic(), None
    max_wait = appear_wait + ALTIUM_START_WAIT if max_wait is None else max_wait
    while True:
        found = _top_windows(cls)
        if found:
            return found[0]
        answer_messages(outcome, ignore)
        now = time.monotonic()
        if since is None and (started is None or started()):
            since = now
        if (since is not None and now - since > appear_wait) or now - begin > max_wait:
            return None
        await asyncio.sleep(0.2)


async def _close_late(cls: str, outcome, ignore: Set[int]) -> None:
    """After giving up on the dialog: close it if it still opens, so it does
    not block Altium. Runs until cancelled by the caller."""
    while True:
        if close_dialogs(cls):
            outcome.notes.append("the dialog opened too late and was closed")
            outcome.cancelled = True
        answer_messages(outcome, ignore)
        await asyncio.sleep(0.5)


async def _press_ok(dialog: int, controls: List[Control], outcome, cancel, press_wait: float) -> None:
    user32 = ctypes.windll.user32
    ok = next((c for c in controls if c.cls == "TXPBitBtn" and c.text.replace("&", "") == "OK"), None)
    if ok is None:
        cancel("the dialog has no OK button")
        return
    _click(ok.hwnd)
    for _ in range(int(press_wait / 0.25)):
        await asyncio.sleep(0.25)
        if not (user32.IsWindow(dialog) and user32.IsWindowVisible(dialog)):
            outcome.pressed = True
            return
    cancel("pressing OK did not close the dialog")


async def drive_dialog(outcome: StitchOutcome, net: str, settings: Dict[str, Optional[float]],
                       stagger: Optional[bool], progress_file: Path, toggle_stagger: bool = False,
                       started: Optional[Callable[[], bool]] = None, appear_wait: float = APPEAR_WAIT,
                       settle: float = 0.8, press_wait: float = 6.0, keep_alive: float = 5.0) -> None:
    """Fill in Add Stitching to Net when it opens, press OK, then close the
    message Altium shows afterwards. Runs until cancelled by the caller; the
    dialog is closed unconfirmed when this gives up, fails or is cancelled.

    toggle_stagger clicks Stagger once without reading it: the dialog opens
    with the setting Altium last used, so when that is known (measured from
    the last stitching) one click gives the other."""
    ignore = open_message_boxes()
    dialog = None

    def cancel(why: str):
        outcome.notes.append(why)
        _post_close(dialog)
        outcome.cancelled = True

    try:
        dialog = await _wait_for_dialog(DIALOG_CLASS, outcome, ignore, started, appear_wait)
        if dialog is None:
            outcome.notes.append(f"the dialog did not open within {appear_wait:g} s")
            await _close_late(DIALOG_CLASS, outcome, ignore)
            return
        outcome.seen = True
        await asyncio.sleep(settle)   # the controls answer only once the form is up
        if not bring_to_front(dialog):
            outcome.notes.append("the dialog could not be made the active window")

        controls = _controls(dialog)
        props = [c for c in controls if c.group == "Properties" and c.cls == "TXPComboBox"]
        outcome.layer_pair = next((c.text for c in props if " - " in c.text), "")
        outcome.net_shown = next((c.text for c in props if " - " not in c.text), "")
        if outcome.net_shown.upper() != net.upper():
            cancel(f"the dialog opened on net {outcome.net_shown!r}, not {net!r}")
        else:
            await _fill_stitching(dialog, controls, outcome, settings, stagger, toggle_stagger, cancel, press_wait)
        # Altium works only after OK; a closed dialog leaves nothing to wait for
        await close_messages(outcome, progress_file, keep_alive, ignore,
                             busy_limit=BUSY_LIMIT if outcome.pressed else 0)
    finally:
        # Never leave Altium blocked behind the dialog
        if dialog is not None and not outcome.pressed and _is_open(dialog):
            _post_close(dialog)
            outcome.cancelled = True


async def _fill_stitching(dialog, controls, outcome, settings, stagger, toggle_stagger, cancel, press_wait):
    user32 = ctypes.windll.user32
    simple = next((c for c in controls if c.group == "Diameters" and c.text.replace("&", "") == "Simple"), None)
    if simple is not None:
        _click(simple.hwnd, at_left=True)
        await asyncio.sleep(0.4)
        controls = _controls(dialog)
    edits = {}
    grid = [c for c in controls if c.group == "Stitching Parameters" and c.cls == "TEditCoord"]
    if grid:
        edits["grid_mils"] = max(grid, key=lambda c: c.y)
    clear = sorted((c for c in controls if c.group == "Same Net Clearances" and c.cls == "TEditCoord"),
                   key=lambda c: c.y)
    if len(clear) == 2:
        edits["via_pad_clearance_mils"], edits["pullback_mils"] = clear
    via = sorted((c for c in controls if c.group == "Via Style" and c.cls == "TEditCoord"
                  and c.text != "N/A" and user32.IsWindowEnabled(c.hwnd)), key=lambda c: c.x)
    if len(via) == 2:
        edits["via_hole_mils"], edits["via_diameter_mils"] = via
    missing = [k for k in SETTINGS if settings.get(k) is not None and k not in edits]
    if missing:
        cancel(f"could not find the dialog's fields for {', '.join(missing)}")
        return
    for key in SETTINGS:
        v = settings.get(key)
        if v is not None:
            user32.SendMessageW(edits[key].hwnd, WM_SETTEXT, 0, ctypes.c_wchar_p(mil_text(v)))
    boxes = {c.text: c for c in controls if c.cls == "TXPCheckBox"}

    # An area limit left on would stitch only the area last used: OK is
    # pressed only once Constrain Area is known to be off
    area = boxes.get("Constrain Area")
    if area is None:
        cancel("could not find the Constrain Area checkbox, so an area limit cannot be ruled out")
        return
    if await set_checkbox(area.hwnd, False) is not False:
        cancel("could not tell whether Constrain Area is off, or turn it off")
        return

    stagger_box = boxes.get("Stagger alternate rows")
    if stagger_box is not None and toggle_stagger:
        _click(stagger_box.hwnd, at_left=True)
        await asyncio.sleep(0.4)
        outcome.applied["stagger_toggled"] = "True"
    elif stagger_box is not None and stagger is not None:
        shown = await set_checkbox(stagger_box.hwnd, stagger)
        outcome.applied["stagger_shown"] = str(shown)
        if shown is None:
            # Checked against the vias placed and corrected by the caller
            outcome.notes.append("could not read the 'Stagger alternate rows' checkbox")
    await asyncio.sleep(0.2)
    for key, c in edits.items():
        outcome.applied[key] = _text(c.hwnd)
    await _press_ok(dialog, controls, outcome, cancel, press_wait)


async def close_messages(outcome, progress_file: Path, keep_alive: float, ignore: Set[int],
                         confirm: Optional[bool] = None, busy_limit: float = BUSY_LIMIT) -> None:
    """Close what Altium shows after OK: how many vias it added, or why none,
    and keep the bridge's progress file fresh for up to busy_limit seconds
    while Altium works. Runs until cancelled by the caller."""
    start = time.monotonic()
    last_touch = start - keep_alive
    while True:
        answer_messages(outcome, ignore, confirm)
        now = time.monotonic()
        if now - start < busy_limit and now - last_touch >= keep_alive:
            try:
                progress_file.touch()
            except OSError:
                pass
            last_touch = now
        await asyncio.sleep(0.3)


async def watch_messages(outcome, ignore: Set[int]) -> None:
    """Answer or close the message boxes a bridge command raises (PCB:Clear
    may ask for confirmation): only OK-only boxes are answered, any other is
    closed, which declines it. Runs until cancelled by the caller."""
    while True:
        answer_messages(outcome, ignore)
        await asyncio.sleep(0.3)


# --- shielding ---------------------------------------------------------------

SHIELD_DIALOG_CLASS = "TViaShieldingOptionsForm"
SHIELD_SETTINGS = ("via_spacing_mils", "distance_mils", "row_step_mils", "via_diameter_mils",
                   "via_hole_mils", "via_pad_clearance_mils", "pullback_mils")


def check_shield_settings(settings: Dict[str, Optional[float]], rows: Optional[int]) -> str:
    """'' when the shielding settings can go to Altium, else what is wrong."""
    for key in SHIELD_SETTINGS:
        v = settings.get(key)
        if v is None:
            continue
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            return f"{key} must be a number"
        if v < 0 or (v == 0 and key in ("via_spacing_mils", "via_diameter_mils", "via_hole_mils")):
            return f"{key} must be positive"
    d, h = settings.get("via_diameter_mils"), settings.get("via_hole_mils")
    if d is not None and h is not None and h >= d:
        return "via_hole_mils must be smaller than via_diameter_mils"
    if rows is not None and (not isinstance(rows, int) or isinstance(rows, bool) or not 1 <= rows <= 20):
        return "rows must be a whole number from 1 to 20"
    return ""


def _type_into(hwnd: int, text: str) -> None:
    """Give a control the keyboard focus and type text into it. Altium's own
    combo boxes take no CB_ messages but select an item typed into them."""
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    theirs = user32.GetWindowThreadProcessId(hwnd, None)
    mine = kernel32.GetCurrentThreadId()
    attached = bool(theirs and user32.AttachThreadInput(mine, theirs, True))
    try:
        user32.SetFocus(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(mine, theirs, False)
    for ch in text:
        user32.PostMessageW(hwnd, 0x0102, ord(ch), 0)   # WM_CHAR


async def drive_shield_dialog(outcome: StitchOutcome, net: str, via_net: str,
                              settings: Dict[str, Optional[float]], rows: Optional[int],
                              toggle: List[str], replace: bool, progress_file: Path,
                              started: Optional[Callable[[], bool]] = None, appear_wait: float = APPEAR_WAIT,
                              settle: float = 0.8, press_wait: float = 6.0, keep_alive: float = 5.0) -> None:
    """Fill in Add Shielding to Net when it opens, press OK, and answer what
    Altium asks next. toggle names checkboxes to click once ("Add shielding
    copper", "Add clearance cutout"): the dialog reopens with the options
    last used, so when those are known a click gives the other. The dialog
    is closed unconfirmed when this gives up, fails or is cancelled."""
    ignore = open_message_boxes()
    dialog = None

    def cancel(why: str):
        outcome.notes.append(why)
        _post_close(dialog)
        outcome.cancelled = True

    try:
        dialog = await _wait_for_dialog(SHIELD_DIALOG_CLASS, outcome, ignore, started, appear_wait)
        if dialog is None:
            outcome.notes.append(f"the dialog did not open within {appear_wait:g} s")
            await _close_late(SHIELD_DIALOG_CLASS, outcome, ignore)
            return
        outcome.seen = True
        await asyncio.sleep(settle)
        if not bring_to_front(dialog):
            outcome.notes.append("the dialog could not be made the active window")
        await _fill_shielding(dialog, outcome, net, via_net, settings, rows, toggle, cancel, press_wait)
        await close_messages(outcome, progress_file, keep_alive, ignore, confirm=replace,
                             busy_limit=BUSY_LIMIT if outcome.pressed else 0)
    finally:
        if dialog is not None and not outcome.pressed and _is_open(dialog):
            _post_close(dialog)
            outcome.cancelled = True


async def _fill_shielding(dialog, outcome, net, via_net, settings, rows, toggle, cancel, press_wait):
    user32 = ctypes.windll.user32
    controls = _controls(dialog)
    target = next((c for c in controls if c.group == "Shielding Parameters" and c.cls == "TXPComboBox"), None)
    outcome.net_shown = target.text if target else ""
    props = [c for c in controls if c.group == "Properties" and c.cls == "TXPComboBox"]
    outcome.layer_pair = next((c.text for c in props if " - " in c.text), "")
    via_box = next((c for c in props if " - " not in c.text), None)
    if outcome.net_shown.upper() != net.upper():
        cancel(f"the dialog opened to shield {outcome.net_shown!r}, not {net!r}")
        return
    if via_box is None:
        cancel("could not find the via net box")
        return
    simple = next((c for c in controls if c.group == "Diameters" and c.text.replace("&", "") == "Simple"), None)
    if simple is not None:
        _click(simple.hwnd, at_left=True)
        await asyncio.sleep(0.4)
        controls = _controls(dialog)
    edits = {}
    shield = sorted((c for c in controls if c.group == "Shielding Parameters" and c.cls == "TEditCoord"),
                    key=lambda c: c.y)
    if len(shield) == 3:
        edits["row_step_mils"], edits["distance_mils"], edits["via_spacing_mils"] = shield
    clear = sorted((c for c in controls if c.group == "Same Net Clearances" and c.cls == "TEditCoord"),
                   key=lambda c: c.y)
    if len(clear) == 2:
        edits["via_pad_clearance_mils"], edits["pullback_mils"] = clear
    via = sorted((c for c in controls if c.group == "Via Style" and c.cls == "TEditCoord"
                  and c.text != "N/A" and user32.IsWindowEnabled(c.hwnd)), key=lambda c: c.x)
    if len(via) == 2:
        edits["via_hole_mils"], edits["via_diameter_mils"] = via
    row_box = next((c for c in controls if c.group == "Shielding Parameters" and c.cls == "TXPEdit"), None)
    missing = [k for k in SHIELD_SETTINGS if settings.get(k) is not None and k not in edits]
    if rows is not None and row_box is None:
        missing.append("rows")
    if missing:
        cancel(f"could not find the dialog's fields for {', '.join(missing)}")
        return
    for key in SHIELD_SETTINGS:
        v = settings.get(key)
        if v is not None:
            user32.SendMessageW(edits[key].hwnd, WM_SETTEXT, 0, ctypes.c_wchar_p(mil_text(v)))
    if rows is not None:
        user32.SendMessageW(row_box.hwnd, WM_SETTEXT, 0, ctypes.c_wchar_p(str(rows)))
    _type_into(via_box.hwnd, via_net)
    await asyncio.sleep(0.5)
    boxes = {c.text: c for c in controls if c.cls == "TXPCheckBox"}
    for label in toggle:
        if label in boxes:
            _click(boxes[label].hwnd, at_left=True)
            await asyncio.sleep(0.4)
    for key, c in edits.items():
        outcome.applied[key] = _text(c.hwnd)
    if row_box is not None:
        outcome.applied["rows"] = _text(row_box.hwnd)
    outcome.applied["via_net"] = _text(via_box.hwnd)
    if outcome.applied["via_net"].upper() != via_net.upper():
        cancel(f"the via net box shows {outcome.applied['via_net']!r} after typing {via_net!r}: "
               "is the net on the board?")
        return
    await _press_ok(dialog, controls, outcome, cancel, press_wait)
