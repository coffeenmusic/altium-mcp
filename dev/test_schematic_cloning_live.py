"""Opt-in cross-sheet cloning checks. Never save sheets or edit original targets."""

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
    if not path.is_file():
        return {"exists": False}
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"exists": True, "sha256": digest, "mtime_ns": path.stat().st_mtime_ns}


def document_states(info):
    return {d["path"]: (d["kind"], d["state"]) for d in info["documents"]}


async def run(args):
    import main
    logging.getLogger().setLevel(logging.WARNING)
    root = REPO / ".altium-mcp" / f"schematic_cloning_{time.strftime('%Y%m%d_%H%M%S')}"
    root.mkdir(parents=True)
    report = {"steps": [], "root": str(root), "sheets_saved": False}
    scratch = []
    project = str(Path(args.project).resolve())
    original = None
    previous_editor = None
    originals = []
    copied = []

    def save(label, value):
        (root / (label + ".json")).write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n", encoding="ascii")

    async def call(label, function, *values, **options):
        result = json.loads(await function(None, *map(str, values), **options))
        if result.get("clone_outcome_unknown") or result.get("close_outcome_unknown"):
            report["executor_outcome_unknown"] = True
        save(label, result)
        report["steps"].append({"label": label, **{k: result.get(k) for k in
            ("success", "cloned_count", "saved", "source_unchanged", "may_have_partial_changes", "error")}})
        print(json.dumps(report["steps"][-1]), flush=True)
        return result

    def require(result):
        assert result.get("success"), result
        assert result.get("saved", False) is False
        return result

    async def snapshot(label, path):
        return require(await call(label, main.edit_schematic, path, operations=[]))

    async def clone(label, source, target, names):
        result = require(await call(label, main.clone_schematic_components, source, target, designators=names))
        assert result["verification"]["passed"] and result["source_unchanged"]
        return result

    def make_copy(source, name):
        path = root / name
        shutil.copy2(source, path)
        copied.append(path)
        scratch.append(path)
        return path

    if not args.exercise:
        print("Pass --exercise to clone into unique scratch documents; originals are read-only sources.")
        return
    try:
        require(await call("select_original", main.select_project, project))
        original = await call("original_before", main.get_project_info)
        assert PureWindowsPath(original["project_path"]) == PureWindowsPath(project)
        originals = [Path(d["path"]) for d in original["documents"]]
        report["original_disk_before"] = {str(p): disk_state(p) for p in originals}
        save("original_disk_before", report["original_disk_before"])
        source_original = Path(args.source).resolve() if args.source else next(Path(d["path"]) for d in original["documents"] if d["kind"] == "SCH" and d["state"] == "open")
        opened = require(await call("focus_source", main.open_document, source_original))
        previous_editor = opened.get("previous_document_path")
        source_baseline = await snapshot("original_source_before", source_original)
        source = make_copy(source_original, "Source_\u03a9_\u0416.SchDoc")
        target = make_copy(source_original, "Target_\u03a9_\u0416.SchDoc")
        report["scratch_disk_before"] = {str(p): disk_state(p) for p in copied}
        save("scratch_disk_before", report["scratch_disk_before"])
        source_before = await snapshot("scratch_source_before", source)
        components = [o for o in source_before["objects"] if o["type"] == "component"]
        names = list(dict.fromkeys(o["designator"] for o in components))[:2]
        assert len(names) == 2
        target_before = await snapshot("scratch_target_before", target)
        # Remove only components; existing wires/labels remain as preservation checks.
        require(await call("clear_target_components", main.edit_schematic, target, operations=[
            {"op": "delete", "type": "component", "target": {"unique_id": o["unique_id"]}}
            for o in target_before["objects"] if o["type"] == "component"]))
        empty_target = await snapshot("target_with_existing_objects", target)
        text = "copy | = , \"' \u03a9 \u0416\nsecond line"
        require(await call("edit_source_unsaved", main.edit_schematic, source, operations=[
            {"op": "update", "type": "component", "target": {"designator": names[0]},
             "properties": {"comment": text, "orientation": 1, "mirror": True, "parameters": {"CLONE_TEST": text}}}]))
        dirty_source_before = await snapshot("dirty_source_before", source)
        # A valid component followed by a missing one must cause no partial copy.
        refused = await call("missing_in_batch", main.clone_schematic_components, source, target, designators=[names[0], "MCP_MISSING_99999"])
        assert not refused["success"] and refused["cloned_count"] == 0 and not refused["may_have_partial_changes"]
        assert (await snapshot("target_after_refusal", target))["objects"] == empty_target["objects"]
        result = await clone("clone_dirty_source", source, str(target).upper().replace("\\", "/"), [n.lower() for n in names])
        assert result["cloned_count"] == 2 and result["source_was_modified"] and result["target_was_modified"]
        first = next(c for c in result["components"] if c["component"]["state"]["designator"].lower() == names[0].lower())
        assert first["component"]["state"]["metadata"]["comment"] == text
        assert first["component"]["state"]["metadata"]["parameters"]["CLONE_TEST"] == text
        target_after = await snapshot("target_after_clone", target)
        new_ids = {row["unique_id"] for item in result["components"] for row in item["uid_map"]}
        assert [o for o in target_after["objects"] if o["unique_id"] not in new_ids] == empty_target["objects"]
        assert (await snapshot("dirty_source_after", source))["objects"] == dirty_source_before["objects"]
        repeated = await call("target_collision", main.clone_schematic_components, source, target, designators=names)
        assert not repeated["success"] and repeated["cloned_count"] == 0 and not repeated["may_have_partial_changes"]
        assert (await snapshot("target_after_collision", target))["objects"] == target_after["objects"]
        unsaved = root / "UnsavedTarget.SchDoc"
        scratch.append(unsaved)
        require(await call("create_unsaved_target", main.create_document, unsaved))
        result = await clone("clone_to_unsaved_target", source_original, unsaved, names)
        assert result["cloned_count"] == 2 and not unsaved.exists()
        same = await call("reject_same_sheet", main.clone_schematic_components, source_original, str(source_original).upper(), designators=names)
        assert not same["success"] and not same["may_have_partial_changes"]
        missing = await call("reject_missing_target", main.clone_schematic_components, source, root / "Missing.SchDoc", designators=names)
        assert not missing["success"] and missing["cloned_count"] == 0
        # Native source is actually Modified; no saved-file reload is allowed.
        dirty_original = next((Path(d["path"]) for d in original["documents"] if d["kind"] == "SCH" and d["state"] == "modified"), None)
        if dirty_original:
            dirty_baseline = await snapshot("original_dirty_before", dirty_original)
            dirty_names = list(dict.fromkeys(o["designator"] for o in dirty_baseline["objects"] if o["type"] == "component"))[:1]
            dirty_target = root / "FromDirtyOriginal.SchDoc"
            scratch.append(dirty_target)
            require(await call("create_dirty_original_target", main.create_document, dirty_target))
            result = await clone("clone_from_dirty_original", dirty_original, dirty_target, dirty_names)
            assert result["source_was_modified"] and result["source_is_modified"]
            assert (await snapshot("original_dirty_after", dirty_original))["objects"] == dirty_baseline["objects"]
        if args.multipart_sheet:
            multipart_target = root / "MultipartTarget.SchDoc"
            scratch.append(multipart_target)
            require(await call("create_multipart_target", main.create_document, multipart_target))
            result = await clone("clone_all_multipart_parts", args.multipart_sheet, multipart_target, [args.multipart_designator])
            parts = [c["component"]["state"]["part_id"] for c in result["components"]]
            assert len(parts) > 1 and len(parts) == len(set(parts))
            report["multipart_parts"] = parts
        report["checks_passed"] = True
        assert (await snapshot("original_source_after", source_original))["objects"] == source_baseline["objects"]
    finally:
        if not report.get("executor_outcome_unknown"):
            for index, path in enumerate(scratch):
                assert path.is_relative_to(root)
                result = await call("cleanup_" + str(index), main.close_document, path, require_clean=False)
                if report.get("executor_outcome_unknown"):
                    break
                require(result)
        if original and not report.get("executor_outcome_unknown"):
            require(await call("restore_project", main.select_project, project))
            current = await call("states_before_restore", main.get_project_info)
            current_states = {PureWindowsPath(d["path"]): d["state"] for d in current["documents"]}
            for index, doc in enumerate(original["documents"]):
                if doc["state"] == "closed" and current_states.get(PureWindowsPath(doc["path"]), "closed") != "closed":
                    # Sources opened read-only by this harness return to their
                    # prior closed state. Refuse to discard any unexpected edit.
                    require(await call("restore_closed_source_" + str(index), main.close_document,
                                       doc["path"], require_clean=True))
            if previous_editor:
                require(await call("restore_editor", main.open_document, previous_editor))
                require(await call("restore_project_after_editor", main.select_project, project))
            after = await call("original_after", main.get_project_info)
            report["original_states_unchanged"] = document_states(original) == document_states(after)
        if original:
            report["original_disk_after"] = {str(p): disk_state(p) for p in originals}
            report["original_disk_unchanged"] = report["original_disk_before"] == report["original_disk_after"]
        report["scratch_disk_after"] = {str(p): disk_state(p) for p in copied}
        report["scratch_disk_unchanged"] = report.get("scratch_disk_before", {}) == report["scratch_disk_after"]
        report["unsaved_targets_not_written"] = all(not p.exists() for p in scratch if p not in copied)
        save("report", report)
        print("REPORT " + str(root / "report.json"), flush=True)
    assert report["checks_passed"] and report["original_states_unchanged"] and report["original_disk_unchanged"]
    assert report["scratch_disk_unchanged"] and report["unsaved_targets_not_written"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--source", help="Saved source in the requested project; default first clean open SchDoc")
    parser.add_argument("--multipart-sheet")
    parser.add_argument("--multipart-designator", default="M1")
    parser.add_argument("--exercise", action="store_true")
    asyncio.run(run(parser.parse_args()))
