"""Check that a project's sheet hierarchy is wired up: sheet entries against
the ports of the child sheet, harness types, and harness definitions.

Reads the SAVED .SchDoc/.Harness files (no Altium needed), so save first.

What Altium's own Design >> Synchronize Sheet Entries and Ports covers is one
sheet symbol at a time, by name. This adds what it does not show:
  * every sheet symbol of the project at once,
  * pairs that probably belong together but are named differently
    (same harness type, or nearly the same name) with a rename suggestion,
  * harness type mismatches and harness types nobody defines,
  * one harness type defined differently on two sheets
    (Altium's fatal "Conflicting harness definition"),
  * .Harness files that disagree with the harness connectors of their sheet.
"""
import difflib
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

from schdoc_reader import read_schdoc_streams, coord

IO_TYPES = {0: "Unspecified", 1: "Output", 2: "Input", 3: "Bidirectional"}
SIDES = {0: "left", 1: "right", 2: "top", 3: "bottom"}
SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}
HIERARCHY_MODES = {0: "Automatic"}
# Which PrjPcb HierarchyMode value is "Strict Hierarchical" (power ports
# local) is not confirmed, so an entry named like a child's power port is
# reported as info in every mode, never switched off by a guessed value.


def _key(name):
    return (name or "").strip().casefold()


def _loose(name):
    """Name without case, spaces, separators or overline backslashes."""
    return re.sub(r"[^0-9a-z]", "", _key(name).replace("\\", ""))


def _repeat_base(name):
    m = re.fullmatch(r"\s*repeat\((.*)\)\s*", name or "", re.IGNORECASE)
    return m.group(1).strip() if m else name


def _int(rec, key):
    try:
        return int(rec.get(key, 0) or 0)
    except ValueError:
        return 0


def _distance(rec):
    """DistanceFromTop: 100 mil units plus a millionths part."""
    return (_int(rec, "DISTANCEFROMTOP") + _int(rec, "DISTANCEFROMTOP_FRAC1") / 1e6) * 100.0


# ------------------------------------------------------------------ reading

def read_project(prjpcb):
    """Documents and connectivity settings of a .PrjPcb."""
    prj = Path(prjpcb)
    sheets, harness_files, settings = [], [], {}
    raw = prj.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", "replace")
    for line in text.splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k == "DocumentPath":
            p = Path(os.path.normpath(prj.parent / v))
            if v.lower().endswith(".schdoc"):
                sheets.append(p)
            elif v.lower().endswith(".harness"):
                harness_files.append(p)
        elif k in ("HierarchyMode", "AllowPortNetNames", "AllowSheetEntryNetNames") and k not in settings:
            settings[k] = v
    return sheets, harness_files, settings


def _points(r):
    n = _int(r, "LOCATIONCOUNT")
    return [(coord(r, f"X{k}"), coord(r, f"Y{k}")) for k in range(1, n + 1)]


def _on_segment(p, a, b, tol=0.6):
    (x, y), (x1, y1), (x2, y2) = p, a, b
    if not (min(x1, x2) - tol <= x <= max(x1, x2) + tol and min(y1, y2) - tol <= y <= max(y1, y2) + tol):
        return False
    dx, dy = x2 - x1, y2 - y1
    length = (dx * dx + dy * dy) ** 0.5
    if length < 1e-9:
        return abs(x - x1) <= tol and abs(y - y1) <= tol
    return abs(dx * (y - y1) - dy * (x - x1)) / length <= tol


def _touches(p, line):
    return any(_on_segment(p, a, b) for a, b in zip(line, line[1:])) or         (len(line) == 1 and _on_segment(p, line[0], line[0]))


def _entry_point(sym, e):
    """Where a sheet entry sits on its symbol's edge."""
    x, y = sym["at"]
    w, h = sym["size"]
    d = e["distance"]
    return {0: (x, y - d), 1: (x + w, y - d), 2: (x + d, y), 3: (x + d, y - h)}.get(e["side"], (x, y - d))


def read_sheet(path):
    """Sheet symbols with their entries, ports, harness connectors, and the
    wire/bus/signal-harness geometry needed to see what is attached."""
    return sheet_from_records(*read_schdoc_streams(path), path=path)


def sheet_from_records(recs, additional=(), path=""):
    """read_sheet on already parsed FileHeader and Additional records."""
    symbols, ports, wires, attach, power, no_erc = {}, [], [], [], set(), []
    for i, r in enumerate(recs):
        if not r:
            continue
        kind = r.get("RECORD")
        if kind == "15":
            symbols[i] = {"designator": "", "file": "", "entries": [],
                          "at": (coord(r, "LOCATION.X"), coord(r, "LOCATION.Y")),
                          "size": (coord(r, "XSIZE"), coord(r, "YSIZE")),
                          "type": r.get("SYMBOLTYPE", "Normal"),
                          # managed (device) sheets: Altium keeps the sheet in
                          # Managed/Sheets/<revision GUID>/ of the project
                          "revision": r.get("REVISIONGUID", ""),
                          "item": r.get("DESIGNITEMID", "")}
        elif kind == "18":
            x, y = coord(r, "LOCATION.X"), coord(r, "LOCATION.Y")
            w = coord(r, "WIDTH") or 500.0
            ends = [(x, y), (x, y + w)] if _int(r, "STYLE") >= 4 else [(x, y), (x + w, y)]
            ports.append({"name": r.get("NAME", ""), "io": _int(r, "IOTYPE"),
                          "harness": r.get("HARNESSTYPE", ""), "at": (x, y), "ends": ends})
            attach += ends
        elif kind in ("27", "26"):
            wires.append(_points(r))
        elif kind == "2":
            # a pin's hot end, placed right on an entry, connects to it
            x, y, length = coord(r, "LOCATION.X"), coord(r, "LOCATION.Y"), coord(r, "PINLENGTH")
            dx, dy = {0: (1, 0), 1: (0, 1), 2: (-1, 0), 3: (0, -1)}[_int(r, "PINCONGLOMERATE") & 3]
            attach.append((x + dx * length, y + dy * length))
        elif kind == "17":
            attach.append((coord(r, "LOCATION.X"), coord(r, "LOCATION.Y")))
            power.add(_key(r.get("TEXT", "")))
        elif kind == "22" and r.get("ISACTIVE", "T") != "F":
            no_erc.append((coord(r, "LOCATION.X"), coord(r, "LOCATION.Y")))
    for r in recs:
        if not r or "OWNERINDEX" not in r:
            continue
        owner = int(r["OWNERINDEX"]) + 1
        if owner not in symbols:
            continue
        kind = r.get("RECORD")
        if kind == "16":
            symbols[owner]["entries"].append({
                "name": r.get("NAME", ""), "io": _int(r, "IOTYPE"), "harness": r.get("HARNESSTYPE", ""),
                "side": _int(r, "SIDE"), "distance": _distance(r)})
        elif kind == "32":
            symbols[owner]["designator"] = r.get("TEXT", "")
        elif kind == "33":
            symbols[owner]["file"] = r.get("TEXT", "")
    for sym in symbols.values():
        for e in sym["entries"]:
            e["at"] = _entry_point(sym, e)

    connectors, harnesses = {}, []
    for i, r in enumerate(additional):
        if r and r.get("RECORD") == "215":
            x, y = coord(r, "LOCATION.X"), coord(r, "LOCATION.Y")
            w, h = coord(r, "XSIZE"), coord(r, "YSIZE")
            pcp = coord(r, "PRIMARYCONNECTIONPOSITION")
            side = _int(r, "HARNESSCONNECTORSIDE")
            primary = {0: (x, y - pcp), 1: (x + w, y - pcp), 2: (x + pcp, y), 3: (x + pcp, y - h)}.get(side)
            connectors[i] = {"type": "", "entries": [], "at": (x, y), "primary": primary}
        elif r and r.get("RECORD") == "218":
            harnesses.append(_points(r))
    for r in additional:
        if not r or r.get("RECORD") not in ("216", "217"):
            continue
        owner = int(r.get("OWNERINDEX", 0) or 0) + 1
        if owner not in connectors:
            continue
        if r["RECORD"] == "217":
            connectors[owner]["type"] = r.get("TEXT", "")
        else:
            connectors[owner]["entries"].append((_distance(r), r.get("NAME", "")))
    hcs = []
    for c in connectors.values():
        c["entries"] = [n for _, n in sorted(c["entries"])]
        hcs.append(c)
    return {"path": Path(path), "symbols": list(symbols.values()), "ports": ports, "connectors": hcs,
            "wires": wires, "harnesses": harnesses, "attach": attach, "power": power, "no_erc": no_erc}


def harness_networks(sheet):
    """Signal harnesses joined where a vertex of one lies on another, each
    with the harness types of the connectors, ports and entries it reaches."""
    hs = sheet["harnesses"]
    parent = list(range(len(hs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(hs):
        for j in range(i + 1, len(hs)):
            if any(_touches(p, hs[j]) for p in a) or any(_touches(p, a) for p in hs[j]):
                parent[find(i)] = find(j)
    nets = defaultdict(list)
    for i in range(len(hs)):
        nets[find(i)].append(hs[i])
    out = []
    for lines in nets.values():
        reached = []
        for c in sheet["connectors"]:
            if c["primary"] and any(_touches(c["primary"], l) for l in lines):
                reached.append(("harness connector", c["type"], c["primary"]))
        for p in sheet["ports"]:
            if any(_touches(e, l) for e in p["ends"] for l in lines):
                reached.append((f"port {p['name']}", p["harness"], p["at"]))
        for sym in sheet["symbols"]:
            for e in sym["entries"]:
                if any(_touches(e["at"], l) for l in lines):
                    reached.append((f"entry {e['name']} of {sym['designator']}", e["harness"], e["at"]))
        out.append({"start": lines[0][0], "end": lines[0][-1], "segments": len(lines), "reached": reached})
    return out


def read_harness_file(path):
    """{type: [entry names]} from a .Harness file ("S1=A,B,C" lines)."""
    out = {}
    for line in Path(path).read_text(encoding="utf-8-sig", errors="replace").splitlines():
        if "=" in line:
            t, names = line.split("=", 1)
            out[t.strip()] = [n for n in names.split(",") if n != ""]
    return out


# ------------------------------------------------------------------ checking

class HierarchyChecker:
    def __init__(self, prjpcb, reader=read_sheet):
        self.prj = Path(prjpcb)
        self.reader = reader
        self.sheet_paths, self.harness_paths, self.settings = read_project(prjpcb)
        self.sheets = {}
        self.issues = []
        self.unreadable = []
        for p in self.sheet_paths:
            try:
                self.sheets[p] = self.reader(p)
            except Exception as e:  # missing or locked file
                self.unreadable.append({"sheet": p.name, "error": str(e)})
        # file name (and name without extension) -> every sheet of that name;
        # two folders may each hold one, managed sheets in particular
        self.by_name = defaultdict(list)
        for p in self.sheets:
            for k in {p.name.casefold(), p.stem.casefold()}:
                self.by_name[k].append(p)
        self.links = []   # (parent sheet, symbol, child path or None)

    @staticmethod
    def at(base):
        x, y = base["symbol_at"]
        return f"{x:g},{y:g}"

    def add(self, severity, kind, message, **extra):
        # a suggested op names its sheet symbol by designator; the location
        # tells apart symbols that share one
        for key in ("suggest", "alternative"):
            op = extra.get(key)
            if op and "symbol" in op and "symbol_at" not in op and "symbol_at" in extra:
                op["symbol_at"] = extra["symbol_at"]
        self.issues.append({"severity": severity, "type": kind, "message": message, **extra})

    # -- hierarchy
    def _load(self, path):
        """A sheet outside the project, read once; None if unreadable."""
        if path not in self.sheets:
            try:
                self.sheets[path] = self.reader(path)
            except Exception:
                return None
        return path

    def resolve_child(self, parent, file_text, sym):
        """(child path or None, in project, candidates when ambiguous)."""
        text = file_text.strip()
        if not text.lower().endswith(".schdoc"):
            text += ".SchDoc"
        name = Path(text).name
        in_project = set(self.sheet_paths)
        # a managed sheet: the copy of exactly this revision
        if sym.get("revision"):
            cand = Path(os.path.normpath(self.prj.parent / "Managed" / "Sheets" / sym["revision"] / name))
            if cand in self.sheets or cand.exists():
                got = self._load(cand)
                return got, got in in_project, []
        # a path given on the symbol, relative to the parent sheet
        if Path(text).parent != Path("."):
            cand = Path(os.path.normpath(parent.parent / text))
            if cand in self.sheets or cand.exists():
                got = self._load(cand)
                return got, got in in_project, []
        cands = self.by_name.get(name.casefold()) or self.by_name.get(Path(name).stem.casefold()) or []
        if len(cands) == 1:
            return cands[0], True, []
        if len(cands) > 1:
            here = [c for c in cands if c.parent == parent.parent]
            if len(here) == 1:
                return here[0], True, []
            return None, True, sorted(cands)
        cand = parent.parent / text
        if cand.exists():
            got = self._load(cand)
            return got, False, []
        return None, False, []

    def build_links(self):
        for path, sh in list(self.sheets.items()):
            for sym in sh["symbols"]:
                files = [f for f in re.split(r"[;]", sym["file"]) if f.strip()]
                if not files:
                    self.add("error", "sheet_symbol_without_file",
                             f"sheet symbol {sym['designator']!r} on {path.name} names no child sheet",
                             sheet=path.name, sheet_symbol=sym["designator"], at=sym["at"])
                design_item = sym.get("type", "Normal") != "Normal"
                for f in files:
                    child, in_project, ambiguous = self.resolve_child(path, f, sym)
                    if ambiguous:
                        rel = [os.path.relpath(c, self.prj.parent) for c in ambiguous]
                        self.add("warning", "child_sheet_ambiguous",
                                 f"sheet symbol {sym['designator']!r} on {path.name} points to {f!r}, and the "
                                 f"project has more than one sheet of that name ({', '.join(rel)}); its "
                                 "entries are not checked",
                                 sheet=path.name, sheet_symbol=sym["designator"], child=f, candidates=rel,
                                 at=sym["at"])
                    elif child is None and design_item:
                        self.add("info", "device_sheet_not_local",
                                 f"sheet symbol {sym['designator']!r} on {path.name} is a "
                                 f"{sym.get('type')} ({sym.get('item') or f}) whose sheet is not on disk; "
                                 "Altium fetches it from the workspace or a device sheet folder, so its "
                                 "entries are not checked",
                                 sheet=path.name, sheet_symbol=sym["designator"], child=f,
                                 design_item=sym.get("item"), at=sym["at"])
                    elif child is None:
                        self.add("error", "child_sheet_missing",
                                 f"sheet symbol {sym['designator']!r} on {path.name} points to {f!r}, "
                                 "which is neither in the project nor in its folder",
                                 sheet=path.name, sheet_symbol=sym["designator"], child=f, at=sym["at"])
                    elif not in_project and not design_item:
                        self.add("warning", "child_not_in_project",
                                 f"{child.name} (child of {sym['designator']!r} on {path.name}) is not in the project",
                                 sheet=path.name, sheet_symbol=sym["designator"], child=child.name)
                    self.links.append((path, sym, child))

    def check_links(self):
        uses = Counter(c for _, _, c in self.links if c is not None)
        for parent, sym, child in self.links:
            if child is None:
                continue
            label = sym["designator"] or sym["file"]
            base = dict(sheet=parent.name, sheet_symbol=label, child=child.name,
                        symbol_at=[round(v, 3) for v in sym["at"]])
            ports = self.sheets[child]["ports"]
            port_by = defaultdict(list)
            for p in ports:
                port_by[_key(p["name"])].append(p)
            seen = Counter()
            matched_ports = set()
            unmatched_entries = []
            for e in sym["entries"]:
                name = _repeat_base(e["name"])
                seen[_key(name)] += 1
                if seen[_key(name)] == 2:
                    self.add("warning", "duplicate_entry",
                             f"{label} on {parent.name} has entry {e['name']!r} more than once",
                             entry=e["name"], **base)
                hits = port_by.get(_key(name))
                if not hits:
                    unmatched_entries.append(e)
                    continue
                matched_ports.add(_key(name))
                p = hits[0]
                if p["name"] != name:
                    self.add("warning", "name_case",
                             f"entry {e['name']!r} of {label} and port {p['name']!r} on {child.name} differ "
                             "only in case; use the same spelling on both",
                             entry=e["name"], port=p["name"], **base)
                if (e["harness"] or "") != (p["harness"] or ""):
                    self.add("error", "harness_type_mismatch",
                             f"entry {e['name']!r} of {label} is harness type {e['harness'] or '(none)'!r}, "
                             f"port {p['name']!r} on {child.name} is {p['harness'] or '(none)'!r}",
                             entry=e["name"], port=p["name"], entry_harness=e["harness"],
                             port_harness=p["harness"], **base,
                             suggest={"op": "set_entry", "sheet": parent.name, "symbol": label,
                                      "name": e["name"], "harness": p["harness"]})
                ios = {p2["io"] for p2 in hits}
                if e["harness"] or p["harness"]:
                    # Altium keeps no I/O type on harness entries/ports (a stale
                    # IOTYPE may remain in the file; the API reads it as 0)
                    continue
                if e["io"] and any(io and io != e["io"] for io in ios):
                    pio = next(io for io in ios if io and io != e["io"])
                    self.add("warning", "io_mismatch",
                             f"entry {e['name']!r} of {label} is {IO_TYPES.get(e['io'])}, "
                             f"port {p['name']!r} on {child.name} is {IO_TYPES.get(pio)}",
                             entry=e["name"], port=p["name"], entry_io=IO_TYPES.get(e["io"]),
                             port_io=IO_TYPES.get(pio), **base,
                             suggest={"op": "set_entry", "sheet": parent.name, "symbol": label,
                                      "name": e["name"], "io": pio})
                elif bool(e["io"]) != any(ios):
                    self.add("warning", "io_unspecified",
                             f"entry {e['name']!r} of {label} is {IO_TYPES.get(e['io'])}, "
                             f"port on {child.name} is {', '.join(IO_TYPES.get(i) for i in sorted(ios))}",
                             entry=e["name"], port=p["name"], **base)

            unmatched_ports = []
            for k, ps in port_by.items():
                if k not in matched_ports:
                    unmatched_ports.append(ps[0])
            power = self.sheets[child]["power"]
            self.pair_up(unmatched_entries, unmatched_ports, port_by, uses[child] > 1, base, sym,
                         self.linked_elsewhere(child, sym), power)

    def linked_elsewhere(self, child, sym):
        """Port names of child that another sheet symbol using it does link."""
        names = set()
        for _, other, c in self.links:
            if c == child and other is not sym:
                names |= {_key(_repeat_base(e["name"])) for e in other["entries"]}
        return names

    def pair_up(self, entries, ports, port_by, shared_child, base, sym, elsewhere=frozenset(), power=frozenset()):
        """Unmatched entries and ports of one symbol/child pair: suggest which
        belong together, report the rest."""
        free = {_key(p["name"]): p for p in ports}
        pairs = []
        for e in entries:
            name = _repeat_base(e["name"])
            choice, why = None, None
            if e["harness"]:
                same = [p for p in free.values() if p["harness"] == e["harness"]]
                if len(same) == 1:
                    choice, why = same[0], f"same harness type {e['harness']}"
            if choice is None:
                loose = [p for p in free.values() if _loose(p["name"]) == _loose(name) and _loose(name)]
                if len(loose) == 1:
                    choice, why = loose[0], "same name apart from case/separators"
            if choice is None and len(name) >= 3:
                names = {p["name"]: p for p in free.values()}
                close = difflib.get_close_matches(name, list(names), n=1, cutoff=0.8)
                if close:
                    choice, why = names[close[0]], "similar name"
            if choice is not None:
                del free[_key(choice["name"])]
                pairs.append((e, choice, why))
            elif _key(name) in power:
                self.add("info", "entry_matches_power_port",
                         f"entry {e['name']!r} of {base['sheet_symbol']} has no port on {base['child']}, "
                         "but a power port of that name; power ports connect globally unless the net "
                         "identifier scope is Strict Hierarchical",
                         entry=e["name"], **base)
            else:
                self.add("warning", "entry_without_port",
                         f"entry {e['name']!r} of {base['sheet_symbol']} on {base['sheet']} ({self.at(base)}) has no port "
                         f"on {base['child']}",
                         entry=e["name"], entry_harness=e["harness"], **base,
                         suggest={"altium": "Design >> Synchronize Sheet Entries and Ports: add the port "
                                            "to the child, or delete the entry"})
        for e, p, why in pairs:
            if shared_child:
                op = {"op": "rename_entry", "sheet": base["sheet"], "symbol": base["sheet_symbol"],
                      "old": e["name"], "new": p["name"]}
                alt = {"op": "rename_port", "sheet": base["child"], "old": p["name"], "new": e["name"]}
                note = f"{base['child']} is used by more than one sheet symbol, so rename the entry"
            else:
                op = {"op": "rename_port", "sheet": base["child"], "old": p["name"], "new": e["name"]}
                alt = {"op": "rename_entry", "sheet": base["sheet"], "symbol": base["sheet_symbol"],
                       "old": e["name"], "new": p["name"]}
                note = "rename the port, the parent sheet's entry names are kept"
            # a pair found by a similar name alone is a guess
            self.add("warning" if why == "similar name" else "error", "probable_pair",
                     f"entry {e['name']!r} of {base['sheet_symbol']} and port {p['name']!r} on "
                     f"{base['child']} do not connect, but probably belong together ({why})",
                     entry=e["name"], port=p["name"], reason=why, note=note, **base,
                     suggest=op, alternative=alt)
        for p in free.values():
            count = len(port_by[_key(p["name"])])
            if _key(p["name"]) in elsewhere:
                self.add("warning", "port_without_entry",
                         f"port {p['name']!r} on {base['child']} has no entry on {base['sheet_symbol']} "
                         f"({base['sheet']} at {self.at(base)}); another sheet symbol using {base['child']} "
                         "does link it, so Altium's ERC does not report this instance",
                         port=p["name"], port_harness=p["harness"], linked_on_other_instance=True, **base,
                         suggest={"op": "add_entry", "sheet": base["sheet"], "symbol": base["sheet_symbol"],
                                  "name": p["name"], "io": p["io"], "harness": p["harness"]})
            elif count > 1:
                self.add("info", "port_without_entry",
                         f"port {p['name']!r} on {base['child']} ({count}x on that sheet, so it may be a "
                         f"local link) has no entry on {base['sheet_symbol']}",
                         port=p["name"], port_harness=p["harness"], **base)
            else:
                self.add("warning", "port_without_entry",
                         f"port {p['name']!r} on {base['child']} has no entry on {base['sheet_symbol']} "
                         f"({base['sheet']} at {self.at(base)}), so it connects to nothing above",
                         port=p["name"], port_harness=p["harness"], **base,
                         suggest={"op": "add_entry", "sheet": base["sheet"], "symbol": base["sheet_symbol"],
                                  "name": p["name"], "io": p["io"], "harness": p["harness"],
                                  "altium": "Design >> Synchronize Sheet Entries and Ports: add the entry"})

    def check_wiring(self):
        """Sheet entries with nothing attached on the parent sheet, and
        signal harnesses whose type nobody gives."""
        for path, sh in self.sheets.items():
            lines = sh["wires"] + sh["harnesses"]
            entry_points = Counter((round(e["at"][0], 1), round(e["at"][1], 1))
                                   for sym in sh["symbols"] for e in sym["entries"])
            for sym in sh["symbols"]:
                for e in sym["entries"]:
                    pt = e["at"]
                    wired = any(abs(pt[0] - v[0]) <= 0.6 and abs(pt[1] - v[1]) <= 0.6
                                for l in lines for v in l)
                    wired = wired or any(abs(pt[0] - a[0]) <= 0.6 and abs(pt[1] - a[1]) <= 0.6
                                         for a in sh["attach"])
                    # two sheet symbols butted together, entry on entry
                    wired = wired or entry_points[(round(pt[0], 1), round(pt[1], 1))] > 1
                    # a No ERC marker on the entry: left open on purpose
                    marked = any(abs(pt[0] - m[0]) <= 0.6 and abs(pt[1] - m[1]) <= 0.6
                                 for m in sh.get("no_erc", ()))
                    if not wired and not marked:
                        self.add("warning", "entry_unwired",
                                 f"entry {e['name']!r} of {sym['designator']} on {path.name} at "
                                 f"{pt[0]:g},{pt[1]:g} has no wire, bus or harness attached",
                                 sheet=path.name, sheet_symbol=sym["designator"], entry=e["name"],
                                 at=[round(v, 3) for v in pt])
            for net in harness_networks(sh):
                types = {t for _, t, _ in net["reached"] if t}
                where = f"{net['start'][0]:g},{net['start'][1]:g}"
                reached = [f"{w} ({t or 'no type'})" for w, t, _ in net["reached"]]
                if not types:
                    self.add("error", "signal_harness_untyped",
                             f"signal harness at {where} on {path.name} reaches no harness connector or "
                             f"typed port/entry, so its harness type is unknown (Altium: fatal 'Missing "
                             f"harness type on harness')" + (f"; it reaches {', '.join(reached)}" if reached else
                                                             "; it is connected to nothing"),
                             sheet=path.name, at=[round(v, 3) for v in net["start"]], reached=reached)
                elif len(types) > 1:
                    self.add("error", "signal_harness_type_conflict",
                             f"signal harness at {where} on {path.name} joins harness types "
                             f"{', '.join(sorted(types))}: {', '.join(reached)}",
                             sheet=path.name, at=[round(v, 3) for v in net["start"]], reached=reached)

    def check_roots(self):
        children = {c for _, _, c in self.links if c is not None}
        parents = {p for p, _, _ in self.links}
        roots = [p for p in self.sheets if p not in children and p in parents]
        orphans = [p for p in self.sheet_paths if p in self.sheets and p not in children and p not in parents]
        if not self.links:
            return [], []
        for p in orphans:
            self.add("info", "sheet_not_in_hierarchy",
                     f"{p.name} is in the project but no sheet symbol uses it",
                     sheet=p.name, ports=len(self.sheets[p]["ports"]))
        for p in roots:
            ports = self.sheets[p]["ports"]
            if ports:
                self.add("info", "top_sheet_ports",
                         f"top sheet {p.name} has {len(ports)} port(s); they connect to nothing above",
                         sheet=p.name, ports=sorted({x["name"] for x in ports}))
        return roots, orphans

    # -- harnesses
    def check_harness(self):
        defs = defaultdict(list)   # type -> [(sheet, entries)]
        for path, sh in self.sheets.items():
            for c in sh["connectors"]:
                if c["type"]:
                    defs[c["type"]].append((path.name, c["entries"], c["at"]))
        for t, ds in sorted(defs.items()):
            sets = {}
            for sheet, entries, at in ds:
                sets.setdefault(frozenset(entries), []).append(sheet)
            if len(sets) > 1:
                variants = [{"sheets": sorted(set(s)), "entries": sorted(k)} for k, s in sets.items()]
                common = frozenset.intersection(*sets)
                self.add("error", "harness_conflict",
                         f"harness type {t!r} is defined with different entries on "
                         + " / ".join(", ".join(v["sheets"]) for v in variants),
                         harness=t, variants=[{**v, "only_here": sorted(set(v["entries"]) - common)}
                                              for v in variants])
            else:
                orders = {tuple(e) for _, e, _ in ds}
                if len(orders) > 1:
                    self.add("info", "harness_order",
                             f"harness type {t!r} has the same entries in a different order on "
                             + ", ".join(sorted({s for s, _, _ in ds})), harness=t)

        used = defaultdict(list)
        for path, sh in self.sheets.items():
            for p in sh["ports"]:
                if p["harness"]:
                    used[p["harness"]].append(f"port {p['name']} on {path.name}")
            for sym in sh["symbols"]:
                for e in sym["entries"]:
                    if e["harness"]:
                        used[e["harness"]].append(f"entry {e['name']} of {sym['designator']} on {path.name}")
        for t, where in sorted(used.items()):
            if t not in defs and t != "*":
                self.add("error", "harness_type_undefined",
                         f"harness type {t!r} is used ({len(where)}x) but no harness connector defines it",
                         harness=t, used_by=where[:20])

        # .Harness files against the connectors of their sheet
        listed = {p.name.casefold() for p in self.harness_paths}
        for path, sh in self.sheets.items():
            hf = path.with_suffix(".Harness")
            want = {}
            for c in sh["connectors"]:
                if c["type"]:
                    want.setdefault(c["type"], c["entries"])
            if not hf.exists():
                if want:
                    self.add("warning", "harness_file_missing",
                             f"{path.name} has harness connectors ({', '.join(sorted(want))}) but no {hf.name}; "
                             "Altium writes it on save/compile", sheet=path.name)
                continue
            have = read_harness_file(hf)
            diff = {}
            for t in sorted(set(want) | set(have)):
                if t not in have:
                    diff[t] = "missing in file"
                elif t not in want:
                    diff[t] = "in file, no connector on the sheet"
                elif set(have[t]) != set(want[t]):
                    diff[t] = {"file_only": sorted(set(have[t]) - set(want[t])),
                               "sheet_only": sorted(set(want[t]) - set(have[t]))}
            if diff:
                self.add("warning", "harness_file_stale",
                         f"{hf.name} does not match the harness connectors on {path.name}",
                         sheet=path.name, harness_file=hf.name, differences=diff)
            if hf.name.casefold() not in listed:
                self.add("info", "harness_file_not_in_project",
                         f"{hf.name} exists but is not in the project", sheet=path.name)

    def run(self):
        self.build_links()
        self.check_links()
        self.check_wiring()
        roots, orphans = self.check_roots()
        self.check_harness()
        self.issues.sort(key=lambda i: (SEVERITY_ORDER[i["severity"]], i["type"],
                                        i.get("sheet", ""), i.get("sheet_symbol", "")))
        return roots, orphans


def check_hierarchy(prjpcb, max_issues=300, min_severity="info", reader=read_sheet):
    c = HierarchyChecker(prjpcb, reader)
    roots, orphans = c.run()
    limit = SEVERITY_ORDER[min_severity]
    shown = [i for i in c.issues if SEVERITY_ORDER[i["severity"]] <= limit]
    counts = defaultdict(lambda: defaultdict(int))
    for i in c.issues:
        counts[i["severity"]][i["type"]] += 1
    mode = c.settings.get("HierarchyMode")
    notes = []
    if mode not in (None, "0"):
        notes.append("net identifier scope is not Automatic: check that it is a hierarchical one, "
                     "otherwise ports connect globally and entry/port mismatches matter less")
    return {
        "project": str(c.prj),
        "settings": {**c.settings, "HierarchyModeName": HIERARCHY_MODES.get(int(mode), "see Project Options")
                     if mode and mode.isdigit() else None},
        "notes": notes,
        "top_sheets": [p.name for p in roots],
        "sheets_not_in_hierarchy": [p.name for p in orphans],
        "sheet_symbols": len(c.links),
        "unreadable": c.unreadable,
        "issue_counts": {s: dict(v) for s, v in counts.items()},
        "issues": shown[:max_issues],
        "truncated": max(0, len(shown) - max_issues),
    }


if __name__ == "__main__":
    import json
    import sys
    print(json.dumps(check_hierarchy(sys.argv[1]), indent=1, ensure_ascii=False))
