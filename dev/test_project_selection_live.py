"""Verify selection of open projects without saving, compiling or editing sheets."""

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
    return {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "mtime_ns": path.stat().st_mtime_ns}


async def run(args):
    import main
    logging.getLogger().setLevel(logging.WARNING)
    root = REPO / ".altium-mcp" / f"project_selection_{time.strftime('%Y%m%d_%H%M%S')}"
    root.mkdir(parents=True)
    scripts = root / "AltiumScript"
    shutil.copytree(REPO / "server" / "AltiumScript", scripts)
    main.altium_bridge.config.script_path = str(scripts / "Altium_API.PrjScr")
    report = {"steps": [], "sheets_saved": False, "projects_saved": False, "compiled": False}
    original = None
    original_info = None
    snapshots = {}
    inputs = []

    def save(label, result):
        (root / f"{label}.json").write_text(json.dumps(result, indent=2, ensure_ascii=True) + "\n", encoding="ascii")

    async def call(label, function, *positional):
        result = json.loads(await function(None, *map(str, positional)))
        save(label, result)
        report["steps"].append({"label": label, **{key: result.get(key) for key in
            ("success", "changed", "project_path", "active_project_path", "documents_unchanged", "error")}})
        print(json.dumps(report["steps"][-1]), flush=True)
        return result

    def require(result):
        assert result.get("success") and result["verification"]["passed"], result
        assert result["saved"] is False and result["project_saved"] is False and result["compiled"] is False
        assert result["documents_before"] == result["documents_after"]
        return result

    def states(info):
        return {document["path"]: (document["kind"], document["state"]) for document in info["documents"]}

    try:
        original_info = await call("original_info", main.get_project_info)
        original = original_info["project_path"]
        report["original_project"] = original
        closed_path = root / "Unopened_\u03a9_\u0416.PrjPcb"
        closed = await call("unopened_discovery", main.select_project, closed_path)
        assert closed["success"] is False and closed["changed"] is False, closed
        assert PureWindowsPath(closed["active_project_path"]) == PureWindowsPath(original)
        available = closed["available_project_paths"]
        report["available_project_paths"] = available
        if args.project:
            target = str(Path(args.project).resolve())
            assert any(PureWindowsPath(path) == PureWindowsPath(target) for path in available), "Requested project must already be open"
        else:
            target = next((path for path in available if PureWindowsPath(path) != PureWindowsPath(original)), original)
        projects = list(dict.fromkeys([original, target]))
        extensions = {".prjpcb", ".libpkg", ".schdoc", ".pcbdoc", ".schlib", ".pcblib", ".outjob", ".bomdoc", ".pcbdwf"}
        inputs = sorted({path for project in projects for path in Path(project).parent.iterdir()
                         if path.is_file() and path.suffix.lower() in extensions})
        report["disk_before"] = {str(path): disk_state(path) for path in inputs}
        snapshots[original] = states(original_info)

        selected = require(await call("select_target", main.select_project, target))
        snapshots[target] = {document["path"]: (document["kind"], document["state"]) for document in selected["documents_before"]}
        report["target_project"] = target
        assert selected["changed"] == (PureWindowsPath(original) != PureWindowsPath(target))
        info = await call("target_info", main.get_project_info)
        assert PureWindowsPath(info["project_path"]) == PureWindowsPath(target), info
        assert states(info) == snapshots[target]
        repeated = require(await call("repeat_target", main.select_project, target))
        assert repeated["changed"] is False
        case_variant = require(await call("case_variant", main.select_project, target.upper().replace("\\", "/")))
        assert case_variant["changed"] is False

        missing = await call("unopened_after_selection", main.select_project, closed_path)
        assert missing["success"] is False and missing["changed"] is False
        assert PureWindowsPath(missing["active_project_path"]) == PureWindowsPath(target)
        invalid = await call("invalid_extension", main.select_project, root / "Script.PrjScr")
        assert invalid["success"] is False and invalid["may_have_changed_focus"] is False
        still_selected = await call("target_after_rejections", main.get_project_info)
        assert PureWindowsPath(still_selected["project_path"]) == PureWindowsPath(target)
        assert states(still_selected) == snapshots[target]

        back = require(await call("switch_back", main.select_project, original))
        assert back["changed"] == (PureWindowsPath(original) != PureWindowsPath(target))
        assert require(await call("repeat_original", main.select_project, original))["changed"] is False
        report["selection_checks_passed"] = True
    finally:
        if original is not None:
            restored = await call("restore_original", main.select_project, original)
            report["original_focus_restored"] = bool(restored.get("success"))
            after = await call("original_info_after", main.get_project_info)
            report["original_document_states_unchanged"] = original_info is not None and states(after) == states(original_info)
        if inputs:
            report["disk_after"] = {str(path): disk_state(path) for path in inputs}
            report["disk_hashes_and_timestamps_unchanged"] = report["disk_before"] == report["disk_after"]
            report["checked_file_count"] = len(inputs)
        save("report", report)
        print(json.dumps({"report": str(root / "report.json"), **{key: value for key, value in report.items()
            if key not in {"steps", "disk_before", "disk_after", "available_project_paths"}}}), flush=True)
    assert report.get("selection_checks_passed") and report["original_focus_restored"]
    assert report["original_document_states_unchanged"] and report["disk_hashes_and_timestamps_unchanged"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", help="Exact already-open target; defaults to another open project if available")
    asyncio.run(run(parser.parse_args()))
