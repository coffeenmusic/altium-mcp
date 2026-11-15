"""Remove an exact library member only after native empty-content preflight."""

import json
from pathlib import PureWindowsPath
import uuid

from altium_files import write_altium_text
from document_management import absolute_path
from library_component_data import text
from schematic_document import disk_state


def prepare(library_path, component_name, dry_run=False):
    path = absolute_path(library_path, {".schlib", ".pcblib"})
    text(component_name, "component_name", True)
    if len(component_name) > 255 or any(ord(c) < 32 for c in component_name):
        raise ValueError("component_name must have at most 255 characters without controls")
    if type(dry_run) is not bool:
        raise ValueError("dry_run must be a boolean")
    return {"library_path": str(path), "component_name": component_name,
            "dry_run": int(dry_run)}


def verify(result, spec):
    errors = []
    if (PureWindowsPath(result.get("library_path", "")) != PureWindowsPath(spec["library_path"])
            or result.get("component_name", "").casefold() != spec["component_name"].casefold()):
        errors.append("target identity mismatch")
    if result.get("saved") is not False or result.get("dry_run") is not bool(spec["dry_run"]):
        errors.append("save/dry-run state mismatch")
    before, after = result.get("components_before"), result.get("components_after")
    if not isinstance(before, list) or not isinstance(after, list):
        return errors + ["missing library inventories"]
    if any(not isinstance(row, dict) or not row.get("_id") or not isinstance(row.get("name"), str)
           for row in before + after):
        return errors + ["invalid library inventory"]
    old, new = ({r["_id"]: r for r in rows} for rows in (before, after))
    if len(old) != len(before) or len(new) != len(after):
        errors.append("duplicate native identities")
    matches = [r for r in before if r["name"].casefold() == spec["component_name"].casefold()]
    found = len(matches) == 1
    if len(matches) > 1 or result.get("found") is not found:
        errors.append("target match count mismatch")
    removed = result.get("removed")
    assessment = result.get("assessment", {})
    if type(removed) is not bool or result.get("changed") is not removed:
        errors.append("removed/changed state mismatch")
    expected = dict(old)
    if removed:
        if (spec["dry_run"] or not found or len(before) < 2 or assessment.get("empty") is not True
                or assessment.get("blockers") != [] or result.get("removable") is not True):
            errors.append("removal was not authorized by empty preflight")
        if found:
            expected.pop(matches[0]["_id"])
    if expected != new:
        errors.append("unexpected library members or survivor properties changed")
    if not removed and not spec["dry_run"] and found:
        errors.append("successful removal did not remove its target")
    if any(type(result.get(k)) is not bool for k in ("was_modified", "is_modified")):
        errors.append("missing Modified states")
    elif (removed and not result["is_modified"]) or (not removed and result["was_modified"] != result["is_modified"]):
        errors.append("Modified state mismatch")
    return errors


async def execute(bridge, exchange_dir, library_path, component_name, dry_run=False):
    try:
        spec = prepare(library_path, component_name, dry_run)
        before_disk = disk_state(spec["library_path"])
        request = exchange_dir / f"empty_library_component_{uuid.uuid4().hex}.txt"
        write_altium_text(request, "\n".join(f"{k}={json.dumps(str(v), ensure_ascii=False)[1:-1]}"
                                            for k, v in spec.items()) + "\n")
    except (ValueError, TypeError, OSError) as exc:
        return json.dumps({"success": False, "error": str(exc), "saved": False, "may_have_partial_changes": False})
    completed = False
    try:
        try:
            response = await bridge.execute_command("remove_empty_library_component", {"spec_file": str(request)})
        except Exception as exc:
            response = {"error": str(exc)}
        if response.get("command_started") is False:
            completed = True
            return json.dumps({"success": False, "error": response.get("error"), "saved": False,
                               "may_have_partial_changes": False})
        result = response.get("result")
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except ValueError:
                result = None
        completed = isinstance(result, dict) and type(result.get("success")) is bool
        if not completed:
            return json.dumps({"success": False, "error": response.get("error", "invalid native reply"),
                               "saved": False, "outcome_unknown": True,
                               "may_have_partial_changes": not dry_run, "spec_file": str(request)})
        errors = verify(result, spec) if result["success"] else []
        try:
            if disk_state(spec["library_path"]) != before_disk:
                errors.append("library file changed unexpectedly")
        except OSError as exc:
            errors.append("cannot verify library file: " + str(exc))
        result["verification"] = {"passed": result["success"] and not errors, "errors": errors}
        if errors:
            result.update(success=False, error="empty component verification failed", may_have_partial_changes=not dry_run)
        for key in ("components_before", "components_after"):
            inventory = result.pop(key, [])
            result[key.replace("components_", "component_names_")] = [row["name"] for row in inventory]
        return json.dumps(result, indent=2, ensure_ascii=False)
    finally:
        if completed:
            request.unlink(missing_ok=True)
