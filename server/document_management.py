"""Exact-path document lifecycle validation and bridge transport."""

import ctypes
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import uuid

from altium_files import write_altium_text


KINDS = {".schdoc": "SCH", ".pcbdoc": "PCB", ".schlib": "SCHLIB", ".pcblib": "PCBLIB"}
PROJECT_EXTENSIONS = {".prjpcb", ".libpkg"}


def absolute_path(value, extensions=None, require_parent=True):
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
        raise ValueError("path must be a nonempty string without control characters")
    win = PureWindowsPath(value)
    if not win.is_absolute() or value.startswith(("\\\\?\\", "\\\\.\\")):
        raise ValueError("path must be an absolute Windows file path")
    for part in win.parts[1:]:
        if (part in (".", "..") or part.endswith((".", " ")) or
                any(c in '<>:"|?*' for c in part) or
                re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)):
            raise ValueError("path contains an unsafe Windows filename")
    path = Path(value).resolve()
    if extensions is not None and path.suffix.lower() not in extensions:
        raise ValueError("unsupported extension; expected " + ", ".join(sorted(extensions)))
    if require_parent and not path.parent.is_dir():
        raise ValueError("parent directory must already exist")
    if path.exists() and not path.is_file():
        raise ValueError("path must identify a file")
    return path


def writable_local_path(path):
    win = PureWindowsPath(str(path))
    if win.drive.startswith("\\\\") or (os.name == "nt" and ctypes.windll.kernel32.GetDriveTypeW(win.anchor) == 4):
        raise ValueError("creation and saving require a local path; network writes are unsupported")
    if path.exists() and not path.stat().st_mode & stat.S_IWRITE:
        raise ValueError("target file is read-only")


def prepare(action, document_path, document_kind=None, project_path=None, dry_run=False):
    path = absolute_path(document_path, KINDS)
    kind = KINDS[path.suffix.lower()]
    spec = {"document_path": str(path), "document_kind": kind}
    if action == "open_document":
        pass  # Native code checks existence only when the exact file is not open.
    elif action == "create_document":
        if document_kind is not None and (not isinstance(document_kind, str) or document_kind.upper() != kind):
            raise ValueError("document_kind must match the filename extension (SCH, PCB, SCHLIB or PCBLIB)")
        writable_local_path(path)
        if path.exists():
            raise ValueError("create_document refuses an existing file")
    elif action == "save_document":
        if type(dry_run) is not bool:
            raise ValueError("dry_run must be a boolean")
        writable_local_path(path)
        spec["dry_run"] = "1" if dry_run else "0"
    elif action == "add_document_to_project":
        project = absolute_path(project_path, PROJECT_EXTENSIONS)
        if not project.is_file():
            raise ValueError("project_path must identify an existing saved project")
        if project.suffix.lower() == ".libpkg" and kind not in ("SCHLIB", "PCBLIB"):
            raise ValueError("LibPkg targets support library documents only")
        spec["project_path"] = str(project)
    else:
        raise ValueError("unknown document operation")
    return spec


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


async def execute(bridge, exchange_dir, action, document_path, **options):
    """Keep timed-out specs: a delayed Altium invocation may still consume them."""
    try:
        spec = prepare(action, document_path, **options)
    except (ValueError, TypeError, OSError) as exc:
        return json.dumps({"success": False, "error": str(exc), "saved": False,
                           "may_have_partial_changes": False})
    path = Path(spec["document_path"])
    is_save = action == "save_document" and spec["dry_run"] == "0"
    before = digest(path) if is_save else None
    spec_path = Path(exchange_dir) / f"document_{uuid.uuid4().hex}.txt"
    write_altium_text(spec_path, "\n".join(f"{key}={json.dumps(value, ensure_ascii=False)[1:-1]}"
                                         for key, value in spec.items()) + "\n")
    response = None
    try:
        response = await bridge.execute_command(action, {"spec_file": str(spec_path)})
        if not response.get("success"):
            return json.dumps({"success": False, "error": response.get("error", "bridge failed"),
                               "saved": False, "save_outcome_unknown": is_save,
                               "may_have_partial_changes": spec.get("dry_run") != "1",
                               "spec_file": str(spec_path)})
        result = response.get("result", {})
        if isinstance(result, str):
            result = json.loads(result)
        else:
            result = dict(result)
        if is_save:
            after = digest(path)
            result["disk"] = {"sha256_before": before, "sha256_after": after,
                              "exists": path.is_file(), "size_bytes": path.stat().st_size if path.is_file() else None}
            if result.get("success") and (not result.get("saved") or not after or not path.stat().st_size or result.get("is_modified")):
                result.update(success=False, error="save readback failed", may_have_partial_changes=True)
        if action == "open_document" and result.get("success"):
            errors = []
            if PureWindowsPath(result.get("document_path", "")) != PureWindowsPath(str(path)) or result.get("document_kind") != spec["document_kind"]:
                errors.append("opened document identity mismatch")
            if result.get("focused") is not True or PureWindowsPath(result.get("active_document_path", "")) != PureWindowsPath(str(path)):
                errors.append("active editor does not match the requested document")
            if result.get("saved") is not False:
                errors.append("opening must not save")
            if type(result.get("already_open")) is not bool or result.get("opened") is not (not result.get("already_open")):
                errors.append("document open/reuse readback is incomplete")
            if result.get("already_open") and result.get("was_modified") != result.get("is_modified"):
                errors.append("opening changed Modified on an existing document")
            result["verification"] = {"passed": not errors, "errors": errors}
            if errors:
                result.update(success=False, error="document open readback failed", may_have_partial_changes=True)
        return json.dumps(result, indent=2, ensure_ascii=False)
    finally:
        if response and response.get("success"):
            spec_path.unlink(missing_ok=True)
