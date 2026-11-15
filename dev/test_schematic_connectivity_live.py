"""Read-only opt-in connectivity test against an open Altium project.

Never saves or edits schematic objects. Compile runs in memory. Full snapshots
and SHA-256 checks stay under the toolkit's .altium-mcp directory.
"""

import argparse
import asyncio
import hashlib
import json
import logging
from pathlib import Path
import shutil
import sys
import time

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "server"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


async def run(args):
    import main
    logging.getLogger().setLevel(logging.WARNING)
    run_dir = REPO / ".altium-mcp" / f"schematic_connectivity_{time.strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True)
    scripts = run_dir / "AltiumScript"
    shutil.copytree(REPO / "server" / "AltiumScript", scripts)
    main.altium_bridge.config.script_path = str(scripts / "Altium_API.PrjScr")
    report = {"sheet_path": args.sheet, "steps": []}

    def save(name, value):
        (run_dir / f"{name}.json").write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")

    async def read(label, compiled=True, expected=None, sheet=None):
        result = json.loads(await main.get_schematic_connectivity(None, sheet or args.sheet, compiled, expected))
        save(label, result)
        report["steps"].append({"label": label, "success": result.get("success"),
                                "pin_count": result.get("pin_count"), "net_count": result.get("net_count"),
                                "checks_passed": result.get("checks", {}).get("passed"), "error": result.get("error")})
        print(json.dumps(report["steps"][-1]), flush=True)
        assert result.get("success"), result.get("error", result)
        return result

    project = json.loads(await main.get_project_info(None))
    save("project_before", project)
    paths = [d["path"] for d in project["documents"] if d["path"].lower().endswith(".schdoc")]
    assert args.sheet in paths
    report["disk_before"] = {p: digest(p) for p in paths}
    before = json.loads(await main.edit_schematic(None, args.sheet, []))
    assert before["success"]
    save("objects_before", before)
    try:
        result = await read("compiled")
        report["coverage"] = {k: result[k] for k in ("placed_pin_count", "model_pin_count", "missing_model_pin_count",
                                                    "unresolved_pin_count", "complete", "compile_errors", "compile_fatals")}
        assert result["saved"] is False and result["compile_performed"] is True
        assert not result["may_be_stale"]
        assert result["pins"] and all(isinstance(p["pin_number"], str) for p in result["pins"])
        # Compare compiler electrical-end coordinates to an independently read
        # schematic editor inventory for ordinary (unmirrored) placed parts.
        editor = {(o["unique_id"], p["designator"]): p for o in before["objects"]
                  if o["type"] == "component" and not o["mirror"] for p in o["pins"]}
        compared = 0
        for p in result["pins"]:
            original = editor.get((p["component_uid"], p["pin_number"]))
            if original and p["resolution"] == "resolved":
                assert abs(original["x"] - p["x"]) < 0.01 and abs(original["y"] - p["y"]) < 0.01
                compared += 1
        report["coordinates_compared"] = compared
        assert compared > 0
        cached = await read("cached", False)
        assert cached["compile_performed"] is False and cached["may_be_stale"] is True
        assert cached["pins"] == result["pins"]
        net = next(n for n in result["nets"] if len(n["pin_indexes"]) >= 2)
        members = [result["pins"][i] for i in net["pin_indexes"]]
        def selector(pin):
            return {k: pin[k] for k in ("designator", "pin_number", "component_uid", "part_id", "instance_id") if pin.get(k) is not None}
        good = {"pins": [selector(p) for p in members], "net_name": net["net_name"], "exact": True}
        positive = await read("expected_positive", False, [good])
        assert positive["checks"]["passed"]
        other = next(p for p in result["pins"] if p["resolution"] == "resolved" and p["net_id"] != net["net_id"])
        negative = [
            {"pins": [selector(members[0])], "net_name": "__WRONG_EXPECTED_NET__"},
            {"pins": [selector(members[0]), selector(other)]},
            {"pins": [selector(members[0]), selector(members[1])], "connected": False},
            {"pins": [selector(members[0])], "exact": True},
            {"pins": [{"designator": "__MISSING__", "pin_number": "1"}], "net_name": "GND"},
        ]
        checked = await read("expected_negative", False, negative)
        assert checked["checks"]["failed_count"] == len(negative)
        disconnected = await read("expected_disconnected", False, [{"pins": [selector(members[0]), selector(other)], "connected": False}])
        assert disconnected["checks"]["passed"]
        unknown = next((p for p in result["pins"] if p["resolution"] != "resolved"), None)
        if unknown:
            failed = await read("expected_unresolved", False, [{"pins": [selector(unknown)], "net_name": "GND"}])
            assert failed["checks"]["passed"] is False
        if args.expected:
            supplied = json.loads(Path(args.expected).read_text(encoding="utf-8"))
            supplied_result = await read("expected_supplied", False, supplied)
            assert supplied_result["checks"]["passed"]
        if args.second_sheet:
            second = await read("second_sheet", False, sheet=args.second_sheet)
            assert second["sheet_path"] == args.second_sheet
            assert all(p["component_uid"] not in {q["component_uid"] for q in result["pins"]} for p in second["pins"])
            again = await read("target_after_other_sheet", False)
            assert again["pins"] == result["pins"]
    finally:
        after = json.loads(await main.edit_schematic(None, args.sheet, []))
        save("objects_after", after)
        final_project = json.loads(await main.get_project_info(None))
        save("project_after", final_project)
        report["objects_unchanged"] = after.get("objects") == before["objects"]
        report["modified_flag_unchanged"] = after.get("was_modified") == before["was_modified"]
        report["disk_after"] = {p: digest(p) for p in paths}
        report["disk_unchanged"] = report["disk_before"] == report["disk_after"]
        report["modified_documents_unchanged"] = ({d["path"] for d in project["documents"] if d["state"] == "modified"} ==
                                                   {d["path"] for d in final_project["documents"] if d["state"] == "modified"})
        save("report", report)
        print(json.dumps({k: v for k, v in report.items() if k not in ("disk_before", "disk_after", "steps")}), flush=True)
    assert report["objects_unchanged"] and report["disk_unchanged"] and report["modified_documents_unchanged"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet", required=True)
    parser.add_argument("--second-sheet")
    parser.add_argument("--expected", help="optional JSON file with independently specified expected_connections")
    asyncio.run(run(parser.parse_args()))
