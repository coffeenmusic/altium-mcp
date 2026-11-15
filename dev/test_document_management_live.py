"""Opt-in lifecycle test: save scratch libraries only; never save sheets/projects.

Temporary memberships in the requested open project are removed in finally.
All new documents remain open for inspection; no source document is edited.
"""

import argparse
import asyncio
import hashlib
import json
import logging
from pathlib import Path
import re
import shutil
import sys
import time

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "server"))


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


async def run(args):
    import main
    logging.getLogger().setLevel(logging.WARNING)
    project = Path(args.project).resolve()
    root = REPO / ".altium-mcp" / f"document_management_{time.strftime('%Y%m%d_%H%M%S')}"
    root.mkdir(parents=True)
    scripts = root / "AltiumScript"
    shutil.copytree(REPO / "server" / "AltiumScript", scripts)
    main.altium_bridge.config.script_path = str(scripts / "Altium_API.PrjScr")
    # Hash every existing file directly in the project directory, including sheets,
    # libraries and PrjPcb; no diagnostics are written into the design directory.
    inputs = [p for p in project.parent.iterdir() if p.is_file()]
    report = {"project_path": str(project), "root": str(root), "steps": [],
              "disk_before": {str(p): digest(p) for p in inputs}}
    paths = {kind: root / (root.name + suffix) for suffix, kind in main.document_management.KINDS.items()}
    paths["SCHLIB"] = root / (root.name + "_\u03a9_\u0416.SchLib")

    async def call(label, function, *values, **options):
        result = json.loads(await function(None, *map(str, values), **options))
        (root / f"{label}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        report["steps"].append({"label": label, **{k: result.get(k) for k in ("success", "changed", "saved", "error")}})
        print(json.dumps(report["steps"][-1]), flush=True)
        return result

    def require(result):
        assert result.get("success"), result
        return result

    if not args.exercise:
        print("Read-only mode: pass --exercise to create scratch documents and test temporary membership.")
        return
    sheet_names = re.findall(r"(?mi)^DocumentPath=(.+\.SchDoc)\s*$", main.read_altium_text(project))
    sheet = next(project.parent / name.strip() for name in sheet_names if (project.parent / name.strip()).is_file())
    sheet_before = require(await call("sheet_before", main.edit_schematic, sheet, operations=[]))
    baseline = await call("project_before", main.get_project_info)
    assert "error" not in baseline, baseline
    assert Path(baseline["project_path"]).resolve() == project, "Focus the requested project before running the test"
    report["original_states_before"] = {d["path"]: d["state"] for d in baseline["documents"]}
    # Membership cleanup is limited to the unique paths generated for this run.
    cleanup = "SandboxLog('remove only lifecycle-test memberships');\n"
    cleanup += f"S1 := {literal(project)};\n"
    cleanup += "Obj1 := nil; for I1 := 0 to GetWorkspace.DM_ProjectCount-1 do begin Obj2 := GetWorkspace.DM_Projects(I1); if LowerCase(Obj2.DM_ProjectFullPath) = LowerCase(S1) then Obj1 := Obj2; end;\n"
    cleanup += "if Obj1 = nil then ResultText := '{\"success\":false}' else begin\n"
    cleanup += "for I1 := Obj1.DM_LogicalDocumentCount-1 downto 0 do begin S2 := Obj1.DM_LogicalDocuments(I1).DM_FullPath;\n"
    # Use the live full path: the DelphiScript source parser can lose Unicode
    # literals, unlike UTF-8 spec transport used by the production tools.
    cleanup += f"if Pos({literal(str(root) + chr(92))}, S2) = 1 then Obj1.DM_RemoveSourceDocument(S2); end;\n"
    cleanup += "Obj1.DM_SetAsCurrentProject; ResultText := '{\"success\":true,\"document_count\":'+IntToStr(Obj1.DM_LogicalDocumentCount)+'}'; end;"
    try:
        for kind, path in paths.items():
            created = require(await call("create_" + kind, main.create_document, path))
            assert created["saved"] is False and not path.exists()
            rejected = await call("duplicate_" + kind, main.create_document, path)
            assert rejected["success"] is False
            preview = require(await call("preview_" + kind, main.save_document, path, dry_run=True))
            assert preview["saved"] is False and not path.exists()
            assert preview["was_modified"] == preview["is_modified"]
        # Focus remains on PcbLib. Save SchLib by explicit path to catch focus bugs.
        for kind in ("SCHLIB", "PCBLIB"):
            saved = require(await call("save_" + kind, main.save_document, paths[kind]))
            assert saved["saved"] and saved["disk"]["size_bytes"] > 0
            assert saved["is_modified"] is False
            repeated_save = require(await call("save_again_" + kind, main.save_document, paths[kind]))
            assert repeated_save["saved"] and repeated_save["is_modified"] is False
            assert (await call("existing_" + kind, main.create_document, paths[kind]))["success"] is False
        for kind, path in paths.items():
            added = require(await call("add_" + kind, main.add_document_to_project, project, path))
            assert added["changed"] and added["saved"] is False and added["project_saved"] is False
            repeated = require(await call("repeat_" + kind, main.add_document_to_project, project, path))
            assert repeated["changed"] is False
            assert repeated["document_count_after"] == added["document_count_after"]
        assert (await call("missing_source", main.add_document_to_project, project, root / "Missing.SchLib"))["success"] is False
        closed = root / "Closed.SchLib"
        shutil.copy2(paths["SCHLIB"], closed)
        assert (await call("save_closed", main.save_document, closed, dry_run=True))["success"] is False
        assert (await call("wrong_kind", main.create_document, root / "Wrong.SchLib", document_kind="PCB"))["success"] is False
        # A saved but unopened project must not be confused with the focused one.
        unopened = root / "Unopened.PrjPcb"
        unopened.write_text("[Design]\nVersion=1.0\n", encoding="ascii")
        assert (await call("unopened_project", main.add_document_to_project, unopened, paths["SCHLIB"]))["success"] is False
    finally:
        cleanup_result = json.loads(await main.run_altium_script(None, cleanup, timeout_seconds=30))
        (root / "cleanup.json").write_text(json.dumps(cleanup_result, indent=2), encoding="utf-8")
        report["cleanup"] = cleanup_result
        if cleanup_result.get("success"):
            report["membership_after_cleanup"] = json.loads(cleanup_result["result"])
            sheet_after = require(await call("sheet_after", main.edit_schematic, sheet, operations=[]))
            report["original_sheet_objects_unchanged"] = sheet_before["objects"] == sheet_after["objects"]
            after = await call("project_after", main.get_project_info)
            assert "error" not in after, after
            report["original_states_after"] = {d["path"]: d["state"] for d in after["documents"]}
            report["original_states_unchanged"] = report["original_states_before"] == report["original_states_after"]
        report["disk_after"] = {str(p): digest(p) for p in inputs}
        report["original_disk_unchanged"] = report["disk_before"] == report["disk_after"]
        report["sheets_not_saved"] = not paths["SCH"].exists() and not paths["PCB"].exists()
        (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("REPORT " + str(root / "report.json"), flush=True)
    assert report["original_disk_unchanged"] and report["sheets_not_saved"]
    assert report["cleanup"]["success"], report["cleanup"]
    assert report["membership_after_cleanup"]["success"]
    assert report["membership_after_cleanup"]["document_count"] == len(baseline["documents"])
    assert report["original_states_unchanged"]
    assert report["original_sheet_objects_unchanged"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="Exact already-open PrjPcb")
    parser.add_argument("--exercise", action="store_true")
    asyncio.run(run(parser.parse_args()))
