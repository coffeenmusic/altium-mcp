"""Project options in a .PrjPcb: reading them by readable name, and changing
them line by line so everything else stays as Altium wrote it.

Pure Python, unit-tested in tests/test_project_options.py. Altium keeps an
open project's options in memory and writes them back on save, so the tool
that writes the file (set_project_options in main.py) closes the project in
Altium first and opens it again afterwards.
"""

import fnmatch
import ntpath
import re
from typing import Dict, List, Optional, Tuple

# Readable name -> (section, key, kind). kind: bool (0/1), int, str.
# Numeric styles are given as stored; their meaning is the order of the
# choices in Project Options.
OPTIONS: Dict[str, Dict[str, Tuple[str, str, str]]] = {
    "cross_references": {
        "auto_cross_references": ("Design", "AutoCrossReferences", "bool"),
        "follow_main_settings": ("Design", "CrossRefFollowFromMainSettings", "bool"),
        "sheet_style": ("Design", "CrossRefSheetStyle", "int"),
        "location_style": ("Design", "CrossRefLocationStyle", "int"),
        "ports": ("Design", "CrossRefPorts", "bool"),
        "cross_sheet_connectors": ("Design", "CrossRefCrossSheets", "bool"),
        "sheet_entries": ("Design", "CrossRefSheetEntries", "bool"),
    },
    "netlist": {
        "hierarchy_mode": ("Design", "HierarchyMode", "int"),
        "allow_port_net_names": ("Design", "AllowPortNetNames", "bool"),
        "allow_sheet_entry_net_names": ("Design", "AllowSheetEntryNetNames", "bool"),
        "append_sheet_number_to_local_nets": ("Design", "AppendSheetNumberToLocalNets", "bool"),
        "single_pin_nets": ("Design", "NetlistSinglePinNets", "bool"),
        "name_nets_hierarchically": ("Design", "NameNetsHierarchically", "bool"),
        "power_port_names_take_priority": ("Design", "PowerPortNamesTakePriority", "bool"),
    },
    "multichannel": {
        "room_naming_style": ("Design", "ChannelRoomNamingStyle", "int"),
        "designator_format": ("Design", "ChannelDesignatorFormatString", "str"),
        "room_level_separator": ("Design", "ChannelRoomLevelSeperator", "str"),
    },
    "class_generation": {
        "component_classes_from_directives": ("PrjClassGen", "CompClassManualEnabled", "bool"),
        "rooms_for_directive_classes": ("PrjClassGen", "CompClassManualRoomEnabled", "bool"),
        "net_classes_from_directives": ("PrjClassGen", "NetClassManualEnabled", "bool"),
        "net_classes_for_buses": ("PrjClassGen", "NetClassAutoBusEnabled", "bool"),
        "net_classes_for_bus_sections": ("PrjClassGen", "NetClassSeparateForBusSections", "bool"),
        "net_classes_for_components": ("PrjClassGen", "NetClassAutoCompEnabled", "bool"),
        "net_classes_for_harnesses": ("PrjClassGen", "NetClassAutoNamedHarnessEnabled", "bool"),
    },
}
# Per sheet, in its [DocumentN] section
SHEET_OPTIONS = {
    "component_class": ("ClassGenCCAutoEnabled", "bool"),
    "room": ("ClassGenCCAutoRoomEnabled", "bool"),
    "net_classes": ("ClassGenNCAutoScope", "str"),
    "class_cluster": ("GenerateClassCluster", "bool"),
}

_SECTION_RE = re.compile(r"^\[(.+)\]\s*$")


class Ini:
    """A .PrjPcb as lines, edited in place."""

    def __init__(self, data: bytes):
        self.bom = data.startswith(b"\xef\xbb\xbf")
        text = data[3:].decode("utf-8") if self.bom else data.decode("utf-8")
        self.nl = "\r\n" if "\r\n" in text else "\n"
        self.trailing = text.endswith(("\n", "\r"))
        self.lines = text.splitlines()

    def to_bytes(self) -> bytes:
        text = self.nl.join(self.lines) + (self.nl if self.trailing else "")
        return (b"\xef\xbb\xbf" if self.bom else b"") + text.encode("utf-8")

    def sections(self) -> List[Tuple[str, int, int]]:
        """(name, first line after the header, end) of every section."""
        found, name, start = [], None, 0
        for i, ln in enumerate(self.lines):
            m = _SECTION_RE.match(ln.strip())
            if m:
                if name is not None:
                    found.append((name, start, i))
                name, start = m.group(1), i + 1
        if name is not None:
            found.append((name, start, len(self.lines)))
        return found

    def get(self, section: str, key: str) -> Optional[str]:
        for name, a, b in self.sections():
            if name == section:
                for ln in self.lines[a:b]:
                    k, sep, v = ln.partition("=")
                    if sep and k == key:
                        return v
        return None

    def set(self, section: str, key: str, value: str) -> str:
        """Set a key; returns its old value ('' when it was missing)."""
        for name, a, b in self.sections():
            if name != section:
                continue
            for i in range(a, b):
                k, sep, v = self.lines[i].partition("=")
                if sep and k == key:
                    self.lines[i] = f"{key}={value}"
                    return v
            end = b
            while end > a and not self.lines[end - 1].strip():
                end -= 1
            self.lines.insert(end, f"{key}={value}")
            return ""
        if self.lines and self.lines[-1].strip():
            self.lines.append("")
        self.lines += [f"[{section}]", f"{key}={value}", ""]
        return ""


def _read(kind: str, raw: Optional[str]):
    if raw is None:
        return None
    if kind == "bool":
        return raw.strip() == "1"
    if kind == "int":
        try:
            return int(raw)
        except ValueError:
            return raw
    return raw


def _write(kind: str, value) -> str:
    if kind == "bool":
        if not isinstance(value, bool) and value not in (0, 1, "0", "1"):
            raise ValueError(f"expected true or false, got {value!r}")
        return "1" if value in (True, 1, "1") else "0"
    if kind == "int":
        if isinstance(value, bool) or not re.fullmatch(r"-?\d+", str(value)):
            raise ValueError(f"expected a whole number, got {value!r}")
        return str(int(value))
    text = str(value)
    if "\n" in text or "\r" in text:
        raise ValueError("a value may not hold a line break")
    return text


def sheet_sections(ini: Ini) -> List[Tuple[str, str]]:
    """(section, DocumentPath) of the project's schematic sheets."""
    out = []
    for name, a, b in ini.sections():
        if re.fullmatch(r"Document\d+", name):
            path = ini.get(name, "DocumentPath") or ""
            if path.lower().endswith(".schdoc"):
                out.append((name, path))
    return out


def read_options(data: bytes) -> dict:
    ini = Ini(data)
    out = {group: {name: _read(kind, ini.get(sec, key)) for name, (sec, key, kind) in opts.items()}
           for group, opts in OPTIONS.items()}
    out["sheets"] = [dict({"sheet": path}, **{name: _read(kind, ini.get(sec, key))
                                              for name, (key, kind) in SHEET_OPTIONS.items()})
                     for sec, path in sheet_sections(ini)]
    comparison = []
    k = 0
    while ini.get("Comparison Options", f"ComparisonOptions{k}") is not None:
        fields = dict(p.partition("=")[::2] for p in ini.get("Comparison Options", f"ComparisonOptions{k}").split("|"))
        comparison.append({"kind": fields.get("Kind", ""), "min_match_percent": _read("int", fields.get("MinPercent")),
                           "min_match_members": _read("int", fields.get("MinMatch")),
                           "match_by_name": fields.get("UseName") == "-1"})
        k += 1
    out["comparison"] = comparison
    return out


def change_options(data: bytes, changes: dict) -> Tuple[bytes, List[dict], List[str]]:
    """Apply changes {group: {name: value}, "sheets": {pattern: {name: value}},
    "raw": [{section, key, value}]}; returns the new file, what changed and
    errors (nothing is applied when there are errors)."""
    ini = Ini(data)
    applied, errors = [], []
    for group, values in (changes or {}).items():
        if group == "sheets":
            sheets = sheet_sections(ini)
            for pattern, opts in (values or {}).items():
                hits = [(sec, path) for sec, path in sheets
                        if fnmatch.fnmatch(ntpath.basename(path).lower(), pattern.lower())
                        or fnmatch.fnmatch(path.lower(), pattern.lower())]
                if not hits:
                    errors.append(f"sheets: no sheet matches '{pattern}'")
                    continue
                for name, value in (opts or {}).items():
                    if name not in SHEET_OPTIONS:
                        errors.append(f"sheets: unknown option '{name}' (use {', '.join(SHEET_OPTIONS)})")
                        continue
                    key, kind = SHEET_OPTIONS[name]
                    try:
                        text = _write(kind, value)
                    except ValueError as e:
                        errors.append(f"sheets.{name}: {e}")
                        continue
                    for sec, path in hits:
                        old = ini.set(sec, key, text)
                        if old != text:
                            applied.append({"sheet": path, "option": name, "from": _read(kind, old or None),
                                            "to": _read(kind, text)})
        elif group == "raw":
            for item in values or []:
                sec, key = str(item.get("section", "")), str(item.get("key", ""))
                if not sec or not key or "=" in key or "]" in sec:
                    errors.append(f"raw: needs section and key, got {item!r}")
                    continue
                try:
                    text = _write("str", item.get("value", ""))
                except ValueError as e:
                    errors.append(f"raw {sec}/{key}: {e}")
                    continue
                old = ini.set(sec, key, text)
                if old != text:
                    applied.append({"section": sec, "key": key, "from": old, "to": text})
        elif group in OPTIONS:
            for name, value in (values or {}).items():
                if name not in OPTIONS[group]:
                    errors.append(f"{group}: unknown option '{name}' (use {', '.join(OPTIONS[group])})")
                    continue
                sec, key, kind = OPTIONS[group][name]
                try:
                    text = _write(kind, value)
                except ValueError as e:
                    errors.append(f"{group}.{name}: {e}")
                    continue
                old = ini.set(sec, key, text)
                if old != text:
                    applied.append({"option": f"{group}.{name}", "from": _read(kind, old or None),
                                    "to": _read(kind, text)})
        else:
            errors.append(f"unknown group '{group}' (use {', '.join(list(OPTIONS) + ['sheets', 'raw'])})")
    if errors:
        return data, [], errors
    return ini.to_bytes(), applied, []
