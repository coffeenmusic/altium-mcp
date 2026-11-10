"""Grid router and pad fanout for route_connections and fanout_pads.

Pure Python, all geometry in mils. Other nets' copper (pads, tracks, arcs,
vias, fills, copper regions) is drawn onto a grid per copper layer, grown by
the clearance plus half the new track's width, so a track whose centre line
stays on free cells keeps its clearance; a second grid per layer is grown by
the via radius instead, for via sites. Polygon pours are not obstacles: they
pour again around new copper.

A cell holds 0 (free), a net number (only that net may use it: its own
copper), or -1 (blocked for every net). Routes run between cell centres in
eight directions, so every segment is 0, 45 or 90 degrees; a layer change
puts a via on a cell free on every layer the via spans.
"""
import heapq
import math
from array import array

DIRS = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]


class RouteError(ValueError):
    pass


def _seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def _point_in_poly(x, y, pts):
    inside = False
    j = len(pts) - 1
    for i in range(len(pts)):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def _arc_points(cx, cy, r, a1, a2, step=10.0):
    sweep = (a2 - a1) % 360 or 360
    n = max(2, int(sweep / step) + 1)
    return [(cx + r * math.cos(math.radians(a1 + sweep * k / (n - 1))),
             cy + r * math.sin(math.radians(a1 + sweep * k / (n - 1)))) for k in range(n)]


class Shape:
    """A piece of copper: kind 'seg' (centre line a-b, half width), 'circle',
    'rect' (centre, half sizes, rotation) or 'poly' (outline)."""

    def __init__(self, kind, net, layers, **g):
        self.kind, self.net, self.layers, self.g = kind, net, layers, g

    def bbox(self):
        g = self.g
        if self.kind == "seg":
            return (min(g["ax"], g["bx"]) - g["hw"], min(g["ay"], g["by"]) - g["hw"],
                    max(g["ax"], g["bx"]) + g["hw"], max(g["ay"], g["by"]) + g["hw"])
        if self.kind == "circle":
            return g["cx"] - g["r"], g["cy"] - g["r"], g["cx"] + g["r"], g["cy"] + g["r"]
        if self.kind == "rect":
            e = math.hypot(g["hx"], g["hy"])
            return g["cx"] - e, g["cy"] - e, g["cx"] + e, g["cy"] + e
        xs, ys = [p[0] for p in g["pts"]], [p[1] for p in g["pts"]]
        return min(xs), min(ys), max(xs), max(ys)

    def dist(self, x, y):
        """Distance from (x, y) to the copper (0 inside)."""
        g = self.g
        if self.kind == "seg":
            return max(0.0, _seg_dist(x, y, g["ax"], g["ay"], g["bx"], g["by"]) - g["hw"])
        if self.kind == "circle":
            return max(0.0, math.hypot(x - g["cx"], y - g["cy"]) - g["r"])
        if self.kind == "rect":
            c, s = g["cos"], g["sin"]
            dx, dy = x - g["cx"], y - g["cy"]
            lx, ly = abs(dx * c + dy * s) - g["hx"], abs(-dx * s + dy * c) - g["hy"]
            return math.hypot(max(lx, 0.0), max(ly, 0.0))
        if _point_in_poly(x, y, g["pts"]):
            return 0.0
        pts = g["pts"]
        return min(_seg_dist(x, y, *pts[i], *pts[(i + 1) % len(pts)]) for i in range(len(pts)))


def shapes_from_layout(lay, signal_layers):
    """Obstacle shapes from a get_layout result (with pads). Pours are left out."""
    out = []
    for p in lay.get("pads", []):
        layers = list(signal_layers) if p["layer"] == "Multi Layer" else [p["layer"]]
        sx, sy = p["size"]
        if p["shape"] == "round" and abs(sx - sy) < 1e-6:
            out.append(Shape("circle", p["net"], layers, cx=p["x"], cy=p["y"], r=sx / 2, pad=True))
        else:
            a = math.radians(p["rotation"])
            out.append(Shape("rect", p["net"], layers, cx=p["x"], cy=p["y"], hx=sx / 2, hy=sy / 2,
                             cos=math.cos(a), sin=math.sin(a), pad=True))
    for t in lay.get("tracks", []):
        out.append(Shape("seg", t["net"], [t["layer"]], ax=t["start"][0], ay=t["start"][1], bx=t["end"][0],
                         by=t["end"][1], hw=t["width"] / 2))
    for a in lay.get("arcs", []):
        pts = _arc_points(a["center"][0], a["center"][1], a["radius"], a["start_angle"], a["end_angle"])
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            out.append(Shape("seg", a["net"], [a["layer"]], ax=ax, ay=ay, bx=bx, by=by, hw=a["width"] / 2))
    for v in lay.get("vias", []):
        out.append(Shape("circle", v["net"], list(signal_layers), cx=v["x"], cy=v["y"], r=v["diameter"] / 2))
    for f in lay.get("fills", []):
        (x1, y1), (x2, y2) = f["corner1"], f["corner2"]
        out.append(Shape("poly", f["net"], [f["layer"]], pts=[(x1, y1), (x2, y1), (x2, y2), (x1, y2)]))
    for r in lay.get("regions", []):
        out.append(Shape("poly", r["net"], [r["layer"]], pts=[(v["x"], v["y"]) for v in r["outline"]]))
    return out


class Board:
    """The routing grids over an area: per layer, a track grid and a via grid."""

    def __init__(self, area, pitch, layers, width, clearance, via_diameter, nets):
        self.x0, self.y0, x1, y1 = area
        self.pitch = float(pitch)
        self.nx = int(math.ceil((x1 - self.x0) / self.pitch)) + 1
        self.ny = int(math.ceil((y1 - self.y0) / self.pitch)) + 1
        if self.nx * self.ny > 2_000_000:
            raise RouteError(f"the area is too large for a {pitch} mil grid ({self.nx} x {self.ny} cells)")
        self.layers = list(layers)
        self.width, self.clearance, self.via_r = width, clearance, via_diameter / 2
        self.net_id = {}
        for n in nets:
            self.id(n)
        # Routes run between cell centres that each keep the clearance; between
        # two such points the centre line can pass a corner a little closer,
        # by at most D - sqrt(D^2 - (step / 2)^2): about 0.2 mil for a 2.5 mil
        # grid and a 9 mil reach. Vias sit on cell centres.
        reach = clearance + width / 2
        step = self.pitch * math.sqrt(2)
        safety = reach - math.sqrt(max(reach * reach - (step / 2) ** 2, 0.0)) + 0.05
        self.grow_track = reach + safety
        self.grow_via = clearance + self.via_r + 0.05
        self.track = {l: array("i", bytes(4 * self.nx * self.ny)) for l in self.layers}
        self.via = {l: array("i", bytes(4 * self.nx * self.ny)) for l in self.layers}

    def id(self, net):
        if not net:
            return -1   # copper with no net blocks every net
        return self.net_id.setdefault(net, len(self.net_id) + 1)

    def cell(self, x, y):
        return int(round((x - self.x0) / self.pitch)), int(round((y - self.y0) / self.pitch))

    def xy(self, ix, iy):
        return self.x0 + ix * self.pitch, self.y0 + iy * self.pitch

    def _mark(self, grid, shape, grow, owner):
        bx1, by1, bx2, by2 = shape.bbox()
        i1, j1 = self.cell(bx1 - grow, by1 - grow)
        i2, j2 = self.cell(bx2 + grow, by2 + grow)
        nx = self.nx
        for j in range(max(0, j1), min(self.ny - 1, j2) + 1):
            y = self.y0 + j * self.pitch
            row = j * nx
            for i in range(max(0, i1), min(nx - 1, i2) + 1):
                if shape.dist(self.x0 + i * self.pitch, y) <= grow:
                    cur = grid[row + i]
                    grid[row + i] = owner if cur in (0, owner) else -1

    def add(self, shape):
        owner = self.id(shape.net)
        for l in shape.layers:
            if l in self.track:
                self._mark(self.track[l], shape, self.grow_track, owner)
                self._mark(self.via[l], shape, self.grow_via, owner)

    def free(self, layer, ix, iy, net):
        if not (0 <= ix < self.nx and 0 <= iy < self.ny):
            return False
        v = self.track[layer][iy * self.nx + ix]
        return v == 0 or v == net

    def via_free(self, ix, iy, net):
        k = iy * self.nx + ix
        return all(self.via[l][k] in (0, net) for l in self.layers)


def route_one(board, net_name, start, end, start_layers, end_layers, via_cost=30.0, bend_cost=2.0, window=None):
    """A* from start to end (mils). start_layers/end_layers: the layers the
    ends can be reached on (a pad's layer, or every layer for a through
    hole). Returns a list of (layer, [points]) legs and the via points, or
    None when no path exists within window (i1, j1, i2, j2)."""
    net = board.id(net_name)
    s, e = board.cell(*start), board.cell(*end)
    layers = board.layers
    i1, j1, i2, j2 = window or (0, 0, board.nx - 1, board.ny - 1)
    P = board.pitch

    def h(i, j):
        dx, dy = abs(i - e[0]), abs(j - e[1])
        return P * (max(dx, dy) + 0.4142 * min(dx, dy))

    openq, came, cost = [], {}, {}
    for l in start_layers:
        if l in board.track:
            k = (s[0], s[1], l)
            cost[k] = 0.0
            came[k] = (None, None)
            heapq.heappush(openq, (h(*s), 0.0, k))
    goal = None
    while openq:
        f, g, k = heapq.heappop(openq)
        if g > cost.get(k, 1e18):
            continue
        i, j, l = k
        if (i, j) == e and l in end_layers:
            goal = k
            break
        prev_dir = came[k][1]
        for d, (di, dj) in enumerate(DIRS):
            ni, nj = i + di, j + dj
            if not (i1 <= ni <= i2 and j1 <= nj <= j2):
                continue
            if not board.free(l, ni, nj, net) and (ni, nj) != e:
                continue
            step = P * (1.4142 if di and dj else 1.0)
            if prev_dir is not None and prev_dir != d:
                step += bend_cost * P
            nk = (ni, nj, l)
            ng = g + step
            if ng < cost.get(nk, 1e18):
                cost[nk] = ng
                came[nk] = (k, d)
                heapq.heappush(openq, (ng + h(ni, nj), ng, nk))
        if len(layers) > 1 and board.via_free(i, j, net):
            for nl in layers:
                if nl == l:
                    continue
                nk = (i, j, nl)
                ng = g + via_cost * P
                if ng < cost.get(nk, 1e18):
                    cost[nk] = ng
                    came[nk] = (k, None)
                    heapq.heappush(openq, (ng + h(i, j), ng, nk))
    if goal is None:
        return None
    cells = []
    k = goal
    while k is not None:
        cells.append(k)
        k = came[k][0]
    cells.reverse()
    legs, vias = [], []
    cur_layer, pts = cells[0][2], [board.xy(cells[0][0], cells[0][1])]
    for (i, j, l) in cells[1:]:
        p = board.xy(i, j)
        if l != cur_layer:
            vias.append(p)
            legs.append((cur_layer, pts))
            cur_layer, pts = l, [p]
        else:
            pts.append(p)
    legs.append((cur_layer, pts))
    # snap the ends onto the exact start and end points
    legs[0][1][0] = (float(start[0]), float(start[1]))
    legs[-1][1][-1] = (float(end[0]), float(end[1]))
    return [(l, _corners(p)) for l, p in legs], vias


def _corners(pts):
    """Keep only the points where the direction changes."""
    if len(pts) <= 2:
        return pts
    out = [pts[0]]
    for a, b, c in zip(pts, pts[1:], pts[2:]):
        d1 = (round(b[0] - a[0], 6), round(b[1] - a[1], 6))
        d2 = (round(c[0] - b[0], 6), round(c[1] - b[1], 6))
        if math.atan2(d1[1], d1[0]) != math.atan2(d2[1], d2[0]):
            out.append(b)
    out.append(pts[-1])
    return out


def leg_shapes(net, layer, pts, width):
    return [Shape("seg", net, [layer], ax=a[0], ay=a[1], bx=b[0], by=b[1], hw=width / 2)
            for a, b in zip(pts, pts[1:]) if a != b]


def fanout_site(board, pad, net_name, width, via_d, max_distance, center, pad_layers):
    """A via site for a pad: tried outward from center first, then the other
    directions, nearest first. Returns (via point, track legs) or None."""
    net = board.id(net_name)
    px, py = pad["x"], pad["y"]
    sx, sy = pad["size"]
    # A pad that holds the whole via (an exposed thermal pad) gets it in the middle
    if min(sx, sy) >= via_d:
        ix, iy = board.cell(px, py)
        if 0 <= ix < board.nx and 0 <= iy < board.ny and board.via_free(ix, iy, net):
            return (px, py), []
    ax, ay = px - center[0], py - center[1]
    outward = math.degrees(math.atan2(ay, ax)) if (ax or ay) else 0.0
    angles = sorted(range(0, 360, 45), key=lambda a: abs(((a - outward + 180) % 360) - 180))
    start = max(sx, sy) / 2 + via_d / 2 + board.clearance
    steps = [start + k * board.pitch for k in range(int(max(0, max_distance - start) / board.pitch) + 1)]
    for a in angles:
        c, s = math.cos(math.radians(a)), math.sin(math.radians(a))
        for dist in steps:
            vx, vy = px + dist * c, py + dist * s
            ix, iy = board.cell(vx, vy)
            if not (0 <= ix < board.nx and 0 <= iy < board.ny) or not board.via_free(ix, iy, net):
                continue
            # the track from the pad centre to the via must stay on free cells
            n = max(2, int(math.hypot(vx - px, vy - py) / (board.pitch / 2)))
            ok = all(board.free(l, *board.cell(px + (vx - px) * t / n, py + (vy - py) * t / n), net)
                     for l in pad_layers if l in board.track for t in range(n + 1))
            if ok:
                return (vx, vy), [(l, [(px, py), (vx, vy)]) for l in pad_layers[:1]]
    return None
