"""Validate the merged server on copies of an open project's saved files.

Run with --exercise using the MCP Python environment. Only the generated
directory under .altium-mcp may be edited or saved. Original document states
and source hashes are checked afterwards. Copies remain open for inspection.
"""

import argparse
import asyncio
import functools
import hashlib
import json
import logging
from pathlib import Path, PureWindowsPath
import re
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
    main.altium_bridge.execute_command = functools.partial(main.altium_bridge.execute_command, timeout=90)
    root = REPO / ".altium-mcp" / ("upstream_live_" + time.strftime("%Y%m%d_%H%M%S"))
    root.mkdir(parents=True)
    report = {"root": str(root), "steps": [], "success": False}
    original = None
    previous_document = None
    inputs = []
    hints = {}
    uncertain = False

    def save_report():
        (root / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="ascii")

    async def call(label, function, *values, negative=False, **options):
        nonlocal uncertain
        result = json.loads(await function(None, *values, **options))
        (root / (label + ".json")).write_text(json.dumps(result, indent=2, ensure_ascii=True), encoding="ascii")
        summary = {"label": label}
        if isinstance(result, dict):
            summary.update({key: result[key] for key in
                ("success", "error", "saved", "created", "edited", "applied_count", "changed") if key in result})
            error = str(result.get("error", "")).lower()
            uncertain = uncertain or bool(result.get("outcome_unknown") or any(
                message in error for message in ("timed out", "timeout", "no response", "not responding")))
        report["steps"].append(summary)
        save_report()
        print(json.dumps(summary), flush=True)
        if negative:
            assert result.get("success") is False, result
        else:
            assert not isinstance(result, dict) or (result.get("success") is not False and not result.get("error")), result
        return result

    try:
        status = await call("runtime", main.get_server_status)
        assert status["mcp_sdk_version"] == "1.30.0", status
        original = await call("original_project", main.get_project_info)
        report["original_project"] = original["project_path"]
        if not args.exercise:
            report["success"] = True
            return
        source_project = Path(original["project_path"])
        inputs = [source_project] + [Path(doc["path"]) for doc in original["documents"] if Path(doc["path"]).is_file()]
        names = [path.name.casefold() for path in inputs[1:]]
        assert len(names) == len(set(names)), "Fixture copy requires distinct document basenames"
        report["original_disk_before"] = {str(path): disk_state(path) for path in inputs}
        for name in ("last_project.txt", "last_board.txt"):
            path = main.EXCHANGE_DIR / name
            hints[path] = path.read_bytes() if path.exists() else None

        target = root / "project"
        target.mkdir()
        for path in inputs[1:]:
            shutil.copy2(path, target / path.name)
        project = target / "Integration.PrjPcb"
        project_text = main.read_altium_text(source_project)
        project_text = re.sub(r"(?m)^DocumentPath=(.+)$",
            lambda match: "DocumentPath=" + PureWindowsPath(match[1].strip()).name, project_text)
        main.write_altium_text(project, project_text)
        opened = await call("open_copy_project", main.open_project, str(project))
        assert opened["verification"]["passed"]
        info = await call("copy_project_info", main.get_project_info)
        assert PureWindowsPath(info["project_path"]) == PureWindowsPath(project)
        assert all(Path(doc["path"]).resolve().is_relative_to(target.resolve()) for doc in info["documents"])
        report["copy_project"] = str(project)
        await call("repeat_select_copy", main.select_project, str(project))
        sheet = next(target / Path(doc["path"]).name for doc in original["documents"] if doc["kind"] == "SCH")
        schlib = next(target / Path(doc["path"]).name for doc in original["documents"] if doc["kind"] == "SCHLIB")
        pcblib = next(target / Path(doc["path"]).name for doc in original["documents"] if doc["kind"] == "PCBLIB")
        opened = await call("open_sheet_copy", main.open_document, str(sheet))
        previous_document = opened.get("previous_document_path")
        baseline = await call("uid_read", main.edit_schematic, str(sheet), [])
        component = min((obj for obj in baseline["objects"] if obj["type"] == "component" and obj.get("pins")),
                        key=lambda obj: len(obj["pins"]))
        x = max(obj["x"] for obj in baseline["objects"]) + 1500
        y = 1500
        marker = "MCP_MERGE_TEST"
        added = await call("uid_add", main.edit_schematic, str(sheet), [
            {"op": "add", "type": "component", "properties": {"source_designator": component["designator"],
             "designator": marker, "x": x, "y": y, "parameters": {"MCP_TEST": "UID \u03a9 \u0416"}}},
            {"op": "add", "type": "wire", "properties": {"points": [x, y + 500, x + 500, y + 500]}},
            {"op": "add", "type": "junction", "properties": {"x": x, "y": y + 500}},
        ])
        assert added["applied_count"] == 3
        uid = added["results"][0]["unique_id"]
        added_component = next(obj for obj in added["objects"] if obj["unique_id"] == uid)
        assert added_component["parameters"]["MCP_TEST"] == "UID \u03a9 \u0416"
        await call("uid_update", main.edit_schematic, str(sheet), [
            {"op": "update", "type": "component", "target": {"unique_id": uid}, "properties": {"comment": "UID update"}}])
        actions = await call("upstream_actions_mm_save", main.edit_schematic, str(sheet), [
            {"op": "move_component", "designator": marker, "x": (x + 100) * .0254, "y": (y + 100) * .0254},
            {"op": "set_parameter", "designator": marker, "name": "MCP_TEST", "value": "Action \u03a9 \u0416"},
        ], unit="mm", save=True)
        assert not actions.get("errors")
        moved = await call("action_readback", main.edit_schematic, str(sheet), [])
        moved_component = next(obj for obj in moved["objects"] if obj["unique_id"] == uid)
        assert abs(moved_component["x"] - x - 100) < .01
        assert moved_component["parameters"]["MCP_TEST"] == "Action \u03a9 \u0416"
        await call("mixed_format_rejected", main.edit_schematic, str(sheet), [
            {"op": "add_junction", "x": 0, "y": 0}, {"op": "delete", "type": "component", "target": {"unique_id": uid}}], negative=True)
        await call("upstream_delete", main.edit_schematic, str(sheet), [{"op": "delete_component", "designator": marker}])
        await call("uid_delete", main.edit_schematic, str(sheet), [
            {"op": "delete", "type": item["type"], "target": {"unique_id": item["unique_id"]}} for item in added["results"][1:]])
        after = await call("uid_restored", main.edit_schematic, str(sheet), [])
        assert after["objects"] == baseline["objects"]
        await call("save_copy_sheet", main.save_document, str(sheet))
        await call("drafting_check", main.check_schematic, str(sheet), max_issues_per_sheet=10)
        connectivity = await call("compiled_connectivity", main.get_schematic_connectivity, str(sheet))
        assert connectivity["pins"] and connectivity["compile_performed"]
        await call("cached_connectivity", main.get_schematic_connectivity, str(sheet), compiled=False)
        await call("project_components_filtered", main.get_project_components, designators=[component["designator"]],
                   include_parameters=True, parameter_names=["Value", "MP"], limit=5)

        await call("open_copy_schlib", main.open_document, str(schlib))
        inventory = await call("symbol_reference_inventory", main.get_symbol_primitives, str(schlib))
        reference = min(inventory["symbols"], key=lambda item: item.get("pins", 0))["name"]
        await call("symbol_reference_geometry", main.get_symbol_primitives, str(schlib), reference)
        parameters = {"Value": "10k", "MF": "Example", "MP": "MCP-2PIN", "HelpURL": "https://example.com/test.pdf",
                      "Package": "0603", "Type": "SMD", "Instructions": "Fit", "System": "Integration test"}
        pins = ["1|A|eElectricPassive|eRotate0|0|0", "2|B|eElectricPassive|eRotate180|600|0"]
        single = await call("create_symbol", main.create_schematic_symbol, "MCP_MERGE_SINGLE",
                            "Test resistor, 10k, 0603 package", pins, parameters)
        assert single["verification"]["passed"]
        spec = [f"LIBRARY|{schlib}", "SYMBOL|MCP_MERGE_BATCH|Test resistor, 10k, 0603 package|1"]
        spec += [f"SYMPARAM|{name}|{value}|0" for name, value in parameters.items()]
        spec += ["PIN|" + pin for pin in pins]
        spec += ["COMMENT|Explicit batch comment|1", "DESCRIPTION|Precision test resistor in 0603 package",
                 "PINDESC|1|Test input", "PINSYMBOL|1|inside|clock", "EDITSYMBOL|MCP_MERGE_SINGLE", "PINPARAM|1|Probe|yes"]
        spec_path = root / "symbols.txt"
        main.write_altium_text(spec_path, "\n".join(spec) + "\n")
        batch = await call("batch_create_and_edit", main.create_symbols_batch, str(spec_path))
        assert batch["created"] == 1 and batch["edited"] == 1 and batch["verification"]["passed"]
        readback = await call("batch_readback", main.set_library_component_data, str(schlib), "MCP_MERGE_BATCH")
        assert readback["after"]["comment"] == "Explicit batch comment"
        assert readback["after"]["description"] == "Precision test resistor in 0603 package"
        await call("reopen_dirty_library", main.open_document, str(schlib))
        await call("open_copy_pcblib", main.open_document, str(pcblib))
        await call("create_footprint", main.create_pcb_footprint, "MCP_MERGE_2PIN", "Integration test footprint",
                   ["1|-1|0|1|1|Rect", "2|1|0|1|1|Rect"])
        await call("footprint_readback", main.get_footprint_primitives, str(pcblib), "MCP_MERGE_2PIN")
        await call("save_copy_pcblib", main.save_document, str(pcblib))
        models = [{"model_name": "MCP_MERGE_2PIN", "library_path": str(pcblib),
                   "pin_map": {"1": ["1"], "2": ["2"]}, "is_current": True}]
        linked = await call("link_footprint_model", main.set_library_component_data, str(schlib), "MCP_MERGE_BATCH", footprint_models=models)
        assert linked["verification"]["passed"]
        repeated = await call("repeat_footprint_model", main.set_library_component_data, str(schlib), "MCP_MERGE_BATCH", footprint_models=models)
        assert repeated["changed"] is False
        await call("save_copy_schlib", main.save_document, str(schlib))

        board = target / "IntegrationScratch.PcbDoc"
        await call("create_scratch_pcb", main.create_document, str(board))
        await call("save_scratch_pcb", main.save_document, str(board))
        await call("add_scratch_pcb_to_copy", main.add_document_to_project, str(project), str(board))
        await call("pcb_layers", main.get_pcb_layers)
        await call("pcb_stackup", main.get_pcb_layer_stackup)
        await call("place_via", main.place_vias, [[1000, 1000]], 30, 15, board_path=str(board))
        await call("create_rule", main.create_pcb_rule, "hole_size", "MCP_MERGE_HOLE", {"min_mils": 10, "max_mils": 30},
                   scope1="IsVia", board_path=str(board))
        rules = await call("pcb_rules_readback", main.get_pcb_rules)
        assert "MCP_MERGE_HOLE" in json.dumps(rules)
        await call("drc", main.run_drc, board_path=str(board), max_violations=10)
        await call("save_modified_scratch_pcb", main.save_document, str(board))
        report["success"] = True
    except Exception as error:
        report["failure"] = repr(error)
        raise
    finally:
        if original and not uncertain:
            if previous_document and any(PureWindowsPath(doc["path"]) == PureWindowsPath(previous_document) for doc in original["documents"]):
                await call("restore_original_document", main.open_document, previous_document)
            await call("restore_original_project", main.select_project, original["project_path"])
            restored = await call("original_project_after", main.get_project_info)
            report["original_document_states_unchanged"] = restored["documents"] == original["documents"]
        if inputs:
            report["original_disk_after"] = {str(path): disk_state(path) for path in inputs}
            report["original_disk_unchanged"] = report["original_disk_before"] == report["original_disk_after"]
        for path, contents in hints.items():
            if contents is not None:
                path.write_bytes(contents)
            elif path.exists():
                path.unlink()
        report["outcome_uncertain"] = uncertain
        if args.exercise:
            report["success"] = bool(report["success"] and report.get("original_disk_unchanged")
                                     and report.get("original_document_states_unchanged") and not uncertain)
        save_report()
        print("REPORT " + str(root / "report.json"), flush=True)
    assert report["success"] and report["original_document_states_unchanged"] and report["original_disk_unchanged"], report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exercise", action="store_true", help="edit and save only the generated test copies")
    asyncio.run(run(parser.parse_args()))
