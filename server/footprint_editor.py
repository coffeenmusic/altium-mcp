"""Validated footprint properties, pad flags/settings and graphic layer edits."""

import json
import math
from pathlib import Path, PureWindowsPath
import re
import uuid
from collections import Counter

from altium_files import read_altium_text, write_altium_text
from library_component_data import library_path, text
from schematic_document import disk_state
from pcb_layers import canonical_name


CONSTANT_NAMES = {
    "eRound", "eRounded", "eRectangular", "eOctagonal", "eRoundedRectangular",
    "eCacheManual",
    "eTopLayer", "eBottomLayer", "eTopOverlay", "eBottomOverlay",
    "eMechanical13", "eMechanical15", "eMultiLayer", "eNoLayer",
}
GEOMETRY = {
    "track": ("x1", "y1", "x2", "y2", "width"),
    "arc": ("cx", "cy", "radius", "start_angle", "end_angle", "width"),
    "fill": ("x1", "y1", "x2", "y2", "rotation"),
}


def number(value, label):
    if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1e7:
        raise ValueError(f"{label} must be a finite number within +/-10000000")
    return format(value, ".15g")


def layer_name(value, allow_no_layer=False, legacy_only=True):
    return canonical_name(value, allow_no_layer=allow_no_layer, legacy_only=legacy_only)


def base(path, footprint_name):
    text(footprint_name, "footprint_name", True)
    return {"library_path": library_path(path, ".pcblib"), "footprint_name": footprint_name}


def pad_selection(path, footprint_name, pad_names):
    spec = base(path, footprint_name)
    if not isinstance(pad_names, list) or not 1 <= len(pad_names) <= 1000:
        raise ValueError("pad_names must contain 1 to 1000 distinct names")
    seen = set()
    for index, name in enumerate(pad_names):
        text(name, "pad name", True)
        if name.casefold() in seen:
            raise ValueError("pad_names must be distinct")
        seen.add(name.casefold())
        spec[f"pad.{index}"] = name
    spec["count"] = str(len(pad_names))
    return spec


def prepare_pads(path, footprint_name, pad_names, mask_expansion_mm=None,
                 paste_expansion_mm=None, mode="manual"):
    spec = pad_selection(path, footprint_name, pad_names)
    if mode not in ("manual", "rule"):
        raise ValueError("mode must be manual or rule")
    if mode == "manual" and mask_expansion_mm is None and paste_expansion_mm is None:
        raise ValueError("manual mode requires at least one expansion")
    if mode == "rule" and (mask_expansion_mm is not None or paste_expansion_mm is not None):
        raise ValueError("rule mode resets both overrides and takes no expansion values")
    for prefix, value in (("mask", mask_expansion_mm), ("paste", paste_expansion_mm)):
        if mode == "rule":
            spec[f"{prefix}_mode"] = "1"
        elif value is not None:
            numeric = number(value, f"{prefix}_expansion_mm")
            if abs(float(numeric)) > 10:
                raise ValueError("mask and paste expansions must be within +/-10 mm")
            spec[f"{prefix}_mode"] = "2"
            spec[f"{prefix}_expansion"] = number(float(numeric) / 0.0254, prefix)
    return spec


def prepare_pad_properties(path, footprint_name, pad_names, is_testpoint_top=None, is_testpoint_bottom=None):
    spec = pad_selection(path, footprint_name, pad_names)
    for key, value in (("is_testpoint_top", is_testpoint_top), ("is_testpoint_bottom", is_testpoint_bottom)):
        if value is not None:
            if type(value) is not bool:
                raise ValueError(f"{key} must be a boolean or omitted")
            spec[key] = "1" if value else "0"
    return spec


def verify_pad_properties(result, spec):
    errors = []
    before, after = result.get("before"), result.get("after")
    count = int(spec["count"])
    if not isinstance(before, list) or not isinstance(after, list) or len(before) != count or len(after) != count:
        return ["pad property snapshot count mismatch"]
    requested = {spec[f"pad.{i}"].casefold() for i in range(count)}
    for rows in (before, after):
        if any(not isinstance(row, dict) or not isinstance(row.get("name"), str) for row in rows):
            return ["invalid pad property snapshot"]
        if {row["name"].casefold() for row in rows} != requested:
            return ["pad identity readback mismatch"]
    before = {row["name"].casefold(): row for row in before}
    edited = {key for key in ("is_testpoint_top", "is_testpoint_bottom") if key in spec}
    changed = 0
    for row in after:
        previous = before[row["name"].casefold()]
        for key in ("is_testpoint_top", "is_testpoint_bottom"):
            if type(row.get(key)) is not bool or type(previous.get(key)) is not bool:
                errors.append("missing native testpoint flag: " + key)
            elif key in edited and row[key] is not (spec[key] == "1"):
                errors.append("testpoint flag readback mismatch: " + key)
        if {k:v for k,v in row.items() if k not in edited} != {k:v for k,v in previous.items() if k not in edited}:
            errors.append("unrequested pad properties changed: " + row["name"])
        changed += any(row.get(key) != previous.get(key) for key in edited)
    if type(result.get("changed")) is not bool or result["changed"] != bool(changed) or result.get("updated_count") != changed:
        errors.append("changed pad count mismatch")
    if any(type(result.get(key)) is not bool for key in ("was_modified", "is_modified")):
        errors.append("missing Modified state flags")
    elif (changed and result["is_modified"] is not True) or (not changed and result["was_modified"] != result["is_modified"]):
        errors.append("pad edit/no-op Modified mismatch")
    return errors


def prepare_layers(path, footprint_name, operations):
    spec = base(path, footprint_name)
    if not isinstance(operations, list) or not 1 <= len(operations) <= 1000:
        raise ValueError("operations must contain 1 to 1000 layer updates")
    selectors = set()
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict) or set(operation) != {"target", "layer"}:
            raise ValueError("each operation must have target and layer")
        target = operation["target"]
        if not isinstance(target, dict) or target.get("type") not in GEOMETRY:
            raise ValueError("target.type must be track, arc or fill")
        kind = target["type"]
        if set(target) != {"type", "layer", *GEOMETRY[kind]}:
            raise ValueError(f"{kind} target requires layer and {', '.join(GEOMETRY[kind])}")
        prefix = f"op.{index}."
        spec[prefix + "type"] = kind
        spec[prefix + "from_layer"] = layer_name(target["layer"], True)
        spec[prefix + "to_layer"] = layer_name(operation["layer"])
        for field in GEOMETRY[kind]:
            spec[prefix + field] = number(target[field], field)
        identity = tuple((key[len(prefix):], value) for key, value in spec.items()
                         if key.startswith(prefix) and key != prefix + "to_layer")
        if identity in selectors:
            raise ValueError("duplicate primitive selector")
        selectors.add(identity)
    spec["count"] = str(len(operations))
    return spec


def prepare_constants(names):
    if not isinstance(names, list) or not names or len(names) > len(CONSTANT_NAMES):
        raise ValueError("names must be a nonempty list of supported enum names")
    if any(not isinstance(name, str) or name not in CONSTANT_NAMES for name in names):
        raise ValueError("unsupported constant; supported: " + ", ".join(sorted(CONSTANT_NAMES)))
    if len(set(names)) != len(names):
        raise ValueError("constant names must be distinct")
    return {"count": str(len(names)), **{f"name.{i}": name for i, name in enumerate(names)}}


def prepare_properties(path, footprint_name, height_mm=None):
    spec = {**base(path, footprint_name), "count": "1"}
    if height_mm is not None:
        if type(height_mm) not in (int, float) or abs(height_mm) > 1000 or not math.isfinite(height_mm) or height_mm < 0:
            raise ValueError("height_mm must be a finite number from 0 to 1000 mm")
        spec["height_mm"] = format(height_mm, ".15g")
    return spec


def verify_properties(result, spec):
    errors = []
    if any(type(result.get(key)) is not bool for key in ("changed", "was_modified", "is_modified")):
        errors.append("missing property state flags")
    before, after = result.get("before"), result.get("after")
    if not isinstance(before, dict) or not isinstance(after, dict):
        return ["missing property snapshots"]
    actual = after.get("height_mm")
    expected = float(spec["height_mm"]) if "height_mm" in spec else before.get("height_mm")
    if type(actual) not in (int, float) or type(expected) not in (int, float) or not math.isfinite(actual) or abs(actual - expected) > .000003:
        errors.append("height_mm readback mismatch")
    if {k:v for k,v in before.items() if k != "height_mm"} != {k:v for k,v in after.items() if k != "height_mm"}:
        errors.append("unrequested footprint properties changed")
    if result.get("changed"):
        if "height_mm" not in spec:
            errors.append("read-only request changed the footprint")
        if result.get("is_modified") is not True or result.get("updated_count") != 1:
            errors.append("changed library is not marked Modified or update count is invalid")
    elif result.get("was_modified") != result.get("is_modified") or result.get("updated_count") != 0:
        errors.append("no-op changed Modified state or update count")
    return errors


async def execute(bridge, exchange_dir, command, prepare, *args, **kwargs):
    mutating = command != "get_pcb_api_constants"
    properties = command in {"set_footprint_properties", "set_footprint_pad_properties"}
    try:
        spec = prepare(*args, **kwargs)
        if properties:
            mutating = any(key in spec for key in ("height_mm", "is_testpoint_top", "is_testpoint_bottom"))
            before_disk = disk_state(spec["library_path"])
    except (ValueError, TypeError, OSError) as exc:
        return json.dumps({"success": False, "error": str(exc), "saved": False,
                           "may_have_partial_changes": False})
    spec_path = exchange_dir / f"{command}_{uuid.uuid4().hex}.txt"
    write_altium_text(spec_path, "\n".join(f"{key}={json.dumps(value, ensure_ascii=False)[1:-1]}"
                                         for key, value in spec.items()) + "\n")
    completed = False
    try:
        try:
            response = await bridge.execute_command(command, {"spec_file": str(spec_path)})
        except Exception as exc:
            response = {"error": str(exc)}
        if response.get("command_started") is False:
            completed = True
            return json.dumps({"success": False, "error": response.get("error", "command was not started"),
                               "command_started": False, "saved": False, "may_have_partial_changes": False})
        result = response.get("result", {})
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except ValueError:
                result = {}
        completed = isinstance(result, dict) and type(result.get("success")) is bool
        if not completed:
            return json.dumps({"success": False, "error": response.get("error", "invalid bridge result"),
                               "outcome_unknown": True, "saved": False,
                               "may_have_partial_changes": mutating, "spec_file": str(spec_path)})
        errors = []
        if command == "get_pcb_api_constants" and "constants" in result:
            expected = {spec[f"name.{i}"] for i in range(int(spec["count"]))}
            values, availability = result.get("constants"), result.get("availability")
            if not isinstance(availability, dict) or set(availability) != expected or not isinstance(values, dict):
                errors.append("constant availability readback mismatch")
            else:
                resolved = set()
                for name, status in availability.items():
                    if (not isinstance(status, dict) or any(type(status.get(k)) is not bool for k in ("available", "resolved", "alias"))
                            or status.get("native_name") != ("eRounded" if status.get("alias") and name == "eRound" else name)
                            or (status.get("alias") and name != "eRound")
                            or (status.get("resolved") and not status.get("available") and not status.get("alias"))
                            or (status.get("available") and (not status.get("resolved") or status.get("alias")))):
                        errors.append("invalid constant availability: " + name)
                    elif status["resolved"]:
                        resolved.add(name)
                if set(values) != resolved or any(type(v) is not int for v in values.values()):
                    errors.append("constant readback mismatch")
                if result.get("success") != (resolved == expected):
                    errors.append("constant success/availability mismatch")
            if result.get("checked_in_running_altium") is not True:
                errors.append("native constant evaluation was not confirmed")
        if result.get("success"):
            if command != "get_pcb_api_constants":
                actual_path = result.get("library_path")
                if not isinstance(actual_path, str) or PureWindowsPath(actual_path) != PureWindowsPath(spec["library_path"]):
                    errors.append("library identity mismatch")
                if str(result.get("footprint_name", "")).casefold() != spec["footprint_name"].casefold():
                    errors.append("footprint identity mismatch")
                if type(result.get("verified_count")) is not int or result["verified_count"] != int(spec["count"]):
                    errors.append("edit readback was not confirmed")
                if properties:
                    verify = verify_pad_properties if command == "set_footprint_pad_properties" else verify_properties
                    errors.extend(verify(result, spec))
            else:
                values = result.get("constants")
                expected = {spec[f"name.{i}"] for i in range(int(spec["count"]))}
                if not isinstance(values, dict) or set(values) != expected or any(type(v) is not int for v in values.values()) or "availability" not in result:
                    errors.append("constant readback mismatch")
            if result.get("saved") is not False:
                errors.append("edit readback was not confirmed")
        if properties:
            try:
                if disk_state(spec["library_path"]) != before_disk:
                    errors.append("library file changed unexpectedly")
            except OSError as exc:
                errors.append("cannot verify library file: " + str(exc))
        if result.get("success") or errors:
            result["verification"] = {"passed": not errors, "errors": errors}
        if errors:
            result.update(success=False, error="footprint API readback failed", may_have_partial_changes=mutating)
        if result.get("success") and command == "edit_footprint_primitives":
            result["accepted_layers"] = list(dict.fromkeys(v for k, v in spec.items() if k.endswith(".to_layer")))
        return json.dumps(result, indent=2, ensure_ascii=False)
    finally:
        if completed:
            spec_path.unlink(missing_ok=True)


BATCH_LINEAR_FIELDS = {
    "PAD": (2, 3, 7, 9, 11, 12, 16, 17, 19, 20, 23, 25),
    "TRACK": (1, 2, 3, 4, 5), "ARC": (1, 2, 3, 6), "FILL": (1, 2, 3, 4),
    "TEXT": (1, 2, 3, 4), "MODEL3D": (6, 7, 8), "VIA": (1, 2, 3, 4),
    "FOOTPRINT": (3,),
}
BATCH_LAYER_FIELDS = {"PAD": (5,), "TRACK": (6,), "ARC": (7,), "FILL": (6,),
                      "TEXT": (6,), "REGION": (1,), "MODEL3D": (2,), "VIA": (5, 6)}


def batch_layer_names(path):
    names = []
    for line in read_altium_text(path).splitlines():
        fields = line.split("|")
        for index in BATCH_LAYER_FIELDS.get(fields[0].strip().upper(), ()):
            if fields[index] not in names:
                names.append(fields[index])
    return names


def batch_units(units):
    if not isinstance(units, str) or units.casefold() not in {"mm", "mil"}:
        raise ValueError("units must be mm or mil")
    return units.casefold()


def prepare_batch_file(spec_file, exchange_dir, units="mil"):
    """Preflight creation properties/units and normalize a private mil-valued copy."""
    units = batch_units(units)
    source = Path(spec_file)
    if not source.is_file():
        raise ValueError("spec_file must be an existing file")
    lines = read_altium_text(source).splitlines()
    fields_by_kind = BATCH_LAYER_FIELDS
    for index, line in enumerate(lines):
        fields = line.split("|")
        kind = fields[0].strip().upper()
        if kind == "FOOTPRINT":
            if not 2 <= len(fields) <= 4 or not fields[1].strip():
                raise ValueError(f"line {index + 1}: expected FOOTPRINT|name|description[|height]")
        elif kind == "EDITFOOTPRINT" and (len(fields) != 2 or not fields[1].strip()):
            raise ValueError(f"line {index + 1}: expected EDITFOOTPRINT|name; Height is creation-only")
        elif kind == "PAD" and len(fields) > 28:
            raise ValueError(f"line {index + 1}: unexpected PAD fields after testpoint_bottom")
        elif units == "mm" and kind and kind not in {*fields_by_kind, "FPLIB", "FOOTPRINT", "EDITFOOTPRINT"}:
            raise ValueError(f"line {index + 1}: unknown record cannot be converted from mm: {kind}")
        if kind == "FPLIB" and len(fields) > 1:
            fields[1] = library_path(fields[1].strip(), ".pcblib")
        for position in fields_by_kind.get(kind, ()):
            if position >= len(fields):
                raise ValueError(f"line {index + 1}: missing layer")
            fields[position] = layer_name(fields[position], legacy_only=False)
        positions = range(3, len(fields)) if kind == "REGION" else BATCH_LINEAR_FIELDS.get(kind, ())
        for position in positions:
            if position >= len(fields) or not fields[position].strip():
                continue
            try:
                value = float(fields[position])
                converted = value / .0254 if units == "mm" else value
                number(converted, "linear value")
            except (ValueError, TypeError, OverflowError) as exc:
                raise ValueError(f"line {index + 1}: invalid linear value at field {position}: {fields[position]}") from exc
            if kind == "FOOTPRINT" and not 0 <= converted * .0254 <= 1000 + 1e-9:
                raise ValueError(f"line {index + 1}: Height must be within 0..1000 mm")
            if units == "mm":
                fields[position] = number(converted, "converted mil value")
        if kind == "PAD":
            for position, allowed in ((26, {"Top Layer", "Multi-Layer"}), (27, {"Bottom Layer", "Multi-Layer"})):
                flag = fields[position].strip() if position < len(fields) else ""
                if flag not in {"", "0", "1"}:
                    raise ValueError(f"line {index + 1}: testpoint flags must be 0, 1 or blank")
                if flag == "1" and fields[5] not in allowed:
                    raise ValueError(f"line {index + 1}: testpoint side is incompatible with {fields[5]}")
        lines[index] = "|".join(fields)
    destination = exchange_dir / f"footprint_batch_{uuid.uuid4().hex}.txt"
    write_altium_text(destination, "\n".join(lines) + "\n")
    return destination


def batch_expectations(prepared_file):
    """Creation-only expected properties, including repeated physical pad names."""
    rows=[];current=None;library=None
    for line in read_altium_text(prepared_file).splitlines():
        fields=line.split("|");kind=fields[0].strip().upper()
        if kind == "FPLIB":
            library=fields[1].strip();current=None
        elif kind == "FOOTPRINT":
            current=dict(library_path=library,footprint_name=fields[1].strip(),
                         height_mm=float(fields[3])*.0254 if len(fields)>3 and fields[3].strip() else 0,pads=[])
            rows.append(current)
        elif kind == "EDITFOOTPRINT":
            current=None
        elif kind == "PAD" and current is not None:
            current["pads"].append(dict(name=fields[1],is_testpoint_top=len(fields)>26 and fields[26].strip()=="1",
                                        is_testpoint_bottom=len(fields)>27 and fields[27].strip()=="1"))
    return rows


def verify_batch(result, expected):
    errors=[];actual=result.get("footprints")
    if not isinstance(actual,list) or len(actual)!=len(expected) or type(result.get("created")) is not int or result.get("created")!=len(expected):
        return ["created footprint readback count mismatch"]
    if result.get("success") is False:
        errors.append("native batch reported failure")
    if result.get("saved") is not False:
        errors.append("batch creation must not save")
    for row,wanted in zip(actual,expected):
        if (not isinstance(row,dict) or row.get("footprint_name")!=wanted["footprint_name"] or
                (wanted["library_path"] and (not isinstance(row.get("library_path"),str) or PureWindowsPath(row["library_path"])!=PureWindowsPath(wanted["library_path"])) )):
            errors.append("created footprint identity/order mismatch")
            continue
        height=row.get("height_mm")
        if type(height) not in (int,float) or not math.isfinite(height) or abs(height-wanted["height_mm"])>.000003:
            errors.append("native Height readback mismatch: "+wanted["footprint_name"])
        pads=row.get("pads")
        if not isinstance(pads,list) or type(row.get("pad_count")) is not int or row.get("pad_count")!=len(wanted["pads"]) or len(pads)!=len(wanted["pads"]):
            errors.append("created pad count mismatch: "+wanted["footprint_name"])
            continue
        if any(not isinstance(p,dict) or not isinstance(p.get("name"),str) or
               any(type(p.get(k)) is not bool for k in ("is_testpoint_top","is_testpoint_bottom")) for p in pads):
            errors.append("missing native pad name/testpoint flags")
            continue
        key=lambda p:(p["name"],p["is_testpoint_top"],p["is_testpoint_bottom"])
        if Counter(map(key,pads))!=Counter(map(key,wanted["pads"])):
            errors.append("native testpoint flag readback mismatch: "+wanted["footprint_name"])
    return errors
