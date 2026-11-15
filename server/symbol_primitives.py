"""Validated in-place edits of existing SchLib drawing objects and pins."""

import json
import math
from pathlib import PureWindowsPath
import uuid

from altium_files import write_altium_text
from library_component_data import library_path, text
from schematic_document import disk_state


FONT = {"font_name", "font_size", "font_bold", "font_italic", "font_underline", "font_strikeout"}
FIELDS = {
    "label": {"text", "x", "y", "orientation", "justification", "color"} | FONT,
    "line": {"x1", "y1", "x2", "y2", "line_width", "color"},
    "rectangle": {"x1", "y1", "x2", "y2", "line_width", "color", "area_color", "is_solid"},
    "polyline": {"vertices", "line_width", "color"},
    "polygon": {"vertices", "line_width", "color", "area_color", "is_solid"},
    "arc": {"cx", "cy", "radius", "start_angle", "end_angle", "line_width", "color"},
    "ellipse": {"cx", "cy", "radius", "secondary_radius", "line_width", "color", "area_color", "is_solid"},
    "pin": {"pin_name", "description", "x", "y", "length", "orientation", "electrical", "show_name", "show_designator", "is_hidden", "color"},
}
BOOLS = {"is_solid", "show_name", "show_designator", "is_hidden"} | (FONT - {"font_name", "font_size"})
STRINGS = {"text", "pin_name", "description", "font_name", "unique_id", "pin_number"}
INTS = {"orientation": (0, 3), "justification": (0, 8), "line_width": (0, 3), "electrical": (0, 7),
        "color": (0, 0xFFFFFF), "area_color": (0, 0xFFFFFF), "font_size": (1, 144),
        "owner_part_id": (-1, 255), "display_mode": (-1, 255)}


def _value(key, value, kind):
    if key in BOOLS:
        if type(value) is not bool:
            raise ValueError(key + " must be boolean")
        return
    if key in STRINGS:
        text(value, key, key in {"font_name", "unique_id", "pin_number"})
        if len(value) > 4096:
            raise ValueError(key + " exceeds 4096 characters")
        return
    if key in INTS:
        low, high = INTS[key]
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"{key} must be an integer within {low}..{high}")
        return
    if key == "vertices":
        minimum = 3 if kind == "polygon" else 2
        if not isinstance(value, list) or not minimum <= len(value) <= 1000:
            raise ValueError(f"vertices needs {minimum}..1000 objects with x/y")
        for point in value:
            if not isinstance(point, dict) or set(point) != {"x", "y"}:
                raise ValueError("vertices needs objects with exactly x/y")
            for axis in ("x", "y"):
                _value(axis, point[axis], kind)
        return
    if type(value) not in (int, float) or abs(value) > 200000 or not math.isfinite(value):
        raise ValueError(key + " must be finite and within +/-200000 mils")
    if key in {"radius", "secondary_radius", "length"} and value <= 0:
        raise ValueError(key + " must be positive")
    if key in {"start_angle", "end_angle"} and not 0 <= value <= 360:
        raise ValueError(key + " must be within 0..360 degrees")


def prepare(path, symbol_name, edits):
    text(symbol_name, "symbol_name", True)
    spec = {"library_path": library_path(path, ".schlib"), "symbol_name": symbol_name}
    if not isinstance(edits, list) or len(edits) > 500:
        raise ValueError("edits must be a list of at most 500 updates; [] reads objects")
    for i, edit in enumerate(edits):
        if not isinstance(edit, dict) or set(edit) != {"target", "properties"}:
            raise ValueError("each edit must have target and properties")
        target, props = edit["target"], edit["properties"]
        if not isinstance(target, dict) or target.get("type") not in FIELDS:
            raise ValueError("target.type must be one of " + ", ".join(FIELDS))
        kind = target["type"]
        selectors = FIELDS[kind] | {"type", "unique_id", "owner_part_id", "display_mode"}
        if kind == "pin":
            selectors |= {"pin_number"}
        if set(target) - selectors:
            raise ValueError("unsupported target selector field")
        if not isinstance(props, dict) or not props or set(props) - FIELDS[kind]:
            raise ValueError("properties must be a nonempty subset of " + ", ".join(sorted(FIELDS[kind])))
        for label, values in (("target", target), ("properties", props)):
            if label == "properties":
                for x, y in (("x", "y"), ("x1", "y1"), ("x2", "y2"), ("cx", "cy")):
                    if (x in values) != (y in values):
                        raise ValueError(f"supply both {x} and {y}")
            for key, value in values.items():
                prefix = f"{i}.{label}."
                if key != "type":
                    _value(key, value, kind)
                if key == "vertices":
                    spec[prefix + "vertex_count"] = len(value)
                    for n, point in enumerate(value):
                        for axis in ("x", "y"):
                            spec[prefix + f"vertex{n}.{axis}"] = point[axis]
                else:
                    spec[prefix + key] = int(value) if type(value) is bool else value
    spec["count"] = len(edits)
    return spec


def same(actual, expected, key=""):
    if type(expected) is bool:
        return type(actual) is bool and actual == expected
    if type(expected) in (int, float):
        return type(actual) in (int, float) and abs(actual - expected) <= .001
    if key == "font_name":
        return isinstance(actual, str) and actual.casefold() == expected.casefold()
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(same(a, b) for a, b in zip(actual, expected))
    if isinstance(expected, dict):
        return isinstance(actual, dict) and set(actual) == set(expected) and all(same(actual[k], v, k) for k, v in expected.items())
    return actual == expected


def verify(result, spec, edits):
    errors = []
    if PureWindowsPath(result.get("library_path", "")) != PureWindowsPath(spec["library_path"]) or result.get("symbol_name", "").casefold() != spec["symbol_name"].casefold():
        errors.append("target identity mismatch")
    if result.get("saved") is not False:
        errors.append("operation must not save")
    if result.get("metadata_before") != result.get("metadata_after") or not isinstance(result.get("metadata_before"), dict):
        errors.append("symbol metadata, models or parameters changed")
    metadata = result.get("metadata_before")
    if not isinstance(metadata, dict) or str(metadata.get("symbol_name", "")).casefold() != spec["symbol_name"].casefold():
        errors.append("native symbol name mismatch")
    if not isinstance(result.get("component_uid_before"), str) or result.get("component_uid_before") != result.get("component_uid_after"):
        errors.append("symbol UID changed or is missing")
    if not result.get("_component_id_before") or result.get("_component_id_before") != result.get("_component_id_after"):
        errors.append("symbol object was replaced")
    before, after = result.get("objects_before"), result.get("objects_after")
    if not isinstance(before, list) or not isinstance(after, list):
        return errors + ["missing object snapshots"]
    if any(not isinstance(row, dict) or not isinstance(row.get("_id"), str) or not row["_id"] for row in before + after):
        return errors + ["invalid object identity snapshot"]
    old = {row["_id"]: row for row in before}
    new = {row["_id"]: row for row in after}
    if len(old) != len(before) or len(new) != len(after) or set(old) != set(new):
        return errors + ["objects were created, deleted or replaced"]
    rows = result.get("results", [])
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return errors + ["invalid edit results"]
    edited = {}
    if len(rows) != len(edits):
        errors.append("edit result count mismatch")
    for i, edit in enumerate(edits):
        matches = [row for row in rows if row.get("index") == i]
        if len(matches) != 1 or matches[0].get("_id") not in new:
            errors.append(f"missing edit readback: {i}")
            continue
        ident = matches[0]["_id"]
        if ident in edited:
            errors.append("same object edited twice")
        edited[ident] = set(edit["properties"])
        for key, expected in edit["properties"].items():
            if not same(new[ident].get(key), expected, key):
                errors.append(f"{i}.{key} readback mismatch")
    changed = 0
    for ident, previous in old.items():
        current = new[ident]
        allowed = edited.get(ident, set())
        if {k:v for k,v in previous.items() if k not in allowed} != {k:v for k,v in current.items() if k not in allowed}:
            errors.append("unrequested object properties changed: " + ident)
        changed += previous != current
    if result.get("updated_count") != changed or result.get("changed") is not bool(changed):
        errors.append("changed object count mismatch")
    if any(type(result.get(k)) is not bool for k in ("was_modified", "is_modified")):
        errors.append("missing Modified states")
    elif (changed and not result["is_modified"]) or (not changed and result["was_modified"] != result["is_modified"]):
        errors.append("edit/no-op Modified mismatch")
    return errors


async def execute(bridge, exchange_dir, path, symbol_name, edits):
    try:
        spec = prepare(path, symbol_name, edits)
        before_disk = disk_state(spec["library_path"])
        request = exchange_dir / f"symbol_primitives_{uuid.uuid4().hex}.txt"
        write_altium_text(request, "\n".join(f"{k}={json.dumps(str(v), ensure_ascii=False)[1:-1]}" for k, v in spec.items()) + "\n")
    except (ValueError, TypeError, OSError) as exc:
        return json.dumps({"success": False, "error": str(exc), "saved": False, "may_have_partial_changes": False})
    completed = False
    try:
        try:
            response = await bridge.execute_command("update_symbol_primitives", {"spec_file": str(request)})
        except Exception as exc:
            response = {"error": str(exc)}
        if response.get("command_started") is False:
            completed = True
            return json.dumps({"success": False, "error": response.get("error"), "saved": False, "may_have_partial_changes": False})
        result = response.get("result")
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except ValueError:
                result = None
        completed = isinstance(result, dict) and type(result.get("success")) is bool
        if not completed:
            return json.dumps({"success": False, "error": response.get("error", "invalid native reply"), "saved": False,
                               "outcome_unknown": True, "may_have_partial_changes": bool(edits), "spec_file": str(request)})
        errors = verify(result, spec, edits) if result["success"] else []
        try:
            if disk_state(spec["library_path"]) != before_disk:
                errors.append("library file changed unexpectedly")
        except OSError as exc:
            errors.append("cannot verify library file: " + str(exc))
        result["verification"] = {"passed": result["success"] and not errors, "errors": errors}
        if errors:
            result.update(success=False, error="symbol primitive verification failed", may_have_partial_changes=bool(edits))
        # Request-local interface IDs are verification keys, never public selectors.
        objects = result.pop("objects_after", [])
        result.pop("objects_before", None)
        result["objects"] = [{k:v for k,v in row.items() if k != "_id"} for row in objects if row.get("type") != "unsupported"]
        result["unsupported_count"] = sum(row.get("type") == "unsupported" for row in objects)
        result.pop("_component_id_before", None)
        result.pop("_component_id_after", None)
        for row in result.get("results", []):
            row.pop("_id", None)
        return json.dumps(result, indent=2, ensure_ascii=False)
    finally:
        if completed:
            request.unlink(missing_ok=True)
