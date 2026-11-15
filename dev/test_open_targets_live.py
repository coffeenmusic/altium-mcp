"""Open closed scratch projects/libraries and prove unsaved library data survives reuse."""

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
from altium_files import read_altium_text, write_altium_text


def disk_state(path):
    return {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "mtime_ns": path.stat().st_mtime_ns}


async def run():
    import main
    logging.getLogger().setLevel(logging.WARNING)
    root = REPO / ".altium-mcp" / f"open_targets_{time.strftime('%Y%m%d_%H%M%S')}"
    root.mkdir(parents=True)
    scripts = root / "AltiumScript"
    shutil.copytree(REPO / "server" / "AltiumScript", scripts)
    main.altium_bridge.config.script_path = str(scripts / "Altium_API.PrjScr")
    report = {"steps": [], "altium_save_requested": False, "compile_requested": False}
    original = None
    original_info = None
    previous_document = None
    inputs = []
    fixtures = []

    async def call(label, function, *positional, **keywords):
        result = json.loads(await function(None, *map(str, positional), **keywords))
        (root / f"{label}.json").write_text(json.dumps(result, indent=2, ensure_ascii=True) + "\n", encoding="ascii")
        report["steps"].append({"label": label, **{key: result.get(key) for key in
            ("success", "opened", "already_open", "is_modified", "error")}})
        print(json.dumps(report["steps"][-1]), flush=True)
        return result

    def require(result, opened=None):
        assert result.get("success") and result["verification"]["passed"], result
        assert result["saved"] is False
        if opened is not None:
            assert result["opened"] is opened and result["already_open"] is not opened, result
        return result

    try:
        original_info = await call("original_info", main.get_project_info)
        original = original_info["project_path"]
        source_dir = Path(original).parent
        extensions = {".prjpcb", ".libpkg", ".schdoc", ".pcbdoc", ".schlib", ".pcblib", ".outjob", ".bomdoc", ".pcbdwf"}
        inputs = sorted(path for path in source_dir.iterdir() if path.is_file() and path.suffix.lower() in extensions)
        report["source_disk_before"] = {str(path): disk_state(path) for path in inputs}
        source_schlib = next(Path(d["path"]) for d in original_info["documents"] if d["kind"] == "SCHLIB")
        source_pcblib = next(Path(d["path"]) for d in original_info["documents"] if d["kind"] == "PCBLIB")
        source_sheet = next(Path(d["path"]) for d in original_info["documents"] if d["kind"] == "SCH")
        schlib, pcblib = root / "Symbols.SchLib", root / "Footprints.PcbLib"
        unicode_lib, sheet = root / "Symbols_\u03a9_\u0416.SchLib", root / "Sheet.SchDoc"
        for source, dest in ((source_schlib, schlib), (source_pcblib, pcblib), (source_schlib, unicode_lib), (source_sheet, sheet)):
            shutil.copy2(source, dest)
        project, package = root / "Controller_\u03a9_\u0416.PrjPcb", root / "Libraries.LibPkg"
        write_altium_text(project, "[Design]\nVersion=1.0\n")
        write_altium_text(package, "[Design]\nVersion=1.0\n[Document1]\nDocumentPath=Symbols.SchLib\n[Document2]\nDocumentPath=Footprints.PcbLib\n")
        fixtures = [project, package, schlib, pcblib, unicode_lib, sheet]
        report["fixture_disk_before"] = {str(path): disk_state(path) for path in fixtures}

        first = require(await call("open_schlib", main.open_document, schlib), True)
        previous_document = first.get("previous_document_path")
        require(await call("repeat_schlib", main.open_document, schlib), False)
        require(await call("open_pcblib", main.open_document, pcblib), True)
        require(await call("repeat_pcblib", main.open_document, pcblib), False)
        require(await call("open_unicode_library", main.open_document, unicode_lib), True)
        require(await call("open_sheet_copy", main.open_document, sheet), True)
        require(await call("repeat_sheet_copy", main.open_document, sheet), False)

        inventory = await call("scratch_symbols", main.get_symbol_primitives, schlib)
        symbol = inventory["symbols"][0]["name"]
        value = "unsaved \u03a9 \u0416 \U0001f680 | = , \"'"
        edited = require(await call("unsaved_scratch_parameter", main.set_library_component_data, schlib, symbol,
                                   parameters={"MCP_OPEN_TEST": value}))
        assert edited["is_modified"] is True
        require(await call("switch_to_pcblib", main.open_document, pcblib), False)
        reused = require(await call("reuse_dirty_schlib_case_variant", main.open_document, str(schlib).upper().replace("\\", "/")), False)
        assert reused["was_modified"] is True and reused["is_modified"] is True

        opened_project = require(await call("open_closed_project", main.open_project, project), True)
        assert opened_project["project_saved"] is False and opened_project["compiled"] is False
        info = await call("new_project_info", main.get_project_info)
        assert PureWindowsPath(info["project_path"]) == PureWindowsPath(project)
        hint = read_altium_text(main.EXCHANGE_DIR / "last_project.txt").strip()
        assert hint.startswith("json:")
        assert json.loads('"' + hint[5:] + '"') == str(project)
        report["unicode_project_context_preserved"] = True
        require(await call("repeat_project", main.open_project, str(project).upper().replace("\\", "/")), False)
        require(await call("open_closed_libpkg", main.open_project, package), True)
        require(await call("repeat_libpkg", main.open_project, package), False)
        retained = require(await call("unsaved_parameter_readback", main.set_library_component_data, schlib, symbol))
        assert retained["after"]["parameters"]["MCP_OPEN_TEST"] == value
        assert retained["is_modified"] is True
        report["unsaved_library_data_preserved"] = True

        for name, function, path in (("missing_document", main.open_document, root / "Missing.SchLib"),
                                     ("missing_project", main.open_project, root / "Missing.PrjPcb")):
            missing = await call(name, function, path)
            assert missing["success"] is False and not path.exists(), missing
            assert missing.get("opened") is False
        invalid = await call("wrong_extension", main.open_project, root / "Script.PrjScr")
        assert invalid["success"] is False
        reused_original = require(await call("reuse_user_project", main.open_project, original), False)
        assert reused_original["documents_unchanged"] is True
        report["open_checks_passed"] = True
    finally:
        if previous_document and original_info and any(PureWindowsPath(d["path"]) == PureWindowsPath(previous_document)
                                                       for d in original_info["documents"]):
            await call("restore_original_editor", main.open_document, previous_document)
        if original is not None:
            restored = await call("restore_original_project", main.select_project, original)
            report["original_project_restored"] = restored.get("success") is True
            after = await call("original_info_after", main.get_project_info)
            report["original_document_states_unchanged"] = after["documents"] == original_info["documents"]
        if inputs:
            report["source_disk_after"] = {str(path): disk_state(path) for path in inputs}
            report["source_disk_unchanged"] = report["source_disk_before"] == report["source_disk_after"]
        if fixtures:
            report["fixture_disk_after"] = {str(path): disk_state(path) for path in fixtures}
            report["fixtures_not_saved_by_altium"] = report["fixture_disk_before"] == report["fixture_disk_after"]
        (root / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="ascii")
        print(json.dumps({"report": str(root / "report.json"), **{key: value for key, value in report.items()
            if key not in {"steps", "source_disk_before", "source_disk_after", "fixture_disk_before", "fixture_disk_after"}}}), flush=True)
    assert all(report.get(key) for key in ("open_checks_passed", "unsaved_library_data_preserved", "original_project_restored",
                                          "original_document_states_unchanged", "source_disk_unchanged", "fixtures_not_saved_by_altium"))


if __name__ == "__main__":
    asyncio.run(run())
