"""Opt-in live smoke test. Edits only new test objects; NEVER saves documents.

Run with the MCP venv's python: dev/test_edit_schematic_live.py --sheet <full path>
Without --exercise, only reads. Artifacts stay in the toolkit checkout.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "server"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


async def run(args):
    import main
    logging.getLogger().setLevel(logging.WARNING)
    main.RESPONSE_TIMEOUT = 25
    # A fresh script project avoids Altium's cached copy of an older unit.
    run_dir = REPO / ".altium-mcp" / f"schematic_edit_{time.strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True)
    script_dir = run_dir / "AltiumScript"
    shutil.copytree(REPO / "server" / "AltiumScript", script_dir)
    main.altium_bridge.config.script_path = str(script_dir / "Altium_API.PrjScr")
    report = {"run_dir": str(run_dir), "sheet_path": args.sheet, "steps": []}

    async def call(operations, label):
        result = json.loads(await main.edit_schematic(None, args.sheet, operations))
        (run_dir / f"{label}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        report["steps"].append({"label": label, "success": result.get("success"),
                                "applied_count": result.get("applied_count"), "error": result.get("error")})
        print(json.dumps(report["steps"][-1]), flush=True)
        return result

    def require(result):
        if not result.get("success"):
            raise AssertionError(result.get("error", result))
        return result

    project = json.loads(await main.get_project_info(None))
    report["project_path"] = project.get("project_path")
    paths = [d["path"] for d in project.get("documents", [])
             if d["path"].lower().endswith(".schdoc")]
    if args.sheet not in paths:
        raise RuntimeError("the test sheet must belong to the open project")
    report["disk_before"] = {p: digest(p) for p in paths}
    baseline = require(await call([], "before"))
    report["was_modified"] = baseline["was_modified"]
    added_ids = []
    try:
        if args.exercise:
            components = [o for o in baseline["objects"] if o["type"] == "component" and len(o.get("pins", [])) >= 2]
            source = min(components, key=lambda o: len(o["pins"]))
            marker = "MCPTEST" + str(int(time.time()))
            x = max(o["x"] for o in baseline["objects"]) + 1500
            y = 1000
            report["source_designator"] = source["designator"]
            report["test_marker"] = marker
            definitions = [
                ("component", {"source_designator": source["designator"], "designator": marker,
                               "x": x, "y": y, "comment": "test | = , quote \" ' \u0422\u0435\u0441\u0442",
                               "parameters": {"MCP_TEST": "initial"}}),
                ("wire", {"points": [x, y + 1000, x + 500, y + 1000]}),
                ("net_label", {"x": x, "y": y + 1000, "text": marker}),
                ("port", {"x": x + 500, "y": y + 1000, "name": "MCP_TST", "width": 800, "io_type": 2, "style": 2}),
                ("power_port", {"x": x, "y": y + 1500, "text": marker, "style": 2, "orientation": 1}),
                ("junction", {"x": x + 250, "y": y + 1000}),
                ("text", {"x": x, "y": y + 2000, "text": marker}),
            ]
            result = await call([{"op": "add", "type": kind, "properties": props}
                                 for kind, props in definitions], "add")
            # Recover any creations from a failed/partial call before raising.
            known_ids = {o["unique_id"] for o in baseline["objects"]}
            added_ids.extend((o["type"], o["unique_id"]) for o in result.get("objects", [])
                             if o["unique_id"] not in known_ids)
            require(result)
            assert len(added_ids) == len(definitions)
            added_ids[:] = [(r["type"], r["unique_id"]) for r in result["results"]]
            objects = {o["unique_id"]: o for o in result["objects"]}
            assert objects[added_ids[0][1]]["comment"] == definitions[0][1]["comment"]
            assert objects[added_ids[0][1]]["parameters"]["MCP_TEST"] == "initial"
            assert len(objects[added_ids[0][1]]["pins"]) == len(source["pins"])
            updates = [
                {"x": x + 100, "y": y + 100, "orientation": 1, "mirror": True,
                 "comment": "updated, | = \" ' \u041f\u0440\u043e\u0432\u0435\u0440\u0435\u043d\u043e", "parameters": {"MCP_TEST": "updated"}},
                {"points": [x, y + 1100, x + 250, y + 1100, x + 500, y + 1200]},
                {"x": x + 100, "y": y + 1100, "text": marker + "_updated", "orientation": 1},
                {"x": x + 500, "y": y + 1200, "name": "MCP_TST2", "width": 1000, "io_type": 3, "style": 3},
                {"x": x + 100, "y": y + 1600, "text": marker + "_updated", "style": 5, "orientation": 3, "show_net_name": False},
                {"x": x + 250, "y": y + 1100},
                {"x": x, "y": y + 2100, "text": marker + "\n\u041f\u0440\u043e\u0432\u0435\u0440\u0435\u043d\u043e", "orientation": 2},
            ]
            result = require(await call([{"op": "update", "type": kind, "target": {"unique_id": uid}, "properties": props}
                                         for (kind, uid), props in zip(added_ids, updates)], "update"))
            objects = {o["unique_id"]: o for o in result["objects"]}
            for (_, uid), props in zip(added_ids, updates):
                for key, value in props.items():
                    if key == "parameters":
                        for name, text in value.items():
                            assert objects[uid][key][name] == text
                    else:
                        assert objects[uid][key] == value, (key, objects[uid][key], value)
            # A valid first operation followed by a missing target must change nothing.
            negative = await call([
                {"op": "update", "type": "component", "target": {"designator": marker}, "properties": {"comment": "MUST NOT APPLY"}},
                {"op": "delete", "type": "wire", "target": {"unique_id": "MISSING_TEST_UID"}},
            ], "preflight_reject")
            assert negative["success"] is False and negative["applied_count"] == 0
            assert negative["may_have_partial_changes"] is False
            assert negative["objects"] == result["objects"]
            invalid = await call([{"op": "add", "type": "wire", "properties": {"points": [1, 2, 3]}}], "invalid_points")
            assert invalid["success"] is False and invalid["applied_count"] == 0
            renamed = require(await call([
                {"op": "update", "type": "component", "target": {"designator": marker},
                 "properties": {"designator": marker + "R", "comment": "renamed by designator"}},
            ], "designator_update"))
            assert next(o for o in renamed["objects"] if o["unique_id"] == added_ids[0][1])["designator"] == marker + "R"
            duplicate = await call([
                {"op": "add", "type": "component", "properties": {"source_designator": source["designator"],
                 "designator": marker + "R", "x": x, "y": y}},
            ], "duplicate_designator")
            assert duplicate["success"] is False and duplicate["applied_count"] == 0
            duplicate_target = await call([
                {"op": "update", "type": "component", "target": {"designator": marker + "R"}, "properties": {"comment": "must not apply"}},
                {"op": "delete", "type": "component", "target": {"unique_id": added_ids[0][1]}},
            ], "duplicate_target")
            assert duplicate_target["success"] is False and duplicate_target["applied_count"] == 0
            assert duplicate_target["objects"] == renamed["objects"]
            if args.library:
                # Discover a real symbol using the existing read-only library tool.
                inventory = json.loads(await main.get_symbol_primitives(None, args.library))
                symbols = [s for s in inventory.get("symbols", []) if s.get("pins", 0) > 0]
                if not symbols:
                    raise AssertionError(f"no symbols in library inventory: {list(inventory)}")
                symbol = min(symbols, key=lambda s: s["pins"])["name"]
                report["library_symbol"] = symbol
                library_add = await call([{"op": "add", "type": "component", "properties": {
                    "symbol_library": args.library, "symbol": symbol, "designator": marker + "L", "x": x + 1000, "y": y,
                }}], "library_add")
                added_ids.extend((r["type"], r["unique_id"]) for r in library_add.get("results", []))
                require(library_add)
                library_component = next(o for o in library_add["objects"] if o.get("designator") == marker + "L")
                assert library_component["symbol"] == symbol and library_component["pins"]
                missing = await call([{"op": "add", "type": "component", "properties": {
                    "symbol_library": args.library, "symbol": "__MCP_SYMBOL_DOES_NOT_EXIST__", "designator": marker + "M", "x": x, "y": y,
                }}], "missing_library_symbol")
                assert missing["success"] is False and missing["applied_count"] == 0
            require(await call([{"op": "delete", "type": kind, "target": {"unique_id": uid}}
                                for kind, uid in added_ids], "delete"))
            added_ids.clear()
    finally:
        if added_ids:
            cleanup = await call([{"op": "delete", "type": kind, "target": {"unique_id": uid}}
                                  for kind, uid in added_ids], "cleanup")
            report["cleanup_success"] = cleanup.get("success")
        final = await call([], "after")
        report["objects_restored"] = final.get("objects") == baseline.get("objects")
        report["disk_after"] = {p: digest(p) for p in paths}
        report["disk_unchanged"] = report["disk_after"] == report["disk_before"]
        (run_dir / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"report": str(run_dir / "report.json"),
                          "objects_restored": report["objects_restored"], "disk_unchanged": report["disk_unchanged"]}), flush=True)
    assert report["objects_restored"] and report["disk_unchanged"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet", required=True)
    parser.add_argument("--exercise", action="store_true", help="create/update/delete isolated test objects")
    parser.add_argument("--library", help="optional .SchLib for real library placement and missing-symbol checks")
    asyncio.run(run(parser.parse_args()))
