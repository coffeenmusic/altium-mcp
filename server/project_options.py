"""Project options in a .PrjPcb: reading them by readable name, and changing
them line by line so everything else stays as Altium wrote it.

Pure Python, unit-tested in tests/test_project_options.py. Altium keeps an
open project's options in memory and writes them back on save, so the tool
that writes the file (set_project_options in main.py) closes the project in
Altium first and opens it again afterwards.
"""

import fnmatch
import ntpath
import os
import re
from typing import Dict, List, Optional, Tuple

# Readable name -> (section, key, kind). kind: bool (0/1), int, str.
# Numeric styles are given as stored; their meaning is the order of the
# choices in Project Options. The cross-reference flags for ports,
# cross-sheet connectors and sheet entries are not booleans (Altium's AD25
# example projects are reported to store CrossRefPorts=3), and no project
# showing what each value means was at hand, so they are read and written
# as the stored whole number.
OPTIONS: Dict[str, Dict[str, Tuple[str, str, str]]] = {
    "cross_references": {
        "auto_cross_references": ("Design", "AutoCrossReferences", "bool"),
        "follow_main_settings": ("Design", "CrossRefFollowFromMainSettings", "bool"),
        "sheet_style": ("Design", "CrossRefSheetStyle", "int"),
        "location_style": ("Design", "CrossRefLocationStyle", "int"),
        "ports": ("Design", "CrossRefPorts", "int"),
        "cross_sheet_connectors": ("Design", "CrossRefCrossSheets", "int"),
        "sheet_entries": ("Design", "CrossRefSheetEntries", "int"),
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
# A line and its ending. Only CR and LF end a line, as in Altium's reader:
# str.splitlines would also split a value at U+2028, U+0085, \x0b or \x0c.
_LINE_RE = re.compile(r"([^\r\n]*)(\r\n|\r|\n|\Z)")
# Altium writes a .PrjPcb without a byte-order mark in the ANSI code page
# ("mbcs", Windows only); cp1252 stands in for it elsewhere, and latin-1
# reads any bytes, so the lines not changed are written back as they were.
_ENCODINGS = ("utf-8", "mbcs", "cp1252", "latin-1")


class Ini:
    """A .PrjPcb as lines, edited in place. Each line keeps its own ending
    and the file is written back in the encoding it was read in."""

    def __init__(self, data: bytes):
        self.bom = data.startswith(b"\xef\xbb\xbf")
        if self.bom:
            self.encoding, text = "utf-8", data[3:].decode("utf-8", errors="surrogateescape")
        else:
            for self.encoding in _ENCODINGS:
                try:
                    text = data.decode(self.encoding)
                    break
                except (UnicodeDecodeError, LookupError):
                    continue
        self.nl = "\r\n" if "\r\n" in text else "\n"
        self.lines: List[str] = []
        self.ends: List[str] = []
        for m in _LINE_RE.finditer(text):
            if m.start() == len(text) and m.end() == len(text):
                break
            self.lines.append(m.group(1))
            self.ends.append(m.group(2))

    def insert(self, index: int, line: str) -> None:
        self.lines.insert(index, line)
        self.ends.insert(index, self.nl)

    def to_bytes(self) -> bytes:
        """The file; raises UnicodeEncodeError when a new value cannot be
        written in the file's encoding."""
        last = len(self.lines) - 1
        text = "".join(ln + (end or (self.nl if i < last else ""))
                       for i, (ln, end) in enumerate(zip(self.lines, self.ends)))
        errors = "surrogateescape" if self.bom else "strict"
        return (b"\xef\xbb\xbf" if self.bom else b"") + text.encode(self.encoding, errors=errors)

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
            self.insert(end, f"{key}={value}")
            return ""
        if self.lines and self.lines[-1].strip():
            self.insert(len(self.lines), "")
        for line in (f"[{section}]", f"{key}={value}", ""):
            self.insert(len(self.lines), line)
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
    if value is None:
        raise ValueError("expected text, got null")
    if isinstance(value, (dict, list)):
        raise ValueError(f"expected text, got {type(value).__name__}")
    text = str(value)
    if not _line_safe(text):
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


def _line_safe(text: str) -> bool:
    """A section or key that cannot break the file into other lines."""
    return not any(c in text for c in "\r\n\x00")


def change_options(data: bytes, changes: dict) -> Tuple[bytes, List[dict], List[str]]:
    """Apply changes {group: {name: value}, "sheets": {pattern: {name: value}},
    "raw": [{section, key, value}]}; returns the new file, what changed and
    errors (nothing is applied when there are errors)."""
    ini = Ini(data)
    applied, errors = [], []
    if changes is None:
        changes = {}
    if not isinstance(changes, dict):
        return data, [], [f"changes must be an object of groups, got {type(changes).__name__}"]
    for group, values in changes.items():
        if group not in OPTIONS and group not in ("sheets", "raw"):
            errors.append(f"unknown group '{group}' (use {', '.join(list(OPTIONS) + ['sheets', 'raw'])})")
            continue
        if values is None:
            continue
        if group == "raw":
            if not isinstance(values, list):
                errors.append(f"raw must be a list of {{section, key, value}}, got {type(values).__name__}")
                continue
        elif not isinstance(values, dict):
            errors.append(f"{group} must be an object, got {type(values).__name__}")
            continue
        if group == "sheets":
            sheets = sheet_sections(ini)
            for pattern, opts in values.items():
                if not isinstance(opts, dict):
                    errors.append(f"sheets['{pattern}'] must be an object of options, got {type(opts).__name__}")
                    continue
                hits = [(sec, path) for sec, path in sheets
                        if fnmatch.fnmatch(ntpath.basename(path).lower(), pattern.lower())
                        or fnmatch.fnmatch(path.lower(), pattern.lower())]
                if not hits:
                    errors.append(f"sheets: no sheet matches '{pattern}'")
                    continue
                for name, value in opts.items():
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
            for item in values:
                if not isinstance(item, dict):
                    errors.append(f"raw: each item must be {{section, key, value}}, got {item!r}")
                    continue
                sec, key = str(item.get("section", "")), str(item.get("key", ""))
                if not sec or not key:
                    errors.append(f"raw: needs section and key, got {item!r}")
                    continue
                # A line break in either would write lines of its own
                if not (_line_safe(sec) and _line_safe(key)) or "=" in key or "[" in sec or "]" in sec \
                        or key.strip() != key or sec.strip() != sec or key.startswith(("[", ";")):
                    errors.append(f"raw: a section or key may not hold line breaks, '=', brackets or "
                                  f"surrounding spaces, got {item!r}")
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
            for name, value in values.items():
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
    if errors:
        return data, [], errors
    try:
        return ini.to_bytes(), applied, []
    except UnicodeEncodeError as e:
        return data, [], [f"'{e.object[e.start:e.end]}' cannot be written in the file's encoding ({ini.encoding})"]


def same_file(a: str, b: str) -> bool:
    """Whether two paths name the same file. A file's identity also matches
    a UNC path with the same file through a mapped drive; when either path
    cannot be checked, they are compared as normalised text."""
    try:
        return os.path.samefile(a, b)
    except (OSError, ValueError):
        return ntpath.normcase(ntpath.normpath(a)) == ntpath.normcase(ntpath.normpath(b))


def match_open_project(path: str, open_projects: List[str]) -> Optional[str]:
    """The path, as Altium spells it, of the open project whose file is path."""
    for candidate in open_projects:
        if candidate and same_file(path, candidate):
            return candidate
    return None
