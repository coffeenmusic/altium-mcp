"""Exact library navigation with content preservation and explicit view limits."""

import json
from pathlib import PureWindowsPath
import uuid

from altium_files import write_altium_text
from document_management import absolute_path
from library_component_data import text, parse_pin_map
from schematic_document import disk_state

CAMERAS = {"top": "Top", "bottom": "Bottom", "front": "Front", "back": "Back",
           "left": "Left", "right": "Right", "isometric": "RotateOrtho",
           "flipped_isometric": "ISOMETRIC"}


def prepare(library_path, component_name=None, part_id=None, display_mode=None,
            fit=True, view_mode=None, camera=None):
    path = absolute_path(library_path, {".schlib", ".pcblib"})
    spec = {"library_path": str(path), "fit": int(fit)}
    if type(fit) is not bool:
        raise ValueError("fit must be a boolean")
    if component_name is not None:
        text(component_name, "component_name", True)
        if len(component_name) > 255 or any(ord(c) < 32 for c in component_name):
            raise ValueError("component_name needs at most 255 characters without controls")
        spec["component_name"] = component_name
    for key, value, low in (("part_id", part_id, 1), ("display_mode", display_mode, 0)):
        if value is not None:
            if type(value) is not int or not low <= value <= 255:
                raise ValueError(f"{key} must be an integer within {low}..255")
            if path.suffix.lower() != ".schlib":
                raise ValueError(key + " applies only to SchLib")
            spec[key] = value
    if view_mode is not None:
        if not isinstance(view_mode, str) or view_mode.casefold() not in {"2d", "3d"}:
            raise ValueError("view_mode must be 2d or 3d")
        view_mode = view_mode.casefold()
    if camera is not None:
        if not isinstance(camera, str) or camera.casefold() not in CAMERAS:
            raise ValueError("camera must be one of " + ", ".join(CAMERAS))
        if path.suffix.lower() != ".pcblib" or view_mode == "2d":
            raise ValueError("camera requires a PcbLib in 3d mode")
        spec["camera"] = camera.casefold()
        spec["camera_to"] = CAMERAS[spec["camera"]]
        view_mode = "3d"
    if view_mode == "3d" and path.suffix.lower() != ".pcblib":
        raise ValueError("3d mode applies only to PcbLib")
    if view_mode is not None:
        spec["view_mode"] = view_mode
    return spec


def actions(spec):
    pcb = PureWindowsPath(spec["library_path"]).suffix.lower() == ".pcblib"
    result = []
    if pcb and "view_mode" in spec:
        result.append({"command": "PCB:SwitchTo" + spec["view_mode"].upper(), "parameters": ""})
    if spec["fit"]:
        if pcb and ("camera_to" in spec or spec.get("view_mode") == "3d"):
            result.append({"command": "IPCB_Board.GraphicalView_ZoomOnRect", "parameters": "physical_bounds"})
        else:
            result.append({"command": "PCB:Zoom" if pcb else "SCH:Zoom", "parameters": "Action=All"})
    if "camera_to" in spec:
        result.append({"command": "PCB:ResetCamera", "parameters": "To=" + spec["camera_to"]})
    return result


def canonical(value, key=""):
    """Ignore iteration order/temporary handles, keeping ordered vertex geometry."""
    if key == "map_as_string" and isinstance(value, str):
        try:
            return canonical(parse_pin_map(value))
        except ValueError:
            return value  # Preserve strict comparison for unrecognized map syntax.
    if isinstance(value, dict):
        result = {k: canonical(v, k) for k, v in value.items() if k != "_id"}
        if result.get("type") == "track":
            a, b = sorted(((result["x1"], result["y1"]), (result["x2"], result["y2"])))
            result.update(x1=a[0], y1=a[1], x2=b[0], y2=b[1])
        return result
    if isinstance(value, list):
        rows = [canonical(row) for row in value]
        return rows if key in {"vertices"} else sorted(rows, key=lambda row: json.dumps(row, sort_keys=True))
    return value


def verify(result, spec):
    errors = []
    if PureWindowsPath(result.get("library_path", "")) != PureWindowsPath(spec["library_path"]):
        errors.append("library identity mismatch")
    selected = result.get("selected", {})
    if not isinstance(selected, dict) or not selected.get("component_name"):
        errors.append("missing native selection readback")
        selected = {}
    if "component_name" in spec and str(selected.get("component_name", "")).casefold() != spec["component_name"].casefold():
        errors.append("component selection mismatch")
    for key in ("part_id", "display_mode"):
        if key in spec and selected.get(key) != spec[key]:
            errors.append(key + " selection mismatch")
    if result.get("actions") != actions(spec):
        errors.append("view command dispatch mismatch")
    if result.get("saved") is not False:
        errors.append("view navigation must not save")
    if (type(result.get("was_modified")) is not bool or type(result.get("is_modified")) is not bool
            or result["was_modified"] != result["is_modified"]):
        errors.append("view navigation changed Modified")
    for key in ("inventory", "content"):
        before, after = result.get(key + "_before"), result.get(key + "_after")
        if before is None or after is None or canonical(before) != canonical(after):
            errors.append(key + " changed or is missing")
    return errors


async def execute(bridge, exchange_dir, library_path, component_name=None, part_id=None,
                  display_mode=None, fit=True, view_mode=None, camera=None):
    try:
        spec = prepare(library_path, component_name, part_id, display_mode, fit, view_mode, camera)
        before_disk = disk_state(spec["library_path"])
        request = exchange_dir / f"library_view_{uuid.uuid4().hex}.txt"
        write_altium_text(request, "\n".join(f"{k}={json.dumps(str(v), ensure_ascii=False)[1:-1]}"
                                            for k, v in spec.items()) + "\n")
    except (ValueError, TypeError, OSError) as exc:
        return json.dumps({"success": False, "error": str(exc), "saved": False, "may_have_view_changes": False})
    completed = False
    try:
        try:
            response = await bridge.execute_command("set_library_view", {"spec_file": str(request)})
        except Exception as exc:
            response = {"error": str(exc)}
        if response.get("command_started") is False:
            completed = True
            return json.dumps({"success": False, "error": response.get("error"), "saved": False,
                               "may_have_view_changes": False})
        result = response.get("result")
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except ValueError:
                result = None
        completed = isinstance(result, dict) and type(result.get("success")) is bool
        if not completed:
            return json.dumps({"success": False, "error": response.get("error", "invalid native reply"),
                               "saved": False, "outcome_unknown": True, "may_have_view_changes": True,
                               "spec_file": str(request)})
        errors = verify(result, spec) if result["success"] else []
        try:
            if disk_state(spec["library_path"]) != before_disk:
                errors.append("library file changed unexpectedly")
        except OSError as exc:
            errors.append("cannot verify library file: " + str(exc))
        result["verification"] = {"passed": result["success"] and not errors, "errors": errors,
                                  "scope": ["native_selection", "content", "inventory", "Modified", "disk"],
                                  "fit_mode_camera": "dispatch_only"}
        if errors:
            result.update(success=False, error="library view verification failed", may_have_partial_changes=True)
        for key in ("inventory_before", "inventory_after", "content_before", "content_after"):
            result.pop(key, None)
        return json.dumps(result, indent=2, ensure_ascii=False)
    finally:
        if completed:
            request.unlink(missing_ok=True)
