"""Shared PCB layer spelling; native Altium still validates layer availability."""

import json
import re


_NAMES = ("Top Layer", "Bottom Layer", "Top Overlay", "Bottom Overlay",
          "Top Paste", "Bottom Paste", "Top Solder Mask", "Bottom Solder Mask",
          "Drill Guide", "Drill Drawing", "Keep Out Layer", "Multi-Layer", "No Layer")
_ALIASES = {re.sub(r"[\s_-]", "", name).casefold(): name for name in _NAMES}
_ALIASES.update({"topsolder": "Top Solder Mask", "bottomsolder": "Bottom Solder Mask",
                 "keepout": "Keep Out Layer"})
_FIELDS = {"layer", "layer_name", "from_layer", "to_layer", "low_layer", "high_layer",
           "start_layer", "end_layer", "layers", "layer_names"}


def canonical_name(value, allow_no_layer=True, legacy_only=False):
    if not isinstance(value, str) or not value.strip() or any(c in value for c in "|\r\n\x00"):
        raise ValueError("layer must be a nonempty name without record delimiters")
    name = value.strip()
    compact = re.sub(r"[\s_-]", "", name).casefold()
    if compact.startswith("e"):
        compact = compact[1:]
    mechanical = re.fullmatch(r"(?:mechanical|mech)(?:layer)?(\d+)", compact)
    if mechanical:
        index = int(mechanical[1])
        if not 1 <= index <= 1024:
            raise ValueError("mechanical layer number must be within 1..1024")
        if legacy_only and index > 16:
            raise ValueError("mechanical layers above 16 require the V7 layer API")
        return f"Mechanical {index}"
    mid = re.fullmatch(r"mid(?:layer)?(\d+)", compact)
    if mid and 1 <= int(mid[1]) <= 30:
        return f"Mid Layer {int(mid[1])}"
    canonical = _ALIASES.get(compact, name)
    if canonical == "No Layer" and not allow_no_layer:
        raise ValueError("No Layer is not a valid destination")
    return canonical


def normalize_fields(value, key=""):
    """Copy JSON payloads, normalizing only layer fields, never free text or paths."""
    if isinstance(value, dict):
        return {k: normalize_fields(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_fields(v, key) for v in value]
    if isinstance(value, str) and value.strip() and key in _FIELDS:
        return canonical_name(value)
    if isinstance(value, str) and key == "result":
        try:
            parsed = json.loads(value)
        except ValueError:
            return value
        if isinstance(parsed, (dict, list)):
            return json.dumps(normalize_fields(parsed), ensure_ascii=False)
    return value


def requested_names(value):
    names = []
    def visit(item, key=""):
        if isinstance(item, dict):
            for k, v in item.items():
                visit(v, k)
        elif isinstance(item, list):
            for v in item:
                visit(v, key)
        elif isinstance(item, str) and key in _FIELDS and item not in names:
            names.append(item)
    visit(value)
    return names
