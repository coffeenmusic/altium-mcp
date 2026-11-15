"""Exact-path opening and selection of Altium projects, without document edits."""

import json
from pathlib import PureWindowsPath
import uuid

from altium_files import write_altium_text
from document_management import absolute_path, PROJECT_EXTENSIONS


def prepare(project_path):
    path = absolute_path(project_path, PROJECT_EXTENSIONS)
    return str(path)


def verify(result, project_path, opening=False):
    errors = []
    active = result.get("active_project_path")
    if not isinstance(active, str) or PureWindowsPath(active) != PureWindowsPath(project_path):
        errors.append("focused project does not match the requested path")
    if result.get("saved") is not False or result.get("project_saved") is not False:
        errors.append("selection must not save documents or the project")
    if not opening or result.get("already_open"):
        if (result.get("documents_unchanged") is not True or not isinstance(result.get("documents_before"), list)
                or not isinstance(result.get("documents_after"), list)
                or result["documents_before"] != result["documents_after"]):
            errors.append("project document open/modified states changed")
    if opening:
        if type(result.get("already_open")) is not bool or result.get("opened") is not (not result.get("already_open")):
            errors.append("project open/reuse readback is incomplete")
        if (result.get("preexisting_documents_preserved") is not True
                or not isinstance(result.get("preexisting_documents_before"), list)
                or result.get("preexisting_documents_before") != result.get("preexisting_documents_after")):
            errors.append("preexisting document open/modified states changed")
        if result.get("compiled") is not False:
            errors.append("opening must not request compilation")
    result["verification"] = {"passed": not errors, "errors": errors}
    if errors:
        result.update(success=False, error="project selection readback failed", may_have_changed_focus=True)
    return result


async def execute(bridge, exchange_dir, project_path, open_if_closed=False):
    try:
        path = prepare(project_path)
    except (ValueError, TypeError, OSError) as exc:
        return json.dumps({"success": False, "error": str(exc), "saved": False, "project_saved": False,
                           "may_have_changed_focus": False})
    command = "open_project" if open_if_closed else "select_project"
    spec_path = exchange_dir / f"{command}_{uuid.uuid4().hex}.txt"
    write_altium_text(spec_path, "project_path=" + json.dumps(path, ensure_ascii=False)[1:-1] + "\n")
    response = None
    try:
        response = await bridge.execute_command(command, {"spec_file": str(spec_path)})
        if not response.get("success"):
            return json.dumps({"success": False, "error": response.get("error", "bridge failed"),
                               "saved": False, "project_saved": False, "may_have_changed_focus": True,
                               "may_have_opened_project": open_if_closed,
                               "spec_file": str(spec_path)})
        result = response.get("result", {})
        if isinstance(result, str):
            result = json.loads(result)
        if result.get("success"):
            verify(result, path, opening=open_if_closed)
        return json.dumps(result, indent=2, ensure_ascii=False)
    finally:
        if response and response.get("success"):
            spec_path.unlink(missing_ok=True)
