"""Read Altium .OutJob files (INI format) into plain dictionaries.

Kept free of Altium and Windows imports so it can be unit tested on its own
and used on a file that is not open in Altium.
"""
import configparser
import re
from pathlib import Path

# Altium V7 layer IDs as they appear in Gerber "Plot.Set" / "AddToAllPlots.Set"
# serialisations ("<id>~1"). Copper and mechanical IDs follow fixed ranges;
# the 0x0103xxxx group was mapped against generated Gerbers (Pad Masters and
# Keep-Out confirmed by toggling them and comparing the output file set).
_SPECIAL_LAYERS = {
    0x01000001: "Top Layer",
    0x0100FFFF: "Bottom Layer",
    0x01030006: "Top Overlay",
    0x01030007: "Bottom Overlay",
    0x01030008: "Top Paste",
    0x01030009: "Bottom Paste",
    0x0103000A: "Top Solder",
    0x0103000B: "Bottom Solder",
    0x0103000D: "Keep-Out Layer",
    0x01030018: "Top Pad Master",
    0x01030019: "Bottom Pad Master",
}


def layer_name(layer_id: int) -> str:
    """Best-effort name for an Altium V7 layer ID."""
    if layer_id in _SPECIAL_LAYERS:
        return _SPECIAL_LAYERS[layer_id]
    if 0x01000002 <= layer_id <= 0x0100003F:
        return f"Mid Layer {layer_id - 0x01000001}"
    if 0x01010001 <= layer_id <= 0x0101003F:
        return f"Internal Plane {layer_id - 0x01010000}"
    if 0x01020001 <= layer_id <= 0x010200FF:
        return f"Mechanical {layer_id - 0x01020000}"
    return f"Unknown layer 0x{layer_id:08X}"


def parse_pipe_record(text: str) -> dict:
    """Parse 'Key=Value|Key=Value' strings. Repeated keys keep the last value."""
    record = {}
    for part in text.split("|"):
        if "=" in part:
            key, value = part.split("=", 1)
            record[key.strip()] = value
    return record


def parse_layer_set(value: str) -> list:
    """Decode 'SerializeLayerHash.Version~2,ClassName~X,<id>~1,...' into layers."""
    layers = []
    for token in (value or "").split(","):
        m = re.fullmatch(r"\s*(\d+)~(\d+)\s*", token)
        if m and m.group(2) != "0":
            layer_id = int(m.group(1))
            layers.append({"id": layer_id, "name": layer_name(layer_id)})
    return layers


def _read_ini(path: Path) -> configparser.RawConfigParser:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    ini = configparser.RawConfigParser(strict=False, interpolation=None)
    ini.optionxform = str  # keep key case
    ini.read_string(text)
    return ini


def _numbered(section, prefix: str):
    """Yield (n, value) for keys like 'OutputMedium1', 'OutputMedium2', ..."""
    n = 1
    while f"{prefix}{n}" in section:
        yield n, section[f"{prefix}{n}"]
        n += 1


def _gerber_summary(record: dict) -> dict:
    plot = parse_layer_set(record.get("Plot.Set", ""))
    return {
        "units": record.get("GerberUnit"),
        "decimals": record.get("NumberOfDecimals"),
        "origin": record.get("OriginPosition"),
        "zeroes": record.get("LeadingAndTrailingZeroesMode"),
        "plot_board_profile": record.get("PlotBoardProfile") == "True",
        "plotted_layers": plot,
        "add_to_all_plots": parse_layer_set(record.get("AddToAllPlots.Set", "")),
        "drill_drawing_plot_all_used_pairs": record.get("PlotUsedDrillDrawingLayerPairs") == "True",
        "drill_drawing_pair0_checked": record.get("PlotDrillDrawingLayerPair0_Checked") == "True",
        "drill_guide_plot_all_used_pairs": record.get("PlotUsedDrillGuideLayerPairs") == "True",
        "drill_guide_pair0_checked": record.get("PlotDrillGuideLayerPair0_Checked") == "True",
    }


def parse_outjob(path) -> dict:
    """Return outputs, containers and decoded settings of an .OutJob file."""
    path = Path(path)
    ini = _read_ini(path)
    if "OutputGroup1" not in ini:
        raise ValueError(f"{path} has no [OutputGroup1] section; not an OutJob?")
    group = ini["OutputGroup1"]
    publish = ini["PublishSettings"] if "PublishSettings" in ini else {}
    generated = ini["GeneratedFilesSettings"] if "GeneratedFilesSettings" in ini else {}

    containers = []
    for m, name in _numbered(group, "OutputMedium"):
        containers.append({
            "index": m,
            "name": name,
            "type": group.get(f"OutputMedium{m}_Type"),
            "output_path": publish.get(f"OutputFilePath{m}"),
            "relative_output_path": generated.get(f"RelativeOutputPath{m}"),
            "output_directory": publish.get(f"OutputDirectory{m}"),
            "base_path": publish.get(f"OutputBasePath{m}"),
            "path_media": publish.get(f"OutputPathMedia{m}"),
            "path_outputer": publish.get(f"OutputPathOutputer{m}"),
            "file_name": publish.get(f"OutputFileName{m}"),
            "file_name_expression": publish.get(f"OutputFileNameMulti{m}"),
            "release_managed": publish.get(f"ReleaseManaged{m}") == "1",
            "add_to_project": generated.get(f"AddToProject{m}") == "1",
            "open_outputs": generated.get(f"OpenOutputs{m}") == "1",
        })

    outputs = []
    for n, out_type in _numbered(group, "OutputType"):
        records = [parse_pipe_record(v) for _, v in _numbered(group, f"Configuration{n}_Item")]
        connected = [
            c["name"] for c in containers
            if group.get(f"OutputEnabled{n}_OutputMedium{c['index']}", "0") not in ("0", "")
        ]
        output = {
            "index": n,
            "type": out_type,
            "name": group.get(f"OutputName{n}"),
            "category": group.get(f"OutputCategory{n}"),
            "enabled": group.get(f"OutputEnabled{n}") == "1",
            "document_path": group.get(f"OutputDocumentPath{n}") or None,
            "variant": group.get(f"OutputVariantName{n}") or None,
            "connected_containers": connected,
        }
        main = next((r for r in records if "Record" in r), records[0] if records else {})
        kind = main.get("Record")
        if out_type == "Gerber":
            output["gerber"] = _gerber_summary(main)
        elif out_type == "NC Drill":
            output["nc_drill"] = {k: main.get(k) for k in (
                "Units", "NumberOfUnits", "NumberOfDecimals", "ZeroesMode", "OriginPosition",
                "GenerateEIADrillFile", "GenerateSeparatePlatedNonPlatedFiles")}
        elif kind in ("TestPointView",):
            output["test_points"] = {k: main.get(k) for k in (
                "Units", "OriginPosition", "GenerateIPCFormat", "OutputBoardOutline")}
        elif kind == "PickPlaceView":
            output["pick_place"] = {k: v for k, v in main.items() if k != "Record"}
        elif out_type in ("PCB Print", "Assembly", "Drill"):
            pages = [r for r in records if r.get("Record") == "PcbPrintOut"]
            layers = [r for r in records if r.get("Record") == "PcbPrintLayer"]
            output["printouts"] = [{
                "name": p.get("Name"),
                "mirror": p.get("Mirror") == "True",
                "include_top_components": p.get("IncludeTopLayerComponents") == "True",
                "include_bottom_components": p.get("IncludeBottomLayerComponents") == "True",
                "layers": [l.get("Layer") or l.get("LayerSet") for l in layers
                           if l.get("PrintOutIndex") == p.get("Index")],
            } for p in pages]
        output["raw_settings"] = records
        outputs.append(output)

    return {
        "path": str(path),
        "group_name": group.get("Name"),
        "variant": group.get("VariantName"),
        "containers": containers,
        "outputs": outputs,
    }
