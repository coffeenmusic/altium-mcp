"""Routing geometry: check routed copper for angles, and draw routes that are right.

Tracks drawn by scripts came out with stubs a few degrees off 45 (a diagonal
whose end was snapped to a grid on its own, or dragged when a via moved) and
with 90 degree corners (#59). check() finds both on a board; route()
builds tracks whose segments are 0, 45 or 90 degrees by construction.

Pure Python, so it is tested without Altium. Coordinates are Altium internal
units (10000 per mil) relative to the board origin unless a name says mils.
"""

import math
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

COORD_PER_MIL = 10000


def to_mils(v: int) -> float:
    return round(v / COORD_PER_MIL, 3)


def parse_geometry(text: str) -> Dict[str, list]:
    """The records of the bridge's get_routing_geometry, relative to the board origin.

    O|x origin|y origin, T|layer|net|x1|y1|x2|y2|width, A|layer|net,
    V|x|y|size and P|x|y|x size|y size, all in internal units.
    """
    ox = oy = 0
    geo = {"tracks": [], "arcs": [], "vias": [], "pads": []}
    for line in text.splitlines():
        f = line.split("|")
        if f[0] == "O":
            ox, oy = int(f[1]), int(f[2])
        elif f[0] == "T" and len(f) >= 8:
            # The net name may itself contain '|': the fields around it are fixed
            x1, y1, x2, y2, w = (int(v) for v in f[-5:])
            geo["tracks"].append({"layer": f[1], "net": "|".join(f[2:-5]),
                                  "x1": x1 - ox, "y1": y1 - oy, "x2": x2 - ox, "y2": y2 - oy, "width": w})
        elif f[0] == "A":
            geo["arcs"].append({"layer": f[1], "net": "|".join(f[2:])})
        elif f[0] == "V":
            geo["vias"].append((int(f[1]) - ox, int(f[2]) - oy, int(f[3])))
        elif f[0] == "P":
            geo["pads"].append((int(f[1]) - ox, int(f[2]) - oy, int(f[3]), int(f[4])))
    return geo


def angle_class(dx: int, dy: int, tol_deg: float = 0.1) -> Tuple[str, float]:
    """('orthogonal' | '45' | 'off-angle' | 'zero-length', degrees off the nearest multiple of 45)."""
    if dx == 0 and dy == 0:
        return "zero-length", 0.0
    a = math.degrees(math.atan2(dy, dx)) % 180
    off = min(a % 45, 45 - a % 45)
    if off > tol_deg:
        return "off-angle", off
    return ("orthogonal" if round(a / 45) % 2 == 0 else "45"), off


def check(geo: Dict[str, list], tol_deg: float = 0.1, nets: Optional[List[str]] = None) -> dict:
    """Off-angle segments and sharp corners in routed tracks.

    A corner is where exactly two segment ends of one net meet on one layer,
    not under a via or pad (there the copper is round or square anyway).
    The turn is the change of direction: 0 is straight on, 45 the normal
    routed corner, 90 a right angle, more than 90 an acute corner.
    """
    want = {n.upper() for n in nets} if nets else None
    tracks = [t for t in geo["tracks"] if want is None or t["net"].upper() in want]
    classes, off_angle = defaultdict(int), []
    for t in tracks:
        dx, dy = t["x2"] - t["x1"], t["y2"] - t["y1"]
        kind, off = angle_class(dx, dy, tol_deg)
        classes[kind] += 1
        if kind == "off-angle":
            off_angle.append({
                "net": t["net"], "layer": t["layer"],
                "start_mils": [to_mils(t["x1"]), to_mils(t["y1"])], "end_mils": [to_mils(t["x2"]), to_mils(t["y2"])],
                "length_mils": to_mils(round(math.hypot(dx, dy))),
                "angle_deg": round(math.degrees(math.atan2(dy, dx)) % 360, 2), "off_by_deg": round(off, 2)})

    ends = defaultdict(list)
    for t in tracks:
        ends[(t["layer"], t["net"], t["x1"], t["y1"])].append((t["x2"] - t["x1"], t["y2"] - t["y1"]))
        ends[(t["layer"], t["net"], t["x2"], t["y2"])].append((t["x1"] - t["x2"], t["y1"] - t["y2"]))

    # Pad sizes are before rotation: the larger one covers both axes
    def covered(x, y):
        return (any(abs(x - vx) <= s / 2 and abs(y - vy) <= s / 2 for vx, vy, s in geo["vias"])
                or any(abs(x - px) <= max(sx, sy) / 2 and abs(y - py) <= max(sx, sy) / 2
                       for px, py, sx, sy in geo["pads"]))

    turns, corners = defaultdict(int), []
    for (layer, net, x, y), dirs in ends.items():
        if len(dirs) != 2 or covered(x, y):
            continue
        (ax, ay), (bx, by) = dirs
        la, lb = math.hypot(ax, ay), math.hypot(bx, by)
        if not la or not lb:
            continue
        turn = 180 - math.degrees(math.acos(max(-1.0, min(1.0, (ax * bx + ay * by) / (la * lb)))))
        if turn < 1:
            turns["straight"] += 1
        elif abs(turn - 45) < 1:
            turns["45"] += 1
        else:
            kind = "90" if abs(turn - 90) < 1 else ("acute" if turn > 90 else "other")
            turns[kind] += 1
            if kind in ("90", "acute"):
                corners.append({"net": net, "layer": layer, "at_mils": [to_mils(x), to_mils(y)],
                                "turn_deg": round(turn, 1), "kind": kind})
    return {"segments": len(tracks), "segment_angles": dict(classes), "corner_turns": dict(turns),
            "off_angle": off_angle, "sharp_corners": corners}


def _sign(v: int) -> int:
    return (v > 0) - (v < 0)


def route(points: List[Tuple[int, int]], diagonal_first: bool = False, chamfer: int = 0) -> List[Tuple[int, int, int, int]]:
    """Segments through the points, each exactly 0, 45 or 90 degrees.

    Between two points that are not in line, the route takes a straight leg
    and a diagonal whose two runs are equal by construction: the diagonal is
    computed from its start point, never snapped on its own. diagonal_first
    puts the diagonal at the start of each stretch. A chamfer (internal
    units, 0 for none) cuts every right-angle corner between two straight
    legs with a 45 degree segment, at most half the shorter leg.
    """
    pts = [tuple(int(v) for v in p) for p in points]
    path = pts[:1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        dx, dy = x1 - x0, y1 - y0
        if dx and dy and abs(dx) != abs(dy):
            d = min(abs(dx), abs(dy))
            if diagonal_first:
                path.append((x0 + _sign(dx) * d, y0 + _sign(dy) * d))
            elif abs(dx) > abs(dy):
                path.append((x1 - _sign(dx) * d, y0))
            else:
                path.append((x0, y1 - _sign(dy) * d))
        path.append((x1, y1))

    # Drop repeated points and points in the middle of a straight run
    clean = []
    for p in path:
        if clean and p == clean[-1]:
            continue
        if len(clean) >= 2:
            (ax, ay), (bx, by) = clean[-2], clean[-1]
            if _sign(bx - ax) == _sign(p[0] - bx) and _sign(by - ay) == _sign(p[1] - by) \
                    and (bx - ax) * (p[1] - by) == (by - ay) * (p[0] - bx):
                clean[-1] = p
                continue
        clean.append(p)

    if chamfer > 0 and len(clean) >= 3:
        out = [clean[0]]
        for i in range(1, len(clean) - 1):
            (ax, ay), (bx, by), (cx, cy) = clean[i - 1], clean[i], clean[i + 1]
            u_in, u_out = (_sign(bx - ax), _sign(by - ay)), (_sign(cx - bx), _sign(cy - by))
            right_angle = (0 in u_in) and (0 in u_out) and u_in[0] * u_out[0] + u_in[1] * u_out[1] == 0
            if right_angle:
                legs = (abs(bx - ax) + abs(by - ay), abs(cx - bx) + abs(cy - by))
                c = min(chamfer, legs[0] // 2, legs[1] // 2)
                if c > 0:
                    out += [(bx - u_in[0] * c, by - u_in[1] * c), (bx + u_out[0] * c, by + u_out[1] * c)]
                    continue
            out.append((bx, by))
        out.append(clean[-1])
        clean = out

    return [(a[0], a[1], b[0], b[1]) for a, b in zip(clean, clean[1:]) if a != b]
