"""Native SchLib designator/type edits using the shared metadata transaction."""

import json
from pathlib import Path, PureWindowsPath
import re
import uuid

from altium_files import write_altium_text
from library_component_data import prepare_request
from schematic_document import disk_state


KINDS = {
    "standard": "Standard",
    "mechanical": "Mechanical",
    "graphical": "Graphical",
    "net_tie_bom": "Net Tie (In BOM)",
    "net_tie_no_bom": "Net Tie",
    "standard_no_bom": "Standard (No BOM)",
}


def kind(value):
    if not isinstance(value, str):
        raise ValueError("component_kind must be a supported name, not an enum integer")
    key = re.sub(r"[\s_()\-]", "", value.casefold())
    names = {re.sub(r"[\s_()\-]", "", alias.casefold()): canonical
             for canonical, label in KINDS.items() for alias in (canonical, label)}
    names["nettienobom"] = "net_tie_no_bom"
    if key not in names:
        raise ValueError("component_kind must be one of " + ", ".join(KINDS))
    return names[key]


def designator(value):
    if not isinstance(value, str) or len(value) > 64 or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*\?", value):
        raise ValueError("designator_template must be a prefix followed by one '?', e.g. J?, D? or C?; prefix: letters/digits/underscore, starting with a letter (max 64 characters)")
    return value


def prepare(library_path, symbol_name, designator_template=None, component_kind=None):
    spec = prepare_request(library_path, symbol_name, None, None, None, None)
    requested = {}
    if designator_template is not None:
        requested["designator_template"] = designator(designator_template)
    if component_kind is not None:
        requested["component_kind"] = kind(component_kind)
    spec += "".join(f"{k}={json.dumps(v, ensure_ascii=False)[1:-1]}\n" for k, v in requested.items())
    return spec, requested


def verify(result, path, symbol_name, requested):
    errors = []
    if PureWindowsPath(result.get("library_path", "")) != PureWindowsPath(path):
        errors.append("library path readback mismatch")
    if result.get("symbol_name", "").casefold() != symbol_name.casefold():
        errors.append("symbol identity readback mismatch")
    if result.get("saved") is not False:
        errors.append("operation must not save")
    before, after = result.get("before"), result.get("after")
    if not isinstance(before, dict) or not isinstance(after, dict):
        return errors + ["missing before/after snapshots"]
    for key, value in requested.items():
        if after.get(key) != value:
            errors.append(key + " readback mismatch")
    for key in ("designator_template", "component_kind"):
        if key not in after:
            errors.append(key + " missing from snapshot")
    # Compare everything except explicitly edited native fields, including all
    # parameters/models, Comment, Description, part count and omitted properties.
    edited = set(requested)
    if "component_kind" in requested:
        edited.add("component_kind_code")
    if {k: v for k, v in before.items() if k not in edited} != {k: v for k, v in after.items() if k not in edited}:
        errors.append("unrequested symbol metadata changed")
    if result.get("changed") and result.get("is_modified") is not True:
        errors.append("changed library is not marked Modified")
    if not result.get("changed") and result.get("was_modified") != result.get("is_modified"):
        errors.append("no-op changed Modified state")
    return errors


async def execute(bridge, exchange_dir, library_path, symbol_name, designator_template=None, component_kind=None):
    try:
        spec, requested = prepare(library_path, symbol_name, designator_template, component_kind)
        path = str(Path(library_path).resolve())
        before_disk = disk_state(path)
        request = exchange_dir / f"library_properties_{uuid.uuid4().hex}.txt"
        write_altium_text(request, spec)
    except (ValueError, TypeError, OSError) as exc:
        return json.dumps({"success": False, "error": str(exc), "saved": False, "may_have_partial_changes": False})
    completed = False
    try:
        try:
            response = await bridge.execute_command("set_library_component_data", {"spec_file": str(request)})
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
        # The older shared writer reports early target refusals as ERROR strings.
        # Only explicit preflight refusals establish that mutation did not start.
        error = response.get("error", "invalid native result")
        if not completed:
            refusal = ("symbol must match exactly once; found ", "target must be an existing .SchLib",
                       "could not open target SchLib", "target SchLib is unavailable",
                       "target is not a SchLib", "opened SchLib does not match the exact requested path",
                       "schematic editor has an active transaction")
            # ExecuteCommand strips the legacy ERROR prefix in normal replies.
            completed = isinstance(error, str) and error.removeprefix("ERROR: ").startswith(refusal)
            failure = {"success": False, "error": error, "saved": False, "may_have_partial_changes": bool(requested) and not completed}
            if not completed:
                failure.update(outcome_unknown=True, spec_file=str(request))
            return json.dumps(failure)
        errors = verify(result, path, symbol_name, requested) if result["success"] else []
        try:
            if disk_state(path) != before_disk:
                errors.append("library file changed unexpectedly")
        except OSError as exc:
            errors.append("cannot verify library file: " + str(exc))
        result["verification"] = {"passed": result["success"] and not errors, "errors": errors}
        if errors:
            result.update(success=False, error="library component properties verification failed", may_have_partial_changes=bool(requested))
        return json.dumps(result, indent=2, ensure_ascii=False)
    finally:
        if completed:
            request.unlink(missing_ok=True)
