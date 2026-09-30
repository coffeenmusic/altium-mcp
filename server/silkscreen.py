"""Silkscreen designator placement support for an agent.

The DelphiScript side exports the board (export_silkscreen_data) as one
pipe-delimited line per object; this module parses it and answers the
questions an agent asks while placing designators by hand: what is wrong,
what does this area look like, where could this designator legally go, and
would this spot work. Pure Python (no numpy) so it runs in the MCP server's
minimal environment.

Conventions:
- All coordinates are mils relative to the board origin.
- A designator position is the CENTRE of the text box Altium reports. That
  makes placement independent of text anchors, justification and bottom-side
  mirroring: the Altium side rotates the text, then moves its box centre.
- Clearances are measured to the INK box. Altium's box for stroke text runs
  half a stroke beyond the ink on every side, and its silk rules check the
  strokes (PrimPrimDistance uses the box and would report false overlaps).
- Readable rotations are 0/90 on the top overlay. Bottom text is mirrored
  (mirror, then rotate CCW), so its readable rotations are 0/270.
"""

import io
import math
import re

# Obstacle classes: each checks against its own clearance
SILK, MASK, EDGE = 0, 1, 2
# Obstacle shapes
RECT, SEG, CIRCLE, POLY = 0, 1, 2, 3

# Pad shape codes (TShape)
_ROUND_SHAPES = (1, 4)            # eRounded, eCircleShape

READABLE_ROTATIONS = {"T": (0, 90), "B": (0, 270)}


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def _rect_rect_dist(a, b):
    dx = max(b[0] - a[2], a[0] - b[2], 0.0)
    dy = max(b[1] - a[3], a[1] - b[3], 0.0)
    return math.hypot(dx, dy)


def _point_rect_dist(px, py, r):
    dx = max(r[0] - px, 0.0, px - r[2])
    dy = max(r[1] - py, 0.0, py - r[3])
    return math.hypot(dx, dy)


def _point_seg_dist(px, py, x1, y1, x2, y2):
    vx, vy = x2 - x1, y2 - y1
    l2 = vx * vx + vy * vy
    if l2 == 0.0:
        return math.hypot(px - x1, py - y1)
    t = ((px - x1) * vx + (py - y1) * vy) / l2
    t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
    return math.hypot(px - (x1 + t * vx), py - (y1 + t * vy))


def _seg_hits_rect(x1, y1, x2, y2, r):
    """Liang-Barsky: does the segment touch the rectangle?"""
    dx, dy = x2 - x1, y2 - y1
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x1 - r[0]), (dx, r[2] - x1), (-dy, y1 - r[1]), (dy, r[3] - y1)):
        if p == 0.0:
            if q < 0.0:
                return False
        else:
            u = q / p
            if p < 0.0:
                if u > t1:
                    return False
                if u > t0:
                    t0 = u
            else:
                if u < t0:
                    return False
                if u < t1:
                    t1 = u
    return True


def _rect_seg_dist(r, x1, y1, x2, y2):
    if _seg_hits_rect(x1, y1, x2, y2, r):
        return 0.0
    d = min(_point_rect_dist(x1, y1, r), _point_rect_dist(x2, y2, r))
    for cx, cy in ((r[0], r[1]), (r[0], r[3]), (r[2], r[1]), (r[2], r[3])):
        d = min(d, _point_seg_dist(cx, cy, x1, y1, x2, y2))
    return d


def _point_in_polygon(px, py, pts):
    inside = False
    n = len(pts)
    j = n - 1
    for i in range(n):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if (yi > py) != (yj > py) and px < (xj - xi) * (py - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _rect_poly_dist(r, pts):
    d = float("inf")
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        d = min(d, _rect_seg_dist(r, x1, y1, x2, y2))
        if d == 0.0:
            return 0.0
    if _point_in_polygon((r[0] + r[2]) / 2, (r[1] + r[3]) / 2, pts):
        return 0.0
    return d


def _polygon_area(pts):
    n = len(pts)
    return abs(sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1]
                   for i in range(n))) / 2 if n >= 3 else 0.0


def rect_overlap_area(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def _inflate(r, d):
    return (r[0] - d, r[1] - d, r[2] + d, r[3] + d)


def _union(a, b):
    if a is None:
        return b
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _arc_points(cx, cy, r, a1, a2, max_step_deg=10.0):
    """Points along a CCW arc from a1 to a2 (degrees); full circle if equal."""
    sweep = (a2 - a1) % 360.0
    if sweep < 1e-6:
        sweep = 360.0
    n = max(2, int(math.ceil(sweep / max_step_deg)))
    return [(cx + r * math.cos(math.radians(a1 + sweep * i / n)),
             cy + r * math.sin(math.radians(a1 + sweep * i / n))) for i in range(n + 1)]


class PolygonIndex:
    """Fast point-in-polygon: edges bucketed by the y band they span."""

    def __init__(self, pts, band=50.0):
        self.pts = pts
        self.band = band
        self.buckets = {}
        n = len(pts)
        for i in range(n):
            (x1, y1), (x2, y2) = pts[i], pts[(i + 1) % n]
            if y1 == y2:
                continue
            for k in range(int(math.floor(min(y1, y2) / band)), int(math.floor(max(y1, y2) / band)) + 1):
                self.buckets.setdefault(k, []).append((x1, y1, x2, y2))

    def contains(self, px, py):
        if not self.pts:
            return True
        inside = False
        for x1, y1, x2, y2 in self.buckets.get(int(math.floor(py / self.band)), ()):
            if (y1 > py) != (y2 > py) and px < (x2 - x1) * (py - y1) / (y2 - y1) + x1:
                inside = not inside
        return inside


class Obstacle:
    __slots__ = ("bbox", "shape", "geom", "cls", "owner", "label")

    def __init__(self, bbox, shape, geom, cls, owner, label):
        self.bbox = bbox
        self.shape = shape
        self.geom = geom
        self.cls = cls
        self.owner = owner
        self.label = label

    def distance(self, r):
        g = self.geom
        if self.shape == RECT:
            return _rect_rect_dist(r, g)
        if self.shape == SEG:
            return max(0.0, _rect_seg_dist(r, g[0], g[1], g[2], g[3]) - g[4])
        if self.shape == CIRCLE:
            return max(0.0, _point_rect_dist(g[0], g[1], r) - g[2])
        return _rect_poly_dist(r, g)


def _seg_obstacle(x1, y1, x2, y2, hw, cls, owner, label):
    bbox = (min(x1, x2) - hw, min(y1, y2) - hw, max(x1, x2) + hw, max(y1, y2) + hw)
    return Obstacle(bbox, SEG, (x1, y1, x2, y2, hw), cls, owner, label)


def _rect_obstacle(r, cls, owner, label):
    return Obstacle(r, RECT, r, cls, owner, label)


class SpatialHash:
    """Uniform grid over bounding boxes."""

    def __init__(self, cell=40.0):
        self.cell = cell
        self.buckets = {}

    def _keys(self, b):
        c = self.cell
        for ix in range(int(math.floor(b[0] / c)), int(math.floor(b[2] / c)) + 1):
            for iy in range(int(math.floor(b[1] / c)), int(math.floor(b[3] / c)) + 1):
                yield (ix, iy)

    def add(self, bbox, item):
        for k in self._keys(bbox):
            self.buckets.setdefault(k, []).append(item)

    def remove(self, bbox, item):
        for k in self._keys(bbox):
            bucket = self.buckets.get(k)
            if bucket and item in bucket:
                bucket.remove(item)

    def query(self, bbox):
        seen = set()
        out = []
        for k in self._keys(bbox):
            for item in self.buckets.get(k, ()):
                if id(item) not in seen:
                    seen.add(id(item))
                    out.append(item)
        return out


# ---------------------------------------------------------------------------
# Board model
# ---------------------------------------------------------------------------

def designator_prefix(designator):
    """'R' for R12, 'FB' for FB3: the part type a reader matches labels by."""
    m = re.match(r"[A-Za-z]+", designator)
    return m.group(0).upper() if m else ""


class Component:
    __slots__ = ("designator", "side", "x", "y", "rotation", "visible", "bbox",
                 "text_box", "text_rotation", "text_height", "stroke", "autopos",
                 "mirrored", "pattern", "bodies", "extent", "prefix", "skeleton", "truetype")

    def text_size0(self):
        """(width, height) of the text box in the text's own rotation-0 frame."""
        w = self.text_box[2] - self.text_box[0]
        h = self.text_box[3] - self.text_box[1]
        rot = self.text_rotation % 180.0
        if abs(rot - 90.0) < 1.0:
            return h, w
        if rot < 1.0 or rot > 179.0:
            return w, h
        return max(w, h), min(w, h)   # non-orthogonal: best effort

    def text_center(self):
        b = self.text_box
        return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def _f(s):
    return float(s) if s else 0.0


def ink_box(altium_box, stroke, truetype=False):
    """The ink of a designator from the box Altium reports: stroke text's box
    runs half a stroke beyond its strokes on every side. TrueType boxes are
    kept as they are (conservative)."""
    k = 0.0 if truetype else stroke / 2
    return (altium_box[0] + k, altium_box[1] + k, altium_box[2] - k, altium_box[3] - k)


class Board:
    """Everything parsed from an export_silkscreen_data dump."""

    def __init__(self, text):
        self.s2s = 0.0
        self.s2m = 0.0
        self.outline_raw = []
        self.cutouts = []
        self.components = {}
        self.silk = []        # (side, obstacle, kind, owner) - kind D/C/F for texts, '' otherwise
        self.mask = []        # (side, obstacle)
        self._parse(text)
        self.outline = self._outline_polygon()
        # A 'Layer Stack Region' reports the cutout kind but is the board
        # itself; keep only cutouts that are genuine holes in the board
        board_area = _polygon_area(self.outline)
        self.cutouts = [c for c in self.cutouts
                        if len(c) >= 3 and _polygon_area(c) < 0.5 * board_area]
        self._compute_extents()

    def _parse(self, text):
        comps = self.components
        bodies = {}
        for line in text.splitlines():
            f = line.rstrip("\r").split("|")
            tag = f[0]
            if tag == "RULE":
                if f[1] == "S2S":
                    self.s2s = max(0.0, _f(f[2]))
                elif f[1] == "S2M":
                    self.s2m = max(0.0, _f(f[2]))
            elif tag == "O":
                self.outline_raw.append((int(f[1]), _f(f[2]), _f(f[3]), _f(f[4]), _f(f[5]),
                                         _f(f[6]), _f(f[7]), _f(f[8])))
            elif tag == "K":
                vals = [_f(v) for v in f[1:]]
                self.cutouts.append(list(zip(vals[0::2], vals[1::2])))
            elif tag == "C":
                c = Component()
                c.designator = f[1]
                c.side = f[2]
                c.x, c.y, c.rotation = _f(f[3]), _f(f[4]), _f(f[5])
                c.visible = f[6] == "1"
                c.bbox = tuple(_f(v) for v in f[7:11])
                c.text_rotation = _f(f[15])
                c.text_height = _f(f[16])
                c.stroke = _f(f[17])
                c.autopos = int(f[18] or 0)
                c.mirrored = f[19] == "1"
                c.pattern = f[20] if len(f) > 20 else ""
                c.truetype = len(f) > 21 and f[21] == "1"
                c.text_box = ink_box(tuple(_f(v) for v in f[11:15]), c.stroke, c.truetype)
                c.bodies = []
                c.extent = None
                c.prefix = designator_prefix(c.designator)
                comps[c.designator] = c
            elif tag == "Y":
                bodies.setdefault(f[1], []).append(tuple(_f(v) for v in f[2:6]))
            elif tag == "ST":
                x1, y1, x2, y2, w = (_f(v) for v in f[3:8])
                self.silk.append((f[1], _seg_obstacle(x1, y1, x2, y2, w / 2, SILK, f[2], "silk track"), "", f[2]))
            elif tag == "SA":
                cx, cy, r, a1, a2, w = (_f(v) for v in f[3:9])
                pts = _arc_points(cx, cy, r, a1, a2)
                sag = r * (1 - math.cos(math.radians(10.0) / 2))
                for (xa, ya), (xb, yb) in zip(pts, pts[1:]):
                    self.silk.append((f[1], _seg_obstacle(xa, ya, xb, yb, w / 2 + sag, SILK, f[2], "silk arc"), "", f[2]))
            elif tag == "SX":
                if f[3] == "D":
                    continue    # designators: obstacles come from Component.text_box
                r = tuple(_f(v) for v in f[4:8])
                if len(f) > 9:
                    r = ink_box(r, _f(f[8]), f[9] == "1")
                label = {"D": "designator", "C": "comment"}.get(f[3], "silk text")
                self.silk.append((f[1], _rect_obstacle(r, SILK, f[2], label), f[3], f[2]))
            elif tag == "SB":
                r = tuple(_f(v) for v in f[3:7])
                self.silk.append((f[1], _rect_obstacle(r, SILK, f[2], "silk fill"), "", f[2]))
            elif tag == "MP":
                self.mask.append((f[1], self._pad_obstacle(f)))
            elif tag == "MV":
                x, y, d = _f(f[3]), _f(f[4]), _f(f[5])
                r = d / 2
                self.mask.append((f[1], Obstacle((x - r, y - r, x + r, y + r), CIRCLE, (x, y, r), MASK, "", "via")))
            elif tag == "MT":
                x1, y1, x2, y2, w = (_f(v) for v in f[3:8])
                self.mask.append((f[1], _seg_obstacle(x1, y1, x2, y2, w / 2, MASK, f[2], "mask opening")))
            elif tag == "MB":
                self.mask.append((f[1], _rect_obstacle(tuple(_f(v) for v in f[3:7]), MASK, f[2], "mask opening")))
        for des, boxes in bodies.items():
            if des in comps:
                comps[des].bodies = boxes

    @staticmethod
    def _pad_obstacle(f):
        owner, pin = f[2], f[3]
        x, y, rot, shape = _f(f[4]), _f(f[5]), _f(f[6]), int(f[7] or 0)
        xs, ys = _f(f[8]), _f(f[9])
        mb = tuple(_f(v) for v in f[10:14])
        label = f"pad {owner}-{pin}" if owner else "pad"
        mw, mh = mb[2] - mb[0], mb[3] - mb[1]
        rad = math.radians(rot)
        c, s = abs(math.cos(rad)), abs(math.sin(rad))
        orthogonal = min(c, s) < 1e-6
        # Mask expansion recovered from the mask bbox and the copper size
        exp = max(0.0, (mw - (xs * c + ys * s)) / (2 * (c + s))) if (c + s) > 0 else 0.0
        if shape in _ROUND_SHAPES:
            if abs(xs - ys) < 1e-6:
                r = mw / 2
                return Obstacle(mb, CIRCLE, ((mb[0] + mb[2]) / 2, (mb[1] + mb[3]) / 2, r), MASK, owner, label)
            # Oblong: a capsule along the long axis
            hw = min(xs, ys) / 2 + exp
            half = (max(xs, ys) - min(xs, ys)) / 2
            ang = rad if xs >= ys else rad + math.pi / 2
            dx, dy = half * math.cos(ang), half * math.sin(ang)
            ob = _seg_obstacle(x - dx, y - dy, x + dx, y + dy, hw, MASK, owner, label)
            return ob
        if orthogonal or shape == 0:
            return _rect_obstacle(mb, MASK, owner, label)
        # Rotated rectangle-like pad: exact polygon
        hx, hy = xs / 2 + exp, ys / 2 + exp
        cr, sr = math.cos(rad), math.sin(rad)
        pts = [(x + px * cr - py * sr, y + px * sr + py * cr)
               for px, py in ((-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy))]
        return Obstacle(mb, POLY, pts, MASK, owner, label)

    def _outline_polygon(self):
        raw = self.outline_raw
        pts = []
        n = len(raw)
        for i, (kind, vx, vy, cx, cy, r, a1, a2) in enumerate(raw):
            if kind == 1 and r > 0:
                nx, ny = raw[(i + 1) % n][1], raw[(i + 1) % n][2]
                s_ang = math.degrees(math.atan2(vy - cy, vx - cx))
                e_ang = math.degrees(math.atan2(ny - cy, nx - cx))
                # The arc runs CCW from Angle1 to Angle2; walk it from this
                # vertex to the next one in whichever direction that implies
                ccw = abs(((s_ang - a1 + 180) % 360) - 180) < 1.0
                if ccw:
                    arc = _arc_points(cx, cy, r, s_ang, e_ang, 5.0)
                else:
                    arc = list(reversed(_arc_points(cx, cy, r, e_ang, s_ang, 5.0)))
                pts.extend(arc[:-1])
            else:
                pts.append((vx, vy))
        return pts

    def _compute_extents(self):
        """Visible footprint of each component on its designator's side:
        union of its mask openings and its silk (designator/comment excluded)."""
        comps = self.components
        for side, ob in self.mask:
            c = comps.get(ob.owner)
            if c is not None and c.side == side:
                c.extent = _union(c.extent, ob.bbox)
        for side, ob, kind, owner in self.silk:
            c = comps.get(owner)
            if c is not None and c.side == side and kind not in ("D", "C"):
                c.extent = _union(c.extent, ob.bbox)
        for c in comps.values():
            if c.extent is None:
                c.extent = c.bbox
            # The part's long axis (a point for square parts): readers match a
            # label to the part it lines up with, not the nearest pad edge
            e = c.extent
            k = min(e[2] - e[0], e[3] - e[1]) / 2
            c.skeleton = (e[0] + k, e[1] + k, e[2] - k, e[3] - k)


# ---------------------------------------------------------------------------
# Candidate search
# ---------------------------------------------------------------------------

def ambiguity_band(d_own):
    """How much closer another part may come before a label reads as a toss-up."""
    return max(AMBIGUITY_MARGIN, AMBIGUITY_RATIO * d_own)


def text_dims(comp, height=None):
    """(w0, h0, height, stroke) of a designator's text box at rotation 0,
    optionally scaled to a new text height (stroke scaled, never below 3)."""
    w0, h0 = comp.text_size0()
    if not height or abs(height - comp.text_height) < 1e-6 or comp.text_height <= 0:
        return w0, h0, comp.text_height, comp.stroke
    k = height / comp.text_height
    stroke = max(3.0, round(comp.stroke * k * 2) / 2)
    pad = h0 - comp.text_height            # box margin from the stroke width
    pad_new = pad * (stroke / comp.stroke) if comp.stroke else pad
    return (w0 - pad) * k + pad_new, height + pad_new, height, stroke


class Candidate:
    __slots__ = ("designator", "rect", "rotation", "cost", "side_name", "height", "stroke", "note")

    def __init__(self, designator, rect, rotation, cost, side_name, height, stroke, note=""):
        self.designator = designator
        self.rect = rect
        self.rotation = rotation
        self.cost = cost
        self.side_name = side_name
        self.height = height
        self.stroke = stroke
        self.note = note

    @property
    def center(self):
        return (self.rect[0] + self.rect[2]) / 2, (self.rect[1] + self.rect[3]) / 2


class Options:
    def __init__(self, max_gap=50.0, orientation="auto", avoid_vias=True,
                 edge_margin=10.0, extra_clearance=0.5, min_height=None, per_group=10):
        self.max_gap = max_gap
        self.orientation = orientation
        self.avoid_vias = avoid_vias
        self.edge_margin = edge_margin
        self.extra_clearance = extra_clearance
        self.min_height = min_height
        self.per_group = per_group


# Cost weights, in mil-equivalents
W_GAP = 1.0            # per mil between the part and its text
W_SLIDE = 0.35         # per mil the text centre slides off the part centre
SIDE_COST = {"N": 0.0, "W": 4.0, "E": 4.0, "S": 7.0, "IN": 60.0}
W_AMBIGUITY = 4.0      # per mil a same-type part is closer than (own + margin)
W_CROSS_AMBIGUITY = 1.0  # the same for parts of another type (R label by a C)
AMBIGUITY_MARGIN = 5.0
AMBIGUITY_RATIO = 0.25   # ...or this fraction of the distance, whichever is larger
FAR_GAP = 15.0         # mils: beyond this a label must be nearest its own part...
SIMILAR_LABEL = 1.4    # ...among parts whose labels are no more than this much smaller
W_OTHER_BODY = 80.0    # x fraction of the text box over another part's body
W_OWN_BODY = 25.0      # x fraction over its own body (hidden after assembly)
CROWDED = 10           # fewer legal spots than this: offer smaller text too
DIVERSITY = 12.0       # mils: min spacing between picks in one side/rotation group


class Placer:
    """Static feasibility and scoring of designator text boxes on one board."""

    def __init__(self, board, scope, options):
        self.board = board
        self.opt = options
        self.scope = set(scope)
        clr = options.extra_clearance
        self.clearance = {SILK: board.s2s + clr, MASK: board.s2m + clr, EDGE: options.edge_margin}
        self.max_clear = max(self.clearance.values())
        self.obstacles = {"T": SpatialHash(), "B": SpatialHash()}
        self.extents = {"T": SpatialHash(80.0), "B": SpatialHash(80.0)}
        self.bodies = {"T": SpatialHash(80.0), "B": SpatialHash(80.0)}
        self.inside = PolygonIndex(board.outline)
        self.holes = [PolygonIndex(c) for c in board.cutouts]
        self._build()

    def _build(self):
        b = self.board
        for side, ob, kind, owner in b.silk:
            if side in self.obstacles:
                self.obstacles[side].add(ob.bbox, ob)
        # Other designators where they are now; the ones being placed are not
        # obstacles to themselves
        for c in b.components.values():
            if c.visible and c.side in self.obstacles and c.designator not in self.scope:
                ob = _rect_obstacle(c.text_box, SILK, c.designator, "designator " + c.designator)
                self.obstacles[c.side].add(ob.bbox, ob)
        for side, ob in b.mask:
            if ob.label == "via" and not self.opt.avoid_vias:
                continue
            if side in self.obstacles:
                self.obstacles[side].add(ob.bbox, ob)
        pts = b.outline
        edges = []
        for i in range(len(pts)):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % len(pts)]
            edges.append(_seg_obstacle(x1, y1, x2, y2, 0.0, EDGE, "", "board edge"))
        for poly in b.cutouts:
            for i in range(len(poly)):
                x1, y1 = poly[i]
                x2, y2 = poly[(i + 1) % len(poly)]
                edges.append(_seg_obstacle(x1, y1, x2, y2, 0.0, EDGE, "", "board cutout"))
        for side in ("T", "B"):
            for ob in edges:
                self.obstacles[side].add(ob.bbox, ob)
        for c in b.components.values():
            if c.side in self.extents:
                self.extents[c.side].add(c.extent, c)
                bodies = c.bodies or [c.extent]
                for body in bodies:
                    self.bodies[c.side].add(body, (c.designator, body))

    # -- static checks -----------------------------------------------------

    def blockers(self, rect, side, first_only=True):
        """Obstacles closer to rect than their clearance (empty = legal)."""
        out = []
        clear = self.clearance
        for ob in self.obstacles[side].query(_inflate(rect, self.max_clear)):
            c = clear[ob.cls]
            bb = ob.bbox
            if (bb[0] - c >= rect[2] or rect[0] - c >= bb[2] or
                    bb[1] - c >= rect[3] or rect[1] - c >= bb[3]):
                continue
            if ob.distance(rect) < c:
                out.append(ob)
                if first_only:
                    return out
        cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
        if not self.inside.contains(cx, cy) or any(h.contains(cx, cy) for h in self.holes):
            out.append(Obstacle(rect, RECT, rect, EDGE, "", "outside board"))
        return out

    def association(self, comp, rect):
        """How clearly rect labels comp. Readers match a label to the part it
        lines up with, so ambiguity uses distances from the label centre to
        each part's long axis (its skeleton); the gap is edge-to-part.
        Returns (gap, d_own, other, d_other, same, d_same)."""
        gap = _rect_rect_dist(rect, comp.extent)
        cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
        d_own = _point_rect_dist(cx, cy, comp.skeleton)
        other = same = None
        d_other = d_same = float("inf")
        reach = d_own + ambiguity_band(d_own) + 1
        for o in self.extents[comp.side].query((cx - reach, cy - reach, cx + reach, cy + reach)):
            if o is comp:
                continue
            d = _point_rect_dist(cx, cy, o.skeleton)
            if d < d_other:
                other, d_other = o, d
            if o.prefix == comp.prefix and d < d_same:
                same, d_same = o, d
        return gap, d_own, other, d_other, same, d_same

    @staticmethod
    def misleading(comp, gap, d_own, other, d_other, d_same):
        """A same-type part clearly closer to the label than its own part -
        or, once the label sits away from its part, any other part whose
        label is about as big (a 100 mil J10 never reads as a capacitor's)."""
        if d_same < d_own - 1.0:
            return True
        return (gap > FAR_GAP and other is not None and d_other < d_own - 1.0
                and comp.text_height <= SIMILAR_LABEL * other.text_height)

    def soft_penalty(self, comp, rect):
        _, d_own, _, d_other, _, d_same = self.association(comp, rect)
        band = ambiguity_band(d_own)
        pen = (W_AMBIGUITY * max(0.0, d_own + band - d_same) +
               W_CROSS_AMBIGUITY * max(0.0, d_own + band - d_other))
        area = max(1e-9, (rect[2] - rect[0]) * (rect[3] - rect[1]))
        for owner, body in self.bodies[comp.side].query(rect):
            frac = rect_overlap_area(rect, body) / area
            if frac > 0:
                pen += (W_OWN_BODY if owner == comp.designator else W_OTHER_BODY) * frac
        return pen

    # -- candidates --------------------------------------------------------

    def _rotations(self, comp):
        rots = READABLE_ROTATIONS.get(comp.side, (0, 90))
        if self.opt.orientation == "horizontal_only":
            return rots[:1]
        return rots

    def _rotation_cost(self, comp, rot):
        e = comp.extent
        ew, eh = e[2] - e[0], e[3] - e[1]
        vertical_text = rot % 180 == 90
        if self.opt.orientation == "horizontal":
            return 30.0 if vertical_text else 0.0
        # auto: text parallel to the part's long axis reads as "belonging" to it
        if eh > 1.3 * ew:
            return 0.0 if vertical_text else 8.0
        return 12.0 if vertical_text else 0.0

    def raw_candidates(self, comp, height=None):
        """Every candidate box around the part with its static cost."""
        w0, h0, height, stroke = text_dims(comp, height)
        e = comp.extent
        ecx, ecy = (e[0] + e[2]) / 2, (e[1] + e[3]) / 2
        ew, eh = e[2] - e[0], e[3] - e[1]
        g0 = min(self.clearance[SILK], self.clearance[MASK])
        gaps = [g for g in (g0, g0 + 2, g0 + 5, g0 + 9, g0 + 14, g0 + 20, g0 + 28, g0 + 38, g0 + 50)
                if g <= self.opt.max_gap] or [g0]
        out = []
        for rot in self._rotations(comp):
            tw, th = (h0, w0) if rot % 180 == 90 else (w0, h0)
            rcost = self._rotation_cost(comp, rot)
            for side in ("N", "S", "W", "E"):
                along = ew if side in ("N", "S") else eh
                tlen = tw if side in ("N", "S") else th
                reach = along / 2 + tlen * 0.3
                step = max(4.0, reach / 12)
                slides = [0.0]
                k = 1
                while k * step <= reach:
                    slides += [k * step, -k * step]
                    k += 1
                # Text across the part's axis on a side reads oddly
                orient = 0.0
                if rot % 180 == 90 and side in ("N", "S"):
                    orient = 6.0
                elif rot % 180 == 0 and side in ("W", "E"):
                    orient = 4.0
                base = SIDE_COST[side] + rcost + orient
                for gap in gaps:
                    for sl in slides:
                        if side == "N":
                            cx, cy = ecx + sl, e[3] + gap + th / 2
                        elif side == "S":
                            cx, cy = ecx + sl, e[1] - gap - th / 2
                        elif side == "W":
                            cx, cy = e[0] - gap - tw / 2, ecy + sl
                        else:
                            cx, cy = e[2] + gap + tw / 2, ecy + sl
                        rect = (cx - tw / 2, cy - th / 2, cx + tw / 2, cy + th / 2)
                        cost = base + W_GAP * gap + W_SLIDE * abs(sl)
                        out.append(Candidate(comp.designator, rect, rot, cost, side, height, stroke))
            # Inside the outline: only for parts big enough to hold the text
            if self._fits_inside(comp, tw, th):
                rect = (ecx - tw / 2, ecy - th / 2, ecx + tw / 2, ecy + th / 2)
                out.append(Candidate(comp.designator, rect, rot, SIDE_COST["IN"] + rcost, "IN", height, stroke))
        return out

    @staticmethod
    def _fits_inside(comp, tw, th):
        """Big parts may carry their designator inside their own outline."""
        e = comp.extent
        return tw < (e[2] - e[0]) - 10 and th < (e[3] - e[1]) - 10

    def candidates(self, comp, height=None):
        """Best legal candidates, a few per (rotation, side) group, spread
        over the free space so they are real alternatives."""
        raw = self.raw_candidates(comp, height)
        raw.sort(key=lambda c: c.cost)
        groups = {}
        legal = []
        limit = self.opt.per_group
        for cand in raw:
            key = (cand.rotation, cand.side_name)
            chosen = groups.setdefault(key, [])
            if len(chosen) >= limit:
                continue
            # Spread each group's picks over the free space: near-duplicates
            # would all collide with the same neighbour
            cx, cy = cand.center
            if any(abs(cx - x) < DIVERSITY and abs(cy - y) < DIVERSITY for x, y in chosen):
                continue
            if self.blockers(cand.rect, comp.side):
                continue
            gap, d_own, other, d_other, _, d_same = self.association(comp, cand.rect)
            if self.misleading(comp, gap, d_own, other, d_other, d_same):
                continue
            cand.cost += self.soft_penalty(comp, cand.rect)
            legal.append(cand)
            chosen.append((cx, cy))
        legal.sort(key=lambda c: c.cost)
        return legal

    def diagnose(self, comp, count=3):
        """Why the best spots are illegal: labels of what blocks them."""
        raw = self.raw_candidates(comp)
        raw.sort(key=lambda c: c.cost)
        seen = []
        for cand in raw:
            for ob in self.blockers(cand.rect, comp.side, first_only=False):
                label = ob.label + (f" ({ob.owner})" if ob.owner and ob.owner not in ob.label else "")
                if label not in seen:
                    seen.append(label)
            if len(seen) >= count:
                break
        return seen[:count]


# ---------------------------------------------------------------------------
# Agent API: options, evaluation, relative placement
# ---------------------------------------------------------------------------

SIDE_ALIASES = {"above": "N", "top": "N", "n": "N", "below": "S", "bottom": "S", "s": "S",
                "left": "W", "w": "W", "right": "E", "e": "E", "center": "IN", "centre": "IN",
                "inside": "IN", "in": "IN"}
SIDE_NAMES = {"N": "above", "S": "below", "W": "left", "E": "right", "IN": "inside"}


def split_scope(board, designators):
    """(designators that can be placed, {designator: why not} for the rest)."""
    ok, skipped = [], {}
    for d in designators:
        c = board.components.get(d)
        if c is None:
            skipped[d] = "not on board"
        elif not c.visible:
            skipped[d] = "designator hidden - pass visible: true to show it"
        elif c.side not in ("T", "B"):
            skipped[d] = "designator not on an overlay layer"
        else:
            ok.append(d)
    return ok, skipped


def options_for(board, designators, count=5, options=None):
    """Ranked legal spots for each designator. Every other designator is an
    obstacle where it is now; the designators asked about are not obstacles
    to each other, so check picks for neighbours together (dry run).

    Returns {des: {"options": [Candidate], "blocked_by": [labels]}}; blocked_by
    is filled when there is no legal spot."""
    opt = options or Options()
    placer = Placer(board, designators, opt)
    out = {}
    for des in designators:
        comp = board.components[des]
        lst = placer.candidates(comp)
        if opt.min_height and opt.min_height < comp.text_height:
            h = comp.text_height - 5
            while h >= opt.min_height - 1e-6:
                for c in placer.candidates(comp, h):
                    c.cost += 10.0 * (comp.text_height - h)
                    c.note = "reduced height"
                    lst.append(c)
                h -= 5
        lst.sort(key=lambda c: c.cost)
        picked = []
        for cand in lst:
            # Only spots check_silkscreen would call good
            if quality_problems(placer, comp, cand.rect, cand.rotation, {}, opt.max_gap):
                continue
            # Distinct spots only, so numbered boxes do not pile up
            area = (cand.rect[2] - cand.rect[0]) * (cand.rect[3] - cand.rect[1])
            if any(rect_overlap_area(cand.rect, p.rect) > 0.3 * area for p in picked):
                continue
            picked.append(cand)
            if len(picked) >= count:
                break
        out[des] = {"options": picked, "blocked_by": [] if picked else placer.diagnose(comp)}
    return out


def _describe(ob, rect, clearance):
    if ob.label == "outside board":
        return "outside the board outline"
    what = ob.label + (f" ({ob.owner})" if ob.owner and ob.owner not in ob.label else "")
    d = ob.distance(rect)
    if d <= 0:
        return f"overlaps {what}"
    return f"{d:.1f} mil from {what} (needs {clearance:.1f})"


def quality_problems(placer, comp, rect, rot, proposals, max_gap):
    """How a label at rect reads, apart from clearances: far from its part,
    taken for another part's label, over a part body, upside down.
    proposals are other labels' new boxes ({des: (rect, rotation)});
    labels not in it are where they are now."""
    found = []
    gap, d_own, other, d_other, same, d_same = placer.association(comp, rect)
    area = max(1e-9, (rect[2] - rect[0]) * (rect[3] - rect[1]))
    inside = rect_overlap_area(rect, comp.extent) > 0.99 * area
    if gap > max_gap:
        # Say whether it still lines up with its part - a label block
        # under an array of parts reads fine when every label does
        e, lx, ly = comp.extent, (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
        aligned = ", in line with it" if (e[0] <= lx <= e[2] or e[1] <= ly <= e[3]) else ""
        found.append(f"{gap:.0f} mil from its part{aligned}")
    # Readers pair labels by elimination: when the nearer same-type part
    # has its own label even closer to it, this one cannot be taken for it
    band = ambiguity_band(d_own)
    if same is not None and d_same < d_own + band:
        srect = proposals[same.designator][0] if same.designator in proposals else (
            same.text_box if same.visible else None)
        if srect is not None and _point_rect_dist((srect[0] + srect[2]) / 2, (srect[1] + srect[3]) / 2,
                                                  same.skeleton) < d_same:
            same = None
    if same is not None and d_same < d_own - band:
        found.append(f"reads as {same.designator}'s label: {same.designator} is closer")
    elif same is not None and d_same < d_own + band:
        found.append(f"ambiguous: {same.designator} is about as close")
    elif not inside and placer.misleading(comp, gap, d_own, other, d_other, float("inf")):
        found.append(f"away from its part and closer to {other.designator}")
    for owner, body in placer.bodies[comp.side].query(rect):
        if rect_overlap_area(rect, body) / area > 0.25:
            found.append("under its own part (hidden after assembly)" if owner == comp.designator
                         else f"over part {owner}")
            break
    if round(rot) % 360 not in READABLE_ROTATIONS.get(comp.side, ()):
        readable = " or ".join(str(r) for r in READABLE_ROTATIONS.get(comp.side, ()))
        found.append(f"rotation {rot:g} reads upside down (use {readable})")
    return found


def evaluate(board, proposals, options=None, geometry=True):
    """Problems with designator boxes {des: (rect, rotation)}: clearance to
    silk, mask openings, the board edge and other designators (current
    positions, or the other proposals), plus placement quality - far from its
    part, closer to another part, over a part body, upside down. With
    geometry=False only quality is checked (Altium's DRC covers the rest),
    plus text over open vias, which pad-only silk-to-mask rules miss.
    Clearances are the board's rules exactly - no placement safety margin.
    Returns {des: [problem, ...]}; an empty list means the spot is good."""
    opt = options or Options(extra_clearance=0.0)
    placer = Placer(board, list(proposals), opt)
    gap_clear = placer.clearance[SILK]
    others = SpatialHash(60.0)
    for des, (rect, _) in proposals.items():
        others.add(rect, (des, rect))
    result = {}
    for des, (rect, rot) in proposals.items():
        comp = board.components[des]
        found = []
        if geometry:
            for ob in placer.blockers(rect, comp.side, first_only=False):
                found.append(_describe(ob, rect, placer.clearance[ob.cls]))
            for other, orect in others.query(_inflate(rect, gap_clear)):
                if other != des and board.components[other].side == comp.side:
                    d = _rect_rect_dist(rect, orect)
                    if d < gap_clear:
                        found.append(f"{d:.1f} mil from designator {other} (needs {gap_clear:.1f})"
                                     if d > 0 else f"overlaps designator {other}")
        elif opt.avoid_vias:
            vias = [ob for ob in placer.blockers(rect, comp.side, first_only=False)
                    if ob.label == "via" and ob.distance(rect) <= 0]
            if vias:
                found.append(f"over {len(vias)} open via{'s' if len(vias) > 1 else ''}")
        found += quality_problems(placer, comp, rect, rot, proposals, opt.max_gap)
        result[des] = found
    return result


def resolve_placement(board, spec):
    """Turn a placement request into (rect, rotation, height, stroke).

    spec: designator plus either x/y (box centre) or side ("above", "below",
    "left", "right", "inside") with optional gap (mils from the part's pads
    and silk; default just clear of them) and offset (slide along the side,
    +x or +y). rotation/height/stroke_width optional; neither x/y nor side
    keeps the current centre."""
    comp = board.components[spec["designator"]]
    rot = spec.get("rotation")
    if rot is None:
        cur = round(comp.text_rotation) % 360
        rot = cur if cur in READABLE_ROTATIONS.get(comp.side, (0,)) else 0
    rot = float(rot) % 360
    w0, h0, height, stroke = text_dims(comp, spec.get("height"))
    if spec.get("stroke_width") is not None:
        pad_old = h0 - height
        stroke_new = float(spec["stroke_width"])
        pad_new = pad_old * stroke_new / stroke if stroke else pad_old
        w0, h0, stroke = w0 - pad_old + pad_new, h0 - pad_old + pad_new, stroke_new
    tw, th = (h0, w0) if round(rot) % 180 == 90 else (w0, h0)
    e = comp.extent
    if spec.get("x") is not None and spec.get("y") is not None:
        cx, cy = float(spec["x"]), float(spec["y"])
    elif spec.get("side"):
        side = SIDE_ALIASES.get(str(spec["side"]).lower())
        if side is None:
            raise ValueError(f"side must be above, below, left, right or inside, not {spec['side']!r}")
        gap = spec.get("gap")
        gap = max(board.s2s, board.s2m) + 0.5 if gap is None else float(gap)
        off = float(spec.get("offset") or 0)
        ecx, ecy = (e[0] + e[2]) / 2, (e[1] + e[3]) / 2
        if side == "N":
            cx, cy = ecx + off, e[3] + gap + th / 2
        elif side == "S":
            cx, cy = ecx + off, e[1] - gap - th / 2
        elif side == "W":
            cx, cy = e[0] - gap - tw / 2, ecy + off
        elif side == "E":
            cx, cy = e[2] + gap + tw / 2, ecy + off
        else:
            cx, cy = ecx + off, ecy
    else:
        cx, cy = comp.text_center()
    return (cx - tw / 2, cy - th / 2, cx + tw / 2, cy + th / 2), rot, height, stroke


def focus_box(board, designators, side="T"):
    """Bounding box of the given parts on one side (None if there are none)."""
    bb = None
    for d in designators:
        c = board.components.get(d)
        if c is not None and c.side == side:
            bb = _union(bb, c.extent)
    return bb


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _nice_step(span, lines=10):
    for step in (5, 10, 25, 50, 100, 250, 500, 1000, 2500):
        if span / step <= lines:
            return step
    return 5000


def render_view(board, boxes, focus=None, side="T", targets=(), problems=(), options=None,
                max_px=1400, margin=100.0, grid=None):
    """PNG of one board side for an agent to read and pick coordinates from.

    Drawn: board edge, part bodies (dim), solder mask openings (copper), silk
    (white), part names at their centres (grey), designator boxes from boxes
    {des: (rect, rotation)} (yellow; problems red; targets cyan with a line to
    their part) and numbered option boxes {des: [Candidate]} (green). A grid
    with labelled lines (mils) lets positions be read off directly. focus is
    the area to show (None = whole board)."""
    from PIL import Image, ImageDraw, ImageFont

    if focus is None:
        xs = [p[0] for p in board.outline] or [0.0, 1000.0]
        ys = [p[1] for p in board.outline] or [0.0, 1000.0]
        focus = (min(xs), min(ys), max(xs), max(ys))
        margin = min(margin, 40.0)
    fx0, fy0, fx1, fy1 = _inflate(focus, margin)
    scale = max_px / max(fx1 - fx0, fy1 - fy0)
    W, H = int((fx1 - fx0) * scale) + 1, int((fy1 - fy0) * scale) + 1

    def pt(x, y):
        return ((x - fx0) * scale, (fy1 - y) * scale)

    def box(r):
        (x0, y0), (x1, y1) = pt(r[0], r[3]), pt(r[2], r[1])
        return [x0, y0, max(x1, x0 + 1), max(y1, y0 + 1)]

    font_cache = {}

    def font(px):
        px = max(8, int(px))
        if px not in font_cache:
            try:
                font_cache[px] = ImageFont.load_default(size=px)
            except TypeError:
                font_cache[px] = ImageFont.load_default()
        return font_cache[px]

    img = Image.new("RGB", (W, H), (18, 22, 28))
    dr = ImageDraw.Draw(img)
    if board.outline:
        dr.polygon([pt(x, y) for x, y in board.outline], fill=(20, 52, 34), outline=(150, 150, 150))

    view = (fx0, fy0, fx1, fy1)

    def visible(bb):
        return not (bb[2] < view[0] or bb[0] > view[2] or bb[3] < view[1] or bb[1] > view[3])

    for c in board.components.values():
        if c.side == side and visible(c.extent):
            for body in c.bodies:
                dr.rectangle(box(body), outline=(60, 80, 70))
    for s, ob in board.mask:
        if s != side or not visible(ob.bbox):
            continue
        col = (120, 90, 40) if ob.label == "via" else (184, 115, 51)
        if ob.shape == CIRCLE:
            x, y, r = ob.geom
            dr.ellipse(box((x - r, y - r, x + r, y + r)), fill=col)
        elif ob.shape == SEG:
            x1, y1, x2, y2, hw = ob.geom
            dr.line([pt(x1, y1), pt(x2, y2)], fill=col, width=max(1, int(2 * hw * scale)))
        elif ob.shape == POLY:
            dr.polygon([pt(x, y) for x, y in ob.geom], fill=col)
        else:
            dr.rectangle(box(ob.geom), fill=col)
    for s, ob, kind, owner in board.silk:
        if s != side or not visible(ob.bbox):
            continue
        if ob.shape == SEG:
            x1, y1, x2, y2, hw = ob.geom
            dr.line([pt(x1, y1), pt(x2, y2)], fill=(235, 235, 235), width=max(1, int(2 * hw * scale)))
        else:
            dr.rectangle(box(ob.geom), outline=(200, 200, 200))

    # Grid over the copper, under the labels: read coordinates off the image
    step = grid or _nice_step(max(fx1 - fx0, fy1 - fy0))
    gf = font(12)
    gx = math.ceil(fx0 / step) * step
    while gx <= fx1:
        px, _ = pt(gx, 0)
        dr.line([(px, 0), (px, H)], fill=(70, 100, 130), width=1)
        dr.text((px + 2, 1), f"{gx:g}", font=gf, fill=(150, 190, 230))
        gx += step
    gy = math.ceil(fy0 / step) * step
    while gy <= fy1:
        _, py = pt(0, gy)
        dr.line([(0, py), (W, py)], fill=(70, 100, 130), width=1)
        dr.text((2, py + 1), f"{gy:g}", font=gf, fill=(150, 190, 230))
        gy += step

    # Part names at part centres, so a part can be identified even when its
    # designator is somewhere else
    name_px = min(14.0, 30 * scale)
    if name_px >= 8:
        nf = font(name_px)
        for c in board.components.values():
            if c.side == side and visible(c.extent):
                cx, cy = pt((c.extent[0] + c.extent[2]) / 2, (c.extent[1] + c.extent[3]) / 2)
                tw = dr.textlength(c.designator, font=nf)
                dr.text((cx - tw / 2, cy - name_px / 2), c.designator, font=nf, fill=(165, 165, 190))

    def label_box(rect, rot, text, col, width=1):
        b = box(rect)
        dr.rectangle(b, outline=col, width=width)
        vertical = round(rot) % 180 == 90
        bw, bh = b[2] - b[0], b[3] - b[1]
        length, thick = (bh, bw) if vertical else (bw, bh)
        f = font(min(thick * 0.8, length / max(1, len(text)) * 1.6))
        tw = int(dr.textlength(text, font=f)) + 2
        th = int(f.size * 1.3) if hasattr(f, "size") else 12
        tile = Image.new("RGBA", (max(1, tw), max(1, th)), (0, 0, 0, 0))
        ImageDraw.Draw(tile).text((1, 0), text, font=f, fill=col + (255,))
        if vertical:
            tile = tile.rotate(90 if round(rot) % 360 == 90 else -90, expand=True)
        img.paste(tile, (int(b[0] + (bw - tile.width) / 2), int(b[1] + (bh - tile.height) / 2)), tile)

    targets, problems = set(targets), set(problems)
    for des, (rect, rot) in boxes.items():
        if visible(rect) and des not in targets:
            label_box(rect, rot, des, (255, 80, 80) if des in problems else (250, 220, 60),
                      2 if des in problems else 1)

    for des in targets:
        c = board.components.get(des)
        if c is None or c.side != side:
            continue
        dr.rectangle(box(c.extent), outline=(60, 220, 255), width=2)
        if des in boxes:
            rect, rot = boxes[des]
            ax, ay = pt((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2)
            bx, by = pt((c.extent[0] + c.extent[2]) / 2, (c.extent[1] + c.extent[3]) / 2)
            dr.line([(ax, ay), (bx, by)], fill=(60, 220, 255), width=1)
            label_box(rect, rot, des, (60, 220, 255), 2)

    many = len(options or {}) > 1
    of = font(13)
    for des, cands in (options or {}).items():
        for i, cand in enumerate(cands, 1):
            b = box(cand.rect)
            dr.rectangle(b, outline=(90, 240, 110), width=2)
            dr.text((b[0] + 2, b[1] + 1), f"{des}#{i}" if many else str(i), font=of, fill=(90, 240, 110))

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()
