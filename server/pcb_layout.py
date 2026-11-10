"""Layout data for get_layout, place_copper and get_unrouted_connections.

The bridge (AltiumScript/pcb_layout.pas) writes and reads one record per
line, pipe-separated, with coordinates as integers in internal units (10,000
per mil) relative to the board origin and angles in thousandths of a degree,
so no number depends on the Windows decimal separator. Agents see mils:

    get_layout                         place_copper
    K|des|x|y|rot|layer|locked|pattern|name x|y|rot|height|stroke|on|comment x|y|rot|height|stroke|on|
      name autoposition|comment autoposition
                                       components
    T|layer|net|width|x1|y1|x2|y2      tracks
    A|layer|net|width|cx|cy|r|a1|a2    arcs
    V|net|x|y|size|hole|low|high       vias
    F|layer|net|x1|y1|x2|y2|rot        fills
    G|layer|net|name|hatch|pourover|removedead|islands|islandarea|necks|neck|
      grid|track|arcapprox|pourindex|locked|obeycutouts|optimalvoidrotation, then its outline as
    S|L|x|y or S|A|x|y|cx|cy|r|a1|a2   (a vertex; A: the edge to the next one is an arc)
    R|layer|net, then S lines          copper regions
    U|layer, then S lines              polygon pour cutouts (a signal layer, or Multi Layer: all)
    O|x1|y1|x2|y2  the area read;  N|des  a designator found;  X|n  objects skipped;  B|board

The layout returned by get_layout is exactly what place_copper takes, so a
group's copper can be read and placed again, here or moved (see transform).
"""
import math

COORD_PER_MIL = 10000
AUTOPOSITION = ["manual", "top_left", "center_left", "bottom_left", "top_center", "center",
                "bottom_center", "top_right", "center_right", "bottom_right"]
HATCH = {0: "hatched_90", 1: "hatched_45", 2: "vertical", 3: "horizontal", 4: "none", 5: "solid"}
HATCH_CODE = {v: k for k, v in HATCH.items()}
POUR_OVER = {0: "none", 1: "same_net", 2: "same_net_polygons"}
# TShape of a pad's top layer
PAD_SHAPES = {1: "round", 2: "rectangle", 3: "octagon", 9: "rounded_rectangle"}
POUR_OVER_CODE = {v: k for k, v in POUR_OVER.items()}
# Coordinates beyond Altium's design space (about 100,000 mil) overflow the bridge
LIMIT_MILS = 100000


class LayoutError(ValueError):
    pass


def _mil(v):
    return round(int(v) / COORD_PER_MIL, 4)


def _deg(v):
    return round(int(v) / 1000, 3)


def _coord(v, what):
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise LayoutError(f"{what}: {v!r} is not a number")
    if not math.isfinite(f) or abs(f) > LIMIT_MILS:
        raise LayoutError(f"{what}: {v!r} is outside the design space (±{LIMIT_MILS} mil)")
    return int(round(f * COORD_PER_MIL))


def _angle(v, what):
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise LayoutError(f"{what}: {v!r} is not a number")
    if not math.isfinite(f):
        raise LayoutError(f"{what}: {v!r} is not a number")
    return int(round(f * 1000))


def _text(v, what):
    s = "" if v is None else str(v)
    if any(c in s for c in "|\r\n"):
        raise LayoutError(f"{what}: {s!r} may not contain '|' or a line break")
    return s


def _vertex(f):
    v = {"x": _mil(f[2]), "y": _mil(f[3])}
    if f[1] == "A":
        v["arc"] = {"center": [_mil(f[4]), _mil(f[5])], "radius": _mil(f[6]),
                    "start_angle": _deg(f[7]), "end_angle": _deg(f[8])}
    return v


def parse_layout(text):
    """The bridge's get_layout lines as a layout dict in mils."""
    out = {"area": None, "components": [], "tracks": [], "arcs": [], "vias": [], "fills": [],
           "regions": [], "cutouts": [], "pours": [], "pads": [], "skipped": 0, "board": "", "found": []}
    shape = None
    for line in text.splitlines():
        f = line.split("|")
        k = f[0]
        if k == "S" and shape is not None:
            shape.append(_vertex(f))
            continue
        shape = None
        if k == "O":
            out["area"] = [_mil(v) for v in f[1:5]]
        elif k == "N":
            out["found"].append(f[1])
        elif k == "K":
            comp = {"designator": f[1], "x": _mil(f[2]), "y": _mil(f[3]), "rotation": _deg(f[4]),
                    "layer": "bottom" if f[5] == "Bottom Layer" else "top", "locked": f[6] == "True", "footprint": f[7]}
            if len(f) >= 20:
                for name, at, auto in (("designator_text", 8, 20), ("comment_text", 14, 21)):
                    comp[name] = {"x": _mil(f[at]), "y": _mil(f[at + 1]), "rotation": _deg(f[at + 2]),
                                  "height": _mil(f[at + 3]), "stroke_width": _mil(f[at + 4]), "visible": f[at + 5] == "True"}
                    if len(f) > auto and f[auto].isdigit() and int(f[auto]) < len(AUTOPOSITION):
                        comp[name]["autoposition"] = AUTOPOSITION[int(f[auto])]
            out["components"].append(comp)
        elif k == "T":
            out["tracks"].append({"net": f[2], "layer": f[1], "width": _mil(f[3]),
                                  "start": [_mil(f[4]), _mil(f[5])], "end": [_mil(f[6]), _mil(f[7])]})
        elif k == "A":
            out["arcs"].append({"net": f[2], "layer": f[1], "width": _mil(f[3]), "center": [_mil(f[4]), _mil(f[5])],
                                "radius": _mil(f[6]), "start_angle": _deg(f[7]), "end_angle": _deg(f[8])})
        elif k == "V":
            out["vias"].append({"net": f[1], "x": _mil(f[2]), "y": _mil(f[3]), "diameter": _mil(f[4]),
                                "hole": _mil(f[5]), "start_layer": f[6], "end_layer": f[7]})
        elif k == "F":
            out["fills"].append({"net": f[2], "layer": f[1], "corner1": [_mil(f[3]), _mil(f[4])],
                                 "corner2": [_mil(f[5]), _mil(f[6])], "rotation": _deg(f[7])})
        elif k == "G":
            pour = {"name": f[3], "net": f[2], "layer": f[1], "hatch": HATCH.get(int(f[4]), f[4]),
                    "pour_over": POUR_OVER.get(int(f[5]), f[5]), "remove_dead_copper": f[6] == "True",
                    "remove_islands": f[7] == "True", "island_area_sq_mils": round(int(f[8]) / 1000, 3),
                    "remove_necks": f[9] == "True", "neck_width_mils": _mil(f[10]),
                    "grid": _mil(f[11]), "track_width": _mil(f[12]), "arc_approximation": _mil(f[13]),
                    "pour_index": int(f[14]), "locked": f[15] == "True",
                    "obey_cutouts": len(f) > 16 and f[16] == "True",
                    "optimal_void_rotation": len(f) > 17 and f[17] == "True", "outline": []}
            out["pours"].append(pour)
            shape = pour["outline"]
        elif k == "R":
            region = {"net": f[2], "layer": f[1], "outline": []}
            out["regions"].append(region)
            shape = region["outline"]
        elif k == "U":
            cut = {"layer": f[1], "outline": []}
            out["cutouts"].append(cut)
            shape = cut["outline"]
        elif k == "P" and len(f) >= 12:
            out["pads"].append({"designator": f[1], "pad": f[2], "x": _mil(f[3]), "y": _mil(f[4]), "layer": f[5],
                                "shape": PAD_SHAPES.get(int(f[6]), f[6]), "size": [_mil(f[7]), _mil(f[8])],
                                "rotation": _deg(f[9]), "hole": _mil(f[10]), "net": "|".join(f[11:])})
        elif k == "X":
            out["skipped"] = int(f[1])
        elif k == "B":
            out["board"] = "|".join(f[1:])
    return out


def transform_point(p, t):
    """Point p moved by transform t: rotate by t['rotation'] degrees about
    t['about'] (default the origin), mirror in x first when t['mirror'], then
    shift by t['dx'], t['dy']."""
    if not t:
        return [float(p[0]), float(p[1])]
    ax, ay = (t.get("about") or [0, 0])
    x, y = float(p[0]) - ax, float(p[1]) - ay
    if t.get("mirror"):
        x = -x
    r = math.radians(float(t.get("rotation", 0)))
    c, s = math.cos(r), math.sin(r)
    x, y = x * c - y * s, x * s + y * c
    return [round(x + ax + float(t.get("dx", 0)), 4), round(y + ay + float(t.get("dy", 0)), 4)]


def transform_angle(a, t):
    """An arc angle under transform t (mirroring reverses the direction, see _arc_angles)."""
    a = float(a)
    if not t:
        return a
    if t.get("mirror"):
        a = 180 - a
    return (a + float(t.get("rotation", 0))) % 360


def _arc_angles(start, end, t):
    """An arc's start and end angles after transform t. Arcs run counter-
    clockwise from start to end, so a mirrored arc swaps them."""
    a1, a2 = transform_angle(start, t), transform_angle(end, t)
    return (a2, a1) if t and t.get("mirror") else (a1, a2)


def _net(v, nets, what):
    n = _text(v or "", what)
    return nets.get(n, n) if nets else n


def _vertex_lines(outline, t, what, arcs_as_points=False):
    if not isinstance(outline, list) or len(outline) < 3:
        raise LayoutError(f"{what}: an outline needs at least three vertices")
    lines = []
    for i, v in enumerate(outline):
        w = f"{what} vertex {i + 1}"
        if isinstance(v, dict):
            x, y, arc = v.get("x"), v.get("y"), v.get("arc")
        elif isinstance(v, (list, tuple)) and len(v) >= 2:
            x, y, arc = v[0], v[1], None
        else:
            raise LayoutError(f"{w}: give [x, y] or {{'x', 'y', 'arc'}}")
        px, py = transform_point([_coord(x, w) / COORD_PER_MIL, _coord(y, w) / COORD_PER_MIL], t)
        if arc and not arcs_as_points:
            c = transform_point(arc["center"], t)
            a1, a2 = _arc_angles(arc["start_angle"], arc["end_angle"], t)
            lines.append(f"S|A|{_coord(px, w)}|{_coord(py, w)}|{_coord(c[0], w)}|{_coord(c[1], w)}|"
                         f"{_coord(arc['radius'], w)}|{_angle(a1, w)}|{_angle(a2, w)}")
        elif arc:
            # A region's outline takes straight edges: the arc as points every 5 degrees
            c = transform_point(arc["center"], t)
            a1, a2 = _arc_angles(arc["start_angle"], arc["end_angle"], t)
            sweep = (a2 - a1) % 360 or 360
            steps = max(2, int(sweep / 5))
            for k in range(steps):
                a = math.radians(a1 + sweep * k / steps)
                r = float(arc["radius"])
                lines.append(f"S|L|{_coord(c[0] + r * math.cos(a), w)}|{_coord(c[1] + r * math.sin(a), w)}")
        else:
            lines.append(f"S|L|{_coord(px, w)}|{_coord(py, w)}")
    return lines


def encode_copper(layout, transform=None, nets=None):
    """place_copper's spec lines for a layout dict (as get_layout returns it).
    Pours come last, sorted by pour_index, so they pour around the copper
    added before them and in their own order. transform moves everything
    (see transform_point); nets renames nets ({old: new})."""
    t = transform or None
    lines = []
    for i, tr in enumerate(layout.get("tracks") or []):
        w = f"track {i + 1}"
        a, b = transform_point(tr["start"], t), transform_point(tr["end"], t)
        lines.append(f"T|{_text(tr['layer'], w)}|{_net(tr.get('net'), nets, w)}|{_coord(tr['width'], w)}|"
                     f"{_coord(a[0], w)}|{_coord(a[1], w)}|{_coord(b[0], w)}|{_coord(b[1], w)}")
    for i, ar in enumerate(layout.get("arcs") or []):
        w = f"arc {i + 1}"
        c = transform_point(ar["center"], t)
        a1, a2 = _arc_angles(ar["start_angle"], ar["end_angle"], t)
        lines.append(f"A|{_text(ar['layer'], w)}|{_net(ar.get('net'), nets, w)}|{_coord(ar['width'], w)}|"
                     f"{_coord(c[0], w)}|{_coord(c[1], w)}|{_coord(ar['radius'], w)}|{_angle(a1, w)}|{_angle(a2, w)}")
    for i, v in enumerate(layout.get("vias") or []):
        w = f"via {i + 1}"
        p = transform_point([v["x"], v["y"]], t)
        lines.append(f"V|{_net(v.get('net'), nets, w)}|{_coord(p[0], w)}|{_coord(p[1], w)}|{_coord(v['diameter'], w)}|"
                     f"{_coord(v['hole'], w)}|{_text(v.get('start_layer') or 'Top Layer', w)}|"
                     f"{_text(v.get('end_layer') or 'Bottom Layer', w)}")
    for i, f in enumerate(layout.get("fills") or []):
        w = f"fill {i + 1}"
        if t and (t.get("rotation") or t.get("mirror")):
            raise LayoutError(f"{w}: fills can only be shifted, not rotated or mirrored")
        a, b = transform_point(f["corner1"], t), transform_point(f["corner2"], t)
        lines.append(f"F|{_text(f['layer'], w)}|{_net(f.get('net'), nets, w)}|{_coord(a[0], w)}|{_coord(a[1], w)}|"
                     f"{_coord(b[0], w)}|{_coord(b[1], w)}|{_angle(f.get('rotation', 0), w)}")
    for i, r in enumerate(layout.get("regions") or []):
        w = f"region {i + 1}"
        lines.append(f"R|{_text(r['layer'], w)}|{_net(r.get('net'), nets, w)}")
        lines.extend(_vertex_lines(r.get("outline"), t, w, arcs_as_points=True))
    for i, c in enumerate(layout.get("cutouts") or []):
        w = f"cutout {i + 1}"
        lines.append(f"U|{_text(c['layer'], w)}")
        lines.extend(_vertex_lines(c.get("outline"), t, w, arcs_as_points=True))
    pours = sorted(enumerate(layout.get("pours") or []), key=lambda ip: (ip[1].get("pour_index") or 0, ip[0]))
    for i, p in pours:
        w = f"pour {i + 1}"
        hatch = p.get("hatch", "solid")
        if hatch not in HATCH_CODE:
            raise LayoutError(f"{w}: hatch must be one of {', '.join(HATCH_CODE)}")
        over = p.get("pour_over", "same_net")
        if over not in POUR_OVER_CODE:
            raise LayoutError(f"{w}: pour_over must be one of {', '.join(POUR_OVER_CODE)}")
        try:
            isl = int(round(float(p.get("island_area_sq_mils", 2500)) * 1000))
        except (TypeError, ValueError):
            raise LayoutError(f"{w}: island_area_sq_mils must be a number")
        if not 0 <= isl < 2 ** 31:
            raise LayoutError(f"{w}: island_area_sq_mils is out of range")
        lines.append("|".join([
            "G", _text(p["layer"], w), _net(p.get("net"), nets, w), _text(p.get("name", ""), w), str(HATCH_CODE[hatch]),
            str(POUR_OVER_CODE[over]), "1" if p.get("remove_dead_copper", True) else "0",
            "1" if p.get("remove_islands") else "0", str(isl), "1" if p.get("remove_necks") else "0",
            str(_coord(p.get("neck_width_mils", 5), w)), str(_coord(p.get("grid") or 0, w)),
            str(_coord(p.get("track_width") or 0, w)),
            str(_coord(p.get("arc_approximation") or 0, w)), str(int(p.get("pour_index") or 0)),
            "1" if p.get("locked") else "0", "1" if p.get("obey_cutouts", True) else "0",
            "1" if p.get("optimal_void_rotation") else "0"]))
        lines.extend(_vertex_lines(p.get("outline"), t, w))
    return lines


def parse_unrouted(text):
    """The bridge's get_unrouted_connections lines: (pads, connections, board)."""
    pads, conns, board = [], [], ""
    for line in text.splitlines():
        f = line.split("|")
        if f[0] == "P":
            pads.append({"designator": f[1], "pad": f[2], "x": _mil(f[3]), "y": _mil(f[4]), "net": f[5]})
        elif f[0] == "C":
            conns.append({"net": f[1], "from": [_mil(f[2]), _mil(f[3])], "to": [_mil(f[4]), _mil(f[5])],
                          "from_layer": f[6], "to_layer": f[7]})
        elif f[0] == "B":
            board = "|".join(f[1:])
    return pads, conns, board


def label_connections(pads, conns, area=None, tolerance=0.5):
    """Name the pad at each end of each connection (when it is one of pads),
    its length, and whether both ends lie in area [x1, y1, x2, y2]."""
    at = {}
    for p in pads:
        at.setdefault((round(p["x"]), round(p["y"])), []).append(p)

    def pad_at(pt):
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for p in at.get((round(pt[0]) + dx, round(pt[1]) + dy), []):
                    if abs(p["x"] - pt[0]) <= tolerance and abs(p["y"] - pt[1]) <= tolerance:
                        return f"{p['designator']}-{p['pad']}"
        return None

    def inside(pt):
        return area is None or (area[0] <= pt[0] <= area[2] and area[1] <= pt[1] <= area[3])

    out = []
    for c in conns:
        out.append(dict(c, from_pad=pad_at(c["from"]), to_pad=pad_at(c["to"]),
                        length=round(math.dist(c["from"], c["to"]), 2),
                        inside=inside(c["from"]) and inside(c["to"])))
    return out


def text_spec(t, what):
    """place_components' field for a designator or comment text dict
    {x, y, rotation, height, stroke_width, visible} (get_layout's form)."""
    if t is None:
        return ""
    if not isinstance(t, dict):
        raise LayoutError(f"{what} must be an object {{x, y, rotation, height, stroke_width, visible}}")
    vals = []
    for k in ("x", "y", "rotation", "height", "stroke_width"):
        try:
            v = float(t[k])
        except KeyError:
            raise LayoutError(f"{what} needs {k}")
        except (TypeError, ValueError):
            raise LayoutError(f"{what}: {k} must be a number")
        if not math.isfinite(v) or abs(v) > LIMIT_MILS:
            raise LayoutError(f"{what}: {k} is out of range")
        vals.append(repr(round(v, 4)))
    vals.append("1" if t.get("visible", True) else "0")
    auto = t.get("autoposition", "manual")
    if auto not in AUTOPOSITION:
        raise LayoutError(f"{what}: autoposition must be one of {', '.join(AUTOPOSITION)}")
    vals.append(str(AUTOPOSITION.index(auto)))
    return ";".join(vals)
