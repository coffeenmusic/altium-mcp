"""Opt-in SchLib metadata test using an unsaved copy of project libraries.

No source library, PCB or schematic is edited or saved. The scratch SchLib stays
open with unsaved test changes; artifacts remain in the toolkit checkout.
"""

import argparse
import asyncio
import copy
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
    root = REPO / ".altium-mcp" / f"library_component_data_{time.strftime('%Y%m%d_%H%M%S')}"
    root.mkdir(parents=True)
    scripts = root / "AltiumScript"
    shutil.copytree(REPO / "server" / "AltiumScript", scripts)
    main.altium_bridge.config.script_path = str(scripts / "Altium_API.PrjScr")
    source = Path(args.schlib).resolve()
    scratch = root / (root.name + ".SchLib")
    shutil.copy2(source, scratch)
    paths = sorted(set(source.parent.glob("*.SchDoc")) | {source, Path(args.pcblib).resolve(), scratch})
    report = {"scratch_library": str(scratch), "source_library": str(source), "steps": [],
              "disk_before": {str(p): digest(p) for p in paths}}

    def save(label, data):
        (root / f"{label}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    async def call(label, symbol=None, path=None, **edits):
        result = json.loads(await main.set_library_component_data(None, str(path or scratch), symbol or args.symbol, **edits))
        save(label, result)
        report["steps"].append({"label": label, **{k: result.get(k) for k in ("success", "changed", "saved", "error")}})
        print(json.dumps(report["steps"][-1]), flush=True)
        return result

    def require(result):
        assert result.get("success"), result.get("error", result)
        assert result["saved"] is False
        assert result["verification"]["passed"]
        return result

    original = require(await call("original_before", path=source))
    before = require(await call("scratch_before"))
    assert before["after"] == original["after"]
    assert before["changed"] is False and before["is_modified"] == before["was_modified"]
    geometry_before = json.loads(await main.get_symbol_primitives(None, str(scratch), args.symbol))
    save("geometry_before", geometry_before)
    try:
        if args.exercise:
            assert args.alternate, '--exercise requires --alternate with compatible pins and extra pads'
            existing = next(m for m in before["after"]["models"] if m["model_type"].upper() == "PCBLIB")
            mapping = copy.deepcopy(existing["pin_map"])
            assert mapping and "1" in mapping and "2" in mapping
            mapping["1"], mapping["2"] = mapping["2"], mapping["1"]
            primary = {"model_name": existing["model_name"], "library_path": args.pcblib,
                       "pin_map": mapping, "is_current": True, "description": "metadata test: swapped pins"}
            alternate_map = {pin: [pin] for pin in mapping}
            footprint_dump = json.loads(await main.get_footprint_primitives(None, args.pcblib, args.alternate))
            save('alternate_footprint', footprint_dump)
            footprint = footprint_dump
            pad_names = {p['name'] for p in footprint['primitives'] if p['type'] == 'pad'}
            assert set(alternate_map) <= pad_names, 'alternate footprint must support identity pin mapping'
            extra_pads = sorted(pad_names - set(alternate_map))
            assert extra_pads, 'alternate footprint needs extra pads to exercise one-to-many mapping'
            alternate_map["1"] += extra_pads
            report['one_to_many_extra_pads'] = extra_pads
            alternate = {"model_name": args.alternate, "library_path": args.pcblib,
                         "pin_map": alternate_map, "is_current": False}
            edits = {"parameters": {"MCP_TEST": "initial | = , \" ' \u03a9 \u0416 \U0001f680\nsecond line"},
                     "comment": "", "description": "unsaved scratch metadata test",
                     "footprint_models": [primary, alternate]}
            applied = require(await call("apply", **edits))
            assert applied["changed"] and applied["is_modified"]
            assert len(applied["after"]["models"]) == len(before["after"]["models"]) + 1
            for name, value in before["after"]["parameters"].items():
                if name.casefold() != 'comment':
                    assert applied["after"]["parameters"][name] == value, name
            repeated = require(await call("repeat", **edits))
            assert repeated["changed"] is False and repeated["before"] == repeated["after"]
            updated = require(await call("parameter_update", parameters={"MCP_TEST": "updated"}))
            assert updated["after"]["models"] == repeated["after"]["models"]
            alternate["is_current"] = True
            switched = require(await call("switch_current", footprint_models=[alternate]))
            assert next(m for m in switched["after"]["models"] if m["model_name"] == primary["model_name"])["is_current"] is False
            assert require(await call("repeat_current", footprint_models=[alternate]))["changed"] is False
            baseline = switched["after"]
            bad_pin_map = copy.deepcopy(mapping)
            bad_pin_map["__MISSING_PIN__"] = bad_pin_map.pop("1")
            bad_pad_map = copy.deepcopy(mapping)
            bad_pad_map["1"] = ["__MISSING_PAD__"]
            incomplete = copy.deepcopy(mapping)
            incomplete.pop("1")
            bad_models = [
                {**primary, "model_name": "__MISSING_FOOTPRINT__"},
                {**primary, "pin_map": bad_pin_map},
                {**primary, "pin_map": bad_pad_map},
                {**primary, "pin_map": incomplete},
            ]
            for index, model in enumerate(bad_models):
                failed = await call(f"preflight_reject_{index}", parameters={"MCP_TEST": "MUST NOT APPLY"}, footprint_models=[model])
                assert failed["success"] is False and failed["may_have_partial_changes"] is False
                assert failed["changed"] is False and failed["before"] == failed["after"]
                assert failed["after"] == baseline
            invalid = await call("invalid_value", parameters={"MCP_TEST": 123})
            assert invalid["success"] is False and invalid["may_have_partial_changes"] is False
            missing = await call("missing_symbol", symbol="__MISSING_SYMBOL__")
            assert missing["success"] is False
            if args.multipart:
                part_before = require(await call("multipart_before", symbol=args.multipart))
                assert part_before["after"]["part_count"] > 1
                model = next(m for m in part_before["after"]["models"] if m["model_type"].upper() == "PCBLIB")
                multi_model = {"model_name": model["model_name"], "library_path": args.pcblib,
                               "pin_map": model["pin_map"], "is_current": True}
                part_after = require(await call("multipart_apply", symbol=args.multipart, footprint_models=[multi_model],
                                                parameters={"MCP_TEST": "multipart"}))
                report["multipart_pin_count"] = len(model["pin_map"])
                assert part_after["after"]["part_count"] == part_before["after"]["part_count"]
    finally:
        after = await call("scratch_after")
        final_original = await call("original_after", path=source)
        geometry_after = json.loads(await main.get_symbol_primitives(None, str(scratch), args.symbol))
        save("geometry_after", geometry_after)
        report["source_metadata_unchanged"] = final_original.get("after") == original["after"]
        report["source_modified_flag_unchanged"] = final_original.get("is_modified") == original["is_modified"]
        report["symbol_geometry_unchanged"] = geometry_before.get("primitives") == geometry_after.get("primitives")
        report["disk_after"] = {str(p): digest(p) for p in paths}
        report["disk_unchanged"] = report["disk_before"] == report["disk_after"]
        report["scratch_unsaved"] = after.get("is_modified")
        save("report", report)
        print(json.dumps({"report": str(root / "report.json"), **{k: v for k, v in report.items()
                          if k not in ("disk_before", "disk_after", "steps")}}), flush=True)
    assert report["source_metadata_unchanged"] and report["source_modified_flag_unchanged"]
    assert report["symbol_geometry_unchanged"] and report["disk_unchanged"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schlib", required=True)
    parser.add_argument("--pcblib", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--alternate", help="compatible footprint with additional pads for one-to-many testing")
    parser.add_argument("--multipart")
    parser.add_argument("--exercise", action="store_true")
    asyncio.run(run(parser.parse_args()))
