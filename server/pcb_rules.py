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


def encode_values(kind: str, values: dict, partial: bool = False) -> Tuple[str, str]:
    """("key=value;..." for Altium, "") or ("", error). partial: only the
    values to change (edit_pcb_rule), so none is required."""
    spec = RULE_KINDS.get(kind)
    if spec is None:
        return "", f"unknown rule_type '{kind}'; one of: {', '.join(RULE_KINDS)}"
    values = dict(values or {})
    unknown = sorted(set(values) - set(spec))
    if unknown:
        return "", f"{kind} takes {', '.join(spec)}; not {', '.join(unknown)}"
    missing = [k for k, (_, _, req) in spec.items() if req and k not in values]
    if missing and not partial:
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
            if num not in (2, 4):
                return "", f"{name} is 2 or 4"
            out.append(f"{key}={int(num)}")
        else:
            out.append(f"{key}={int(round(num * COORD_PER_MIL))}")
    vals = {k.split("=")[0]: int(k.split("=")[1]) for k in out if not k.startswith("connect_style")}
    for lo, hi in (("min", "max"), ("min_diameter", "max_diameter"), ("min_hole", "max_hole")):
        if lo in vals and hi in vals and vals[lo] > vals[hi]:
            return "", f"{lo}_mils is larger than {hi}_mils"
    # Altium stores a preferred value outside the range, or a via hole as
    # large as the via, without complaint
    for lo, mid, hi in (("min", "preferred", "max"), ("min_diameter", "preferred_diameter", "max_diameter"),
                        ("min_hole", "preferred_hole", "max_hole")):
        if mid in vals and lo in vals and hi in vals and not vals[lo] <= vals[mid] <= vals[hi]:
            return "", f"{mid}_mils must be between {lo}_mils and {hi}_mils"
    for size in ("min", "preferred", "max"):
        hole, diameter = f"{size}_hole", f"{size}_diameter"
        if hole in vals and diameter in vals and vals[hole] >= vals[diameter]:
            return "", f"{hole}_mils must be smaller than {diameter}_mils"
    return ";".join(out), ""


def check_merged(kind: str, current: dict, changes: dict) -> str:
    """'' when an edit's values, merged with the rule's current ones (as
    pcb_rule_info reads them), pass create_pcb_rule's checks, else why not:
    an edit of one value must not leave min above max, or a via hole as
    large as the via."""
    spec = RULE_KINDS.get(kind, {})
    merged = {k: v for k, v in (current or {}).items() if k in spec}
    merged.update(changes or {})
    _, error = encode_values(kind, merged)
    if error:
        now = ", ".join(f"{k}={v}" for k, v in sorted((current or {}).items()) if k in spec and k not in changes)
        return f"{error} (with the rule's other values: {now})" if now else error
    return ""


def move_notes(result: dict, name: str) -> dict:
    """Notes for a reply of edit_pcb_rule or set_rule_priority that re-added
    rules to keep or set an order: how to undo the move, and a warning when
    a re-added rule came from the design (a schematic directive or a room),
    or its unique id changed, which Update PCB uses to match rules."""
    notes = {}
    if not result.get("rules_moved"):
        return notes
    before = result.get("priority_before")
    if before is not None and before != result.get("priority"):
        notes["undo"] = (f"Altium's Undo may not restore the old order (re-adding a rule renumbers "
                         f"it). set_rule_priority(name={result.get('name', name)!r}, "
                         f"priority={before}) puts it back.")
    design = result.get("readded_from_design") or []
    changed = result.get("unique_id_changed") or []
    if design or changed:
        parts = []
        if design:
            parts.append(f"{', '.join(design)} came from the design (directive or room) and "
                         f"were taken off and added again")
        if changed:
            parts.append(f"{', '.join(changed)} got a new unique id")
        notes["move_warning"] = ("; ".join(parts) + ". Check that the next Update PCB does not add "
                                 "them a second time.")
    return notes


def scope_or_all(scope) -> str:
    """A rule scope as sent to Altium: an empty or blank one is "All"."""
    return (scope or "").strip() or "All"


# No board has this many rules of a kind, so a priority past it means "last"
# just as well; the bridge reads it with StrToInt, which overflows past 2^31
PRIORITY_MAX = 1_000_000


def priority_value(priority) -> Tuple[int, str]:
    """set_rule_priority's priority as sent to the bridge, clamped to
    PRIORITY_MAX (beyond the last rule moves it last anyway), or (0, error)."""
    # bool is an int in Python: True would pass as priority 1
    if isinstance(priority, bool) or not isinstance(priority, int) or priority < 1:
        return 0, "priority is a whole number from 1"
    return min(priority, PRIORITY_MAX), ""
