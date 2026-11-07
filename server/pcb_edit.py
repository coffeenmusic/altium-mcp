"""Checking and encoding delete_pcb_objects and modify_pcb_objects requests.

Objects are picked by kind, net, layer and area; every filter given must
match. Area is [x1, y1, x2, y2] in mils from the board origin, and an object
matches when it lies inside it. Values go to Altium in internal units (10000
per mil), so the Pascal side never parses a decimal.
"""
from typing import Optional, Tuple

COORD_PER_MIL = 10000
KINDS = ("track", "arc", "via", "polygon", "region", "fill")


def _coord(v) -> int:
    return int(round(float(v) * COORD_PER_MIL))


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def filters(kinds, net: str, layer: str, area) -> Tuple[Optional[dict], str]:
    """The request's filter fields, or None and what is wrong."""
    if kinds is None:
        kinds = list(KINDS)
    if isinstance(kinds, str):
        kinds = [kinds]
    kinds = [str(k).strip().lower() for k in kinds]
    unknown = [k for k in kinds if k not in KINDS]
    if unknown or not kinds:
        return None, f"kinds are some of {', '.join(KINDS)}"
    for name, v in (("net", net), ("layer", layer)):
        if any(c in (v or "") for c in '|"\r\n'):
            return None, f"{name} cannot contain | \" or a line break"
    encoded_area = ""
    if area is not None:
        if not isinstance(area, (list, tuple)) or len(area) != 4 or not all(_number(v) for v in area):
            return None, "area is [x1, y1, x2, y2] in mils"
        x1, y1, x2, y2 = area
        x1, x2 = sorted((x1, x2))
        y1, y2 = sorted((y1, y2))
        if x1 == x2 or y1 == y2:
            return None, "area has no size"
        encoded_area = "|".join(str(_coord(v)) for v in (x1, y1, x2, y2))
    return {"kinds": "|" + "|".join(dict.fromkeys(kinds)) + "|", "net": net or "",
            "layer": layer or "", "area": encoded_area}, ""


def narrowed(kinds, net: str, layer: str, area) -> bool:
    """Whether anything narrows the match below every object on the board."""
    return bool(net or layer or area is not None or (kinds is not None and set(
        [kinds] if isinstance(kinds, str) else kinds) != set(KINDS)))


def changes(width_mils=None, via_diameter_mils=None, via_hole_mils=None, new_net=None,
            new_layer: str = "") -> Tuple[Optional[dict], str]:
    """The request's change fields, or None and what is wrong."""
    for name, v in (("width_mils", width_mils), ("via_diameter_mils", via_diameter_mils),
                    ("via_hole_mils", via_hole_mils)):
        if v is not None and (not _number(v) or v <= 0):
            return None, f"{name} must be a positive number"
    if via_diameter_mils is not None and via_hole_mils is not None and via_hole_mils >= via_diameter_mils:
        return None, "via_hole_mils must be smaller than via_diameter_mils"
    if new_net is not None and any(c in str(new_net) for c in '|"\r\n'):
        return None, "new_net cannot contain | \" or a line break"
    if all(v is None for v in (width_mils, via_diameter_mils, via_hole_mils, new_net)) and not new_layer:
        return None, "nothing to change: give width_mils, via_diameter_mils, via_hole_mils, new_net or new_layer"
    return {"width": _coord(width_mils) if width_mils is not None else -1,
            "via_size": _coord(via_diameter_mils) if via_diameter_mils is not None else -1,
            "via_hole": _coord(via_hole_mils) if via_hole_mils is not None else -1,
            "set_net": "true" if new_net is not None else "false",
            "new_net": new_net or "", "new_layer": new_layer or ""}, ""
