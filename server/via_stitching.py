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
  * The checkboxes are Altium's own controls that answer no state query, so
    whether one is ticked is read from how it is drawn (a light tick in its
    box), and it is clicked to change it. They draw and take clicks reliably
    only while the dialog is the active window, which Windows often refuses
    a dialog opened from the background, so the dialog is brought to the
    front first and a state counts only when the drawn control and the
    screen agree. The result's stagger is measured from the vias placed.

A group's stored settings (grid, stagger, clearances) are read from the saved
.PcbDoc, where each group is a union record with UNIONTYPE=2.
"""
import asyncio
import ctypes
import re
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from sandbox import _process_name

DIALOG_CLASS = "TViaStitching_FloodOptionsForm"
MESSAGE_CLASS = "TMessageForm"
WM_SETTEXT, WM_GETTEXT, WM_CLOSE = 0x000C, 0x000D, 0x0010
WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON = 0x0201, 0x0202, 0x0001

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


def stored_groups(pcbdoc: bytes) -> Dict[int, dict]:
    """The stitching groups saved in a .PcbDoc, by union index."""
    out = {}
    for m in re.finditer(rb"\|UNIONTYPE=2\|FUNIONINDEX=(\d+)([^\x00]*)", pcbdoc):
        fields = {}
        for kv in m.group(2).decode("latin-1").split("|"):
            if "=" in kv:
                k, v = kv.split("=", 1)
                fields.setdefault(k, v)
        out[int(m.group(1))] = {
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


def stored_shielding(pcbdoc: bytes) -> Dict[int, dict]:
    """The shielding groups saved in a .PcbDoc (UNIONTYPE=6), by union index."""
    out = {}
    for m in re.finditer(rb"\|UNIONTYPE=6\|FUNIONINDEX=(\d+)([^\x00]*)", pcbdoc):
        fields = {}
        for kv in m.group(2).decode("latin-1").split("|"):
            if "=" in kv:
                k, v = kv.split("=", 1)
                fields.setdefault(k, v)
        out[int(m.group(1))] = {
            "shielded_net": fields.get("NETNAME", ""),
            "via_net": fields.get("VIANETNAME", ""),
            "via_spacing_mils": _mils(fields.get("STEP")),
            "distance_mils": _mils(fields.get("DISTANCE")),
            "rows": int(fields["ROWCOUNT"]) if fields.get("ROWCOUNT", "").isdigit() else None,
            "add_copper": fields.get("ADDCOPPER", "").upper() == "TRUE",
            "add_cutout": fields.get("ADDPOLYCUTOUT", "").upper() == "TRUE",
        }
    return out


def read_stored_groups(board_path: str) -> Dict[int, dict]:
    try:
        return stored_groups(Path(board_path).read_bytes())
    except OSError:
        return {}


def read_stored_shielding(board_path: str) -> Dict[int, dict]:
    try:
        return stored_shielding(Path(board_path).read_bytes())
    except OSError:
        return {}


# --- the dialog --------------------------------------------------------------

def _cls(h) -> str:
    b = ctypes.create_unicode_buffer(128)
    ctypes.windll.user32.GetClassNameW(h, b, 128)
    return b.value


def _text(h) -> str:
    b = ctypes.create_unicode_buffer(512)
    ctypes.windll.user32.SendMessageW(h, WM_GETTEXT, 512, b)
    return b.value


def _top_windows(cls: str) -> List[int]:
    user32 = ctypes.windll.user32
    found = []

    def cb(h, _):
        if user32.IsWindowVisible(h) and _cls(h) == cls and _process_name(h).lower() == "x2.exe":
            found.append(h)
        return True
    user32.EnumWindows(_proto(cb), 0)
    return found


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


def tick_drawn(image) -> bool:
    """Whether a checkbox image shows a tick: light pixels inside the box at
    its left, inside the border (which lights up under the mouse)."""
    h = image.height
    inner = image.crop((4, 4, max(5, h - 4), max(5, h - 4)))
    return sum(inner.histogram()[236:]) >= 4


def _on_screen(hwnd: int):
    from PIL import ImageGrab
    r = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
    try:
        return ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom)).convert("L")
    except OSError:
        return None


def _ticked(hwnd: int) -> Optional[bool]:
    """The checkbox's state when the drawn control and the screen agree."""
    drawn, shown = _snapshot(hwnd), _on_screen(hwnd)
    if drawn is None or shown is None:
        return None
    a, b = tick_drawn(drawn), tick_drawn(shown)
    return a if a == b else None


async def _ticked_settled(hwnd: int, tries: int = 12, agree: int = 3) -> Optional[bool]:
    """The checkbox's state once it reads the same several times running: a
    box caught while the dialog repaints reads as unticked."""
    last, run = None, 0
    for _ in range(tries):
        state = _ticked(hwnd)
        if state is not None and state == last:
            run += 1
            if run >= agree:
                return state
        else:
            last, run = state, 1 if state is not None else 0
        await asyncio.sleep(0.25)
    return None


def bring_to_front(hwnd: int) -> bool:
    """Make the dialog the active window. Windows lets only the foreground
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


async def drive_dialog(outcome: StitchOutcome, net: str, settings: Dict[str, Optional[float]],
                       stagger: Optional[bool], progress_file: Path, toggle_stagger: bool = False,
                       settle: float = 0.8, press_wait: float = 6.0, keep_alive: float = 5.0) -> None:
    """Fill in Add Stitching to Net when it opens, press OK, then close the
    message Altium shows afterwards. Runs until cancelled by the caller.

    toggle_stagger clicks Stagger once without reading it: the dialog opens
    with the setting Altium last used, so when that is known (measured from
    the last stitching) one click gives the other."""
    user32 = ctypes.windll.user32
    dialog = None
    while dialog is None:
        found = _top_windows(DIALOG_CLASS)
        if found:
            dialog = found[0]
        else:
            await asyncio.sleep(0.2)
    outcome.seen = True
    await asyncio.sleep(settle)   # the controls answer only once the form is up
    if not bring_to_front(dialog):
        outcome.notes.append("the dialog could not be made the active window")

    def cancel(why: str):
        outcome.notes.append(why)
        user32.PostMessageW(dialog, WM_CLOSE, 0, 0)
        outcome.cancelled = True

    controls = _controls(dialog)
    props = [c for c in controls if c.group == "Properties" and c.cls == "TXPComboBox"]
    outcome.layer_pair = next((c.text for c in props if " - " in c.text), "")
    outcome.net_shown = next((c.text for c in props if " - " not in c.text), "")
    if outcome.net_shown.upper() != net.upper():
        cancel(f"the dialog opened on net {outcome.net_shown!r}, not {net!r}")
    else:
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
        else:
            for key in SETTINGS:
                v = settings.get(key)
                if v is not None:
                    user32.SendMessageW(edits[key].hwnd, WM_SETTEXT, 0, ctypes.c_wchar_p(mil_text(v)))
            boxes = {c.text: c for c in controls if c.cls == "TXPCheckBox"}
            wanted = {"Constrain Area": False}
            if stagger is not None and not toggle_stagger:
                wanted["Stagger alternate rows"] = stagger
            if toggle_stagger and "Stagger alternate rows" in boxes:
                _click(boxes["Stagger alternate rows"].hwnd, at_left=True)
                await asyncio.sleep(0.4)
                outcome.applied["stagger_toggled"] = "True"
            for label, want in wanted.items():
                box = boxes.get(label)
                if box is None:
                    continue
                state = await _ticked_settled(box.hwnd)
                if state is None:
                    outcome.notes.append(f"could not read the {label!r} checkbox")
                elif state != want:
                    _click(box.hwnd, at_left=True)
                    await asyncio.sleep(0.4)
                    if await _ticked_settled(box.hwnd) != want:
                        outcome.notes.append(f"the {label!r} checkbox did not change")
            await asyncio.sleep(0.2)
            for key, c in edits.items():
                outcome.applied[key] = _text(c.hwnd)
            if "Stagger alternate rows" in boxes:
                outcome.applied["stagger_shown"] = str(await _ticked_settled(boxes["Stagger alternate rows"].hwnd))
            # Stagger is checked against the vias placed and corrected by the
            # caller; only an area limit that stays on is worth stopping for
            if any("'Constrain Area' checkbox did not change" in n for n in outcome.notes):
                cancel("Constrain Area is on and could not be turned off")
            else:
                ok = next((c for c in controls if c.cls == "TXPBitBtn" and c.text.replace("&", "") == "OK"), None)
                if ok is None:
                    cancel("the dialog has no OK button")
                else:
                    _click(ok.hwnd)
                    for _ in range(int(press_wait / 0.25)):
                        await asyncio.sleep(0.25)
                        if not (user32.IsWindow(dialog) and user32.IsWindowVisible(dialog)):
                            outcome.pressed = True
                            break
                    if not outcome.pressed:
                        cancel("pressing OK did not close the dialog")
    await close_messages(outcome, progress_file, keep_alive)


async def close_messages(outcome, progress_file: Path, keep_alive: float, confirm: bool = False) -> None:
    """Close what Altium shows after OK: how many vias it added, or why none.
    A Confirm (the net already has shielding: replace it?) is answered Yes
    only when confirm is set."""
    user32 = ctypes.windll.user32
    while True:
        for box in _top_windows(MESSAGE_CLASS):
            title = _text(box)
            outcome.messages.append(title)
            answers = ("Yes", "OK") if confirm or title != "Confirm" else ("No", "Cancel", "OK")
            ok = next((c for c in _controls(box) if c.cls == "TXPBitBtn"
                       and c.text.replace("&", "") in answers), None)
            if ok is not None:
                _click(ok.hwnd)
            else:
                user32.PostMessageW(box, WM_CLOSE, 0, 0)
            await asyncio.sleep(0.5)
        try:
            progress_file.touch()
        except OSError:
            pass
        await asyncio.sleep(0.3 if not outcome.messages else keep_alive)


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
                              settle: float = 0.8, press_wait: float = 6.0, keep_alive: float = 5.0) -> None:
    """Fill in Add Shielding to Net when it opens, press OK, and answer what
    Altium asks next. toggle names checkboxes to click once ("Add shielding
    copper", "Add clearance cutout"): the dialog reopens with the options
    last used, so when those are known a click gives the other."""
    user32 = ctypes.windll.user32
    dialog = None
    while dialog is None:
        found = _top_windows(SHIELD_DIALOG_CLASS)
        if found:
            dialog = found[0]
        else:
            await asyncio.sleep(0.2)
    outcome.seen = True
    await asyncio.sleep(settle)
    if not bring_to_front(dialog):
        outcome.notes.append("the dialog could not be made the active window")

    def cancel(why: str):
        outcome.notes.append(why)
        user32.PostMessageW(dialog, WM_CLOSE, 0, 0)
        outcome.cancelled = True

    controls = _controls(dialog)
    target = next((c for c in controls if c.group == "Shielding Parameters" and c.cls == "TXPComboBox"), None)
    outcome.net_shown = target.text if target else ""
    props = [c for c in controls if c.group == "Properties" and c.cls == "TXPComboBox"]
    outcome.layer_pair = next((c.text for c in props if " - " in c.text), "")
    via_box = next((c for c in props if " - " not in c.text), None)
    if outcome.net_shown.upper() != net.upper():
        cancel(f"the dialog opened to shield {outcome.net_shown!r}, not {net!r}")
    elif via_box is None:
        cancel("could not find the via net box")
    else:
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
        else:
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
            else:
                ok = next((c for c in controls if c.cls == "TXPBitBtn" and c.text.replace("&", "") == "OK"), None)
                if ok is None:
                    cancel("the dialog has no OK button")
                else:
                    _click(ok.hwnd)
                    for _ in range(int(press_wait / 0.25)):
                        await asyncio.sleep(0.25)
                        if not (user32.IsWindow(dialog) and user32.IsWindowVisible(dialog)):
                            outcome.pressed = True
                            break
                    if not outcome.pressed:
                        cancel("pressing OK did not close the dialog")
    await close_messages(outcome, progress_file, keep_alive, confirm=replace)
