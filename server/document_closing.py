"""Exact-path close requests, explicit discard policy and readback checks."""

import json
from pathlib import PureWindowsPath
import uuid

from altium_files import write_altium_text
from document_management import absolute_path


SCRIPT_EXTENSIONS = {".prjscr", ".pas", ".dfm", ".vbs", ".js"}


def prepare(document_path, require_clean=True):
    if type(require_clean) is not bool:
        raise ValueError("require_clean must be a boolean")
    path = absolute_path(document_path, require_parent=False)
    if path.suffix.lower() in SCRIPT_EXTENSIONS:
        raise ValueError("closing script projects or script documents is unsupported")
    return {"document_path": str(path), "require_clean": "1" if require_clean else "0"}


def verify(result, path, require_clean):
    errors = []
    actual_path = result.get("document_path")
    if not isinstance(actual_path, str) or PureWindowsPath(actual_path) != PureWindowsPath(path):
        errors.append("closed target identity mismatch")
    if result.get("closed") is not True or type(result.get("already_closed")) is not bool:
        errors.append("target closure was not confirmed")
    if result.get("saved") is not False or result.get("project_saved") is not False:
        errors.append("closing must not save")
    if result.get("unrelated_documents_preserved") is not True:
        errors.append("unrelated document states changed")
    if result.get("remaining_open_paths") != []:
        errors.append("some affected documents remain open")
    if require_clean and result.get("discarded_paths") != []:
        errors.append("clean close reported discarded changes")
    result["verification"] = {"passed": not errors, "errors": errors}
    if errors:
        result.update(success=False, error="document close readback failed", may_have_partial_changes=True)
    return result


async def execute(bridge, exchange_dir, document_path, require_clean=True):
    try:
        spec = prepare(document_path, require_clean)
    except (ValueError, TypeError, OSError) as exc:
        return json.dumps({"success": False, "error": str(exc), "saved": False,
                           "project_saved": False, "may_have_partial_changes": False})
    spec_path = exchange_dir / f"close_document_{uuid.uuid4().hex}.txt"
    write_altium_text(spec_path, "\n".join(f"{key}={json.dumps(value, ensure_ascii=False)[1:-1]}"
                                         for key, value in spec.items()) + "\n")
    response = None
    completed = False
    try:
        try:
            response = await bridge.execute_command("close_document", {"spec_file": str(spec_path)})
        except Exception as exc:
            response = {"success": False, "error": str(exc)}
        result = response.get("result", {})
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                result = {}
        completed = isinstance(result, dict) and type(result.get("success")) is bool
        # The bridge lifts native success=false to its outer envelope while
        # retaining result. A known clean-only refusal is not a transport timeout.
        if not completed:
            return json.dumps({"success": False, "error": response.get("error", "bridge failed"),
                               "saved": False, "project_saved": False, "close_outcome_unknown": True,
                               "may_have_partial_changes": True, "spec_file": str(spec_path)})
        if result.get("success"):
            verify(result, spec["document_path"], require_clean)
        return json.dumps(result, indent=2, ensure_ascii=False)
    finally:
        if completed:
            spec_path.unlink(missing_ok=True)
