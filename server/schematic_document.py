"""Exact-target sheet options, view commands and project-only save validation."""

import configparser
import hashlib
import json
from pathlib import Path, PureWindowsPath
import uuid

from altium_files import read_altium_text, write_altium_text
from document_management import absolute_path, writable_local_path


SHEET_SIZES = {"A0", "A1", "A2", "A3", "A4"}


def prepare_sheet(sheet_path, sheet_size=None, mode=None):
    path = absolute_path(sheet_path, {".schdoc"})
    spec = {"sheet_path": str(path)}
    if sheet_size is not None:
        if not isinstance(sheet_size, str) or sheet_size.upper() not in SHEET_SIZES:
            raise ValueError("sheet_size must be A0, A1, A2, A3 or A4")
        spec["sheet_size"] = sheet_size.upper()
    if mode is not None:
        if mode not in ("all", "sheet"):
            raise ValueError("mode must be all (objects) or sheet (page)")
        spec["mode"] = mode
    return spec


def prepare_project(project_path, dry_run=False):
    if type(dry_run) is not bool:
        raise ValueError("dry_run must be a boolean")
    path = absolute_path(project_path, {".prjpcb", ".libpkg"})
    if not path.is_file():
        raise ValueError("project_path must identify an existing saved project")
    writable_local_path(path)
    return {"project_path": str(path), "dry_run": "1" if dry_run else "0"}


def disk_state(path):
    path = Path(path)
    if not path.is_file():
        return None
    return (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)


def project_documents(path):
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read_string(read_altium_text(path))
    return {str((Path(path).parent / parser[section]["DocumentPath"]).resolve()).casefold()
            for section in parser.sections() if section.lower().startswith("document")
            and "DocumentPath" in parser[section]}


async def execute(bridge, exchange_dir, command, spec):
    project = command == "save_project"
    dry_run = spec.get("dry_run") == "1"
    mutating = (project and not dry_run) or (command == "set_schematic_sheet_properties" and "sheet_size" in spec)
    request = exchange_dir / f"{command}_{uuid.uuid4().hex}.txt"
    target = spec["project_path" if project else "sheet_path"]
    target_before = disk_state(target)
    # Guard every saved project member against accidental source saves.
    source_before = {}
    if project:
        source_before = {path: disk_state(path) for path in project_documents(target)}
        if not dry_run:
            # Include newly added model members absent from the saved project.
            # The native writer rechecks this exact set before saving.
            checked = json.loads(await execute(bridge, exchange_dir, command, {**spec, "dry_run": "1"}))
            if not checked.get("success"):
                return json.dumps(checked)
            paths = checked.get("source_paths")
            if not isinstance(paths, list) or any(not isinstance(p, str) for p in paths):
                return json.dumps({"success": False, "error": "invalid project preflight source list", "saved": False,
                                   "project_saved": False, "may_have_partial_changes": False})
            source_before.update({path: disk_state(path) for path in paths})
            spec = {**spec, "expected_source_count": str(len(paths)),
                    **{f"expected_source.{i}": path for i, path in enumerate(paths)}}
    write_altium_text(request, "\n".join(f"{key}={json.dumps(value, ensure_ascii=False)[1:-1]}"
                                       for key, value in spec.items()) + "\n")
    completed = False
    try:
        try:
            response = await bridge.execute_command(command, {"spec_file": str(request)})
        except Exception as exc:
            response = {"error": str(exc)}
        if response.get("command_started") is False:
            completed = True
            return json.dumps({"success": False, "error": response.get("error"), "saved": False,
                               "project_saved": False, "may_have_partial_changes": False})
        result = response.get("result", {})
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except ValueError:
                result = {}
        completed = isinstance(result, dict) and type(result.get("success")) is bool
        if not completed:
            return json.dumps({"success": False, "error": response.get("error", "invalid bridge result"),
                               "saved": False, "project_saved": False, "outcome_unknown": True,
                               "may_have_partial_changes": mutating, "spec_file": str(request)})
        errors = []
        actual = result.get("project_path" if project else "sheet_path")
        if result.get("success"):
            if not isinstance(actual, str) or PureWindowsPath(actual) != PureWindowsPath(target):
                errors.append("target identity mismatch")
            if result.get("saved") is not False:
                errors.append("source documents must not be saved")
            if project:
                if result.get("project_saved") is not (not dry_run):
                    errors.append("project save result mismatch")
                if not dry_run:
                    try:
                        paths = result.get("source_paths")
                        if not isinstance(paths, list) or any(not isinstance(p, str) for p in paths):
                            raise ValueError("invalid live source path list")
                        actual_members = project_documents(target)
                        expected_members = {str(Path(p).resolve()).casefold() for p in paths}
                        if actual_members != expected_members:
                            errors.append("saved project membership does not match the live project")
                    except (OSError, ValueError, TypeError, configparser.Error) as exc:
                        errors.append("cannot verify saved project membership: " + str(exc))
                if result.get("source_states_unchanged") is not True:
                    errors.append("source buffer states changed")
            elif command == "zoom_schematic":
                if result.get("was_modified") != result.get("is_modified") or result.get("zoom_requested") is not True:
                    errors.append("zoom changed Modified or was not dispatched")
            elif "sheet_size" in spec:
                if result.get("sheet_size") != spec["sheet_size"]:
                    errors.append("sheet size readback mismatch")
                if result.get("changed") and result.get("is_modified") is not True:
                    errors.append("changed sheet is not marked Modified")
        if (not project or dry_run) and disk_state(target) != target_before:
            errors.append("operation unexpectedly changed the target file")
        if project:
            try:
                source_after = {path: disk_state(path) for path in source_before}
                result["source_files_unchanged"] = source_before == source_after
                if source_before != source_after:
                    errors.append("source document files changed")
            except OSError as exc:
                result["source_files_unchanged"] = False
                errors.append("cannot verify source files: " + str(exc))
        result["verification"] = {"passed": not errors, "errors": errors}
        if errors:
            result.update(success=False, error="schematic/project verification failed", may_have_partial_changes=True)
        return json.dumps(result, indent=2, ensure_ascii=False)
    finally:
        if completed:
            request.unlink(missing_ok=True)
