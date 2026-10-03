"""Label blocks: a row of parts gets a matching row of designators.

When parts sit in a row and their designators do not fit next to them, the
designators go together in a block, in the same order as the parts, where
there is room - even some way off. A block away from its parts needs a link
the reader can follow: a silk leader line with an arrowhead pointing at the
parts, or a matching index marker (a capital letter in a circle, square or
triangle) next to the block and next to the parts. A block right against its
row needs no link.

Same conventions as silkscreen.py: mils from the board origin, designator
positions are text-box centres. Blocks are laid out in (u, v) coordinates:
u runs along the row in reading order (left to right, or top to bottom), v
across it.
"""

import math

import silkscreen as ss
from silkscreen import SILK, MASK

LINE_W = 4.0            # leader and marker line width
ARROW = 10.0            # arrowhead stroke length
MARKER_SIZE = 25.0      # marker letter height
MARKER_STROKE = 4.0
NEAR_GAP = 20.0         # a block this close to its row, alongside it, needs no link
ROW_GAP = 50.0          # parts further apart than this along a row are not one row
SHAPES = ("circle", "square", "triangle")
LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"     # no I or O: they read as 1 and 0

# Cost weights, in mil-equivalents
LINK_COST = {"none": 0.0, "leader": 30.0, "index": 90.0}
W_LEADER = 0.2          # per mil of leader
W_BLOCK_GAP = 0.5       # per mil between block and row
W_SHIFT = 0.1           # per mil the block centre slides along the row
STYLE_COST = {"aligned": 0.0, "packed": 6.0, "line": 10.0}
SMALLER_TEXT = 2.0      # per mil of text height below the row's own


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------

def _span(e, axis):
    """(u0, u1, v0, v1) of an extent along a row on axis 'x' or 'y'."""
    return (e[0], e[2], e[1], e[3]) if axis == "x" else (e[1], e[3], e[0], e[2])


def find_row(board, designator, max_gap=ROW_GAP):
    """The parts in a row with designator: (axis, members in reading order).
    A row is parts side by side along x (or y), overlapping across it by at
    least half the smaller part, of similar size across the row, with gaps
    of at most max_gap. Returns (None, [designator]) for a part in no row of
    three or more."""
    seed = board.components[designator]
    pool = [c for c in board.components.values() if c.side == seed.side and c.extent]
    best = (None, [seed])
    for axis in ("x", "y"):
        row = [seed]
        for direction in (1, -1):
            cur = seed
            while True:
                cu0, cu1, cv0, cv1 = _span(cur.extent, axis)
                nxt, nxt_step = None, None
                for c in pool:
                    if c in row:
                        continue
                    u0, u1, v0, v1 = _span(c.extent, axis)
                    small, big = sorted((v1 - v0, cv1 - cv0))
                    if small <= 0 or big > 2.5 * small:
                        continue                    # much bigger across the row
                    if min(v1, cv1) - max(v0, cv0) < 0.5 * small:
                        continue                    # not in line
                    short, long_ = sorted((u1 - u0, cu1 - cu0))
                    if long_ > 3 * short:
                        continue                    # e.g. an IC among passives
                    step = ((u0 + u1) - (cu0 + cu1)) / 2 * direction
                    gap = u0 - cu1 if direction == 1 else cu0 - u1
                    if step <= 0 or gap < -0.5 * short or gap > max_gap:
                        continue
                    if nxt_step is None or step < nxt_step:
                        nxt, nxt_step = c, step
                if nxt is None:
                    break
                row.append(nxt)
                cur = nxt
        if len(row) > len(best[1]):
            best = (axis, row)
    axis, row = best
    # Parts that go without a designator stay part of the row's geometry
    # but get no label in the block
    row = [c for c in row if c.designator == designator or not _no_label(c)]
    if len(row) < 3:
        return None, [designator]
    return axis, [c.designator for c in sorted(row, key=lambda c: _reading_key(c, axis))]


NO_LABEL = ("MT", "FID")        # mounting holes and fiducials never need a designator


def _no_label(c):
    """Mounting holes, fiducials, and test points whose designator is hidden."""
    return c.prefix in NO_LABEL or (c.prefix == "TP" and not c.visible)


def _reading_key(c, axis):
    e = c.extent
    return (e[0] + e[2]) / 2 if axis == "x" else -(e[1] + e[3]) / 2


def row_axis(board, members):
    """'x' when the parts spread along x, else 'y'."""
    xs = [(board.components[d].extent[0] + board.components[d].extent[2]) / 2 for d in members]
    ys = [(board.components[d].extent[1] + board.components[d].extent[3]) / 2 for d in members]
    return "x" if max(xs) - min(xs) >= max(ys) - min(ys) else "y"


# ---------------------------------------------------------------------------
# (u, v) <-> board coordinates
# ---------------------------------------------------------------------------

class Frame:
    """u along the row in reading order, v across it."""

    def __init__(self, axis):
        self.axis = axis

    def rect_uv(self, r):
        if self.axis == "x":
            return r
        return (-r[3], r[0], -r[1], r[2])

    def rect_xy(self, r):
        if self.axis == "x":
            return r
        u0, v0, u1, v1 = r
        return (v0, -u1, v1, -u0)

    def pt_xy(self, u, v):
        return (u, v) if self.axis == "x" else (v, -u)


# ---------------------------------------------------------------------------
# Graphics
# ---------------------------------------------------------------------------

def track(x1, y1, x2, y2, w=LINE_W):
    return {"kind": "track", "x1": round(x1, 3), "y1": round(y1, 3), "x2": round(x2, 3),
            "y2": round(y2, 3), "w": w}


def to_items(side, graphics, op="+"):
    """Graphics as edit_silk_graphics items."""
    out = []
    for g in graphics:
        if g["kind"] == "track":
            out.append(f"{op}T|{side}|{g['x1']}|{g['y1']}|{g['x2']}|{g['y2']}|{g['w']}")
        elif g["kind"] == "arc":
            out.append(f"{op}A|{side}|{g['cx']}|{g['cy']}|{g['r']}|{g['a1']}|{g['a2']}|{g['w']}")
        else:
            out.append(f"{op}X|{side}|{g['cx']}|{g['cy']}|{g['size']}|{g['stroke']}|{g.get('rot', 0)}|{g['text']}")
    return out


def _graphic_rects(g):
    """Boxes covering a graphic's ink, for clearance checks. Straight tracks
    are covered by a few boxes along them so diagonals are not inflated."""
    if g["kind"] == "track":
        hw = g["w"] / 2
        x1, y1, x2, y2 = g["x1"], g["y1"], g["x2"], g["y2"]
        n = 1 if x1 == x2 or y1 == y2 else 4
        out = []
        for i in range(n):
            ax, ay = x1 + (x2 - x1) * i / n, y1 + (y2 - y1) * i / n
            bx, by = x1 + (x2 - x1) * (i + 1) / n, y1 + (y2 - y1) * (i + 1) / n
            out.append((min(ax, bx) - hw, min(ay, by) - hw, max(ax, bx) + hw, max(ay, by) + hw))
        return out
    if g["kind"] == "arc":
        r = g["r"] + g["w"] / 2
        return [(g["cx"] - r, g["cy"] - r, g["cx"] + r, g["cy"] + r)]
    w, h = _letter_size(g["text"], g["size"], g["stroke"])
    return [(g["cx"] - w / 2, g["cy"] - h / 2, g["cx"] + w / 2, g["cy"] + h / 2)]


def _letter_size(text, size, stroke):
    """Ink box of stroke text (measured in Altium: 'A' at 25/4 is 20.7 x 29)."""
    narrow = sum(1 for ch in text if ch in "I1")
    return (len(text) - narrow) * size * 2 / 3 + narrow * size / 3 + stroke, size + stroke


def marker_graphics(cx, cy, letter, shape):
    """A capital letter in a shape, centred on (cx, cy): (graphics, bbox)."""
    lw, lh = _letter_size(letter, MARKER_SIZE, MARKER_STROKE)
    clear = 2.5 + LINE_W / 2             # letter to shape centreline
    g = []
    if shape == "circle":
        r = math.ceil(math.hypot(lw / 2, lh / 2) + clear)
        g.append({"kind": "arc", "cx": round(cx, 3), "cy": round(cy, 3), "r": r, "a1": 0, "a2": 360, "w": LINE_W})
        ty = cy
        half = r + LINE_W / 2
        box = (cx - half, cy - half, cx + half, cy + half)
    elif shape == "square":
        s = math.ceil(max(lw, lh) / 2 + clear)
        pts = [(cx - s, cy - s), (cx + s, cy - s), (cx + s, cy + s), (cx - s, cy + s)]
        g += [track(*pts[i], *pts[(i + 1) % 4]) for i in range(4)]
        ty = cy
        half = s + LINE_W / 2
        box = (cx - half, cy - half, cx + half, cy + half)
    else:
        # Equilateral, point up; the letter's box (plus clearance) sits on the base
        bw, bh = lw + 2 * clear, lh + 2 * clear
        side = bw + 2 * bh / math.sqrt(3)
        height = side * math.sqrt(3) / 2
        base = cy - height / 3            # centroid at (cx, cy)
        pts = [(cx - side / 2, base), (cx + side / 2, base), (cx, base + height)]
        g += [track(*pts[i], *pts[(i + 1) % 3]) for i in range(3)]
        ty = base + bh / 2
        hw = LINE_W / 2
        box = (cx - side / 2 - hw, base - hw, cx + side / 2 + hw, base + height + hw)
    g.append({"kind": "text", "cx": round(cx, 3), "cy": round(ty, 3), "size": MARKER_SIZE,
              "stroke": MARKER_STROKE, "rot": 0, "text": letter})
    return g, box


def next_marker(used):
    """The first (letter, shape) not in used: letters in circles, then squares,
    then triangles."""
    for shape in SHAPES:
        for letter in LETTERS:
            if (letter, shape) not in used:
                return letter, shape
    return None


# ---------------------------------------------------------------------------
# Block options
# ---------------------------------------------------------------------------

class BlockOption:
    __slots__ = ("members", "side", "axis", "style", "height", "labels", "link", "marker",
                 "graphics", "gap", "cost", "summary")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    @property
    def rect(self):
        return _bounds(self.labels.values())

    def placements(self):
        out = []
        for d in self.members:
            r, rot, h, stroke = self.labels[d]
            out.append({"designator": d, "x": round((r[0] + r[2]) / 2, 3), "y": round((r[1] + r[3]) / 2, 3),
                        "rotation": rot, "height": h, "stroke_width": stroke, "visible": True})
        return out

    def to_dict(self, n):
        d = {"n": n, "summary": self.summary, "style": self.style, "text_height": self.height,
             "gap_mils": round(self.gap, 1), "link": self.link}
        if self.marker:
            d["marker"] = {"letter": self.marker[0], "shape": self.marker[1]}
        return d


def _bounds(rects):
    rects = list(rects)
    if rects and len(rects[0]) == 4 and not isinstance(rects[0][0], (int, float)):
        rects = [r[0] for r in rects]
    return (min(r[0] for r in rects), min(r[1] for r in rects),
            max(r[2] for r in rects), max(r[3] for r in rects))


class _Checker:
    """Clearance and body checks for new labels and graphics."""

    def __init__(self, board, members, side, ignore=()):
        self.side = side
        self.placer = ss.Placer(board, members, ss.Options())
        self.silk = self.placer.clearance[SILK]
        self.mask = self.placer.clearance[MASK]
        # Graphics about to be replaced (an earlier block of these parts)
        for g in ignore:
            area = ss._inflate(_bounds(_graphic_rects(g)), 2.0)
            for ob in self.placer.obstacles[side].query(area):
                b = ob.bbox
                if (not ob.owner and ob.cls == SILK and area[0] <= b[0] and area[1] <= b[1]
                        and b[2] <= area[2] and b[3] <= area[3]):
                    self.placer.obstacles[side].remove(b, ob)

    def legal(self, rect, taken=()):
        """Clear of everything on the board (other than the members' labels)
        and of taken boxes, on the board, and not over a part body."""
        if self.placer.blockers(rect, self.side):
            return False
        for t in taken:
            if ss._rect_rect_dist(rect, t) < self.silk:
                return False
        area = max(1e-9, (rect[2] - rect[0]) * (rect[3] - rect[1]))
        for owner, body in self.placer.bodies[self.side].query(rect):
            if ss.rect_overlap_area(rect, body) / area > 0.25:
                return False
        return True

    def graphic_legal(self, graphics, taken=()):
        for g in graphics:
            for r in _graphic_rects(g):
                if self.placer.blockers(r, self.side):
                    return False
                if any(ss._rect_rect_dist(r, t) < self.silk for t in taken):
                    return False
        return True


def block_options(board, members, count=3, min_height=25.0, max_distance=500.0, link="auto",
                  used_markers=(), ignore=()):
    """Ranked blocks for the given parts (one row, one side). Each option has
    the labels' boxes, rotations and sizes, and the link graphics.
    link: "auto" (cheapest), "none", "leader" or "index". ignore: graphics
    that are being replaced (they are not obstacles)."""
    comps = [board.components[d] for d in members]
    side = comps[0].side
    if any(c.side != side for c in comps) or side not in ("T", "B"):
        raise ValueError("the parts of a block must all be on the same side")
    axis = row_axis(board, members)
    members = sorted(members, key=lambda d: _reading_key(board.components[d], axis))
    comps = [board.components[d] for d in members]
    fr = Frame(axis)
    chk = _Checker(board, members, side, ignore)
    gap_min = chk.silk + 1.0

    band = fr.rect_uv(_bounds(c.extent for c in comps))
    centers_u = [(fr.rect_uv(c.extent)[0] + fr.rect_uv(c.extent)[2]) / 2 for c in comps]
    vertical = 90 if side == "T" else 270
    rot_stack = vertical if axis == "x" else 0      # long side across the row
    rot_line = 0 if axis == "x" else vertical       # long side along the row

    h0 = min(c.text_height for c in comps)
    heights = [h0]
    while heights[-1] - 5 >= min_height - 1e-6:
        heights.append(heights[-1] - 5)

    def layouts():
        """(style, height, rotation, [(du, dv)], [u offsets from the block start], aligned u-centres or None)."""
        for h in heights:
            dims = [ss.text_dims(c, h) for c in comps]          # (w0, h0, height, stroke)
            st = [(d[1], d[0]) for d in dims]                   # stack: thin along u
            # aligned: each label at its part's u when the pitch allows
            ok = all(centers_u[i + 1] - centers_u[i] >= (st[i][0] + st[i + 1][0]) / 2 + gap_min
                     for i in range(len(comps) - 1))
            if ok:
                yield "aligned", h, rot_stack, st, dims, list(centers_u)
            pitch = max(s[0] for s in st) + gap_min
            yield "packed", h, rot_stack, st, dims, [i * pitch for i in range(len(comps))]
            ln = [(d[0], d[1]) for d in dims]
            gap = max(gap_min, 0.35 * h)
            offs, u = [], 0.0
            for du, _ in ln:
                offs.append(u + du / 2)
                u += du + gap
            yield "line", h, rot_line, ln, dims, offs

    def label_rects(sizes, offs, u_shift, sign, v_edge):
        """Block labels in uv: centred at offs + u_shift along u, flush with
        v_edge (the edge facing the row) across it."""
        out = []
        thick = max(dv for _, dv in sizes)
        for (du, dv), u in zip(sizes, offs):
            uc = u + u_shift
            if sign > 0:
                v0 = v_edge if dv == thick else v_edge + (thick - dv) / 2
            else:
                v0 = v_edge - dv if dv == thick else v_edge - thick + (thick - dv) / 2
            out.append((uc - du / 2, v0, uc + du / 2, v0 + dv))
        return out

    def flush(sizes, offs, u_shift, sign, v_edge, style):
        # stack labels share the near edge; line labels share the centre line
        if style == "line":
            return label_rects(sizes, offs, u_shift, sign, v_edge)
        out = []
        for (du, dv), u in zip(sizes, offs):
            uc = u + u_shift
            v0 = v_edge if sign > 0 else v_edge - dv
            out.append((uc - du / 2, v0, uc + du / 2, v0 + dv))
        return out

    g0 = max(chk.silk, chk.mask) + 0.5
    near_gaps = [g0 + k for k in (0, 2, 5, 10, 15, 20, 30, 40, 55, 70, 90, 120)]
    row_len = band[2] - band[0]
    row_mid = (band[0] + band[2]) / 2

    found = []
    seen = set()
    for style, h, rot, sizes, dims, offs in layouts():
        span = (offs[0] - sizes[0][0] / 2, offs[-1] + sizes[-1][0] / 2)
        mid = (span[0] + span[1]) / 2
        blen = span[1] - span[0]
        thick = max(dv for _, dv in sizes)
        if style == "aligned":
            shifts = [0.0]
        else:
            reach = (row_len + blen) / 2 + 20
            shifts = [row_mid - mid + s for s in _steps(reach, 10.0)]
        positions = []
        for sign in (1, -1):
            for gp in near_gaps:
                v_edge = band[3] + gp if sign > 0 else band[1] - gp
                for sh in shifts:
                    positions.append((sh, sign, v_edge))
        if style != "aligned":
            # Further out, anywhere within max_distance of the row
            step = 20.0
            u_lo, u_hi = band[0] - max_distance - mid, band[2] + max_distance - mid
            v_lo, v_hi = band[1] - max_distance, band[3] + max_distance
            u = u_lo
            while u <= u_hi:
                v = v_lo
                while v <= v_hi:
                    sign = 1 if v > (band[1] + band[3]) / 2 else -1
                    v_edge = v if sign > 0 else v + thick
                    positions.append((u, sign, v_edge))
                    v += step
                u += step
        for sh, sign, v_edge in positions:
            uv = flush(sizes, offs, sh, sign, v_edge, style)
            brect = _bounds(uv)
            if ss._rect_rect_dist(brect, band) < g0:
                continue
            key = (style, h, round(brect[0]), round(brect[1]))
            if key in seen:
                continue
            seen.add(key)
            xy = [fr.rect_xy(r) for r in uv]
            if not all(chk.legal(r) for r in xy):
                continue
            gap = ss._rect_rect_dist(brect, band)
            if gap > max_distance:
                continue
            base = (W_BLOCK_GAP * gap + STYLE_COST[style] + SMALLER_TEXT * (h0 - h)
                    + W_SHIFT * abs((brect[0] + brect[2]) / 2 - row_mid))
            labels = {d: (r, rot, dm[2], dm[3]) for d, r, dm in zip(members, xy, dims)}
            found.append((base, style, h, labels, uv, brect, gap))

    # Visit blocks by the least they can cost with the cheapest link they
    # could have; stop once no remaining block can beat the picks
    def least(f):
        base, _, _, _, _, brect, gap = f
        overlap = min(brect[2], band[2]) - max(brect[0], band[0])
        if gap <= NEAR_GAP and overlap >= 0.6 * min(brect[2] - brect[0], band[2] - band[0])                 and link in ("auto", "none"):
            return base
        if link == "none":
            return float("inf")
        if link == "index":
            return base + LINK_COST["index"]
        return base + LINK_COST["leader"] + W_LEADER * gap

    found = [(least(f),) + f for f in found]
    found.sort(key=lambda f: f[0])
    options = []
    for i, (bound, base, style, h, labels, uv, brect, gap) in enumerate(found):
        if i >= 800 or bound == float("inf"):
            break
        picked = _distinct(options, count)
        if len(picked) >= count and bound >= max(o.cost for o in picked):
            break
        link_kind, graphics, marker, link_cost, note = _link(
            board, chk, fr, band, uv, brect, gap, side, link, used_markers, labels)
        if link_kind is None:
            continue
        opt = BlockOption(members=members, side=side, axis=axis, style=style, height=h, labels=labels,
                          link=link_kind, marker=marker, graphics=graphics, gap=gap,
                          cost=base + link_cost)
        opt.summary = _summary(opt, fr, band, brect, note)
        options.append(opt)
    return axis, members, _distinct(options, count)


def _distinct(options, count):
    """The cheapest options whose blocks do not mostly overlap."""
    picked = []
    for o in sorted(options, key=lambda o: o.cost):
        r = o.rect
        area = max(1e-9, (r[2] - r[0]) * (r[3] - r[1]))
        if any(ss.rect_overlap_area(r, p.rect) > 0.3 * area for p in picked):
            continue
        picked.append(o)
        if len(picked) >= count:
            break
    return picked


def _steps(reach, step):
    out, k = [0.0], 1
    while k * step <= reach:
        out += [k * step, -k * step]
        k += 1
    return out


def _link(board, chk, fr, band, uv, brect, gap, side, want, used_markers, labels):
    """(kind, graphics, marker, cost, note) of the cheapest allowed link, or
    kind None."""
    taken = [r for r, _, _, _ in labels.values()]
    overlap = min(brect[2], band[2]) - max(brect[0], band[0])
    beside = overlap >= 0.6 * min(brect[2] - brect[0], band[2] - band[0])
    if gap <= NEAR_GAP and beside and want in ("auto", "none"):
        return "none", [], None, LINK_COST["none"], ""
    if want == "none":
        return None, None, None, 0, ""
    if want in ("auto", "leader"):
        leader = _leader(chk, fr, band, brect, taken)
        if leader:
            graphics, length = leader
            return "leader", graphics, None, LINK_COST["leader"] + W_LEADER * length, f"{length:.0f} mil leader"
        if want == "leader":
            return None, None, None, 0, ""
    marker = next_marker(used_markers)
    if marker is None:
        return None, None, None, 0, ""
    placed = _markers(chk, fr, band, brect, taken, marker)
    if placed is None:
        return None, None, None, 0, ""
    return "index", placed, marker, LINK_COST["index"], f"index {marker[0]} in a {marker[1]}"


def _leader(chk, fr, band, brect, taken):
    """A straight or L-shaped silk line from the block to the row, with an
    arrowhead at the row: (graphics in xy, length) or None."""
    w = LINE_W
    off_silk = chk.silk + w / 2 + 0.5        # from the block's labels
    off_mask = chk.mask + w / 2 + 0.5        # from the row's pads
    cands = []
    lo, hi = max(brect[0], band[0]), min(brect[2], band[2])
    if lo + 2 * w < hi:
        # Straight across: the block is beside the row along u
        for u in _sweep(lo + w, hi - w):
            if brect[1] >= band[3]:
                cands.append([(u, brect[1] - off_silk), (u, band[3] + off_mask)])
            else:
                cands.append([(u, brect[3] + off_silk), (u, band[1] - off_mask)])
    lo, hi = max(brect[1], band[1]), min(brect[3], band[3])
    if lo + 2 * w < hi:
        # Straight along: the block is in line with the row
        for v in _sweep(lo + w, hi - w):
            if brect[0] >= band[2]:
                cands.append([(brect[0] - off_silk, v), (band[2] + off_mask, v)])
            else:
                cands.append([(brect[2] + off_silk, v), (band[0] - off_mask, v)])
    if not cands:
        # L-shape: out of the block's end along u, then across into the row
        vb = (brect[1] + brect[3]) / 2
        for u in _sweep(band[0] + w, band[2] - w):
            if brect[0] >= band[2] or brect[2] <= band[0]:
                u_start = brect[0] - off_silk if brect[0] >= band[2] else brect[2] + off_silk
                v_end = band[3] + off_mask if vb > band[3] else band[1] - off_mask
                cands.append([(u_start, vb), (u, vb), (u, v_end)])
    best = None
    for pts in cands:
        length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))
        if length < 2 * ARROW or (best and length >= best[1]):
            continue
        g = _polyline_with_arrow(fr, pts)
        if chk.graphic_legal(g, taken):
            best = (g, length)
    return best


def _sweep(lo, hi, step=5.0):
    """Points from the middle of [lo, hi] outwards."""
    mid = (lo + hi) / 2
    out = [mid]
    k = 1
    while mid - k * step >= lo:
        out += [mid - k * step, mid + k * step]
        k += 1
    return out


def _polyline_with_arrow(fr, pts):
    g = []
    xy = [fr.pt_xy(u, v) for u, v in pts]
    for a, b in zip(xy, xy[1:]):
        g.append(track(a[0], a[1], b[0], b[1]))
    (px, py), (tx, ty) = xy[-2], xy[-1]
    d = math.hypot(tx - px, ty - py)
    ux, uy = (tx - px) / d, (ty - py) / d
    k = ARROW / math.sqrt(2)
    for sx in (1, -1):
        # Back from the tip at 45 degrees on each side
        bx = tx - k * ux + sx * k * (-uy)
        by = ty - k * uy + sx * k * ux
        g.append(track(tx, ty, bx, by))
    return g


def _markers(chk, fr, band, brect, taken, marker):
    """Matching markers next to the block and next to the row: graphics or None."""
    letter, shape = marker
    _, box = marker_graphics(0.0, 0.0, letter, shape)
    half_w, half_h = (box[2] - box[0]) / 2, (box[3] - box[1]) / 2
    hu, hv = (half_w, half_h) if fr.axis == "x" else (half_h, half_w)
    gap = max(chk.silk, chk.mask) + 1.0

    def spots(rect):
        """Marker centres around rect, nearest first."""
        cu, cv = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
        along = _sweep(rect[0] + hu, rect[2] - hu, 10.0) if rect[2] - rect[0] > 2 * hu else [cu]
        out = []
        for g in (gap, gap + 10, gap + 25, gap + 45):
            out += [(rect[0] - g - hu, cv), (rect[2] + g + hu, cv)]
            for su in along:
                out += [(su, rect[3] + g + hv), (su, rect[1] - g - hv)]
        return out

    def place(uv_pt):
        x, y = fr.pt_xy(*uv_pt)
        g, b = marker_graphics(x, y, letter, shape)
        return (g, b) if chk.legal(b, taken) else None

    at_row = [m for m in (place(p) for p in spots(band)) if m is not None]
    if not at_row:
        return None
    for pb in spots(brect):
        at_block = place(pb)
        if at_block is None:
            continue
        for g, b in at_row:
            if ss._rect_rect_dist(b, at_block[1]) >= chk.silk:
                return at_block[0] + g
    return None


def _summary(opt, fr, band, brect, note):
    where = []
    if brect[1] >= band[3]:
        where.append("above" if fr.axis == "x" else "right of")
    elif brect[3] <= band[1]:
        where.append("below" if fr.axis == "x" else "left of")
    elif brect[0] >= band[2]:
        where.append("after the last part, in line" if fr.axis == "x" else "below, in line")
    else:
        where.append("before the first part, in line" if fr.axis == "x" else "above, in line")
    style = {"aligned": "each label in line with its part", "packed": "side by side",
             "line": "end to end"}[opt.style]
    link = {"none": "no link needed", "leader": "leader line", "index": "index markers"}[opt.link]
    text = f"{style}, {where[0]} the row, {opt.gap:.0f} mil away, {opt.height:g} mil text, {link}"
    return text + (f" ({note})" if note else "")


# ---------------------------------------------------------------------------
# Recorded blocks
# ---------------------------------------------------------------------------

def record(option):
    """What to remember about an applied block to recognise it later."""
    return {"members": list(option.members), "side": option.side,
            "labels": {d: [round((r[0] + r[2]) / 2, 3), round((r[1] + r[3]) / 2, 3), rot]
                       for d, (r, rot, _, _) in option.labels.items()},
            "link": option.link, "graphics": option.graphics,
            "marker": list(option.marker) if option.marker else None}


def check_record(board, rec, tol=1.5):
    """Problems with a recorded block on the board as it is now (empty = intact)."""
    problems = []
    for d, (x, y, rot) in rec["labels"].items():
        c = board.components.get(d)
        if c is None or not c.visible:
            problems.append(f"{d} is missing or hidden")
            continue
        cx, cy = c.text_center()
        if abs(cx - x) > tol or abs(cy - y) > tol or round(c.text_rotation) % 360 != round(rot) % 360:
            problems.append(f"{d} has moved out of the block")
    free = [(k, v) for s, k, v in board.free_silk if s == rec["side"]]
    for g in rec["graphics"]:
        if not any(_same_graphic(g, k, v) for k, v in free):
            problems.append(f"link {g['kind']} at ({g.get('x1', g.get('cx'))}, {g.get('y1', g.get('cy'))}) is missing")
    return problems


def _same_graphic(g, kind, v, tol=0.3):
    if g["kind"] == "track" and kind == "T":
        a = (g["x1"], g["y1"], g["x2"], g["y2"])
        return (all(abs(p - q) <= tol for p, q in zip(a, v[:4])) or
                all(abs(p - q) <= tol for p, q in zip((a[2], a[3], a[0], a[1]), v[:4])))
    if g["kind"] == "arc" and kind == "A":
        return all(abs(p - q) <= tol for p, q in zip((g["cx"], g["cy"], g["r"]), v[:3]))
    if g["kind"] == "text" and kind == "X":
        return v[2] == g["text"] and abs(v[0] - g["cx"]) <= tol + 1 and abs(v[1] - g["cy"]) <= tol + 1
    return False


def is_association_problem(problem):
    """Quality problems a block's link answers: distance from the part and
    which part a label reads as."""
    return (" mil from its part" in problem or problem.startswith(("reads as", "ambiguous", "away from its part")))


def used_markers(board, records):
    """(letter, shape) pairs taken on the board: recorded blocks, plus any
    free single-letter text (counted for every shape, to be safe)."""
    used = {tuple(r["marker"]) for r in records if r.get("marker")}
    for _, kind, v in board.free_silk:
        if kind == "X" and len(v[2]) == 1 and v[2].isalpha():
            used |= {(v[2].upper(), s) for s in SHAPES}
    return used
