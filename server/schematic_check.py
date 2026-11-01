"""Drafting checks for a saved schematic sheet (.SchDoc), without Altium.

The sheet is read with schdoc_reader and every check works on its geometry:

  label_not_at_end    a net label away from the free end of its wire
  label_justification a label at a free end whose text runs off the end of the
                      wire instead of back along it
  label_orientation   a horizontal label on a vertical wire, or the reverse
  label_off_wire      a net label touching no wire and no pin
  dangling_wire       a wire end that connects to nothing and carries no label
  pin_tap             a wire branches right at a pin instead of a few grids
                      away from it (a decoupling cap sitting on its rail)
  wire_through_body   a wire crossing a component's drawn body
  sheet_entry_margin  a sheet symbol's first entry well below its top edge
  sheet_symbol_text   a sheet symbol's designator or file name not above it
  text_overlap        a designator, parameter, label or port text overlapping
                      a wire, a pin, a body or other text

Text extents are measured with the sheet's own fonts through Windows GDI where
available, so overlap tests follow what Altium draws closely; elsewhere an
average character width is used.

For a component whose text collides, the result suggests a placement for its
designator and visible parameters that collides with nothing (designator left
and value right of a vertical part, above/below a horizontal one, ...).
"""
import math
import os
import sys
from collections import defaultdict
from pathlib import Path

from schdoc_reader import coord, read_schdoc_streams

# Altium draws schematic text at 0.903 x the font size in 10-mil units
EM_PER_POINT = 9.03
# How far, in mils, things may overlap before it counts (rounding, line width)
TOL = 2.0
TEXT_SHRINK = 8.0
# Text may not even touch a wire or pin: a value whose bottom edge sits on a
# wire reads as part of that net
WIRE_CLEARANCE = -3.0
# A net label may start this many grids in from its wire's free end
LABEL_INSET = 2.5


# ---------------------------------------------------------------- text sizes

class _GdiFonts:
    """Text widths from Windows GDI, at a large size and scaled down."""
    REF = 1000

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ct, self.wt = ctypes, wintypes
        self.gdi, self.user = ctypes.windll.gdi32, ctypes.windll.user32
        self.gdi.CreateFontW.restype = wintypes.HANDLE
        self.gdi.CreateCompatibleDC.restype = wintypes.HANDLE
        self.gdi.SelectObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.gdi.GetTextExtentPoint32W.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, ctypes.c_int,
                                                   ctypes.POINTER(wintypes.SIZE)]
        self.dc = self.gdi.CreateCompatibleDC(None)
        self.fonts = {}

    def width(self, text, name, bold, italic):
        key = (name, bold, italic)
        if key not in self.fonts:
            self.fonts[key] = self.gdi.CreateFontW(-self.REF, 0, 0, 0, 700 if bold else 400, int(italic),
                                                   0, 0, 1, 0, 0, 4, 0, name)
        self.gdi.SelectObject(self.dc, self.fonts[key])
        size = self.wt.SIZE()
        self.gdi.GetTextExtentPoint32W(self.dc, text, len(text), self.ct.byref(size))
        return size.cx / self.REF


_gdi = None


def text_width_em(text, name="Times New Roman", bold=False, italic=False):
    """Width of text in ems of its font."""
    global _gdi
    if sys.platform == "win32":
        try:
            if _gdi is None:
                _gdi = _GdiFonts()
            return _gdi.width(text, name, bold, italic)
        except Exception:
            _gdi = False
    return 0.5 * len(text)


# ---------------------------------------------------------------- geometry

class Box:
    __slots__ = ("l", "b", "r", "t")

    def __init__(self, l, b, r, t):
        self.l, self.b, self.r, self.t = min(l, r), min(b, t), max(l, r), max(b, t)

    def shrink(self, d):
        return Box(self.l + d, self.b + d, self.r - d, self.t - d)

    def overlaps(self, o):
        return self.l < o.r and o.l < self.r and self.b < o.t and o.b < self.t

    def contains(self, x, y, tol=TOL):
        return self.l - tol <= x <= self.r + tol and self.b - tol <= y <= self.t + tol

    def crossed_by(self, seg):
        """Does the axis-aligned or diagonal segment pass through the interior?"""
        (x1, y1), (x2, y2) = seg
        # Liang-Barsky clip against the box
        dx, dy = x2 - x1, y2 - y1
        t0, t1 = 0.0, 1.0
        for p, q in ((-dx, x1 - self.l), (dx, self.r - x1), (-dy, y1 - self.b), (dy, self.t - y1)):
            if p == 0:
                if q <= 0:
                    return False
            else:
                t = q / p
                if p < 0:
                    t0 = max(t0, t)
                else:
                    t1 = min(t1, t)
                if t0 >= t1:
                    return False
        return t1 - t0 > 1e-9

    def as_list(self):
        return [round(self.l, 1), round(self.b, 1), round(self.r, 1), round(self.t, 1)]


def text_box(x, y, width, height, orientation, justification):
    """Box of a text anchored at (x, y). Justification 0..8 is bottom-left,
    bottom-centre, bottom-right, middle-left, ..., top-right; orientation turns
    the text in 90 degree steps counter-clockwise around its anchor."""
    h = justification % 3
    v = justification // 3
    u0 = -width * h / 2.0
    w0 = -height * v / 2.0
    u1, w1 = u0 + width, w0 + height
    o = orientation % 4
    if o == 0:
        return Box(x + u0, y + w0, x + u1, y + w1)
    if o == 1:
        return Box(x - w1, y + u0, x - w0, y + u1)
    if o == 2:
        return Box(x - u1, y - w1, x - u0, y - w0)
    return Box(x + w0, y - u1, x + w1, y - u0)


def _near(a, b, tol=TOL):
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def _on_segment(p, seg, tol=TOL):
    (x1, y1), (x2, y2) = seg
    if not (min(x1, x2) - tol <= p[0] <= max(x1, x2) + tol and min(y1, y2) - tol <= p[1] <= max(y1, y2) + tol):
        return False
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    if length == 0:
        return _near(p, (x1, y1), tol)
    return abs((p[0] - x1) * dy - (p[1] - y1) * dx) / length <= tol


def _interior(p, seg, tol=TOL):
    return _on_segment(p, seg, tol) and not _near(p, seg[0], tol) and not _near(p, seg[1], tol)


# ---------------------------------------------------------------- the sheet

PIN_DIR = {0: (1, 0), 1: (0, 1), 2: (-1, 0), 3: (0, -1)}
BODY_RECORDS = {"5", "6", "7", "8", "10", "11", "12", "13", "14"}


class Sheet:
    def __init__(self, records, additional=()):
        self.records = records
        hdr = next((r for r in records if r and r.get("RECORD") == "31"), {})
        self.fonts = {}
        for i in range(1, int(hdr.get("FONTIDCOUNT", 0) or 0) + 1):
            self.fonts[i] = (hdr.get(f"FONTNAME{i}", "Times New Roman"),
                             float(hdr.get(f"SIZE{i}", 10) or 10),
                             hdr.get(f"BOLD{i}") == "T", hdr.get(f"ITALIC{i}") == "T")
        grid = coord(hdr, "SNAPGRIDSIZE") if hdr.get("SNAPGRIDSIZE") else 100.0
        self.grid = grid or 100.0
        # A metric grid (1 mm = 39.37 mil) is not a whole number of 10 mils
        self.metric = abs(self.grid / 10 - round(self.grid / 10)) > 0.01

        self.components = {}   # record index -> dict
        self.pins = []         # {comp, name, hot, base, orient}
        self.texts = []        # {kind, owner, text, box, ...}
        self.wires = []        # list of point lists
        self.buses = []
        self.junctions = []
        self.labels = []       # net labels
        self.attach = []       # points a wire end may legitimately end on
        self.glyphs = []       # {owner, box}: power-port and connector symbols
        self.sheet_symbols = {}  # record index -> {box, entries, name, file}
        self._parse()
        self._parse_harness(additional)

    def text_size(self, text, font_id):
        name, size, bold, italic = self.fonts.get(int(font_id or 1), ("Times New Roman", 10, False, False))
        em = size * EM_PER_POINT
        # The box from the anchor up to the top of capitals, about one em:
        # stacked parameters on a 100 mil pitch then sit just clear of each other
        return text_width_em(text, name, bold, italic) * em, em

    def _owner_chain(self, idx):
        rec = self.records[idx]
        return int(rec.get("OWNERINDEX", -1)) + 1 if rec and "OWNERINDEX" in rec else None

    def _parse(self):
        recs = self.records
        template = {i for i, r in enumerate(recs) if r and r.get("RECORD") == "39"}
        # Objects belonging to the sheet template (title block, border) are
        # not part of the drawing
        skip = set(template)
        for i, r in enumerate(recs):
            if r and "OWNERINDEX" in r and int(r["OWNERINDEX"]) + 1 in skip:
                skip.add(i)

        for i, r in enumerate(recs):
            if r and r.get("RECORD") == "1":
                self.components[i] = {
                    "index": i, "designator": "?", "lib": r.get("LIBREFERENCE", ""),
                    "part": int(r.get("CURRENTPARTID", 1) or 1),
                    "mode": int(r.get("DISPLAYMODE", 0) or 0),
                    "orientation": int(r.get("ORIENTATION", 0) or 0),
                    "body": None, "pins": [], "texts": [],
                }

        def visible_child(r, comp):
            part = int(r.get("OWNERPARTID", -1) or -1)
            mode = int(r.get("OWNERPARTDISPLAYMODE", 0) or 0)
            return part in (-1, 0, comp["part"]) and mode == comp["mode"]

        for i, r in enumerate(recs):
            if not r or i in skip:
                continue
            kind = r.get("RECORD")
            owner = self._owner_chain(i)
            comp = self.components.get(owner) if owner is not None else None

            if kind == "2" and comp is not None:
                if not visible_child(r, comp):
                    continue
                cong = int(r.get("PINCONGLOMERATE", 0) or 0)
                if cong & 0x04:
                    continue
                orient = cong & 3
                base = (coord(r, "LOCATION.X"), coord(r, "LOCATION.Y"))
                length = coord(r, "PINLENGTH")
                d = PIN_DIR[orient]
                hot = (base[0] + d[0] * length, base[1] + d[1] * length)
                pin = {"comp": comp, "name": r.get("DESIGNATOR", r.get("NAME", "")),
                       "hot": hot, "base": base, "orient": orient}
                comp["pins"].append(pin)
                self.pins.append(pin)

            elif kind in BODY_RECORDS and comp is not None:
                if not visible_child(r, comp):
                    continue
                box = _graphic_box(r)
                if box is not None:
                    b = comp["body"]
                    comp["body"] = box if b is None else Box(min(b.l, box.l), min(b.b, box.b),
                                                             max(b.r, box.r), max(b.t, box.t))

            elif kind in ("34", "41") and comp is not None:
                if kind == "34":
                    comp["designator"] = r.get("TEXT", "?")
                if r.get("ISHIDDEN") == "T" or not r.get("TEXT") or "LOCATION.X" not in r and "LOCATION.Y" not in r:
                    continue
                text = r["TEXT"]
                if text.startswith("="):
                    continue
                w, h = self.text_size(text, r.get("FONTID", 1))
                t = {"kind": "designator" if kind == "34" else "parameter",
                     "name": "Designator" if kind == "34" else r.get("NAME", ""),
                     "owner": comp, "text": text,
                     "anchor": (coord(r, "LOCATION.X"), coord(r, "LOCATION.Y")),
                     "orientation": int(r.get("ORIENTATION", 0) or 0),
                     "justification": int(r.get("JUSTIFICATION", 0) or 0),
                     "size": (w, h)}
                t["box"] = text_box(*t["anchor"], w, h, t["orientation"], t["justification"])
                comp["texts"].append(t)
                self.texts.append(t)

            elif kind == "27":
                self.wires.append(_points(r))
            elif kind == "26":
                self.buses.append(_points(r))
            elif kind == "29":
                self.junctions.append((coord(r, "LOCATION.X"), coord(r, "LOCATION.Y")))
            elif kind == "22":
                self.attach.append((coord(r, "LOCATION.X"), coord(r, "LOCATION.Y")))
            elif kind == "37":
                self.attach.append((coord(r, "LOCATION.X"), coord(r, "LOCATION.Y")))
                self.attach.append((coord(r, "CORNER.X"), coord(r, "CORNER.Y")))
            elif kind == "25":
                self._net_label(r)
            elif kind == "17":
                self._power_port(r)
            elif kind == "18":
                self._port(r)
            elif kind == "15":
                box = Box(coord(r, "LOCATION.X"), coord(r, "LOCATION.Y") - coord(r, "YSIZE"),
                          coord(r, "LOCATION.X") + coord(r, "XSIZE"), coord(r, "LOCATION.Y"))
                self.glyphs.append({"owner": "sheet symbol", "box": box, "wire_ends": True})
                self.sheet_symbols[i] = {"box": box, "entries": [], "name": None, "file": None}
            elif kind == "16" and owner in self.sheet_symbols:
                # DistanceFromTop is in 100 mil units, with a millionths part
                dist = (int(r.get("DISTANCEFROMTOP", 0) or 0)
                        + int(r.get("DISTANCEFROMTOP_FRAC1", 0) or 0) / 1e6) * 100.0
                self.sheet_symbols[owner]["entries"].append(
                    {"name": r.get("NAME", ""), "side": int(r.get("SIDE", 0) or 0), "distance": dist})
            elif kind in ("32", "33") and owner in self.sheet_symbols:
                text = r.get("TEXT", "")
                x, y = coord(r, "LOCATION.X"), coord(r, "LOCATION.Y")
                o, j = int(r.get("ORIENTATION", 0) or 0), int(r.get("JUSTIFICATION", 0) or 0)
                w, h = self.text_size(text, r.get("FONTID", 1))
                t = {"kind": "sheet designator" if kind == "32" else "sheet file name", "owner": None,
                     "text": text, "anchor": (x, y), "box": text_box(x, y, w, h, o, j)}
                self.sheet_symbols[owner]["name" if kind == "32" else "file"] = t
                if text and r.get("ISHIDDEN") != "T":
                    self.texts.append(t)
            elif kind == "4" and owner is None:
                text = r.get("TEXT", "")
                if text and not r.get("ISHIDDEN") == "T":
                    w, h = self.text_size(text, r.get("FONTID", 1))
                    box = text_box(coord(r, "LOCATION.X"), coord(r, "LOCATION.Y"), w, h,
                                   int(r.get("ORIENTATION", 0) or 0), int(r.get("JUSTIFICATION", 0) or 0))
                    self.texts.append({"kind": "note", "owner": None, "text": text, "box": box})

        for c in self.components.values():
            if c["body"] is None and c["pins"]:
                xs = [p["base"][0] for p in c["pins"]]
                ys = [p["base"][1] for p in c["pins"]]
                c["body"] = Box(min(xs), min(ys), max(xs), max(ys))

    def _parse_harness(self, records):
        """Harness objects, from the sheet's "Additional" stream. A wire ends on
        a harness connector's edge, at an entry, as on a sheet symbol's; a
        signal harness joins wires as a bus does."""
        for r in records:
            kind = r.get("RECORD") if r else None
            if kind == "215":
                x, y = coord(r, "LOCATION.X"), coord(r, "LOCATION.Y")
                box = Box(x, y - coord(r, "YSIZE"), x + coord(r, "XSIZE"), y)
                self.glyphs.append({"owner": "harness connector", "box": box, "wire_ends": True})
            elif kind == "218":
                self.buses.append(_points(r))

    def _net_label(self, r):
        text = r.get("TEXT", "")
        x, y = coord(r, "LOCATION.X"), coord(r, "LOCATION.Y")
        w, h = self.text_size(text, r.get("FONTID", 1))
        o, j = int(r.get("ORIENTATION", 0) or 0), int(r.get("JUSTIFICATION", 0) or 0)
        lab = {"kind": "net label", "owner": None, "text": text, "anchor": (x, y),
               "orientation": o, "justification": j, "box": text_box(x, y, w, h, o, j)}
        self.labels.append(lab)
        self.texts.append(lab)

    def _power_port(self, r):
        text = r.get("TEXT", "")
        x, y = coord(r, "LOCATION.X"), coord(r, "LOCATION.Y")
        o = int(r.get("ORIENTATION", 0) or 0)
        style = int(r.get("STYLE", 0) or 0)
        connector = r.get("ISCROSSSHEETCONNECTOR") == "T"
        self.attach.append((x, y))
        d = PIN_DIR[o]
        if connector:
            glyph_len, text_gap = 100.0, 120.0
        elif style in (4, 5, 6):
            glyph_len, text_gap = 200.0, 210.0
        else:
            glyph_len, text_gap = 100.0, 90.0
        gx, gy = x + d[0] * glyph_len, y + d[1] * glyph_len
        half = 60.0
        glyph = Box(min(x, gx) - (half if d[0] == 0 else 0), min(y, gy) - (half if d[1] == 0 else 0),
                    max(x, gx) + (half if d[0] == 0 else 0), max(y, gy) + (half if d[1] == 0 else 0))
        self.glyphs.append({"owner": f"port {text}", "box": glyph})
        if r.get("SHOWNETNAME") != "T" or not text:
            return
        w, h = self.text_size(text, r.get("FONTID", 1))
        tx, ty = x + d[0] * text_gap, y + d[1] * text_gap
        if d[1] != 0:   # text centred above or below the symbol
            box = Box(tx - w / 2, ty if d[1] > 0 else ty - h, tx + w / 2, ty + h if d[1] > 0 else ty)
        else:           # text beside it, vertically centred
            box = Box(tx if d[0] > 0 else tx - w, ty - h / 2, tx + w if d[0] > 0 else tx, ty + h / 2)
        self.texts.append({"kind": "port text", "owner": None, "text": text, "box": box,
                           "port_at": (x, y)})

    def _port(self, r):
        x, y = coord(r, "LOCATION.X"), coord(r, "LOCATION.Y")
        width = coord(r, "WIDTH") or 500.0
        height = coord(r, "HEIGHT") or 100.0
        vertical = int(r.get("STYLE", 0) or 0) >= 4
        if vertical:
            self.attach += [(x, y), (x, y + width)]
            box = Box(x - height / 2, y, x + height / 2, y + width)
        else:
            self.attach += [(x, y), (x + width, y)]
            box = Box(x, y - height / 2, x + width, y + height / 2)
        self.glyphs.append({"owner": f"port {r.get('NAME', '')}", "box": box, "port": True})

    # ---------------------------------------------------------- helpers

    def segments(self):
        for wi, pts in enumerate(self.wires):
            for k in range(len(pts) - 1):
                yield wi, (pts[k], pts[k + 1])


def _points(r):
    n = int(r.get("LOCATIONCOUNT", 0) or 0)
    return [(coord(r, f"X{k}"), coord(r, f"Y{k}")) for k in range(1, n + 1)]


def _graphic_box(r):
    kind = r["RECORD"]
    if kind in ("10", "13", "14"):
        return Box(coord(r, "LOCATION.X"), coord(r, "LOCATION.Y"), coord(r, "CORNER.X"), coord(r, "CORNER.Y"))
    if kind in ("5", "6", "7"):
        pts = _points(r)
        if not pts:
            return None
        return Box(min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))
    if kind in ("8", "11", "12"):
        x, y = coord(r, "LOCATION.X"), coord(r, "LOCATION.Y")
        rx = coord(r, "RADIUS")
        ry = coord(r, "SECONDARYRADIUS") or rx
        if kind == "12":
            return _arc_box(x, y, rx, float(r.get("STARTANGLE", 0) or 0), float(r.get("ENDANGLE", 360) or 360))
        return Box(x - rx, y - ry, x + rx, y + ry)
    return None


def _arc_box(x, y, rad, a0, a1):
    if a1 <= a0:
        a1 += 360
    angles = [a0, a1] + [k * 90 for k in range(int(a0 // 90) + 1, int(a1 // 90) + 1)]
    xs = [x + rad * math.cos(math.radians(a)) for a in angles]
    ys = [y + rad * math.sin(math.radians(a)) for a in angles]
    return Box(min(xs), min(ys), max(xs), max(ys))


# ---------------------------------------------------------------- checks

class Checker:
    def __init__(self, sheet: Sheet):
        self.s = sheet
        self.issues = []
        self.grid = sheet.grid
        self._wire_networks()

    def fmt(self, v):
        if self.s.metric:
            return round(v * 0.0254, 2)
        return round(v, 1)

    def at(self, p):
        return [self.fmt(p[0]), self.fmt(p[1])]

    def add(self, kind, where, message, **extra):
        self.issues.append({"type": kind, "at": self.at(where), "message": message, **extra})

    # wire runs: wires joined end-to-end or by a T
    def _wire_networks(self):
        wires = self.s.wires
        parent = list(range(len(wires)))

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        segs = list(self.s.segments())
        for wi, pts in enumerate(wires):
            for p in pts:
                for wj, seg in segs:
                    if wj != wi and _on_segment(p, seg):
                        parent[find(wi)] = find(wj)
        self.network = [find(i) for i in range(len(wires))]

        # free ends: a wire end touching no pin, wire, port or marker
        hot = [p["hot"] for p in self.s.pins]
        self.free_ends = []   # (point, wire index, direction back along the wire)
        for wi, pts in enumerate(wires):
            if len(pts) < 2:
                continue
            # direction from the first point clearly away from the end: wires
            # drawn on a metric grid can end in segments a fraction of a mil long
            def away(seq):
                return next((q for q in seq[1:] if not _near(q, seq[0])), seq[1])
            for end, nxt in ((pts[0], away(pts)), (pts[-1], away(pts[::-1]))):
                if any(_near(end, h) for h in hot):
                    continue
                if any(_near(end, a) for a in self.s.attach):
                    continue
                if any(wj != wi and _on_segment(end, seg) for wj, seg in segs):
                    continue
                if any(_on_segment(end, (b[k], b[k + 1])) for b in self.s.buses for k in range(len(b) - 1)):
                    continue
                if any(g.get("wire_ends") and g["box"].contains(*end) for g in self.s.glyphs):
                    continue
                length = math.hypot(nxt[0] - end[0], nxt[1] - end[1]) or 1.0
                self.free_ends.append((end, wi, ((nxt[0] - end[0]) / length, (nxt[1] - end[1]) / length)))

    def check_labels(self):
        segs = list(self.s.segments())
        labelled_ends = set()
        for lab in self.s.labels:
            p = lab["anchor"]
            on = [(wi, seg) for wi, seg in segs if _on_segment(p, seg)]
            if not on:
                if not any(_near(p, pin["hot"]) for pin in self.s.pins):
                    self.add("label_off_wire", p, f"net label {lab['text']} does not touch a wire or pin, "
                                                  "so it names nothing", label=lab["text"])
                continue
            nets = {self.network[wi] for wi, _ in on}
            ends = [fe for fe in self.free_ends if self.network[fe[1]] in nets]

            # the wire direction under the label decides its orientation
            seg = on[0][1]
            horizontal = abs(seg[0][1] - seg[1][1]) <= TOL
            vertical = abs(seg[0][0] - seg[1][0]) <= TOL
            if (horizontal and lab["orientation"] % 2 == 1) or (vertical and lab["orientation"] % 2 == 0
                                                                  and not horizontal):
                self.add("label_orientation", p,
                         f"net label {lab['text']} is drawn {'vertical' if lab['orientation'] % 2 else 'horizontal'} "
                         f"on a {'horizontal' if horizontal else 'vertical'} wire",
                         label=lab["text"], suggest={"orientation": 0 if horizontal else 1})
            if not ends:
                continue

            # nearest free end of this run
            end, wi, back = min(ends, key=lambda fe: math.hypot(fe[0][0] - p[0], fe[0][1] - p[1]))
            box = lab["box"]
            # extent of the text along the wire, measured from the free end
            # (positive = over the wire, negative = past its end)
            if abs(back[0]) >= abs(back[1]):
                near_edge = (box.l - end[0]) if back[0] > 0 else (end[0] - box.r)
                far_edge = (box.r - end[0]) if back[0] > 0 else (end[0] - box.l)
                anchor_gap = abs(p[0] - end[0])
            else:
                near_edge = (box.b - end[1]) if back[1] > 0 else (end[1] - box.t)
                far_edge = (box.t - end[1]) if back[1] > 0 else (end[1] - box.b)
                anchor_gap = abs(p[1] - end[1])
            labelled_ends.add((round(end[0], 1), round(end[1], 1)))
            outward = near_edge + far_edge < 0
            # Two house styles are both fine: text sitting on the wire and
            # running back along it, or a middle-justified label anchored on
            # the end whose text continues past it like a port. What reads
            # wrong is bottom-justified text hanging off the end of the wire.
            end_cap = outward and lab["justification"] // 3 == 1 and anchor_gap <= self.grid / 2
            suggest = {"x": self.fmt(end[0]), "y": self.fmt(end[1]),
                       "orientation": 0 if abs(back[0]) >= abs(back[1]) else 1,
                       "justification": self._label_justification(back)}
            if not outward and near_edge > LABEL_INSET * self.grid:
                self.add("label_not_at_end", p,
                         f"net label {lab['text']} sits {self.fmt(near_edge)} {self.unit} short of its wire's free end",
                         label=lab["text"], free_end=self.at(end), suggest=suggest)
            elif not end_cap and min(near_edge, far_edge) < -self.grid / 2:
                self.add("label_justification", p,
                         f"net label {lab['text']} hangs {self.fmt(-min(near_edge, far_edge))} {self.unit} past "
                         "the end of its wire; justify it so the text runs back along the wire",
                         label=lab["text"], free_end=self.at(end), suggest=suggest)
        self._labelled_ends = labelled_ends

    @staticmethod
    def _label_justification(back):
        """Bottom-left (0) when the wire runs on to the right of / above the
        free end, bottom-right (2) when it runs to the left / below."""
        if abs(back[0]) >= abs(back[1]):
            return 0 if back[0] > 0 else 2
        return 0 if back[1] > 0 else 2

    @property
    def unit(self):
        return "mm" if self.s.metric else "mil"

    def check_dangling(self):
        for end, wi, back in self.free_ends:
            key = (round(end[0], 1), round(end[1], 1))
            if key in self._labelled_ends:
                continue
            if any(_near(end, lab["anchor"], self.grid) or lab["box"].contains(*end, TOL)
                   for lab in self.s.labels):
                continue
            self.add("dangling_wire", end, "wire end connects to nothing and carries no net label")

    def check_pin_taps(self):
        segs = list(self.s.segments())
        ports = self.s.attach
        for pin in self.s.pins:
            # An IC's supply pins joined by one wire are normal; the problem is
            # a two-pin part (decoupling cap, pull-up) tapping a net at its pin
            if len(pin["comp"]["pins"]) != 2:
                continue
            h = pin["hot"]
            branches = 0
            for wi, seg in segs:
                if _near(h, seg[0]) or _near(h, seg[1]):
                    branches += 1
                elif _interior(h, seg):
                    branches += 2
            branches += sum(1 for a in ports if _near(h, a))
            if branches >= 2:
                c = pin["comp"]
                d = PIN_DIR[pin["orient"]]
                tap = (h[0] + d[0] * 2 * self.grid, h[1] + d[1] * 2 * self.grid)
                self.add("pin_tap", h,
                         f"{c['designator']} pin {pin['name']} is itself the branch point of {branches} "
                         "connections; move the part so a short wire runs from the pin to the junction",
                         designator=c["designator"], pin=pin["name"],
                         suggest={"junction_at": self.at(tap)})

    def check_bodies(self):
        reported = set()
        for wi, seg in self.s.segments():
            for c in self.s.components.values():
                body = c["body"]
                if body is None or (body.r - body.l) < TOL or (body.t - body.b) < TOL:
                    continue
                if body.shrink(TOL).crossed_by(seg) and (wi, c["index"]) not in reported:
                    reported.add((wi, c["index"]))
                    mid = ((seg[0][0] + seg[1][0]) / 2, (seg[0][1] + seg[1][1]) / 2)
                    self.add("wire_through_body", mid, f"a wire crosses the body of {c['designator']}",
                             designator=c["designator"])

    # ---------------------------------------------------------- text

    def obstacles(self, skip_owner=None):
        """Everything text may not overlap: (description, box or segment, kind)."""
        out = []
        for wi, seg in self.s.segments():
            out.append(("wire", seg, "seg"))
        for p in self.s.pins:
            if p["comp"] is skip_owner:
                continue
            out.append((f"pin {p['comp']['designator']}.{p['name']}", (p["base"], p["hot"]), "seg"))
        for c in self.s.components.values():
            if c["body"] is not None and c is not skip_owner:
                out.append((f"body of {c['designator']}", c["body"], "box"))
        for g in self.s.glyphs:
            out.append((g["owner"], g["box"], "box"))
        for t in self.s.texts:
            if skip_owner is not None and t["owner"] is skip_owner:
                continue
            out.append((self._text_name(t), t["box"], "box"))
        return out

    @staticmethod
    def _text_name(t):
        if t["owner"] is not None:
            return f"{t['owner']['designator']} {t['name'].lower()} '{t['text']}'"
        return f"{t['kind']} '{t['text']}'"

    def _hits(self, t, box, obstacles):
        hits = []
        inner = box.shrink(TEXT_SHRINK)
        snug = box.shrink(WIRE_CLEARANCE)
        for name, geom, kind in obstacles:
            if kind == "seg":
                if t["kind"] == "net label" and _on_segment(t["anchor"], geom, TOL * 2):
                    continue
                if t["kind"] == "port text" and (_near(t["port_at"], geom[0]) or _near(t["port_at"], geom[1])):
                    continue
                if (inner if t["kind"] in ("net label", "port text") else snug).crossed_by(geom):
                    hits.append(name)
            else:
                if geom is box:
                    continue
                if inner.overlaps(geom.shrink(TEXT_SHRINK if name.startswith(("net label", "port text")) or "'" in name
                                              else 0)):
                    hits.append(name)
        return hits

    def check_text(self):
        obstacles = self.obstacles()
        seen = set()
        crowded = defaultdict(list)
        for t in self.s.texts:
            own_body = t["owner"]["body"] if t["owner"] is not None else None
            hits = []
            for name in self._hits(t, t["box"], obstacles):
                # a part's own text may touch its own pins, not its body
                if t["owner"] is not None and name.startswith(f"pin {t['owner']['designator']}."):
                    continue
                if t["kind"] == "port text" and name.startswith("port "):
                    continue
                if name == self._text_name(t):
                    continue
                if t["owner"] is not None and name == f"body of {t['owner']['designator']}":
                    continue
                hits.append(name)
            if own_body is not None and t["box"].shrink(TEXT_SHRINK).overlaps(own_body):
                hits.append(f"its own body")
            new = []
            for h in hits:
                key = frozenset((self._text_name(t), h))
                if key in seen:
                    continue
                seen.add(key)
                new.append(h)
            if not new:
                continue
            centre = ((t["box"].l + t["box"].r) / 2, (t["box"].b + t["box"].t) / 2)
            self.add("text_overlap", centre, f"{self._text_name(t)} overlaps {', '.join(sorted(set(new)))}",
                     text=t["text"], overlaps=sorted(set(new)),
                     **({"designator": t["owner"]["designator"]} if t["owner"] is not None else {}))
            if t["owner"] is not None:
                crowded[t["owner"]["index"]].append(t)

        for idx in crowded:
            self._suggest_text(self.s.components[idx])

    def _suggest_text(self, c):
        """Try standard text arrangements around the part and attach the first
        one that collides with nothing to that part's text_overlap issues."""
        texts = sorted(c["texts"], key=lambda t: (t["kind"] != "designator", t["name"]))
        if not texts or c["body"] is None:
            return
        obstacles = self.obstacles(skip_owner=c)
        # this part's own pins still count, but text may touch their hot ends
        for p in c["pins"]:
            obstacles.append((f"pin {c['designator']}.{p['name']}", (p["base"], p["hot"]), "seg"))
        best = None
        for name, layout in self._candidates(c, texts):
            boxes = [text_box(x, y, t["size"][0], t["size"][1], 0, j) for t, (x, y, j) in zip(texts, layout)]
            hits = 0
            for t, box in zip(texts, boxes):
                hits += len(self._hits(t, box, obstacles))
                if box.shrink(TEXT_SHRINK).overlaps(c["body"]):
                    hits += 1
            for a in range(len(boxes)):
                for b in range(a + 1, len(boxes)):
                    if boxes[a].shrink(TEXT_SHRINK).overlaps(boxes[b]):
                        hits += 1
            if best is None or hits < best[0]:
                best = (hits, name, layout)
            if hits == 0:
                break
        if best is None:
            return
        hits, name, layout = best
        suggest = {"layout": name, "clear": hits == 0,
                   "texts": [{"parameter": t["name"], "x": self.fmt(x), "y": self.fmt(y),
                              "orientation": 0, "justification": j}
                             for t, (x, y, j) in zip(texts, layout)]}
        for issue in self.issues:
            if issue["type"] == "text_overlap" and issue.get("designator") == c["designator"]:
                issue["suggest"] = suggest

    def _candidates(self, c, texts):
        """Named layouts: lists of (x, y, justification) per text, in mils."""
        b = c["body"]
        g = self.grid
        line = max(t["size"][1] for t in texts)
        n = len(texts)
        cx, cy = (b.l + b.r) / 2, (b.b + b.t) / 2
        pins = c["pins"]
        two_pin = len(pins) == 2
        vertical = two_pin and abs(pins[0]["hot"][0] - pins[1]["hot"][0]) < TOL
        gap = max(g / 2, 20.0)

        def stack(x, just, top):
            # texts top to bottom starting at top (middle-justified rows)
            return [(x, top - line * (k + 0.5), just) for k in range(n)]

        out = []
        if vertical:
            mid_top = cy + line * n / 2
            out.append(("right of the body", stack(b.r + gap, 3, mid_top)))
            if n >= 2:
                split = [(b.l - gap, cy, 5), (b.r + gap, cy, 3)] + \
                        [(b.r + gap, cy - line * k, 3) for k in range(1, n - 1)]
                out.append(("designator left, value right", split))
            out.append(("left of the body", stack(b.l - gap, 5, mid_top)))
        elif two_pin:
            above = [(cx, b.t + gap + line * (n - 1 - k), 1) for k in range(n)]
            below = [(cx, b.b - gap - line * (k + 1), 1) for k in range(n)]
            out.append(("above the body", above))
            if n >= 2:
                split = [(cx, b.t + gap, 1)] + [(cx, b.b - gap - line * k, 7) for k in range(n - 1)]
                out.append(("designator above, value below", split))
            out.append(("below the body", below))
        else:
            out.append(("designator above, rest below",
                        [(b.l, b.t + gap, 0)] + [(b.l, b.b - gap - line * k, 6) for k in range(n - 1)]))
            out.append(("all above", [(b.l, b.t + gap + line * (n - 1 - k), 0) for k in range(n)]))
            out.append(("all below", [(b.l, b.b - gap - line * k, 6) for k in range(n)]))
            out.append(("right of the body", stack(b.r + gap, 3, b.t)))
        # fall back to the same layouts pushed a grid further out
        more = []
        for name, layout in out:
            more.append((name + " (one grid further out)",
                         [(x + (g if x > cx else -g if x < cx else 0),
                           y + (g if y > b.t else -g if y < b.b else 0), j) for x, y, j in layout]))
        return out + more

    def check_sheet_symbols(self):
        """Altium's own layout: the first entry one grid (100 mil, or the next
        grid step on a metric sheet) below the top edge, and both the
        designator and the file name above the symbol, file name on the edge
        and designator a line above it. The bottom may keep more room."""
        want = math.ceil(100.0 / self.grid - 1e-6) * self.grid
        line = 100.0
        for sym in self.s.sheet_symbols.values():
            box = sym["box"]
            label = (sym["name"] or {}).get("text") or "sheet symbol"
            sides = [e for e in sym["entries"] if e["side"] in (0, 1)]
            if sides:
                first = min(e["distance"] for e in sides)
                if first > want + self.grid / 2:
                    self.add("sheet_entry_margin", (box.l, box.t - first),
                             f"the first entry of {label} is {self.fmt(first)} {self.unit} below the top edge; "
                             f"move the entries up by {self.fmt(first - want)} {self.unit} so it sits "
                             f"{self.fmt(want)} {self.unit} from the edge",
                             sheet_symbol=label, suggest={"first_entry_from_top": self.fmt(want),
                                                          "move_entries_up": self.fmt(first - want)})
            below = [t for t in (sym["name"], sym["file"]) if t and t["box"].b < box.t - TOL]
            if below:
                self.add("sheet_symbol_text", below[0]["anchor"],
                         f"{' and '.join(t['kind'] for t in below)} of {label} not above the symbol; "
                         "put the file name on the top edge and the designator a line above it",
                         sheet_symbol=label,
                         suggest={"file_name": {"x": self.fmt(box.l), "y": self.fmt(box.t), "justification": 0},
                                  "designator": {"x": self.fmt(box.l), "y": self.fmt(box.t + line),
                                                 "justification": 0}})

    def run(self):
        self.check_labels()
        self.check_dangling()
        self.check_pin_taps()
        self.check_bodies()
        self.check_text()
        self.check_sheet_symbols()
        order = ["pin_tap", "label_not_at_end", "label_justification", "label_orientation", "label_off_wire",
                 "dangling_wire", "wire_through_body", "sheet_entry_margin", "sheet_symbol_text", "text_overlap"]
        self.issues.sort(key=lambda i: (order.index(i["type"]), -i["at"][1], i["at"][0]))
        return self.issues


def check_sheet(path) -> dict:
    sheet = Sheet(*read_schdoc_streams(path))
    checker = Checker(sheet)
    issues = checker.run()
    counts = defaultdict(int)
    for i in issues:
        counts[i["type"]] += 1
    return {"sheet": str(path), "unit": checker.unit, "grid": checker.fmt(sheet.grid),
            "components": len(sheet.components), "issue_counts": dict(counts), "issues": issues}


def project_sheets(prjpcb) -> list:
    """The .SchDoc files a .PrjPcb lists, as absolute paths."""
    prj = Path(prjpcb)
    out = []
    for line in prj.read_text(encoding="cp1252", errors="replace").splitlines():
        if line.startswith("DocumentPath=") and line.lower().endswith(".schdoc"):
            # normpath, not resolve: keep the path the way the project names
            # it rather than whatever junction it leads through
            out.append(Path(os.path.normpath(prj.parent / line.split("=", 1)[1])))
    return out


if __name__ == "__main__":
    import json
    for arg in sys.argv[1:]:
        paths = project_sheets(arg) if arg.lower().endswith(".prjpcb") else [arg]
        for p in paths:
            print(json.dumps(check_sheet(p), indent=1))
