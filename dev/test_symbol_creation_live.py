"""Verify creation metadata in an unsaved scratch library, preserving design files."""

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
from altium_files import write_altium_text


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def run(args):
    import main
    logging.getLogger().setLevel(logging.WARNING)
    root = REPO / ".altium-mcp" / f"symbol_creation_{time.strftime('%Y%m%d_%H%M%S')}"
    root.mkdir(parents=True)
    scripts = root / "AltiumScript"
    shutil.copytree(REPO / "server" / "AltiumScript", scripts)
    main.altium_bridge.config.script_path = str(scripts / "Altium_API.PrjScr")
    source = Path(args.schlib).resolve()
    scratch = root / (root.name + ".SchLib")
    shutil.copy2(source, scratch)
    paths = sorted({p for p in source.parent.iterdir() if p.suffix.lower() in {".schdoc", ".schlib", ".pcbdoc", ".prjpcb"}} | {scratch})
    before_hashes = {str(path): digest(path) for path in paths}
    report = {"source": str(source), "scratch": str(scratch), "steps": []}

    def save(label, result):
        (root / f"{label}.json").write_text(json.dumps(result, ensure_ascii=True, indent=2), encoding="ascii")

    async def call(label, function, *positional, **keywords):
        result = json.loads(await function(None, *positional, **keywords))
        save(label, result)
        report["steps"].append({"label": label, **{k: result.get(k) for k in ("success", "error", "created", "saved")}})
        print(json.dumps(report["steps"][-1]), flush=True)
        return result

    inventory = await call("source_inventory", main.get_symbol_primitives, str(source))
    reference = inventory["symbols"][0]["name"]
    original = await call("source_before", main.set_library_component_data, str(source), reference)
    assert original["success"], original
    scratch_inventory = await call("scratch_inventory", main.get_symbol_primitives, str(scratch))
    initial_count = scratch_inventory["symbol_count"]
    parameters = {"Value": "10k\u03a9", "MF": "Example \u0416", "MP": "R-10K",
                  "HelpURL": "https://example.com/datasheet.pdf", "Package": "0603", "Type": "SMD",
                  "Instructions": "Fit", "System": "Test power", "Extra": "\u03a9 \U0001f680 | = , \"'\nsecond line"}
    description = "Resistor, 10k\u03a9, 1%, 100mW, 0603 SMD | \"precision\" \u0416 \U0001f680"
    pins = ["1|A|eElectricPassive|eRotate0|0|0", "2|B|eElectricPassive|eRotate180|600|0"]
    try:
        single = await call("single", main.create_schematic_symbol, "MCP_METADATA_SINGLE", description, pins, parameters)
        assert single["success"] and single["verification"]["passed"], single
        assert single["saved"] is False and single["pins_count"] == 2
        actual = await call("single_readback", main.set_library_component_data, str(scratch), "MCP_METADATA_SINGLE")
        assert actual["success"] and actual["is_modified"]
        assert actual["after"]["description"] == description
        assert all(actual["after"]["parameters"][name] == value for name, value in parameters.items())
        assert "Pacage" not in actual["after"]["parameters"]

        batch_lines = [f"LIBRARY|{scratch}"]
        requests = []
        for index in range(2):
            name = f"MCP_METADATA_BATCH_{index}"
            desc = f"Test resistor, 10k, 0603, batch {index}"
            values = {**parameters, "MP": name, "Extra": "\u03a9 | = , \"'"}
            batch_lines.append(f"SYMBOL|{name}|{desc}|2")
            batch_lines.extend(f"PARAM|{key.lower()}|{value}" for key, value in values.items())
            batch_lines.extend(["PIN|1|A|eElectricPassive|eRotate0|0|0|1", "PIN|2|B|eElectricPassive|eRotate180|600|0|2"])
            requests.append((name, desc, values))
        batch_path = root / "batch.txt"
        write_altium_text(batch_path, "\n".join(batch_lines))
        batch = await call("batch", main.create_symbols_batch, str(batch_path))
        assert batch["success"] and batch["created"] == 2 and batch["verification"]["passed"], batch
        assert all(item["part_count"] == 2 for item in batch["components"])
        for index, (name, desc, values) in enumerate(requests):
            result = await call(f"batch_readback_{index}", main.set_library_component_data, str(scratch), name)
            assert result["after"]["description"] == desc
            actual_values = {key.casefold(): value for key, value in result["after"]["parameters"].items()}
            assert all(actual_values[key.casefold()] == value for key, value in values.items())

        invalid = await call("missing_parameter", main.create_schematic_symbol, "MCP_MUST_NOT_EXIST", description, pins,
                             {key: value for key, value in parameters.items() if key != "System"})
        assert invalid["success"] is False and invalid["may_have_partial_changes"] is False
        invalid = await call("obsolete_parameter", main.create_schematic_symbol, "MCP_MUST_NOT_EXIST", description, pins,
                             {**parameters, "Pacage": "0603"})
        assert invalid["success"] is False and invalid["may_have_partial_changes"] is False
        invalid = await call("placeholder_description", main.create_schematic_symbol, "MCP_MUST_NOT_EXIST", "New Component", pins, parameters)
        assert invalid["success"] is False and invalid["may_have_partial_changes"] is False
        invalid_batch = "\n".join(batch_lines).replace("MCP_METADATA_BATCH_", "MCP_MUST_NOT_EXIST_")
        invalid_batch = invalid_batch.replace("PARAM|system|Test power", "PARAM|system|", 1)
        write_altium_text(root / "invalid_batch.txt", invalid_batch)
        invalid = await call("invalid_batch", main.create_symbols_batch, str(root / "invalid_batch.txt"))
        assert invalid["success"] is False and invalid["may_have_partial_changes"] is False
        final = await call("final_inventory", main.get_symbol_primitives, str(scratch))
        assert final["symbol_count"] == initial_count + 3
        assert not any(item["name"].startswith("MCP_MUST_NOT_EXIST") for item in final["symbols"])
        report["created_count"] = 3
        report["metadata_verified"] = True
    finally:
        after = await call("source_after", main.set_library_component_data, str(source), reference)
        report["source_metadata_unchanged"] = after.get("after") == original["after"]
        report["source_modified_unchanged"] = after.get("is_modified") == original["is_modified"]
        report["disk_unchanged"] = before_hashes == {str(path): digest(path) for path in paths}
        save("report", report)
        print(json.dumps({"report": str(root / "report.json"), **{k: v for k, v in report.items() if k != "steps"}}), flush=True)
    assert report["source_metadata_unchanged"] and report["source_modified_unchanged"] and report["disk_unchanged"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schlib", required=True)
    asyncio.run(run(parser.parse_args()))
