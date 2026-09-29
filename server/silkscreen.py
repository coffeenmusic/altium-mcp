"""Silkscreen designator placement: geometry model, candidate search, solver.

The DelphiScript side exports the board (export_silkscreen_data) as one
pipe-delimited line per object; this module parses it, finds a legal spot for
every designator and renders previews. Pure Python (no numpy) so it runs in
the MCP server's minimal environment.

Conventions:
- All coordinates are mils relative to the board origin.
- A designator position is the CENTRE of the text box Altium reports. That
  makes placement independent of text anchors, justification and bottom-side
  mirroring: the Altium side rotates the text, then moves its box centre.
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
                 "mirrored", "pattern", "bodies", "extent", "prefix", "skeleton")

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
                c.text_box = tuple(_f(v) for v in f[11:15])
                c.text_rotation = _f(f[15])
                c.text_height = _f(f[16])
                c.stroke = _f(f[17])
                c.autopos = int(f[18] or 0)
                c.mirrored = f[19] == "1"
                c.pattern = f[20] if len(f) > 20 else ""
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
                r = tuple(_f(v) for v in f[4:8])
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
    def __init__(self, max_gap=50.0, orientation="auto", keep_valid=True, avoid_vias=True,
                 edge_margin=10.0, extra_clearance=0.5, min_height=None, per_group=10):
        self.max_gap = max_gap
        self.orientation = orientation
        self.keep_valid = keep_valid
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
FAR_GAP = 15.0         # mils: beyond this a label must be nearest its own part
W_OTHER_BODY = 80.0    # x fraction of the text box over another part's body
W_OWN_BODY = 25.0      # x fraction over its own body (hidden after assembly)
KEEP_BONUS = 15.0      # current position is preferred when it is legal
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
            # Designators being placed are not obstacles - their new boxes are
            # handled by the solver's conflict check instead
            if kind == "D" and owner in self.scope:
                continue
            if side in self.obstacles:
                self.obstacles[side].add(ob.bbox, ob)
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
        reach = d_own + AMBIGUITY_MARGIN + 1
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
    def misleading(gap, d_own, d_other, d_same):
        """A same-type part clearly closer to the label than its own part -
        or, once the label sits away from its part, any other part."""
        if d_same < d_own - 1.0:
            return True
        return gap > FAR_GAP and d_other < d_own - 1.0

    def soft_penalty(self, comp, rect):
        _, d_own, _, d_other, _, d_same = self.association(comp, rect)
        pen = (W_AMBIGUITY * max(0.0, d_own + AMBIGUITY_MARGIN - d_same) +
               W_CROSS_AMBIGUITY * max(0.0, d_own + AMBIGUITY_MARGIN - d_other))
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

    def text_dims(self, comp, height=None):
        """(w0, h0, height, stroke) of the text box at rotation 0, optionally
        scaled to a new text height."""
        w0, h0 = comp.text_size0()
        if not height or abs(height - comp.text_height) < 1e-6 or comp.text_height <= 0:
            return w0, h0, comp.text_height, comp.stroke
        k = height / comp.text_height
        stroke = max(3.0, round(comp.stroke * k * 2) / 2)
        pad = h0 - comp.text_height            # box margin from the stroke width
        pad_new = pad * (stroke / comp.stroke) if comp.stroke else pad
        return (w0 - pad) * k + pad_new, height + pad_new, height, stroke

    def raw_candidates(self, comp, height=None):
        """Every candidate box around the part with its static cost."""
        w0, h0, height, stroke = self.text_dims(comp, height)
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

    def current_candidate(self, comp):
        """The designator's present position, if it is legal, readable and
        clearly belongs to its part."""
        rot = round(comp.text_rotation) % 360
        if rot not in READABLE_ROTATIONS.get(comp.side, ()):
            return None
        rect = comp.text_box
        if self.blockers(rect, comp.side):
            return None
        tw, th = rect[2] - rect[0], rect[3] - rect[1]
        inside = self._fits_inside(comp, tw, th) and rect_overlap_area(rect, comp.extent) >= 0.99 * tw * th
        gap, d_own, _, _, _, d_same = self.association(comp, rect)
        if not inside and (gap > self.opt.max_gap or d_same < d_own + AMBIGUITY_MARGIN):
            return None
        area = max(1e-9, tw * th)
        for owner, body in self.bodies[comp.side].query(rect):
            frac = rect_overlap_area(rect, body) / area
            if owner != comp.designator and frac > 0.02:
                return None          # over another part
            if owner == comp.designator and frac > 0.25 and not inside:
                return None          # under its own (small) part
        cost = self.soft_penalty(comp, rect) + (SIDE_COST["IN"] if inside else 0.0) - KEEP_BONUS
        return Candidate(comp.designator, rect, rot, cost, "KEEP",
                         comp.text_height, comp.stroke, "kept")

    def candidates(self, comp, height=None):
        """Best legal candidates, a few per (rotation, side) group so the
        solver has real alternatives when neighbours compete."""
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
            gap, d_own, _, d_other, _, d_same = self.association(comp, cand.rect)
            if self.misleading(gap, d_own, d_other, d_same):
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
# Solver
# ---------------------------------------------------------------------------

class _Placed:
    """Placed designator boxes with a spatial index for conflict checks."""

    def __init__(self, gap):
        self.gap = gap
        self.hash = SpatialHash(60.0)
        self.by_des = {}

    def conflicts(self, rect, ignore=()):
        out = []
        for cand in self.hash.query(_inflate(rect, self.gap)):
            if cand.designator in ignore:
                continue
            if _rect_rect_dist(rect, cand.rect) < self.gap:
                out.append(cand.designator)
        return out

    def put(self, cand):
        self.by_des[cand.designator] = cand
        self.hash.add(cand.rect, cand)

    def take(self, des):
        cand = self.by_des.pop(des)
        self.hash.remove(cand.rect, cand)
        return cand


EVICT_COST = 50.0      # displacing a placed designator...
EVICT_HISTORY = 30.0   # ...and more each time it has been displaced before


def solve(cands, gap, iterations=3000, top=40):
    """Choose one candidate per designator with no two boxes closer than gap.

    1. Greedy: most-constrained designators first, cheapest free candidate.
    2. Ejection chains: an unplaced designator takes the spot that is
       cheapest counting the neighbours it would displace; those are evicted
       and immediately retry their own free alternatives, or queue up to
       evict in turn. A per-designator eviction history makes repeat
       evictions expensive, so the search does not cycle. The best state
       seen (fewest unplaced, then lowest cost) wins.
    3. Polish: every designator moves to a cheaper spot that has opened up.
    Returns (placed {des: Candidate}, unplaced [des]).
    """
    placed = _Placed(gap)
    order = sorted(cands, key=lambda d: (len(cands[d]), cands[d][0].cost if cands[d] else 0))
    queue = []
    for des in order:
        free = next((c for c in cands[des] if not placed.conflicts(c.rect)), None)
        if free is not None:
            placed.put(free)
        else:
            queue.append(des)

    def score():
        return (len(queue), sum(c.cost for c in placed.by_des.values()))

    best = (score(), dict(placed.by_des), list(queue))
    history = {}
    steps = 0
    while queue and steps < iterations:
        steps += 1
        des = queue.pop(0)
        choice, choice_cost, choice_blockers = None, float("inf"), ()
        for cand in cands[des][:top]:
            blockers = placed.conflicts(cand.rect)
            total = cand.cost + sum(EVICT_COST + EVICT_HISTORY * history.get(b, 0) for b in blockers)
            if total < choice_cost:
                choice, choice_cost, choice_blockers = cand, total, blockers
        if choice is None:
            continue
        for b in choice_blockers:
            placed.take(b)
            history[b] = history.get(b, 0) + 1
        placed.put(choice)
        for b in choice_blockers:
            free = next((c for c in cands[b] if not placed.conflicts(c.rect)), None)
            if free is not None:
                placed.put(free)
            else:
                queue.append(b)
        sc = score()
        if sc < best[0]:
            best = (sc, dict(placed.by_des), list(queue))

    # Restore the best state seen
    placed = _Placed(gap)
    for cand in best[1].values():
        placed.put(cand)
    unplaced = best[2]

    for _ in range(3):
        moved = False
        for des in list(placed.by_des):
            cur = placed.by_des[des]
            for cand in cands[des]:
                if cand.cost >= cur.cost - 1e-9:
                    break
                if not placed.conflicts(cand.rect, ignore=(des,)):
                    placed.take(des)
                    placed.put(cand)
                    moved = True
                    break
        for des in list(unplaced):
            free = next((c for c in cands[des] if not placed.conflicts(c.rect)), None)
            if free is not None:
                placed.put(free)
                unplaced.remove(des)
                moved = True
        if not moved:
            break
    return dict(placed.by_des), unplaced


def plan_silkscreen(board, designators=None, options=None):
    """Plan designator positions. Returns a dict with 'placements'
    (designator -> Candidate), 'kept', 'unplaced' ({des: reason}) and 'skipped'."""
    opt = options or Options()
    comps = board.components
    skipped = {}
    if designators:
        scope = []
        for d in designators:
            c = comps.get(d)
            if c is None:
                skipped[d] = "not on board"
            elif not c.visible:
                skipped[d] = "designator hidden"
            elif c.side not in ("T", "B"):
                skipped[d] = "designator not on an overlay layer"
            else:
                scope.append(d)
    else:
        scope = [d for d, c in comps.items() if c.visible and c.side in ("T", "B")]

    placer = Placer(board, scope, opt)
    cands = {}
    for des in scope:
        comp = comps[des]
        lst = placer.candidates(comp)
        if opt.keep_valid:
            cur = placer.current_candidate(comp)
            if cur is not None:
                lst.append(cur)
                lst.sort(key=lambda c: c.cost)
        # Crowded parts also get smaller-text alternatives (when allowed):
        # each 5 mil step down costs like 50 mil of distance
        if len(lst) < CROWDED and opt.min_height and opt.min_height < comp.text_height:
            h = comp.text_height - 5
            while h >= opt.min_height - 1e-6:
                smaller = placer.candidates(comp, h)
                for c in smaller:
                    c.cost += 10.0 * (comp.text_height - h)
                    c.note = "reduced height"
                lst += smaller
                if len(smaller) >= CROWDED:
                    break
                h -= 5
            lst.sort(key=lambda c: c.cost)
        cands[des] = lst

    gap = placer.clearance[SILK]
    placed, unplaced = solve({d: l for d, l in cands.items() if l}, gap)
    reasons = {}
    for des in scope:
        if des in placed:
            continue
        if not cands[des]:
            blocked = placer.diagnose(comps[des])
            reasons[des] = "no legal spot within max_gap" + (": blocked by " + ", ".join(blocked) if blocked else "")
        else:
            reasons[des] = "every legal spot collides with another designator"
    kept = sorted(d for d, c in placed.items() if c.side_name == "KEEP")
    return {"placer": placer, "placements": placed, "kept": kept,
            "unplaced": reasons, "skipped": skipped, "scope": scope}


# ---------------------------------------------------------------------------
# Quality report for the CURRENT designator positions
# ---------------------------------------------------------------------------

def assess_current(board, designators=None, options=None):
    """Geometric quality issues that Altium's DRC does not report: text far
    from or ambiguous between parts, over another part's body, outside the
    board, or rotated to read upside down."""
    opt = options or Options()
    comps = board.components
    scope = [d for d in (designators or comps) if d in comps and comps[d].visible
             and comps[d].side in ("T", "B")]
    placer = Placer(board, scope, opt)
    issues = []
    for des in scope:
        c = comps[des]
        rect = c.text_box
        gap, d_own, _, _, same, d_same = placer.association(c, rect)
        found = []
        if gap > opt.max_gap:
            found.append(f"{gap:.0f} mil from its part")
        if same is not None and d_same < d_own + AMBIGUITY_MARGIN:
            found.append(f"ambiguous: {same.designator} is as close ({d_same:.0f} vs {d_own:.0f} mil)")
        area = max(1e-9, (rect[2] - rect[0]) * (rect[3] - rect[1]))
        for owner, body in placer.bodies[c.side].query(rect):
            frac = rect_overlap_area(rect, body) / area
            if frac > 0.25:
                found.append("under its own part" if owner == des else f"over part {owner}")
                break
        if any(ob.cls == EDGE for ob in placer.blockers(rect, c.side, first_only=False)):
            found.append("outside or too close to the board edge")
        if round(c.text_rotation) % 360 not in READABLE_ROTATIONS.get(c.side, ()):
            found.append(f"rotation {c.text_rotation:g} reads upside down")
        if found:
            issues.append({"designator": des, "issues": found})
    return issues


# ---------------------------------------------------------------------------
# Preview rendering
# ---------------------------------------------------------------------------

def render_preview(board, boxes, focus=None, side="T", highlight=(), max_px=1600, margin=120.0):
    """PNG of one board side: mask openings, silk, board edge and the given
    designator boxes ({designator: (rect, rotation)}). Designators in
    highlight are drawn in red. focus is a bbox to zoom to (None = board)."""
    from PIL import Image, ImageDraw, ImageFont

    if focus is None:
        xs = [p[0] for p in board.outline] or [0.0, 1000.0]
        ys = [p[1] for p in board.outline] or [0.0, 1000.0]
        focus = (min(xs), min(ys), max(xs), max(ys))
    fx0, fy0, fx1, fy1 = _inflate(focus, margin)
    scale = max_px / max(fx1 - fx0, fy1 - fy0)
    W, H = int((fx1 - fx0) * scale) + 1, int((fy1 - fy0) * scale) + 1

    def pt(x, y):
        return ((x - fx0) * scale, (fy1 - y) * scale)

    def box(r):
        (x0, y0), (x1, y1) = pt(r[0], r[3]), pt(r[2], r[1])
        return [x0, y0, max(x1, x0 + 1), max(y1, y0 + 1)]

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
    moving = set(boxes)
    for s, ob, kind, owner in board.silk:
        if s != side or not visible(ob.bbox):
            continue
        if kind == "D" and owner in moving:
            continue
        if ob.shape == SEG:
            x1, y1, x2, y2, hw = ob.geom
            dr.line([pt(x1, y1), pt(x2, y2)], fill=(235, 235, 235), width=max(1, int(2 * hw * scale)))
        else:
            dr.rectangle(box(ob.geom), outline=(200, 200, 200))

    font_cache = {}

    def font(px):
        px = max(8, int(px))
        if px not in font_cache:
            try:
                font_cache[px] = ImageFont.load_default(size=px)
            except TypeError:
                font_cache[px] = ImageFont.load_default()
        return font_cache[px]

    for des, (rect, rot) in boxes.items():
        if not visible(rect):
            continue
        col = (255, 80, 80) if des in highlight else (250, 220, 60)
        b = box(rect)
        dr.rectangle(b, outline=col, width=2 if des in highlight else 1)
        vertical = rot % 180 == 90
        bw, bh = b[2] - b[0], b[3] - b[1]
        length, thick = (bh, bw) if vertical else (bw, bh)
        f = font(min(thick * 0.8, length / max(1, len(des)) * 1.6))
        tw = int(dr.textlength(des, font=f)) + 2
        th = int(f.size * 1.3) if hasattr(f, "size") else 12
        tile = Image.new("RGBA", (max(1, tw), max(1, th)), (0, 0, 0, 0))
        ImageDraw.Draw(tile).text((1, 0), des, font=f, fill=col + (255,))
        if vertical:
            tile = tile.rotate(90 if rot % 360 == 90 else -90, expand=True)
        img.paste(tile, (int(b[0] + (bw - tile.width) / 2), int(b[1] + (bh - tile.height) / 2)), tile)

    for des in highlight:
        c = board.components.get(des)
        if c is not None and c.side == side and des not in boxes and visible(c.extent):
            dr.rectangle(box(c.extent), outline=(255, 60, 60), width=2)

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


def focus_box(board, designators, side="T"):
    """Bounding box of the given parts' extents on one side (None if empty)."""
    bb = None
    for d in designators:
        c = board.components.get(d)
        if c is not None and c.side == side:
            bb = _union(bb, c.extent)
    return bb
