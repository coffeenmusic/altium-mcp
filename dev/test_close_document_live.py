"""Opt-in close tests: never save originals; discard only unique scratch buffers."""

import argparse
import asyncio
import hashlib
import json
import logging
from pathlib import Path, PureWindowsPath
import shutil
import sys
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "server"))


def disk_state(path):
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"sha256": digest, "mtime_ns": path.stat().st_mtime_ns}


def states(info):
    return {d["path"]: (d["kind"], d["state"]) for d in info["documents"]}


async def run(args):
    import main
    logging.getLogger().setLevel(logging.WARNING)
    root = REPO / ".altium-mcp" / f"close_document_{time.strftime('%Y%m%d_%H%M%S')}"
    root.mkdir(parents=True)
    report = {"root": str(root), "steps": [], "sheets_saved": False, "projects_saved": False}
    original = str(Path(args.project).resolve())
    previous_document = None
    scratch_paths = []
    scratch_projects = []
    before = None
    inputs = []

    def save(label, value):
        (root / f"{label}.json").write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n", encoding="ascii")

    async def call(label, function, *values, **options):
        result = json.loads(await function(None, *map(str, values), **options))
        save(label, result)
        report["steps"].append({"label": label, **{key: result.get(key) for key in
            ("success", "closed", "already_closed", "changed", "saved", "may_have_partial_changes", "error")}})
        print(json.dumps(report["steps"][-1]), flush=True)
        return result

    def require(result):
        assert result.get("success"), result
        assert result.get("saved", False) is False and result.get("project_saved", False) is False
        return result

    async def close(label, path, require_clean=True):
        result = require(await call(label, main.close_document, path, require_clean=require_clean))
        assert result["verification"]["passed"] and result["closed"]
        return result

    async def copy_source(kind, name):
        source = next(Path(d["path"]) for d in before["documents"] if d["kind"].upper() == kind)
        dest = root / name
        shutil.copy2(source, dest)
        scratch_paths.append(dest)
        report.setdefault("scratch_disk_before", {})[str(dest)] = disk_state(dest)
        return dest

    if not args.exercise:
        print("Pass --exercise to close scratch documents/projects and test clean-only refusals on originals.")
        return
    try:
        require(await call("select_original", main.select_project, original))
        before = await call("original_before", main.get_project_info)
        assert PureWindowsPath(before["project_path"]) == PureWindowsPath(original), before
        inputs = [p for p in Path(original).parent.iterdir() if p.is_file() and p.suffix.lower() in
                  {".prjpcb", ".libpkg", ".schdoc", ".pcbdoc", ".schlib", ".pcblib", ".outjob", ".pcbdwf", ".bomdoc"}]
        report["original_disk_before"] = {str(p): disk_state(p) for p in inputs}
        sheet = next(d["path"] for d in before["documents"] if d["kind"] == "SCH" and d["state"] == "open")
        # Refuse actual existing unsaved data without closing any original buffer.
        dirty_original = [d["path"] for d in before["documents"] if d["state"] == "modified"]
        for index, path in enumerate(dirty_original):
            refused = await call(f"original_dirty_{index}", main.close_document, path)
            assert refused["success"] is False and refused["closed_paths"] == [] and refused["may_have_partial_changes"] is False
        if dirty_original:
            refused = await call("original_project_dirty_child", main.close_document, original)
            assert refused["success"] is False and refused["closed_paths"] == [] and refused["may_have_partial_changes"] is False
            assert any(PureWindowsPath(p) == PureWindowsPath(dirty_original[0]) for p in refused["blocked_paths"])
        for kind, extension in (("SCH", "SchDoc"), ("PCB", "PcbDoc"), ("SCHLIB", "SchLib"), ("PCBLIB", "PcbLib")):
            path = await copy_source(kind, f"Copy_{kind}_\u03a9_\u0416.{extension}")
            opened = require(await call("open_" + kind, main.open_document, path))
            if previous_document is None:
                previous_document = opened.get("previous_document_path")
            require(await call("focus_original_" + kind, main.open_document, sheet))
            result = await close("close_nonactive_" + kind, str(path).upper().replace("\\", "/"))
            assert result["changed"] and result["discarded_paths"] == []
            assert (await close("repeat_" + kind, path))["already_closed"]
        unsaved = root / "Unsaved.SchDoc"
        scratch_paths.append(unsaved)
        require(await call("create_unsaved", main.create_document, unsaved))
        await close("close_unsaved_blank", unsaved)
        assert not unsaved.exists()
        dirty = await copy_source("SCH", "Dirty.SchDoc")
        require(await call("open_dirty", main.open_document, dirty))
        require(await call("edit_dirty", main.edit_schematic, dirty, operations=[
            {"op": "add", "type": "text", "properties": {"x": 100, "y": 100, "text": "UNSAVED CLOSE TEST"}}]))
        refused = await call("refuse_dirty", main.close_document, dirty)
        assert not refused["success"] and not refused["closed_paths"] and refused["may_have_partial_changes"] is False
        discarded = await close("discard_dirty", dirty, False)
        assert discarded["discarded_paths"] == [str(dirty)]
        for extension in ("PrjPcb", "LibPkg"):
            project = root / f"Empty_\u03a9_\u0416.{extension}"
            project.write_text("[Design]\nVersion=1.0\n", encoding="ascii")
            scratch_projects.append(project)
            report["scratch_disk_before"][str(project)] = disk_state(project)
            require(await call("open_empty_" + extension, main.open_project, project))
            require(await call("select_original_" + extension, main.select_project, original))
            await close("close_empty_nonactive_" + extension, project)
            assert (await close("repeat_project_" + extension, project))["already_closed"]
            require(await call("reopen_for_membership_" + extension, main.open_project, project))
            library = next(p for p in scratch_paths if p.suffix.lower() == ".schlib")
            require(await call("dirty_membership_" + extension, main.add_document_to_project, project, library))
            refused = await call("refuse_modified_project_" + extension, main.close_document, project)
            assert not refused["success"] and not refused["closed_paths"] and refused["was_modified"]
            result = await close("discard_membership_" + extension, project, False)
            assert str(project) in result["discarded_paths"]
        clean_child = await copy_source("SCH", "ChildClean.SchDoc")
        dirty_child = await copy_source("SCH", "ChildDirty.SchDoc")
        project = root / "WithChildren.PrjPcb"
        project.write_text("[Design]\nVersion=1.0\n[Document1]\nDocumentPath=ChildClean.SchDoc\n[Document2]\nDocumentPath=ChildDirty.SchDoc\n", encoding="ascii")
        scratch_projects.append(project)
        report["scratch_disk_before"][str(project)] = disk_state(project)
        require(await call("open_children_project", main.open_project, project))
        require(await call("open_clean_child", main.open_document, clean_child))
        require(await call("open_dirty_child", main.open_document, dirty_child))
        require(await call("edit_dirty_child", main.edit_schematic, dirty_child, operations=[
            {"op": "add", "type": "text", "properties": {"x": 200, "y": 200, "text": "UNSAVED CHILD CLOSE TEST"}}]))
        refused = await call("refuse_project_dirty_child", main.close_document, project)
        assert not refused["success"] and refused["closed_paths"] == []
        assert require(await call("clean_child_still_open", main.open_document, clean_child))["already_open"]
        assert require(await call("dirty_child_still_open", main.open_document, dirty_child))["is_modified"]
        result = await close("discard_project_children", project, False)
        assert len(result["closed_paths"]) == 3 and str(dirty_child) in result["discarded_paths"]
        assert (await close("missing_path", root / "Deleted" / "Missing.SchDoc"))["already_closed"]
        assert not (await call("invalid_policy", main.close_document, dirty, require_clean=1))["success"]
        assert not (await call("reject_script", main.close_document, root / "Altium_API.PrjScr"))["success"]
        report["close_checks_passed"] = True
    finally:
        # Only unique scratch paths may be force-closed, even after a failed check.
        for index, path in enumerate(scratch_paths + scratch_projects):
            if not path.is_relative_to(root):
                raise AssertionError("cleanup escaped the scratch directory")
            await close(f"cleanup_{index}", path, False)
        if before:
            require(await call("restore_original_project", main.select_project, original))
            if previous_document and PureWindowsPath(previous_document).suffix.lower() in main.document_management.KINDS:
                require(await call("restore_original_editor", main.open_document, previous_document))
                require(await call("restore_project_after_editor", main.select_project, original))
            after = await call("original_after", main.get_project_info)
            report["original_document_states_unchanged"] = states(before) == states(after)
            report["original_disk_after"] = {str(p): disk_state(p) for p in inputs}
            report["original_disk_unchanged"] = report["original_disk_before"] == report["original_disk_after"]
        report["scratch_disk_after"] = {p: disk_state(Path(p)) for p in report.get("scratch_disk_before", {})}
        report["scratch_disk_unchanged"] = report.get("scratch_disk_before", {}) == report["scratch_disk_after"]
        save("report", report)
        print("REPORT " + str(root / "report.json"), flush=True)
    assert report["close_checks_passed"] and report["original_document_states_unchanged"]
    assert report["original_disk_unchanged"] and report["scratch_disk_unchanged"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="Exact existing open project; no original is closed")
    parser.add_argument("--exercise", action="store_true")
    asyncio.run(run(parser.parse_args()))
