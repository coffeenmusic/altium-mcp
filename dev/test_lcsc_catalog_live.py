"""Live LCSC lookup and supplier fields on new symbols in an unsaved SchLib copy."""

import argparse
import asyncio
import hashlib
import json
import logging
from pathlib import Path
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
    root = REPO / ".altium-mcp" / f"lcsc_catalog_{time.strftime('%Y%m%d_%H%M%S')}"
    root.mkdir(parents=True)
    scripts = root / "AltiumScript"
    shutil.copytree(REPO / "server" / "AltiumScript", scripts)
    main.altium_bridge.config.script_path = str(scripts / "Altium_API.PrjScr")
    source = Path(args.schlib).resolve()
    scratch = root / (root.name + ".SchLib")
    shutil.copy2(source, scratch)
    extensions = {".schdoc", ".pcbdoc", ".schlib", ".pcblib", ".prjpcb", ".libpkg", ".outjob", ".bomdoc"}
    paths = sorted({p for p in source.parent.iterdir() if p.is_file() and p.suffix.lower() in extensions} | {scratch})
    report = {"scratch": str(scratch), "source": str(source), "steps": [],
              "disk_before": {str(path): disk_state(path) for path in paths}}
    original = None
    previous_document = None

    async def call(label, function, *positional, **keywords):
        result = json.loads(await function(None, *positional, **keywords))
        (root / f"{label}.json").write_text(json.dumps(result, indent=2, ensure_ascii=True) + "\n", encoding="ascii")
        report["steps"].append({"label": label, **{key: result.get(key) for key in
            ("success", "changed", "saved", "result_count", "error")}})
        print(json.dumps(report["steps"][-1]), flush=True)
        return result

    def require(result):
        assert result.get("success"), result
        assert result.get("saved") is False
        if "verification" in result:
            assert result["verification"]["passed"], result
        return result

    try:
        original = await call("original_project", main.get_project_info)
        by_code = require(await call("info_by_code", main.get_lcsc_component_info, args.lcsc_id, qty=100))
        component = by_code["components"][0]
        assert component["parameters"] and component["lcsc_id"] == args.lcsc_id
        by_mpn = require(await call("info_by_mpn", main.get_lcsc_component_info, component["mpn"], qty=100))
        assert any(item["mpn"] == component["mpn"] for item in by_mpn["components"])
        opened = require(await call("open_scratch", main.open_document, str(scratch)))
        previous_document = opened.get("previous_document_path")
        inventory = await call("inventory_before", main.get_symbol_primitives, str(scratch))
        name = "MCP_LCSC_" + component["lcsc_id"]
        core = {"Value": component["mpn"], "MF": component["manufacturer"], "MP": component["mpn"],
                "HelpURL": component["product_url"], "Package": component["package"], "Type": "IC",
                "Instructions": "Fit", "System": "LCSC integration test", "User Note": "Keep \u03a9 \u0416 \U0001f680 | = , \"'"}
        description = f"IC: {component['description']}; package {component['package']}"
        created = require(await call("create", main.create_schematic_symbol, name, description, [], core))
        geometry_before = await call("geometry_before", main.get_symbol_primitives, str(scratch), name)
        applied = require(await call("apply", main.set_library_component_lcsc_data, str(scratch), name, args.lcsc_id, qty=100))
        assert applied["changed"] is True and applied["is_modified"] is True
        assert all(applied["after"]["parameters"][key] == value for key, value in core.items())
        for key in ("description", "comment", "models"):
            assert applied["after"][key] == created["metadata"][key]
        expected = main.lcsc_catalog.library_parameters(component)
        assert set(applied["lcsc_parameter_names"]) == set(expected)
        assert applied["after"]["parameters"]["JLCPCB Part#"] == args.lcsc_id
        assert "Pacage" not in applied["after"]["parameters"]
        repeated = require(await call("repeat", main.set_library_component_lcsc_data, str(scratch), name, args.lcsc_id, qty=100))
        assert set(repeated["after"]["parameters"]) == set(applied["after"]["parameters"])
        report["repeat_changed"] = repeated["changed"]
        geometry_after = await call("geometry_after", main.get_symbol_primitives, str(scratch), name)
        report["symbol_geometry_unchanged"] = geometry_before["primitives"] == geometry_after["primitives"]
        readback = require(await call("independent_readback", main.set_library_component_data, str(scratch), name))
        assert readback["after"] == repeated["after"]

        wrong_name = "MCP_LCSC_WRONG_MPN"
        wrong_core = {**core, "MP": "INTENTIONALLY_DIFFERENT_MPN"}
        wrong = require(await call("create_mismatch_fixture", main.create_schematic_symbol, wrong_name,
                                   "Test IC with intentionally mismatched part number", [], wrong_core))
        rejected = await call("reject_mismatch", main.set_library_component_lcsc_data, str(scratch), wrong_name, args.lcsc_id)
        assert rejected["success"] is False and rejected["may_have_partial_changes"] is False
        unchanged = require(await call("mismatch_readback", main.set_library_component_data, str(scratch), wrong_name))
        assert unchanged["after"] == wrong["metadata"]
        final_inventory = await call("inventory_after", main.get_symbol_primitives, str(scratch))
        assert final_inventory["symbol_count"] == inventory["symbol_count"] + 2
        report.update(lookup_verified=True, supplier_fields_verified=True, core_fields_preserved=True,
                      mismatch_rejected_without_edits=True, saved=False)
    finally:
        if original is not None:
            if previous_document and any(document["path"] == previous_document for document in original["documents"]):
                await call("restore_editor", main.open_document, previous_document)
            restored = await call("restore_project", main.select_project, original["project_path"])
            report["original_project_restored"] = restored.get("success") is True
            after = await call("original_project_after", main.get_project_info)
            report["original_document_states_unchanged"] = after["documents"] == original["documents"]
        report["disk_after"] = {str(path): disk_state(path) for path in paths}
        report["source_and_scratch_disk_unchanged"] = report["disk_before"] == report["disk_after"]
        (root / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="ascii")
        print(json.dumps({"report": str(root / "report.json"), **{key: value for key, value in report.items()
            if key not in {"steps", "disk_before", "disk_after"}}}), flush=True)
    assert all(report.get(key) for key in ("lookup_verified", "supplier_fields_verified", "core_fields_preserved",
        "mismatch_rejected_without_edits", "symbol_geometry_unchanged", "original_project_restored",
        "original_document_states_unchanged", "source_and_scratch_disk_unchanged"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schlib", required=True, help="Existing library to copy; original is never opened or edited")
    parser.add_argument("--lcsc-id", default="C136648")
    asyncio.run(run(parser.parse_args()))
