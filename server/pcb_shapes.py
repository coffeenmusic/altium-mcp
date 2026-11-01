"""Checking and encoding the outlines of place_polygon_pour and place_cutout.

An outline is a list of [x, y] corners in mils relative to the board origin,
joined by straight edges and closed back to the first corner. It goes to
Altium as 'x|y' records in internal units (10000 per mil).
"""
from typing import List, Optional, Tuple

COORD_PER_MIL = 10000


def _cross(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _segments_cross(p1, p2, p3, p4) -> bool:
    """Do segments p1p2 and p3p4 intersect (touching counts)?"""
    d1, d2 = _cross(p3, p4, p1), _cross(p3, p4, p2)
    d3, d4 = _cross(p1, p2, p3), _cross(p1, p2, p4)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) and 0 not in (d1, d2, d3, d4):
        return True

    def on(p, q, r):  # q on segment pr, given collinear
        return min(p[0], r[0]) <= q[0] <= max(p[0], r[0]) and min(p[1], r[1]) <= q[1] <= max(p[1], r[1])

    return ((d1 == 0 and on(p3, p1, p4)) or (d2 == 0 and on(p3, p2, p4)) or
            (d3 == 0 and on(p1, p3, p2)) or (d4 == 0 and on(p1, p4, p2)))


def outline_points(outline) -> Tuple[Optional[List[Tuple[int, int]]], str]:
    """The corners in internal units, or (None, error). Rejects fewer than
    three corners, repeated consecutive corners, a closing corner equal to
    the first (it closes by itself), no area, and edges that cross."""
    pts = []
    try:
        for p in outline or []:
            if len(p) != 2:
                raise ValueError
            pts.append((int(round(float(p[0]) * COORD_PER_MIL)), int(round(float(p[1]) * COORD_PER_MIL))))
    except (TypeError, ValueError):
        return None, "outline must be a list of [x, y] corners in mils"
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts.pop()  # closed explicitly: drop the repeat
    if len(pts) < 3:
        return None, "outline needs at least three corners"
    n = len(pts)
    if any(pts[i] == pts[(i + 1) % n] for i in range(n)):
        return None, "outline repeats a corner"
    area2 = sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n))
    if area2 == 0:
        return None, "outline encloses no area"
    for i in range(n):
        a1, a2 = pts[i], pts[(i + 1) % n]
        for j in range(i + 1, n):
            if j == i or (j + 1) % n == i or j == (i + 1) % n:
                continue  # neighbouring edges share a corner
            if _segments_cross(a1, a2, pts[j], pts[(j + 1) % n]):
                return None, f"outline edges {i + 1} and {j + 1} cross; give the corners in order around the shape"
    return pts, ""


def encode(points) -> str:
    return ";".join(f"{x}|{y}" for x, y in points)
