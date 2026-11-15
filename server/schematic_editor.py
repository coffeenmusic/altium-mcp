"""Validate and serialize edit_schematic requests without an Altium dependency."""

import json
import math
from pathlib import Path, PureWindowsPath
from collections import Counter
from document_management import absolute_path


FIELDS = {
    "component": {"x", "y", "orientation", "mirror", "designator", "comment",
                  "description", "design_item_id", "parameters", "source_designator", "symbol_library", "symbol"},
    "wire": {"points"},
    "net_label": {"x", "y", "text", "orientation"},
    "port": {"x", "y", "name", "io_type", "style", "width"},
    "power_port": {"x", "y", "text", "orientation", "style", "show_net_name"},
    "junction": {"x", "y"},
    "text": {"x", "y", "text", "orientation"},
}
CREATE_ONLY = {"source_designator", "symbol_library", "symbol"}


def _text(value, name, nonempty=False):
    if not isinstance(value, str) or "\x00" in value:
        raise ValueError(f"{name} must be a string without NUL characters")
    if nonempty and not value.strip():
        raise ValueError(f"{name} must not be empty")


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number in mils")
    if abs(value) > 200000 or not math.isfinite(value):
        raise ValueError(f"{name} must be finite and within +/-200000 mils")


def validate_request(sheet_path, operations):
    """Reject malformed requests before dispatch; return a flat escaped spec.

    JSON string escaping makes newlines, pipes, equals and quotes unambiguous
    in the line-oriented TStringList transport. Files are written with a BOM.
    """
    _text(sheet_path, "sheet_path", True)
    sheet_path = str(absolute_path(sheet_path, {".schdoc"}))
    if not isinstance(operations, list) or len(operations) > 500:
        raise ValueError("operations must be a list of at most 500 operations")
    spec = {"sheet_path": str(Path(sheet_path).resolve()), "count": len(operations)}
    for index, operation in enumerate(operations):
        prefix = f"{index}."
        if not isinstance(operation, dict) or set(operation) - {"op", "type", "target", "properties"}:
            raise ValueError(f"operation {index}: expected op, type, target and properties")
        action, kind = operation.get("op"), operation.get("type")
        if action not in ("add", "update", "delete") or not isinstance(kind, str) or kind not in FIELDS:
            raise ValueError(f"operation {index}: unsupported op or type")
        spec[prefix + "op"], spec[prefix + "type"] = action, kind
        target = operation.get("target")
        if action == "add":
            if target is not None:
                raise ValueError(f"operation {index}: add must not have a target")
        else:
            if not isinstance(target, dict) or len(target) != 1:
                raise ValueError(f"operation {index}: target needs exactly one unique_id or designator")
            key = next(iter(target))
            if key not in ("unique_id", "designator") or (key == "designator" and kind != "component"):
                raise ValueError(f"operation {index}: designator targets are only valid for components")
            _text(target[key], "target." + key, True)
            spec[prefix + "target_" + key] = target[key]
        props = operation.get("properties", {})
        if not isinstance(props, dict) or set(props) - FIELDS[kind]:
            raise ValueError(f"operation {index}: unsupported properties for {kind}")
        if action == "delete" and props:
            raise ValueError(f"operation {index}: delete must not have properties")
        if action == "update" and (not props or CREATE_ONLY.intersection(props)):
            raise ValueError(f"operation {index}: update needs editable properties; symbol sources are add-only")
        if ("x" in props) != ("y" in props):
            raise ValueError(f"operation {index}: supply both x and y")
        if action == "add":
            required = {"points"} if kind == "wire" else {"x", "y"}
            required |= {"component": {"designator"}, "port": {"name"},
                         "net_label": {"text"}, "power_port": {"text"}, "text": {"text"}}.get(kind, set())
            if required - props.keys():
                raise ValueError(f"operation {index}: missing {sorted(required - props.keys())}")
            if kind == "component":
                clone = "source_designator" in props
                library = "symbol_library" in props or "symbol" in props
                if clone == library or (library and not {"symbol_library", "symbol"} <= props.keys()):
                    raise ValueError(f"operation {index}: supply source_designator OR symbol_library and symbol")
        for key, value in props.items():
            if key in ("x", "y", "width"):
                _number(value, key)
                if key == "width" and value <= 0:
                    raise ValueError("width must be positive")
            elif key in ("orientation", "io_type", "style"):
                limit = 3 if key in ("orientation", "io_type") else (7 if kind == "port" else 10)
                if type(value) is not int or not 0 <= value <= limit:
                    raise ValueError(f"{key} must be an integer from 0 to {limit}")
            elif key in ("mirror", "show_net_name"):
                if not isinstance(value, bool):
                    raise ValueError(f"{key} must be a boolean")
            elif key == "points":
                if not isinstance(value, list) or len(value) < 4 or len(value) % 2 or len(value) > 2000:
                    raise ValueError("points must contain 2 to 1000 x,y pairs")
                for coordinate in value:
                    _number(coordinate, "points coordinate")
                spec[prefix + "point_count"] = len(value) // 2
                for n, coordinate in enumerate(value):
                    spec[prefix + f"point{n}"] = coordinate
                continue
            elif key == "parameters":
                if not isinstance(value, dict):
                    raise ValueError("parameters must be an object of string values")
                names = set()
                spec[prefix + "parameter_count"] = len(value)
                for n, (name, text) in enumerate(value.items()):
                    _text(name, "parameter name", True)
                    _text(text, "parameter value")
                    if name.casefold() in names or name.casefold() in ("comment", "designator"):
                        raise ValueError("parameter names must be distinct; use comment/designator properties")
                    names.add(name.casefold())
                    spec[prefix + f"parameter{n}.name"] = name
                    spec[prefix + f"parameter{n}.value"] = text
                continue
            else:
                _text(value, key, key in ("designator", "source_designator", "symbol_library", "symbol", "name"))
            if key == "symbol_library" and (not PureWindowsPath(value).is_absolute() or
                                           Path(value).suffix.lower() != ".schlib" or not Path(value).is_file()):
                raise ValueError(f"symbol_library must be an existing .SchLib: {value}")
            spec[prefix + key] = int(value) if isinstance(value, bool) else value
    return "\n".join(f"{key}={json.dumps(str(value), ensure_ascii=False)[1:-1]}" for key, value in spec.items()) + "\n"


def compact_result(result, mode):
    """Reduce client output; native full snapshots still support verification."""
    result = dict(result)
    objects = result.pop("objects", [])
    result["return_mode"] = mode
    result["object_count"] = len(objects)
    result["object_counts"] = dict(Counter(obj["type"] for obj in objects))
    if mode == "changed":
        changed = {row["unique_id"] for row in result.get("results", []) if row.get("unique_id")}
        result["objects"] = [obj for obj in objects if obj.get("unique_id") in changed]
    return result
