"""Reading an Altium database library (.DbLib) and its Excel table, so that
edit_schematic can place a part by its key the way Altium's own placement
from a DbLib does.

Altium's script API has SchServer.LoadComponentFromDatabaseLibrary, but on
AD25 it returns nil for every form of library name and table tried. So the
part's row is read here: the symbol is placed from its SchLib (Library Ref,
Library Path), then given what Altium writes for a DbLib part, as found on a
part Altium placed itself:
  * SourceLibraryName = the .DbLib file name, LibraryPath = '*',
    DatabaseTableName = the table ('Resistor$'), DesignItemId = the key;
  * every column of the row as a parameter, named by the field map's
    ParameterName (the column name if it has none), hidden unless the map
    says VisibleOnAdd; bracketed names ([Description], [Library Ref], ...)
    are Altium's own fields, not parameters;
  * the footprint from Footprint Ref (and Footprint Ref2, Ref3 as
    alternatives), found through the libraries like any DbLib footprint;
  * the key as the comment, unless the row has a Comment column.

Only Excel (.xlsx) tables are read, with the standard library: the file is
a zip of XML. A cell is read as Altium's Excel driver gives it: a percent
format as a percentage, and decimals with the Windows decimal separator
(on a decimal-comma system, 0,5%).
"""
import configparser
import ctypes
import os
import re
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


class DbLibError(ValueError):
    pass


def _options(text: str) -> Dict[str, str]:
    out = {}
    for part in text.split("|"):
        key, sep, value = part.partition("=")
        if sep:
            out[key] = value
    return out


def read_dblib(path: str) -> dict:
    """The .DbLib's table names (enabled ones), its database file and its
    field maps: {"database": path, "tables": [...], "fields": {table: [map]}}.
    Each map: field (column), parameter, visible, key (FieldType 0)."""
    p = Path(path)
    if not p.is_file():
        raise DbLibError(f"DbLib not found: {path}")
    raw = p.read_bytes()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", "replace")
    ini = configparser.ConfigParser(interpolation=None, strict=False)
    ini.optionxform = str
    ini.read_string(text.replace("\x00", ""))
    links = ini["DatabaseLinks"] if ini.has_section("DatabaseLinks") else {}
    db = links.get("LibraryDatabasePath", "")
    if not db:
        m = re.search(r"Data Source=([^;]+)", links.get("ConnectionString", ""))
        db = m.group(1) if m else ""
    if db and links.get("DatabasePathRelative", "0") == "1" and not Path(db).is_absolute():
        db = str(p.parent / db)
    tables = [ini[s]["TableName"] for s in ini.sections()
              if s.startswith("Table") and ini[s].get("Enabled", "True") == "True" and "TableName" in ini[s]]
    fields: Dict[str, List[dict]] = {}
    for s in ini.sections():
        if s.startswith("FieldMap") and "Options" in ini[s]:
            o = _options(ini[s]["Options"])
            fields.setdefault(o.get("TableNameOnly", ""), []).append({
                "field": o.get("FieldNameOnly", ""), "parameter": o.get("ParameterName", ""),
                "visible": o.get("VisibleOnAdd", "False") == "True", "key": o.get("FieldType") == "0"})
    return {"path": str(p), "name": p.name, "database": db, "tables": tables, "fields": fields,
            "search_path": links.get("LibrarySearchPath", "")}


def decimal_separator() -> str:
    """The Windows user's decimal separator (LOCALE_SDECIMAL)."""
    try:
        buf = ctypes.create_unicode_buffer(8)
        if ctypes.windll.kernel32.GetLocaleInfoW(0x0400, 0x000E, buf, 8):
            return buf.value or "."
    except (AttributeError, OSError):
        pass
    return "."


def _number(v: float, sep: str) -> str:
    text = str(int(v)) if float(v).is_integer() else f"{v:.15g}"
    return text.replace(".", sep)


def _cell_text(cell, strings: List[str], percent_styles=frozenset(), sep: str = ".") -> str:
    kind = cell.get("t")
    if kind == "inlineStr":
        return "".join(t.text or "" for t in cell.iter(f"{{{_NS['m']}}}t"))
    v = cell.find("m:v", _NS)
    if v is None or v.text is None:
        return ""
    if kind == "s":
        return strings[int(v.text)]
    if kind in ("str", "b", "e"):
        return v.text
    try:
        f = float(v.text)
    except ValueError:
        return v.text
    if cell.get("s") is not None and int(cell.get("s")) in percent_styles:
        return _number(round(f * 100, 10), sep) + "%"
    return _number(f, sep)


def _percent_styles(z: zipfile.ZipFile) -> frozenset:
    """Indexes of the cell styles whose number format is a percentage."""
    if "xl/styles.xml" not in z.namelist():
        return frozenset()
    root = ElementTree.fromstring(z.read("xl/styles.xml"))
    custom = {int(f.get("numFmtId")): f.get("formatCode", "")
              for f in root.iter(f"{{{_NS['m']}}}numFmt")}
    xfs = root.find("m:cellXfs", _NS)
    out = set()
    for i, xf in enumerate(xfs.findall("m:xf", _NS) if xfs is not None else []):
        fid = int(xf.get("numFmtId", "0"))
        if fid in (9, 10) or "%" in custom.get(fid, "").replace(r"\%", ""):
            out.add(i)
    return frozenset(out)


def _column(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_xlsx_sheet(path: str, sheet: str) -> List[Dict[str, str]]:
    """The rows of one sheet as {header: value}, the first row as headers."""
    try:
        z = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as e:
        raise DbLibError(f"cannot read {path}: {e}")
    with z:
        names = set(z.namelist())
        strings = []
        if "xl/sharedStrings.xml" in names:
            root = ElementTree.fromstring(z.read("xl/sharedStrings.xml"))
            strings = ["".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")) for si in root.findall("m:si", _NS)]
        percent = _percent_styles(z)
        book = ElementTree.fromstring(z.read("xl/workbook.xml"))
        rels = ElementTree.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        targets = {r.get("Id"): r.get("Target") for r in rels}
        target = None
        for s in book.iter(f"{{{_NS['m']}}}sheet"):
            if s.get("name", "").lower() == sheet.lower():
                target = targets.get(s.get(_REL))
        if target is None:
            raise DbLibError(f"no sheet '{sheet}' in {path}")
        target = target.lstrip("/")
        target = target if target.startswith("xl/") else "xl/" + target
        root = ElementTree.fromstring(z.read(target))
    rows = []
    sep = decimal_separator()
    for row in root.iter(f"{{{_NS['m']}}}row"):
        cells = {}
        for c in row.findall("m:c", _NS):
            cells[_column(c.get("r"))] = _cell_text(c, strings, percent, sep)
        rows.append(cells)
    if not rows:
        return []
    headers = {i: h.strip() for i, h in rows[0].items() if h.strip()}
    return [{h: r.get(i, "") for i, h in headers.items()} for r in rows[1:] if any(v for v in r.values())]


def key_field(lib: dict, table: str, rows: List[Dict[str, str]]) -> str:
    """The table's key column: the field mapped as key, else the first."""
    for f in lib["fields"].get(table, []):
        if f["key"]:
            return f["field"]
    return next(iter(rows[0])) if rows else ""


def find_part(lib: dict, part: str, table: Optional[str] = None) -> Tuple[str, Dict[str, str]]:
    """(table, row) of the part whose key is `part`, in `table` or any
    enabled table."""
    if not lib["database"].lower().endswith((".xlsx", ".xlsm")):
        raise DbLibError(f"only Excel tables can be read; this DbLib uses {lib['database']}")
    tables = lib["tables"]
    if table:
        wanted = table if table.endswith("$") else table + "$"
        tables = [t for t in tables if t.lower() == wanted.lower()]
        if not tables:
            raise DbLibError(f"no enabled table '{table}' in {lib['name']}; tables: {', '.join(lib['tables'])}")
    found = []
    for t in tables:
        rows = read_xlsx_sheet(lib["database"], t.rstrip("$"))
        key = key_field(lib, t, rows)
        found += [(t, r) for r in rows if r.get(key, "").strip() == part.strip()]
    if not found:
        raise DbLibError(f"'{part}' is not a key in {lib['name']}" + (f" table {tables[0]}" if table else ""))
    if len(found) > 1:
        raise DbLibError(f"'{part}' is in several tables ({', '.join(t for t, _ in found)}); give table")
    return found[0]


def _find_file(lib: dict, name: str) -> Optional[str]:
    """A library file named in a row: relative to the DbLib's folder, its
    search path, or found by name below them."""
    if not name:
        return None
    base = Path(lib["path"]).parent
    roots = [base] + [Path(p) if Path(p).is_absolute() else base / p
                      for p in lib.get("search_path", "").split(";") if p]
    for root in roots:
        # normpath, not resolve: a profile folder reached through a junction
        # must keep the name Altium knows it by
        if (root / name).is_file():
            return os.path.normpath(str(root / name))
    for root in roots:
        hits = list(root.rglob(Path(name).name)) if root.is_dir() else []
        if hits:
            return os.path.normpath(str(hits[0]))
    return None


def place_operation(op: dict) -> dict:
    """A place_component operation given as dblib + part_number turned into
    the SchLib placement plus the DbLib link (keys db_*)."""
    lib = read_dblib(op["dblib"])
    table, row = find_part(lib, str(op["part_number"]), op.get("table"))
    maps = {f["field"]: f for f in lib["fields"].get(table, [])}

    def system(name):
        for field, m in maps.items():
            if m["parameter"].lower() == f"[{name}]".lower():
                return row.get(field, "").strip()
        return row.get(name, "").strip()

    symbol, library = system("Library Ref"), system("Library Path")
    if not symbol or not library:
        raise DbLibError(f"the row of '{op['part_number']}' has no Library Ref or Library Path")
    library_file = _find_file(lib, library)
    if not library_file:
        raise DbLibError(f"symbol library '{library}' of '{op['part_number']}' not found next to {lib['name']}")
    params = []
    for field, value in row.items():
        m = maps.get(field)
        name = m["parameter"] if m and m["parameter"] else field
        if name.startswith("["):
            continue
        params.append((name, value, bool(m and m["visible"])))
    footprints = [system(f) for f in ("Footprint Ref", "Footprint Ref2", "Footprint Ref3")]
    out = dict(op)
    for k in ("dblib", "part_number", "table"):
        out.pop(k, None)
    out.update({"library": library_file, "symbol": symbol, "db_library": lib["name"], "db_table": table,
                "db_key": str(op["part_number"]).strip(), "db_description": system("Description"),
                "db_parameters": params, "db_footprints": [f for f in footprints if f]})
    # Altium's DbLib placement shows the key as the comment
    if not op.get("comment"):
        out["comment"] = system("Comment") or out["db_key"]
    return out
