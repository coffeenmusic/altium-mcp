"""Objects in an area, clearance between two objects, and dragging copper
with its connections: the Python side of the 'geometry' bridge command
(pcb_utils.pas).

Pure Python, unit-tested in tests/test_pcb_geometry.py. Altium sends each
object as a '|' string of integers (internal units, angles in thousandths of
a degree). Coordinates shown to the caller are relative to the board origin,
in mils or mm, like the other PCB tools. Every object also gets a "ref": an
opaque string in absolute internal units that clearance and drag_copper take
to find that object again.
"""

from typing import List, Optional

COORD_PER = {"mil": 10000.0, "mm": 10000.0 / 0.0254}
KINDS = ("track", "arc", "via", "pad", "fill", "region", "polygon", "text", "component")
# Pad shapes as Altium numbers them (TShape)
PAD_SHAPES = {1: "round", 2: "rectangle", 3: "octagonal", 9: "rounded_rectangle"}


def unit_factor(unit: str) -> float:
    try:
        return COORD_PER[unit.lower()]
    except KeyError:
        raise ValueError("unit must be 'mil' or 'mm'")


def to_coord(value: float, unit: str) -> int:
    return int(round(float(value) * unit_factor(unit)))


def _ints(fields: List[str], n: int) -> List[int]:
    return [int(f) for f in fields[:n]]


def parse_object(record: str, origin=(0, 0), unit: str = "mil") -> Optional[dict]:
    """One record as a dict with board-relative coordinates in `unit`."""
    parts = record.split("|")
    if len(parts) < 4 or parts[0] not in KINDS:
        return None
    kind, layer, net, owner, rest = parts[0], parts[1], parts[2], parts[3], parts[4:]
    k = unit_factor(unit)
    ox, oy = origin

    def x(v):
        return round((int(v) - ox) / k, 4)

    def y(v):
        return round((int(v) - oy) / k, 4)

    def d(v):
        return round(int(v) / k, 4)

    obj = {"kind": kind, "layer": layer}
    if net:
        obj["net"] = net
    if owner:
        obj["component"] = owner
    if kind == "track":
        x1, y1, x2, y2, w = _ints(rest, 5)
        obj.update({"x1": x(x1), "y1": y(y1), "x2": x(x2), "y2": y(y2), "width": d(w)})
        if rest[5:6] == ["1"]:
            obj["in_polygon"] = True
        obj["ref"] = f"track|{layer}|{x1}|{y1}|{x2}|{y2}"
    elif kind == "arc":
        cx, cy, r, a1, a2, w = _ints(rest, 6)
        obj.update({"cx": x(cx), "cy": y(cy), "radius": d(r), "start_angle": a1 / 1000, "end_angle": a2 / 1000,
                    "width": d(w)})
        if rest[6:7] == ["1"]:
            obj["in_polygon"] = True
        obj["ref"] = f"arc|{layer}|{cx}|{cy}|{r}"
    elif kind == "via":
        vx, vy, size, hole = _ints(rest, 4)
        low, _, high = layer.partition(":")
        obj.update({"layer": None, "low_layer": low, "high_layer": high, "x": x(vx), "y": y(vy),
                    "size": d(size), "hole": d(hole), "ref": f"via|{vx}|{vy}"})
        del obj["layer"]
    elif kind == "pad":
        px, py, xs, ys, shape, hole, rot = _ints(rest, 7)
        name = "|".join(rest[7:])
        obj.update({"name": name, "x": x(px), "y": y(py), "x_size": d(xs), "y_size": d(ys),
                    "shape": PAD_SHAPES.get(shape, shape), "hole": d(hole), "rotation": rot / 1000})
        obj["ref"] = f"pad|{owner}|{name}" if owner else f"pad|{px}|{py}"
    elif kind == "fill":
        x1, y1, x2, y2, rot = _ints(rest, 5)
        obj.update({"x1": x(x1), "y1": y(y1), "x2": x(x2), "y2": y(y2), "rotation": rot / 1000,
                    "ref": f"fill|{layer}|{x1}|{y1}|{x2}|{y2}"})
    elif kind in ("region", "polygon"):
        left, bottom, right, top = _ints(rest, 4)
        obj["bounds"] = [x(left), y(bottom), x(right), y(top)]
        if kind == "region":
            if rest[4:5] == ["1"]:
                obj["in_polygon"] = True
            obj["ref"] = f"region|{layer}|{left}|{bottom}|{right}|{top}"
    elif kind == "text":
        tx, ty, size, rot = _ints(rest, 4)
        obj.update({"x": x(tx), "y": y(ty), "height": d(size), "rotation": rot / 1000, "text": "|".join(rest[4:])})
    elif kind == "component":
        cx, cy, rot, left, bottom, right, top = _ints(rest, 7)
        obj.update({"designator": owner, "x": x(cx), "y": y(cy), "rotation": rot / 1000,
                    "bounds": [x(left), y(bottom), x(right), y(top)], "footprint": "|".join(rest[7:]),
                    "ref": f"component|{owner}"})
        obj.pop("component", None)
    return obj


def via_layers(low: str, high: str, stack: Optional[List[str]] = None) -> List[str]:
    """The copper layers a via spans, lower-cased: every layer of the stack
    (copper layer names top to bottom, as Altium sends them) from one end to
    the other, so a blind via Top Layer:Mid Layer 2 is on Mid Layer 1 too.
    Without a stack, or with an end not in it, only its two end layers."""
    low, high = low.lower(), high.lower()
    names = [n.lower() for n in (stack or [])]
    if low in names and high in names:
        i, j = sorted((names.index(low), names.index(high)))
        return names[i:j + 1]
    return [low, high]


def _layer_match(obj: dict, layers: List[str], stack: Optional[List[str]] = None) -> bool:
    wanted = {l.lower() for l in layers}
    if obj["kind"] == "via":
        ends = {obj["low_layer"].lower(), obj["high_layer"].lower()}
        through = ends == {"top layer", "bottom layer"}
        return through or bool(set(via_layers(obj["low_layer"], obj["high_layer"], stack)) & wanted)
    layer = (obj.get("layer") or "").lower()
    return layer in wanted or layer == "multi-layer"


def select(records: List[str], origin, unit: str, layers: Optional[List[str]] = None,
           nets: Optional[List[str]] = None, stack: Optional[List[str]] = None) -> List[dict]:
    out = []
    want_nets = {n.lower() for n in nets} if nets else None
    for rec in records:
        obj = parse_object(rec, origin, unit)
        if obj is None:
            continue
        if layers and not _layer_match(obj, layers, stack):
            continue
        if want_nets is not None and (obj.get("net") or "").lower() not in want_nets:
            continue
        out.append(obj)
    return out


# How many objects Altium sends at most when a layer or net filter is set:
# the filters run here, after Altium's limit, so it sends this many and the
# caller's limit is applied to what passes
SCAN_CAP = 20000


def scan_limit(limit: int, layers=None, nets=None) -> int:
    """The limit to give Altium for objects_in_area's `limit`."""
    limit = max(1, int(limit))
    return max(limit, SCAN_CAP) if (layers or nets) else limit


def area_reply(result: dict, unit: str, limit: int, layers=None, nets=None) -> dict:
    """objects_in_area's reply from the 'area' command's result: filtered by
    layer and net first, then cut to limit. total counts the objects that
    pass the filters; None when Altium stopped at its cap before the filters
    ran, so the count is not known."""
    records = result.get("objects", [])
    origin = (result.get("origin_x", 0), result.get("origin_y", 0))
    matched = select(records, origin, unit, layers, nets, result.get("copper_layers"))
    altium_total = result.get("total", len(records))
    if not (layers or nets):
        total = altium_total
    elif altium_total <= len(records):
        total = len(matched)
    else:
        total = None
    objects = matched[:max(1, int(limit))]
    reply = {"board_path": result.get("board_path"), "unit": unit, "total": total, "returned": len(objects),
             "truncated": total is None or len(objects) < total, "objects": objects}
    if total is None:
        reply["note"] = (f"The box holds {altium_total} objects and Altium sent the first {len(records)}; the "
                         "layer and net filters ran on those, so matches may be missing. Use a smaller box.")
    elif reply["truncated"]:
        reply["note"] = f"Only the first {len(objects)} of {total}: raise limit or use a smaller box."
    return reply


def normalize_ref(ref: str) -> str:
    """A ref from objects_in_area as it is, or a pad written 'U1-3'."""
    ref = (ref or "").strip()
    if not ref:
        raise ValueError("empty object reference")
    if "|" in ref:
        kind = ref.split("|", 1)[0]
        if kind not in ("track", "arc", "via", "pad", "fill", "region", "component"):
            raise ValueError(f"cannot find a '{kind}' by reference; use a ref from objects_in_area")
        return ref
    if "-" in ref:
        designator, _, pad = ref.rpartition("-")
        if designator and pad:
            return f"pad|{designator}|{pad}"
    raise ValueError(f"'{ref}' is not a ref from objects_in_area or a pad like 'U1-3'")


def describe_clearance(result: dict, unit: str) -> dict:
    k = unit_factor(unit)
    out = {"distance": round(result["distance"] / k, 4), "unit": unit}
    if "rule" in result:
        out.update({"rule": result["rule"], "rule_gap": round(result["rule_gap"] / k, 4),
                    "violates_rule": bool(result.get("rule_violated"))})
    else:
        out["rule"] = None
    return out
