"""Checking and encoding delete_pcb_objects and modify_pcb_objects requests.

Objects are picked by kind, net, layer and area, or as a group's copper by
designators; every filter given must match. Area is [x1, y1, x2, y2] in mils
from the board origin, and an object matches when it lies inside it. Values
go to Altium in internal units (10000 per mil), so the Pascal side never
parses a decimal. Only copper is ever matched: objects on copper layers and
vias, never keepouts, pads or anything of a component.
"""
from typing import Optional, Tuple

COORD_PER_MIL = 10000
# "cutout" is a polygon pour cutout: matched only when asked for by name
KINDS = ("track", "arc", "via", "polygon", "region", "fill", "cutout")
DEFAULT_KINDS = ("track", "arc", "via", "polygon", "region", "fill")


def _coord(v) -> int:
    return int(round(float(v) * COORD_PER_MIL))


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def normal_kinds(kinds):
    """The kinds asked for, lower case; None or [] means DEFAULT_KINDS."""
    if kinds is None or kinds == []:
        return list(DEFAULT_KINDS)
    if isinstance(kinds, str):
        kinds = [kinds]
    return [str(k).strip().lower() for k in kinds]


def filters(kinds, net: str, layer: str, area, designators=None, margin_mils=40,
            include_locked=False) -> Tuple[Optional[dict], str]:
    """The request's filter fields, or None and what is wrong."""
    kinds = normal_kinds(kinds)
    unknown = [k for k in kinds if k not in KINDS]
    if unknown:
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
        if max(abs(v) for v in (x1, y1, x2, y2)) > 100000:
            return None, "area must lie within ±100,000 mil"
        encoded_area = "|".join(str(_coord(v)) for v in (x1, y1, x2, y2))
    if designators is not None:
        if isinstance(designators, str):
            designators = [designators]
        if not isinstance(designators, list) or not all(isinstance(d, str) and d.strip() for d in designators):
            return None, "designators must be a list of names"
        if any(c in d for d in designators for c in ';|"'):
            return None, "designators cannot contain ; | or \""
    if not _number(margin_mils) or not 0 <= margin_mils <= 10000:
        return None, "margin_mils must be a number from 0 to 10000"
    return {"kinds": "|" + "|".join(dict.fromkeys(kinds)) + "|", "net": net or "",
            "layer": layer or "", "area": encoded_area,
            "designators": ";".join(d.strip() for d in designators or []),
            "margin": str(_coord(margin_mils)),
            "include_locked": "true" if include_locked else "false"}, ""


def narrowed(kinds, net: str, layer: str, area, designators=None) -> bool:
    """Whether anything narrows the match below all the copper on the board."""
    return bool(net or layer or area is not None or designators
                or not set(DEFAULT_KINDS) <= set(normal_kinds(kinds)))


def changes(width_mils=None, via_diameter_mils=None, via_hole_mils=None, new_net=None,
            new_layer: str = "") -> Tuple[Optional[dict], str]:
    """The request's change fields, or None and what is wrong. A hole given
    alone, or a diameter given alone, is checked against each via's other
    size in Altium."""
    for name, v in (("width_mils", width_mils), ("via_diameter_mils", via_diameter_mils),
                    ("via_hole_mils", via_hole_mils)):
        if v is not None and (not _number(v) or v <= 0):
            return None, f"{name} must be a positive number"
    if via_diameter_mils is not None and via_hole_mils is not None and via_hole_mils >= via_diameter_mils:
        return None, "via_hole_mils must be smaller than via_diameter_mils"
    if new_net is not None and any(c in str(new_net) for c in '|"\r\n'):
        return None, "new_net cannot contain | \" or a line break"
    if any(c in (new_layer or "") for c in '|"\r\n'):
        return None, "new_layer cannot contain | \" or a line break"
    if all(v is None for v in (width_mils, via_diameter_mils, via_hole_mils, new_net)) and not new_layer:
        return None, "nothing to change: give width_mils, via_diameter_mils, via_hole_mils, new_net or new_layer"
    return {"width": _coord(width_mils) if width_mils is not None else -1,
            "via_size": _coord(via_diameter_mils) if via_diameter_mils is not None else -1,
            "via_hole": _coord(via_hole_mils) if via_hole_mils is not None else -1,
            "set_net": "true" if new_net is not None else "false",
            "new_net": new_net or "", "new_layer": new_layer or ""}, ""
