"""Checking and encoding create_pcb_rule's values before they reach Altium.

Each rule kind takes its own values. Distances are given in mils and sent
to Altium in internal units (10000 per mil), so the Pascal side never parses
a decimal number.
"""
from typing import Dict, Tuple

COORD_PER_MIL = 10000

# kind -> {value name: (key sent to Altium, "mils" | "count" | "word", required)}
RULE_KINDS: Dict[str, Dict[str, tuple]] = {
    "clearance": {"gap_mils": ("gap", "mils", True)},
    "width": {"min_mils": ("min", "mils", True), "max_mils": ("max", "mils", True),
              "preferred_mils": ("preferred", "mils", True)},
    "hole_size": {"min_mils": ("min", "mils", True), "max_mils": ("max", "mils", True)},
    "routing_via": {"min_diameter_mils": ("min_diameter", "mils", True),
                    "max_diameter_mils": ("max_diameter", "mils", True),
                    "preferred_diameter_mils": ("preferred_diameter", "mils", True),
                    "min_hole_mils": ("min_hole", "mils", True),
                    "max_hole_mils": ("max_hole", "mils", True),
                    "preferred_hole_mils": ("preferred_hole", "mils", True)},
    "solder_mask_expansion": {"expansion_mils": ("expansion", "mils", True)},
    "paste_mask_expansion": {"expansion_mils": ("expansion", "mils", True)},
    "polygon_connect": {"connect_style": ("connect_style", "word", True),
                        "conductor_width_mils": ("conductor_width", "mils", False),
                        "air_gap_mils": ("air_gap", "mils", False),
                        "entries": ("entries", "count", False)},
}

CONNECT_STYLES = ("relief", "direct", "none")
# Values that may be negative: a mask expansion below zero shrinks the opening
SIGNED = {"expansion"}


def encode_values(kind: str, values: dict) -> Tuple[str, str]:
    """("key=value;..." for Altium, "") or ("", error)."""
    spec = RULE_KINDS.get(kind)
    if spec is None:
        return "", f"unknown rule_type '{kind}'; one of: {', '.join(RULE_KINDS)}"
    values = dict(values or {})
    unknown = sorted(set(values) - set(spec))
    if unknown:
        return "", f"{kind} takes {', '.join(spec)}; not {', '.join(unknown)}"
    missing = [k for k, (_, _, req) in spec.items() if req and k not in values]
    if missing:
        return "", f"{kind} needs {', '.join(missing)}"
    out = []
    for name, (key, unit, _) in spec.items():
        if name not in values:
            continue
        v = values[name]
        if unit == "word":
            v = str(v).lower()
            if v not in CONNECT_STYLES:
                return "", f"{name} is one of {', '.join(CONNECT_STYLES)}"
            out.append(f"{key}={v}")
            continue
        try:
            num = float(v)
        except (TypeError, ValueError):
            return "", f"{name} must be a number"
        if num < 0 and key not in SIGNED:
            return "", f"{name} cannot be negative"
        if unit == "count":
            if num != int(num) or num < 1:
                return "", f"{name} must be a whole number of at least 1"
            out.append(f"{key}={int(num)}")
        else:
            out.append(f"{key}={int(round(num * COORD_PER_MIL))}")
    vals = {k.split("=")[0]: int(k.split("=")[1]) for k in out if not k.startswith("connect_style")}
    for lo, hi in (("min", "max"), ("min_diameter", "max_diameter"), ("min_hole", "max_hole")):
        if lo in vals and hi in vals and vals[lo] > vals[hi]:
            return "", f"{lo} is larger than {hi}"
    return ";".join(out), ""
