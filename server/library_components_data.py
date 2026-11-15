"""Paged, field-selective library metadata reads through one native command."""

import json
from pathlib import PureWindowsPath
import uuid

from altium_files import write_altium_text
from document_management import absolute_path
from library_component_data import text, enrich
from schematic_document import disk_state

FIELDS = ("properties", "parameters", "pins", "models", "pads")
DEFAULT_FIELDS = ("properties", "parameters", "models")
SUPPORTED = {"SCHLIB": set(FIELDS) - {"pads"}, "PCBLIB": {"properties", "pads"}}


def prepare(library_paths, component_names=None, fields=None, offset=0, limit=50):
    if isinstance(library_paths, str):
        library_paths = [library_paths]
    if not isinstance(library_paths, list) or not 1 <= len(library_paths) <= 16:
        raise ValueError("library_paths needs one path or a list of 1..16 paths")
    paths = [str(absolute_path(p, {".schlib", ".pcblib"})) for p in library_paths]
    if len({PureWindowsPath(p) for p in paths}) != len(paths):
        raise ValueError("library_paths must be distinct")
    if component_names is not None:
        if not isinstance(component_names, list) or not 1 <= len(component_names) <= 500:
            raise ValueError("component_names must be omitted or contain 1..500 exact names")
        for name in component_names:
            text(name, "component_name", True)
            if len(name) > 255 or any(ord(c) < 32 for c in name):
                raise ValueError("component names need at most 255 characters without controls")
        if len({n.casefold() for n in component_names}) != len(component_names):
            raise ValueError("component_names must be distinct, case-insensitively")
    if fields is None:
        fields = list(DEFAULT_FIELDS)
    if (not isinstance(fields, list) or not fields or any(not isinstance(f, str) or f not in FIELDS for f in fields)
            or len(set(fields)) != len(fields)):
        raise ValueError("fields must be a nonempty distinct subset of " + ", ".join(FIELDS))
    if type(offset) is not int or not 0 <= offset <= 1000000:
        raise ValueError("offset must be an integer within 0..1000000")
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("limit must be an integer within 1..500")
    spec = {"path_count": len(paths), "name_count": len(component_names or []), "offset": offset, "limit": limit}
    spec.update({f"path.{i}": p for i, p in enumerate(paths)})
    spec.update({f"name.{i}": n for i, n in enumerate(component_names or [])})
    spec.update({"field." + f: int(f in fields) for f in FIELDS})
    return spec, paths, fields


def format_result(result, paths, fields, component_names, offset, limit, validation=False):
    errors = []
    libraries = result.get("libraries")
    raw_rows = result.pop("rows", None)
    if not isinstance(libraries, list) or len(libraries) != len(paths) or not isinstance(raw_rows, list):
        raise ValueError("missing library summaries or rows")
    if any(not isinstance(row, dict) for row in libraries + raw_rows):
        raise ValueError("library summaries and rows must be objects")
    if result.get("success") and (any(row.get("success") is not True for row in libraries)
                                  or result.get("library_errors") or result.get("component_errors")):
        errors.append("success contradicts partial read errors")
    for index, (library, path) in enumerate(zip(libraries, paths)):
        expected_kind = "SCHLIB" if PureWindowsPath(path).suffix.lower() == ".schlib" else "PCBLIB"
        if (library.get("index") != index or PureWindowsPath(library.get("library_path", "")) != PureWindowsPath(path)
                or library.get("kind") != expected_kind):
            errors.append("library identity mismatch")
        library["unsupported_fields"] = [f for f in fields if f not in SUPPORTED[expected_kind]]
        if library.get("success"):
            if (type(library.get("was_modified")) is not bool or library.get("was_modified") != library.get("is_modified")
                    or library.get("active_before") != library.get("active_after")
                    or library.get("part_before") != library.get("part_after")
                    or library.get("display_before") != library.get("display_after")):
                errors.append("library buffer or member/part/display selection changed")
    rows = []
    seen = set()
    for row in raw_rows:
        index = row.get("library_index")
        name = row.get("component_name")
        if type(index) is not int or not 0 <= index < len(paths) or not isinstance(name, str) or not name:
            errors.append("invalid component identity")
            continue
        key = (index, name.casefold())
        if key in seen or (component_names is not None and name.casefold() not in {n.casefold() for n in component_names}):
            errors.append("duplicate or unrequested component")
        seen.add(key)
        kind = libraries[index]["kind"]
        item = {"library_path": paths[index], "library_kind": kind, "component_name": name}
        if kind == "SCHLIB":
            metadata = row.get("symbol_data")
            if not isinstance(metadata, dict) or str(metadata.get("symbol_name", "")).casefold() != name.casefold():
                errors.append("symbol metadata identity mismatch")
                continue
            if "properties" in fields:
                keys = ("description", "comment", "designator_template", "designator_read_only_state",
                        "component_kind", "component_kind_code", "part_count")
                item["properties"] = {k: metadata[k] for k in keys}
                item["properties"].update({k: row[k] for k in ("display_mode_count", "current_part_id", "display_mode")})
            if "parameters" in fields:
                if not isinstance(metadata.get("parameters"), dict):
                    raise ValueError("missing parameter dictionary")
                item["parameters"] = metadata["parameters"]
            if validation:
                item["parameter_records"] = row["parameter_records"]
            if "models" in fields:
                models = metadata.get("models")
                if not isinstance(models, list) or any(not isinstance(m, dict) or not isinstance(m.get("model_type"), str)
                                                       or not isinstance(m.get("map_as_string"), str) for m in models):
                    raise ValueError("invalid model snapshots")
                item["models"] = enrich(metadata)["models"]
            if "pins" in fields:
                pins = row.get("pins")
                if not isinstance(pins, list) or row.get("pin_count") != len(pins):
                    errors.append("pin count/readback mismatch")
                    pins = []
                if any(not isinstance(p, dict) or p.get("type") != "pin" or not isinstance(p.get("pin_number"), str) for p in pins):
                    raise ValueError("invalid native pin snapshots")
                item["pins"] = []
                for pin in pins:
                    public = {k: v for k, v in pin.items() if k not in {"_id", "type", "pin_parameters"}}
                    public["parameters"] = pin.get("pin_parameters", {}).get("parameters", [])
                    item["pins"].append(public)
                item["pin_count"] = len(pins)
        else:
            if "properties" in fields:
                item["properties"] = row["properties"]
            if "pads" in fields:
                pads = row.get("pads")
                if not isinstance(pads, list) or row.get("pad_count") != len(pads):
                    errors.append("pad count/readback mismatch")
                    pads = []
                for pad in pads:
                    if not isinstance(pad, dict) or not isinstance(pad.get("name"), str) or any(type(pad.get(k)) is not bool for k in ("is_testpoint_top", "is_testpoint_bottom")):
                        errors.append("missing native pad identity or testpoint flags")
                item["pads"] = pads
                item["pad_count"] = len(pads)
            if validation:
                details = row["pad_details"]
                if not isinstance(details, list) or len(details) != item["pad_count"]:
                    raise ValueError("validation pad details/count mismatch")
                item["pad_details"] = details
                item["other_copper_count"] = row["other_copper_count"]
                item["other_aperture_count"] = row["other_aperture_count"]
        rows.append(item)
    total = result.get("matched_count")
    if type(total) is not int or total < 0:
        errors.append("invalid matched count")
        total = 0
    page_count = min(limit, max(0, total - offset))
    if result.get("page_count") != page_count or len(rows) + len(result.get("component_errors", [])) != page_count:
        errors.append("pagination counts mismatch")
    if result.get("saved") is not False:
        errors.append("batch reads must not save")
    if result.get("view_restored") is not True:
        errors.append("previous view was not restored")
    if result.get("previous_document_path") and PureWindowsPath(result["previous_document_path"]) != PureWindowsPath(result.get("active_document_path", "")):
        errors.append("restored view identity mismatch")
    found = result.pop("found_names", [])
    result["missing_names"] = [n for n in component_names or [] if n.casefold() not in {str(v).casefold() for v in found}]
    result.update(components=rows, returned_count=len(rows), fields=fields, offset=offset, limit=limit,
                  has_more=offset + page_count < total,
                  next_offset=offset + page_count if offset + page_count < total else None)
    result["complete"] = bool(result.get("success")) and not result["has_more"] and not result["missing_names"]
    return errors


async def execute(bridge, exchange_dir, library_paths, component_names=None, fields=None, offset=0, limit=50, *, validation=False):
    try:
        spec, paths, selected_fields = prepare(library_paths, component_names, fields, offset, limit)
        if validation:
            spec["validation"] = 1
        before = {p: disk_state(p) for p in paths}
        request = exchange_dir / f"library_components_{uuid.uuid4().hex}.txt"
        write_altium_text(request, "\n".join(f"{k}={json.dumps(str(v), ensure_ascii=False)[1:-1]}" for k, v in spec.items()) + "\n")
    except (ValueError, TypeError, OSError) as exc:
        return json.dumps({"success": False, "error": str(exc), "saved": False})
    completed = False
    try:
        try:
            response = await bridge.execute_command("get_library_components_data", {"spec_file": str(request)})
        except Exception as exc:
            response = {"error": str(exc)}
        if response.get("command_started") is False:
            completed = True
            return json.dumps({"success": False, "error": response.get("error"), "saved": False, "may_have_view_changes": False})
        result = response.get("result")
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except ValueError:
                result = None
        completed = isinstance(result, dict) and type(result.get("success")) is bool
        if not completed:
            return json.dumps({"success": False, "error": response.get("error", "invalid native reply"), "saved": False,
                               "outcome_unknown": True, "may_have_view_changes": True, "spec_file": str(request)})
        errors = []
        if result["success"] or (isinstance(result.get("libraries"), list) and len(result["libraries"]) == len(paths)):
            try:
                errors = format_result(result, paths, selected_fields, component_names, offset, limit, validation)
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                errors.append("invalid batch readback: " + str(exc))
                result.pop("rows", None)
        else:
            result.pop("rows", None)
        try:
            if {p: disk_state(p) for p in paths} != before:
                errors.append("library files changed unexpectedly")
        except OSError as exc:
            errors.append("cannot verify library files: " + str(exc))
        result["verification"] = {"passed": result["success"] and not errors, "errors": errors}
        if errors:
            result.update(success=False, complete=False, error="library batch read verification failed")
        return json.dumps(result, indent=2, ensure_ascii=False)
    finally:
        if completed:
            request.unlink(missing_ok=True)
