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
    region:x,y,w,h       a rectangle of the screen, in physical pixels

The pure parts (parsing, choosing a window, clipping) are here so they can be
tested without Altium; the window walking needs pywin32 and runs on Windows.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

KINDS = ("current", "document", "dialog", "panel", "region")

# Top-level windows of Altium that are never a dialog
FRAME_CLASSES = {"TDocumentForm", "TApplication"}
# Smaller than this, a top-level window is a tooltip or a hint, not a dialog
MIN_DIALOG_SIZE = 60

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

    @property
    def width(self) -> int:
        return self.rect[2] - self.rect[0]

    @property
    def height(self) -> int:
        return self.rect[3] - self.rect[1]


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


def clip(rect: Rect, bounds: Rect) -> Optional[Rect]:
    """rect cut to bounds, or None when nothing is left."""
    l, t = max(rect[0], bounds[0]), max(rect[1], bounds[1])
    r, b = min(rect[2], bounds[2]), min(rect[3], bounds[3])
    if r <= l or b <= t:
        return None
    return (l, t, r, b)


def is_blank(img) -> bool:
    """True when an image (PIL) is a single colour: a view not painted yet."""
    lo_hi = img.getextrema()
    return all(lo == hi for lo, hi in lo_hi)
