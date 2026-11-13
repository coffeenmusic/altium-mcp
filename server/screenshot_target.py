"""What get_screenshot captures, said precisely.

`target` names one thing on screen, and the capture shows exactly that:

    current              the Altium window as it is now; nothing is switched
    document             the active document's editing area; nothing is switched
    document:<name>      that document's editing area. <name> is a file name
                         (PD_Mon.SchDoc) or a full path. Only an open document,
                         unless open_if_closed; the previously active document
                         is brought back afterwards, and one that had to be
                         opened is closed again (when it has no changes).
    dialog               the topmost Altium dialog or popup
    panel:<name>         one panel, e.g. panel:Projects or panel:Components
    region:x,y,w,h       a rectangle inside Altium's main window, in pixels of
                         a 'current' capture (0,0 is its top left corner).
                         Nothing outside Altium's main window is ever captured.

The pure parts (parsing, choosing a window, clipping) are here so they can be
tested without Altium; the window walking needs pywin32 and runs on Windows.
"""
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from altium_files import long_path

KINDS = ("current", "document", "dialog", "panel", "region")

# Top-level windows of Altium that are never a dialog
FRAME_CLASSES = {"TDocumentForm", "TApplication"}
# Smaller than this, a top-level window is a tooltip or a hint, not a dialog
MIN_DIALOG_SIZE = 60
# A panel undocked from Altium's main window is a top-level window of this class
FLOATING_PANEL_CLASSES = {"TClientBarControl"}
# Altium's document kinds by file extension
DOCUMENT_KINDS = {".schdoc": "SCH", ".pcbdoc": "PCB", ".schlib": "SCHLIB",
                  ".pcblib": "PCBLIB", ".outjob": "OUTPUTJOB"}

Rect = Tuple[int, int, int, int]  # left, top, right, bottom


@dataclass
class Target:
    kind: str                         # one of KINDS
    name: str = ""                    # document or panel name
    region: Optional[Rect] = None     # for kind == "region"

    def describe(self) -> str:
        if self.kind == "region":
            l, t, r, b = self.region
            return f"region:{l},{t},{r - l},{b - t}"
        return f"{self.kind}:{self.name}" if self.name else self.kind


class TargetError(ValueError):
    pass


def parse_target(target: Optional[str]) -> Target:
    """Read the target string. An empty target means 'current'."""
    text = (target or "").strip()
    if not text:
        return Target("current")
    kind, sep, rest = text.partition(":")
    kind = kind.strip().lower()
    rest = rest.strip()
    if kind not in KINDS:
        raise TargetError(f"Unknown screenshot target '{text}'. Use one of: current, document, "
                          f"document:<file name or path>, dialog, panel:<name>, region:x,y,w,h")
    if kind in ("current", "dialog"):
        if rest:
            raise TargetError(f"'{kind}' takes no name (got '{text}')")
        return Target(kind)
    if kind == "document":
        return Target("document", rest)
    if kind == "panel":
        if not rest:
            raise TargetError("panel needs a name, e.g. panel:Projects")
        return Target("panel", rest)
    # region
    parts = [p.strip() for p in rest.split(",")]
    if len(parts) != 4:
        raise TargetError(f"region needs x,y,width,height (got '{rest}')")
    try:
        x, y, w, h = (int(round(float(p))) for p in parts)
    except ValueError:
        raise TargetError(f"region values must be numbers (got '{rest}')")
    if w <= 0 or h <= 0:
        raise TargetError(f"region width and height must be positive (got {w}x{h})")
    return Target("region", region=(x, y, x + w, y + h))


def implied_view_type(target: Optional[str], view_type: Optional[str],
                      zoom_to: Optional[Sequence[str]]) -> Optional[str]:
    """view_type to go by. zoom_to on its own means a board, as it always has."""
    if zoom_to and not (target and target.strip()) and not (view_type and view_type.strip()):
        return "pcb"
    return view_type


def resolve(target: Optional[str], view_type: Optional[str]) -> Optional[Target]:
    """The target to capture, or None for the old view_type behaviour.

    view_type alone keeps working as before ('pcb'/'sch' bring a document of
    that kind to the front); target wins when both are given.
    """
    if target and target.strip():
        return parse_target(target)
    if view_type and view_type.strip():
        return None
    return Target("current")


@dataclass
class Win:
    """A window as seen by the walker: enough to choose one without win32."""
    hwnd: int
    cls: str
    text: str
    rect: Rect
    parent_cls: str = ""
    children: List["Win"] = field(default_factory=list)
    pid: int = 0

    @property
    def width(self) -> int:
        return self.rect[2] - self.rect[0]

    @property
    def height(self) -> int:
        return self.rect[3] - self.rect[1]


def should_return_foreground(previous_pid: int, front_pid: int, altium_pids: Set[int]) -> bool:
    """Altium is in front and another program was before: hand the foreground
    back. Not when Altium was in front anyway, or the user has moved on."""
    return bool(previous_pid) and previous_pid not in altium_pids and front_pid in altium_pids


def pick_main_window(top_windows: Sequence[Win], altium_pids: Set[int]) -> Optional[Win]:
    """Altium's main frame: the largest window of an Altium process with its
    name in the title. The title alone is not enough: a maximised browser tab
    called 'Altium...' has it too."""
    frames = [w for w in top_windows if w.pid in altium_pids and "Altium" in w.text]
    return max(frames, key=lambda w: w.width * w.height, default=None)


def pick_dialog(top_windows: Sequence[Win]) -> Optional[Win]:
    """The topmost Altium window that is a dialog. top_windows is in z-order,
    topmost first, as EnumWindows lists them."""
    for w in top_windows:
        if w.cls in FRAME_CLASSES:
            continue
        if w.width < MIN_DIALOG_SIZE or w.height < MIN_DIALOG_SIZE:
            continue
        return w
    return None


def _norm(s: str) -> str:
    return " ".join((s or "").split()).lower()


def panel_names(children: Sequence[Win]) -> List[str]:
    """Names of the visible panels: TViewComponent windows that are not a
    document view (those sit on a TdxTabSheet)."""
    names = []
    for c in children:
        if c.cls == "TViewComponent" and c.parent_cls != "TdxTabSheet" and c.text \
                and c.width > 0 and c.height > 0 and c.text not in names:
            names.append(c.text)
    return names


def pick_panel(children: Sequence[Win], name: str) -> Optional[Win]:
    want = _norm(name)
    for c in children:
        if c.cls == "TViewComponent" and c.parent_cls != "TdxTabSheet" \
                and _norm(c.text) == want and c.width > 0 and c.height > 0:
            return c
    return None


def document_views(children: Sequence[Win]) -> List[Win]:
    """The visible document editing areas (one, or more when split)."""
    return [c for c in children
            if c.cls == "TViewComponent" and c.parent_cls == "TdxTabSheet"
            and c.width > 0 and c.height > 0]


def _file_name(path_or_name: str) -> str:
    return path_or_name.replace("/", "\\").rsplit("\\", 1)[-1]


def pick_document_view(children: Sequence[Win], document: str) -> Optional[Win]:
    """The editing area showing `document` (a file name or a path). Altium
    titles the view with the sheet number in front, e.g. '[24] PD_Mon.SchDoc'.
    An empty `document` means the active one: the only, or first, visible view."""
    views = document_views(children)
    if not document:
        return views[0] if views else None
    want = _norm(_file_name(document))
    for v in views:
        title = _norm(v.text)
        if title == want or title.endswith(" " + want) or title.endswith("]" + want):
            return v
    return None


def focus_params(t: Target, zoom_to, open_if_closed: bool) -> Dict:
    """The focus_document request for a document target. The path goes the way
    Altium writes it: as written, an open document may not be recognised, and
    open_if_closed would then open a second copy of it. zoom_to is a list of
    designators, or "board" for the whole board outline."""
    params = {"path": long_path(t.name), "open_if_closed": "true" if open_if_closed else "false"}
    if zoom_to == "board":
        params["zoom_board"] = "true"
    elif zoom_to:
        params["designators"] = list(zoom_to)
    return params


def view_document(title: Optional[str]) -> str:
    """The file name in a document view's title: Altium puts the sheet number
    in front ('[24] PD_Mon.SchDoc') and marks unsaved changes with a star."""
    text = (title or "").strip()
    if text.startswith("[") and "]" in text:
        text = text.split("]", 1)[1]
    return text.rstrip(" *").strip()


def document_kind(name: str) -> str:
    """Altium's kind for a file name, '' for anything else."""
    return DOCUMENT_KINDS.get(os.path.splitext(name or "")[1].lower(), "")


def captured_metadata(t: Target, document: Optional[str], kind: Optional[str],
                      view_title: Optional[str]) -> Dict:
    """What was captured, under the keys view_type captures report too.

    document and kind are what Altium said, when it was asked; otherwise the
    view's title tells. All None for a dialog, panel or region: no document.
    """
    if t.kind not in ("current", "document"):
        return {"captured_kind": None, "captured_document": None, "view_type": None}
    name = _file_name(document) if document else view_document(view_title)
    kind = (kind or document_kind(name)).upper()
    return {"captured_kind": kind or None, "captured_document": name or None,
            "view_type": {"PCB": "pcb", "SCH": "sch"}.get(kind)}


def clip(rect: Rect, bounds: Rect) -> Optional[Rect]:
    """rect cut to bounds, or None when nothing is left."""
    l, t = max(rect[0], bounds[0]), max(rect[1], bounds[1])
    r, b = min(rect[2], bounds[2]), min(rect[3], bounds[3])
    if r <= l or b <= t:
        return None
    return (l, t, r, b)


def region_in_window(region: Rect, window: Rect) -> Rect:
    """Screen rectangle of a region given relative to Altium's main window.

    `window` is the window's rectangle on screen as a 'current' capture takes
    it, so region coordinates are pixels of that capture. A region that does
    not lie entirely inside the window is refused, never cut or moved:
    get_screenshot captures nothing outside Altium.
    """
    l, t, r, b = region
    wl, wt, wr, wb = window
    width, height = wr - wl, wb - wt
    if l < 0 or t < 0 or r > width or b > height:
        raise TargetError(
            f"region:{l},{t},{r - l},{b - t} is not inside Altium's main window "
            f"({width}x{height}). Give x,y,w,h in pixels of a 'current' capture.")
    return (wl + l, wt + t, wl + r, wt + b)


def covering(rect: Rect, above: Sequence[Win]) -> List[Win]:
    """Windows of other programs that lie over part of rect.

    `above` are the windows above the captured one in z-order, without
    Altium's own. A screen copy would show any of them, so a capture with
    one in the way is refused: get_screenshot captures nothing but Altium.
    """
    return [w for w in above if clip(w.rect, rect) is not None]


def covering_error(programs: Sequence[str]) -> str:
    """Why a covered capture was refused. Names only the programs (e.g.
    chrome.exe), never window titles: those can carry anything the user has
    open, such as a mail subject or an address."""
    names = []
    for p in programs:
        p = p or "another program"
        if p not in names:
            names.append(p)
    shown = ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
    return (f"Nothing was captured: Altium is not in front, a window of {shown} lies over "
            f"the area. Bring Altium to the front, or move that window away, and try again.")


def altium_windows(top_windows: Sequence[Win], frame_hwnd: int, frame_enabled: bool) -> List[Dict]:
    """Altium's own windows besides its main frame, topmost first, as
    {hwnd, class, title, rect, kind, modal}: kind "panel" for a floating
    panel, "dialog" for anything else. A dialog is modal when the main frame
    is disabled, which is what a modal dialog does to the window it belongs
    to. Tooltips, hints and Altium's hidden helper windows are left out.
    The titles are Altium's own, so they may be reported."""
    out = []
    for w in top_windows:
        if w.hwnd == frame_hwnd or w.cls in FRAME_CLASSES \
                or w.width < MIN_DIALOG_SIZE or w.height < MIN_DIALOG_SIZE:
            continue
        panel = w.cls in FLOATING_PANEL_CLASSES
        out.append({"hwnd": w.hwnd, "class": w.cls, "title": w.text, "rect": list(w.rect),
                    "kind": "panel" if panel else "dialog", "modal": not panel and not frame_enabled})
    return out


def _area(r: Rect) -> int:
    return max(0, r[2] - r[0]) * max(0, r[3] - r[1])


def altium_windows_over(rect: Rect, windows: Sequence[Dict]) -> Tuple[Optional[str], List[Dict]]:
    """(why a capture of rect is refused, or None; the floating panels over it).

    Altium's own windows are above its main frame, so a screen copy shows
    any of them that lies over the area. A dialog there refuses the capture:
    it is what Altium is showing, not the view asked for. A floating panel
    only gets a warning, with the share of the capture it covers in
    covers_percent: get_screenshot can hide it for the capture instead.
    """
    over = []
    for w in windows:
        part = clip(tuple(w["rect"]), rect)
        if part is not None:
            over.append(dict(w, covers_percent=round(100 * _area(part) / max(1, _area(rect)))))
    dialogs = [w for w in over if w["kind"] != "panel"]
    if dialogs:
        d = dialogs[0]
        return (f"Nothing was captured: Altium's {'modal ' if d['modal'] else ''}dialog "
                f"'{d['title']}' ({d['class']}) lies over the area. Capture it with target=\"dialog\", "
                f"or close it with dismiss_dialog, and try again.", [])
    return None, over


def panel_warning(panel: Dict) -> str:
    return (f"Altium's floating panel '{panel['title'] or panel['class']}' covers about "
            f"{panel['covers_percent']}% of the capture. Pass hide_floating_panels=true to hide it "
            f"for the capture, or dock it in Altium.")


def dismissable(windows: Sequence[Dict], hwnd_or_title) -> Tuple[Optional[Dict], str]:
    """(the Altium dialog to dismiss, "") or (None, why not). Only a dialog
    from altium_windows: never the main frame, a panel or another program."""
    dialogs = [w for w in windows if w["kind"] == "dialog"]
    try:
        found = [w for w in dialogs if w["hwnd"] == int(str(hwnd_or_title).strip())]
    except ValueError:
        found = []
    if not found:   # a title, which may also be all digits
        want = _norm(str(hwnd_or_title))
        found = [w for w in dialogs if _norm(w["title"]) == want]
    if len(found) == 1:
        return found[0], ""
    open_ = ", ".join(f"{w['hwnd']} '{w['title']}'" for w in dialogs) or "none"
    if not found:
        return None, f"No open Altium dialog is {hwnd_or_title!r}. Open dialogs: {open_}"
    return None, f"Several Altium dialogs are titled {hwnd_or_title!r}; give the hwnd. Open dialogs: {open_}"
def save_target(path: Optional[str], roots: Sequence[str]) -> Tuple[Optional[str], str]:
    """(where to write a capture, "") or (None, why not). An absolute path
    of a .png file in an existing folder inside one of roots (the user's
    profile): a screenshot tool should not write just anywhere."""
    text = (path or "").strip()
    if not text.lower().endswith(".png") or not os.path.isabs(text):
        return None, "save_path is the full path of a .png file"
    full = os.path.realpath(text)
    if not os.path.isdir(os.path.dirname(full)):
        return None, f"The folder of save_path does not exist: {os.path.dirname(text)}"
    for root in roots:
        root = os.path.realpath(root)
        try:
            if os.path.normcase(os.path.commonpath([full, root])) == os.path.normcase(root):
                return full, ""
        except ValueError:
            pass  # another drive
    return None, "save_path must be inside your user profile folder"


def park_point(views: Sequence[Rect], screen: Rect, cursor: Tuple[int, int]) -> Optional[Tuple[int, int]]:
    """Where to move the mouse pointer for a capture, or None to leave it:
    over a document view Altium draws its heads-up coordinate display by
    the pointer, and a capture would show it. Just outside the views, on
    the screen: left of them, right of them, above, or below."""
    if not views:
        return None
    l, t = min(v[0] for v in views), min(v[1] for v in views)
    r, b = max(v[2] for v in views), max(v[3] for v in views)
    x, y = cursor
    if not (l <= x < r and t <= y < b):
        return None
    for px, py in ((l - 1, t), (r, t), (l, t - 1), (l, b)):
        if screen[0] <= px < screen[2] and screen[1] <= py < screen[3]:
            return (px, py)
    return None


def is_blank(img) -> bool:
    """True when an image (PIL) is a single colour: a view not painted yet."""
    lo_hi = img.getextrema()
    return all(lo == hi for lo, hi in lo_hi)
