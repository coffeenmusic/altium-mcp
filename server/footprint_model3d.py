"""Exact-target, unsaved edits of existing footprint STEP bodies."""

import json
from pathlib import Path, PureWindowsPath
import re
import uuid

from altium_files import write_altium_text
from document_management import absolute_path
from footprint_editor import number
from library_component_data import text
from schematic_document import disk_state


POSE = {"rotation_x", "rotation_y", "rotation_z", "z_mm"}
FIELDS = POSE | {"x_mm", "y_mm", "embed", "model_path"}


def prepare(library_path, footprint_name, body_index=None, changes=None, delete=False,
            expected_model_file=None, body_id=None):
    path = absolute_path(library_path, {".pcblib"})
    text(footprint_name, "footprint_name", True)
    if type(delete) is not bool:
        raise ValueError("delete must be a boolean")
    if body_index is not None and (type(body_index) is not int or not 0 <= body_index < 10000):
        raise ValueError("body_index must be a zero-based integer within 0..9999")
    if changes is None:
        changes = {}
    if not isinstance(changes, dict) or not set(changes) <= FIELDS:
        raise ValueError("changes supports only " + ", ".join(sorted(FIELDS)))
    if delete and changes:
        raise ValueError("delete cannot be combined with changes")
    if set(changes) & POSE and not POSE <= set(changes):
        raise ValueError("rotation/Z edits require rotation_x, rotation_y, rotation_z and z_mm together; live out-parameter readback is unavailable")
    if "model_path" in changes and not POSE <= set(changes):
        raise ValueError("replacement requires the complete rotation_x, rotation_y, rotation_z, z_mm pose")
    spec = {"library_path": str(path), "footprint_name": footprint_name,
            "delete": "1" if delete else "0", "mutating": "1" if delete or changes else "0"}
    if body_index is not None:
        spec["body_index"] = str(body_index)
    if body_id is not None:
        if not isinstance(body_id, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", body_id):
            raise ValueError("body_id must be a body_address string from the current inventory")
        spec["body_id"] = body_id
    if expected_model_file is not None:
        text(expected_model_file, "expected_model_file", True)
        spec["expected_model_file"] = expected_model_file
    for key, value in changes.items():
        if key == "model_path":
            model = absolute_path(value, {".step", ".stp"})
            if not model.is_file() or not model.stat().st_size:
                raise ValueError("model_path must identify a nonempty existing STEP file")
            spec[key] = str(model)
        elif key == "embed":
            if type(value) is not bool:
                raise ValueError("embed must be a boolean")
            spec[key] = "1" if value else "0"
        else:
            limit = 360000 if key.startswith("rotation_") else 1000
            if type(value) in (int, float) and abs(value) > limit:
                raise ValueError(f"{key} exceeds +/-{limit}")
            spec[key] = number(value, key)
    return spec


def near(actual, expected):
    return type(actual) in (int, float) and type(expected) in (int, float) and abs(actual - expected) < .000003


def verify(result, spec):
    errors = []
    if (not isinstance(result.get("library_path"), str) or
            PureWindowsPath(result["library_path"]) != PureWindowsPath(spec["library_path"]) or
            str(result.get("footprint_name", "")).casefold() != spec["footprint_name"].casefold()):
        errors.append("library/footprint identity mismatch")
    if result.get("saved") is not False:
        errors.append("save was not forbidden")
    before, after = result.get("before"), result.get("after")
    if not isinstance(before, list) or not isinstance(after, list):
        return errors + ["missing native body snapshots"]
    if any(not isinstance(row, dict) for row in before + after):
        return errors + ["invalid native body snapshot"]
    mutating = spec["mutating"] == "1"
    selected = result.get("body_index")
    if mutating or "body_index" in spec or "body_id" in spec or "expected_model_file" in spec:
        if type(selected) is not int or not 0 <= selected < len(before):
            return errors + ["invalid selected body index"]
        if "body_index" in spec and selected != int(spec["body_index"]):
            errors.append("body index mismatch")
        if "expected_model_file" in spec and before[selected].get("model_file") != spec["expected_model_file"]:
            errors.append("expected model filename mismatch")
        if "body_id" in spec and before[selected].get("body_address") != spec["body_id"]:
            errors.append("body ID mismatch")
    old_by_id = {row.get("body_address"):row for row in before}
    new_by_id = {row.get("body_address"):row for row in after}
    if (None in old_by_id or None in new_by_id or len(old_by_id) != len(before) or len(new_by_id) != len(after)):
        return errors + ["missing or duplicate body identity"]
    deleting = spec["delete"] == "1"
    if deleting:
        wanted = dict(old_by_id)
        del wanted[before[selected]["body_address"]]
        if new_by_id != wanted:
            errors.append("deletion changed other bodies or did not remove its target")
    elif old_by_id.keys() != new_by_id.keys():
        errors.append("body identity/count changed")
    else:
        for i, old in enumerate(before):
            new = new_by_id[old["body_address"]]
            if i != selected or not mutating:
                if old != new:
                    errors.append("unrequested body changed")
                continue
            for key in ("x_mm", "y_mm"):
                if not near(new.get(key), float(spec[key]) if key in spec else old.get(key, float("inf"))):
                    errors.append("model position mismatch: " + key)
            if type(new.get("embed")) is not bool or new["embed"] != (spec["embed"] == "1" if "embed" in spec else old.get("embed")):
                errors.append("model embedding mismatch")
            if "model_path" in spec:
                if (not isinstance(new.get("model_file"), str) or
                        PureWindowsPath(new["model_file"]).name != PureWindowsPath(spec["model_path"]).name):
                    errors.append("replacement filename mismatch")
                if result.get("replacement_loaded") is not True:
                    errors.append("replacement model was not loaded")
            elif new.get("model_file") != old.get("model_file"):
                errors.append("unrequested model replacement")
            for key in ("body_address", "layer", "model_type"):
                if new.get(key) != old.get(key):
                    errors.append("body identity/type/layer changed: " + key)
            if not POSE <= set(spec) and any(new.get(k) != old.get(k) for k in ("overall_height_mm", "standoff_height_mm")):
                errors.append("unrequested body height change")
    # The full native footprint dumps independently guard every non-body primitive,
    # footprint Height and description. Library origin/focus are view state only.
    excluded = {"bodies_3d", "library_origin_x", "library_origin_y"}
    dumps = [result.get("footprint_before"), result.get("footprint_after")]
    if not all(isinstance(v, dict) for v in dumps):
        errors.append("missing footprint geometry snapshots")
    elif {k:v for k,v in dumps[0].items() if k not in excluded} != {k:v for k,v in dumps[1].items() if k not in excluded}:
        errors.append("non-body footprint properties/geometry changed")
    elif any(p.get('type') in {'unreadable', 'unknown'} for dump in dumps for p in dump.get('primitives', []) if isinstance(p, dict)):
        errors.append("incomplete non-body geometry readback")
    if any(type(result.get(k)) is not bool for k in ("changed", "was_modified", "is_modified")):
        errors.append("missing Modified/change flags")
    elif ((result["changed"] and not result["is_modified"]) or
          (not result["changed"] and result["was_modified"] != result["is_modified"]) or
          (not mutating and result["changed"])):
        errors.append("Modified/change flags mismatch")
    elif result["changed"] != (old_by_id != new_by_id or POSE <= set(spec) or "model_path" in spec):
        errors.append("change count does not match native snapshots/request")
    if POSE <= set(spec):
        wanted = {key:float(spec[key]) for key in POSE}
        if result.get("pose_applied") != wanted:
            errors.append("pose application was not acknowledged")
    return errors


async def execute(bridge, exchange_dir, *args, **kwargs):
    try:
        spec = prepare(*args, **kwargs)
        original_disk = disk_state(spec["library_path"])
    except (ValueError, TypeError, OSError) as exc:
        return json.dumps(dict(success=False, error=str(exc), saved=False, may_have_partial_changes=False))
    mutating = spec["mutating"] == "1"
    request = exchange_dir / f"edit_footprint_model3d_{uuid.uuid4().hex}.txt"
    write_altium_text(request, "\n".join(f"{k}={json.dumps(v, ensure_ascii=False)[1:-1]}" for k,v in spec.items()) + "\n")
    completed = False
    try:
        try:
            response = await bridge.execute_command("edit_footprint_model3d", {"spec_file": str(request)})
        except Exception as exc:
            response = {"error": str(exc)}
        if response.get("command_started") is False:
            completed = True
            return json.dumps(dict(success=False, command_started=False, saved=False,
                                   error=response.get("error", "command was not started"), may_have_partial_changes=False))
        result = response.get("result")
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except ValueError:
                result = None
        completed = isinstance(result, dict) and type(result.get("success")) is bool
        if not completed:
            return json.dumps(dict(success=False, error=response.get("error", "invalid bridge result"),
                                   outcome_unknown=True, may_have_partial_changes=mutating, saved=False, spec_file=str(request)))
        errors = verify(result, spec) if result["success"] else []
        try:
            if disk_state(spec["library_path"]) != original_disk:
                errors.append("library file changed unexpectedly")
        except OSError as exc:
            errors.append("cannot verify library file: " + str(exc))
        if result["success"] or errors:
            result["verification"] = dict(passed=not errors, errors=errors,
                pose_readback_available=False,
                scope="Native X/Y, embedding, filename, body count, unchanged non-body geometry and disk; rotation/Z application only")
        if errors:
            result.update(success=False, error="3D model edit verification failed", may_have_partial_changes=mutating)
        return json.dumps(result, indent=2, ensure_ascii=False)
    finally:
        if completed:
            request.unlink(missing_ok=True)
