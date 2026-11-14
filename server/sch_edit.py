"""Checking and encoding edit_schematic's operations for Altium.

Each operation becomes one pipe-delimited line of the spec file the bridge
reads (sch_edit_spec.txt). Coordinates are given in mil or mm, absolute on
the sheet with Altium's origin at the bottom left, and sent in internal
units, so a metric sheet keeps its exact positions.

A point can also name a pin, "U20.VCC" or "U20.3": a wire's points, or "at"
in place of x and y. The pins are read live from the sheet first
(get_schematic_pins) and resolve_pins puts their connection points in.
Because the pins are read once, before any operation runs, a reference to a
part that an earlier operation in the same call moves, places or deletes is
refused: its pins are no longer where they were read.
"""
import copy
import re
from typing import Dict, List, Tuple

COORD_PER = {"mil": 10000, "mm": 10000 / 0.0254}
ROTATIONS = {0: 0, 90: 1, 180: 2, 270: 3}
POWER_STYLES = {"circle": 0, "arrow": 1, "bar": 2, "wave": 3, "power_ground": 4,
                "signal_ground": 5, "earth": 6}
DELETE_KINDS = ("wire", "net_label", "power_port", "junction", "no_erc", "port", "note")
PORT_IO = {"unspecified": 0, "output": 1, "input": 2, "bidirectional": 3}
# Altium's TPortArrowStyle order; ports and sheet entries share it
PORT_STYLES = {"none": 0, "left": 1, "right": 2, "left_right": 3,
               "none_vertical": 4, "top": 5, "bottom": 6, "top_bottom": 7}
# Operations whose x, y may be given as "at": a pin reference
AT_OPS = ("add_net_label", "add_power_port", "add_junction", "add_no_erc", "add_port")
# Operations that move a part's pins, so a later reference to them is stale
PIN_MOVING_OPS = ("move_component", "delete_component", "place_component")

# op -> (required keys, optional keys)
OPS = {
    "move_component": (("designator",), ("part", "x", "y", "rotation", "mirror")),
    "delete_component": (("designator",), ("part",)),
    "set_parameter": (("designator", "name", "value"), ("part", "visible")),
    "move_text": (("designator", "name"), ("part", "x", "y", "justification", "orientation", "visible")),
    "add_wire": (("points",), ()),
    "add_net_label": (("x", "y", "text"), ("orientation", "justification")),
    "add_power_port": (("x", "y", "text"), ("style", "orientation", "show_net_name")),
    "add_junction": (("x", "y"), ()),
    "add_port": (("x", "y", "name"), ("io_type", "style", "width")),
    "add_no_erc": (("x", "y"), ()),
    "delete_at": (("kind", "x", "y"), ("text",)),
    "place_component": (("library", "symbol", "designator", "x", "y"),
                        ("rotation", "mirror", "comment",
                         # filled in from a DbLib row by dblib.place_operation
                         "db_library", "db_table", "db_key", "db_description", "db_parameters", "db_footprints")),
}


class OpError(ValueError):
    pass


def _text(v, what) -> str:
    s = str(v)
    if any(c in s for c in "|\r\n"):
        raise OpError(f"{what} cannot contain '|' or a line break")
    return s


def _flag(v) -> str:
    """-1 keeps the current value, 0/1 sets it."""
    return "-1" if v is None else ("1" if v else "0")


def _int_in(v, lo, hi, what) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise OpError(f"{what} must be a whole number")
    if not lo <= n <= hi or n != float(v):
        raise OpError(f"{what} must be {lo}..{hi}")
    return n


def _rotation(v) -> str:
    if v is None:
        return "-1"
    try:
        return str(ROTATIONS[int(v) % 360])
    except (TypeError, ValueError, KeyError):
        raise OpError("rotation is 0, 90, 180 or 270 degrees")


def _is_ref(v) -> bool:
    return isinstance(v, str) and "." in v.strip(".")


def split_part(des: str) -> Tuple[str, int]:
    """("U1", 1) for "U1A", a part of a multi-part component; (des, 0) when
    there is no part letter."""
    m = re.fullmatch(r"(.*\d)([A-Z])", des.upper())
    return (m.group(1), ord(m.group(2)) - 64) if m else (des.upper(), 0)


def _ref_designator(ref: str) -> str:
    return ref.split(".", 1)[0].strip().upper()


def pin_designators(operations: list) -> List[str]:
    """The designators named by pin references in the operations. "U1A"
    also asks for "U1": Altium gives every part of U1 the designator U1."""
    refs = []
    for op in operations or []:
        if not isinstance(op, dict):
            continue
        if op.get("op") == "add_wire" and isinstance(op.get("points"), list):
            refs += [p for p in op["points"] if _is_ref(p)]
        if op.get("op") in AT_OPS and _is_ref(op.get("at")):
            refs.append(op["at"])
    names = set()
    for r in refs:
        des = _ref_designator(r)
        names |= {des, split_part(des)[0]}
    return sorted(names)


def _part_name(p: dict) -> str:
    """U1A for a pin of part 1 of U1."""
    part = int(p.get("part", 0) or 0)
    return str(p.get("designator", "")) + (chr(64 + part) if 0 < part <= 26 else "")


def find_pin(ref: str, pins: List[dict]) -> dict:
    """The pin "U20.VCC" or "U20.3" names: by pin number first, then by name.
    A name several pins share (power pins) must be given by number. "U1A.3"
    is pin 3 of part A of a multi-part U1. A pin common to every part is
    drawn on each placed part, so it needs the part letter."""
    des, _, pin = ref.partition(".")
    des, pin = des.strip().upper(), pin.strip()
    mine = [p for p in pins if str(p.get("designator", "")).upper() == des]
    if not mine:
        base, part = split_part(des)
        if part:
            mine = [p for p in pins if str(p.get("designator", "")).upper() == base
                    and int(p.get("part", 0) or 0) == part]
            if not mine and any(str(p.get("designator", "")).upper() == base for p in pins):
                raise OpError(f"{ref}: part {des[-1]} of {base} is not on the sheet")
    if not mine:
        # In case Altium gives a placed part its letter: "U1.3" on U1A
        mine = [p for p in pins if split_part(str(p.get("designator", "")))[0] == des]
    if not mine:
        raise OpError(f"{ref}: no component {des} on the sheet")
    hit = [p for p in mine if str(p.get("pin", "")).upper() == pin.upper()]
    if not hit:
        hit = [p for p in mine if str(p.get("name", "")).upper() == pin.upper()]
    if len(hit) == 1:
        return hit[0]
    if not hit:
        raise OpError(f"{ref}: {des} has no pin {pin!r}; its pins are "
                      + ", ".join(f"{p['pin']} ({p['name']})" for p in mine))
    parts = sorted({_part_name(p) for p in hit})
    if len({str(p.get("pin", "")) for p in hit}) == 1 and len(parts) == len(hit):
        raise OpError(f"{ref}: pin {hit[0]['pin']} is common to every part of {des} and is drawn "
                      f"on each placed part ({', '.join(parts)}); name the part, e.g. "
                      f"{parts[0]}.{hit[0]['pin']}")
    raise OpError(f"{ref}: {len(hit)} pins of {des} are named {pin!r} ("
                  + ", ".join(str(p["pin"]) for p in hit) + "); give the pin number"
                  + (f" and the part ({', '.join(parts)})" if len(parts) > 1 else ""))


def resolve_pins(operations: list, pins: List[dict], unit: str = "mil") -> Tuple[list, str]:
    """The operations with pin references replaced by the pins' connection
    points in unit; pins come from get_schematic_pins in internal units.
    The pins were read before any operation runs, so a reference to a part
    that an earlier operation moves, places or deletes is an error."""
    scale = COORD_PER.get(unit, COORD_PER["mil"])
    changed = set()

    def xy(ref):
        des = _ref_designator(ref)
        stale = {des, split_part(des)[0]} & changed
        if stale:
            raise OpError(f"{ref}: an earlier operation moves, places or deletes {stale.pop()}, and "
                          "the pins are read before any operation runs; refer to its pins in a "
                          "second edit_schematic call")
        p = find_pin(ref, pins)
        return [round(p["x"] / scale, 6), round(p["y"] / scale, 6)]

    out = []
    for i, op in enumerate(operations or []):
        try:
            op = copy.deepcopy(op)
            if isinstance(op, dict) and op.get("op") == "add_wire" and isinstance(op.get("points"), list):
                op["points"] = [xy(p) if _is_ref(p) else p for p in op["points"]]
            if isinstance(op, dict) and op.get("op") in AT_OPS and "at" in op:
                if not _is_ref(op["at"]):
                    raise OpError("at is a pin reference, e.g. \"U20.VCC\" or \"U20.3\"")
                if "x" in op or "y" in op:
                    raise OpError("give at, or x and y, not both")
                op["x"], op["y"] = xy(op.pop("at"))
            if isinstance(op, dict) and op.get("op") in PIN_MOVING_OPS and op.get("designator") is not None:
                changed.add(str(op["designator"]).strip().upper())
            out.append(op)
        except OpError as e:
            return [], f"operation {i + 1}: {e}"
    return out, ""


def encode_operations(operations: list, unit: str = "mil") -> Tuple[List[str], str]:
    """(spec lines, "") or ([], "operation N: what is wrong")."""
    if unit not in COORD_PER:
        return [], "unit is mil or mm"
    scale = COORD_PER[unit]

    def c(v, what="coordinate"):
        try:
            return str(int(round(float(v) * scale)))
        except (TypeError, ValueError):
            raise OpError(f"{what} must be a number")

    lines = []
    for i, op in enumerate(operations or []):
        try:
            if not isinstance(op, dict) or op.get("op") not in OPS:
                raise OpError(f"'op' must be one of {', '.join(OPS)}")
            kind = op["op"]
            required, optional = OPS[kind]
            if "at" in op:
                raise OpError("'at' names a pin; it is replaced before encoding (pins not resolved)")
            missing = [k for k in required if op.get(k) is None]
            if missing:
                raise OpError(f"{kind} needs {', '.join(missing)}")
            unknown = sorted(set(op) - set(required) - set(optional) - {"op"})
            if unknown:
                raise OpError(f"{kind} does not take {', '.join(unknown)}")
            part = str(_int_in(op.get("part", 0), 0, 255, "part"))

            if kind == "move_component":
                if ("x" in op) != ("y" in op):
                    raise OpError("give both x and y, or neither")
                xy = [c(op["x"]), c(op["y"])] if "x" in op else ["", ""]
                lines.append("|".join(["MOVE", _text(op["designator"], "designator"), part] + xy +
                                      [_rotation(op.get("rotation")), _flag(op.get("mirror"))]))
            elif kind == "delete_component":
                lines.append("|".join(["DELETE", _text(op["designator"], "designator"), part]))
            elif kind == "set_parameter":
                lines.append("|".join(["PARAM", _text(op["designator"], "designator"), part,
                                       _text(op["name"], "name"), _text(op["value"], "value"),
                                       _flag(op.get("visible"))]))
            elif kind == "move_text":
                if ("x" in op) != ("y" in op):
                    raise OpError("give both x and y, or neither")
                xy = [c(op["x"]), c(op["y"])] if "x" in op else ["", ""]
                just = "-1" if op.get("justification") is None else str(
                    _int_in(op["justification"], 0, 8, "justification"))
                orient = "-1" if op.get("orientation") is None else str(
                    _int_in(op["orientation"], 0, 3, "orientation"))
                lines.append("|".join(["TEXT", _text(op["designator"], "designator"), part,
                                       _text(op["name"], "name")] + xy + [just, orient, _flag(op.get("visible"))]))
            elif kind == "add_wire":
                pts = op["points"]
                if not isinstance(pts, list) or len(pts) < 2 or any(len(p) != 2 for p in pts):
                    raise OpError("points is a list of at least two [x, y]")
                lines.append("|".join(["WIRE"] + [c(v) for p in pts for v in p]))
            elif kind == "add_net_label":
                lines.append("|".join(["LABEL", c(op["x"]), c(op["y"]),
                                       str(_int_in(op.get("orientation", 0), 0, 3, "orientation")),
                                       str(_int_in(op.get("justification", 0), 0, 8, "justification")),
                                       _text(op["text"], "text")]))
            elif kind == "add_power_port":
                style = op.get("style", "bar")
                style = POWER_STYLES.get(style, style) if isinstance(style, str) else style
                style = _int_in(style, 0, 10, f"style (or one of {', '.join(POWER_STYLES)})")
                lines.append("|".join(["POWER", c(op["x"]), c(op["y"]),
                                       str(_int_in(op.get("orientation", 1), 0, 3, "orientation")), str(style),
                                       "1" if op.get("show_net_name", True) else "0", _text(op["text"], "text")]))
            elif kind == "add_port":
                io = op.get("io_type", "unspecified")
                io = PORT_IO.get(io, io) if isinstance(io, str) else io
                io = _int_in(io, 0, 3, f"io_type (or one of {', '.join(PORT_IO)})")
                style = op.get("style", "right")
                style = PORT_STYLES.get(style, style) if isinstance(style, str) else style
                style = _int_in(style, 0, 7, f"style (or one of {', '.join(PORT_STYLES)})")
                width = op.get("width", 0) or 0
                if not isinstance(width, (int, float)) or width < 0:
                    raise OpError("width must be a positive number (0: Altium sizes it to the name)")
                lines.append("|".join(["PORT", c(op["x"]), c(op["y"]), str(io), str(style),
                                       c(width), _text(op["name"], "name")]))
            elif kind in ("add_junction", "add_no_erc"):
                lines.append("|".join(["JUNCTION" if kind == "add_junction" else "NOERC", c(op["x"]), c(op["y"])]))
            elif kind == "delete_at":
                if op["kind"] not in DELETE_KINDS:
                    raise OpError(f"kind is one of {', '.join(DELETE_KINDS)}")
                lines.append("|".join(["DELETE_AT", op["kind"], c(op["x"]), c(op["y"]),
                                       _text(op.get("text", ""), "text")]))
            elif kind == "place_component":
                lib = _text(op["library"], "library")
                if not lib.lower().endswith(".schlib"):
                    raise OpError("library must be the full path of a .SchLib file")
                fields = ["PLACE", _text(op["designator"], "designator"), lib,
                          _text(op["symbol"], "symbol"), c(op["x"]), c(op["y"]),
                          _rotation(op.get("rotation", 0)), "1" if op.get("mirror") else "0",
                          _text(op.get("comment", ""), "comment")]
                if op.get("db_library"):
                    # DB|library|table|key|description|n|name|visible|value...|m|footprint...
                    params = op.get("db_parameters") or []
                    fields += ["DB", _text(op["db_library"], "db_library"), _text(op["db_table"], "db_table"),
                               _text(op["db_key"], "db_key"), _text(op.get("db_description", ""), "description"),
                               str(len(params))]
                    for name, value, visible in params:
                        fields += [_text(name, "parameter name"), "1" if visible else "0",
                                   _text(value, f"value of {name}")]
                    footprints = op.get("db_footprints") or []
                    fields += [str(len(footprints))] + [_text(f, "footprint") for f in footprints]
                lines.append("|".join(fields))
        except OpError as e:
            return [], f"operation {i + 1}: {e}"
    if not lines:
        return [], "operations is empty"
    return lines, ""
