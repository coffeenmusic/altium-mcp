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
    are Altium's own fields, not parameters. The field map's AddMode ("Add
    To Design") is honoured: 1 do not add, 2 add, 3 add only if the cell is
    not blank; 0 (Default) takes the library's AddMode in [DatabaseLinks];
  * the footprint from Footprint Ref (and Footprint Ref2, Ref3 as
    alternatives), found through the libraries like any DbLib footprint;
  * the key as the comment, unless the row has a Comment column.

Only Excel (.xlsx) tables are read, with the standard library: the file is
a zip of XML. A cell is read as Altium's Excel driver gives it: a percent
format as a percentage, and decimals with the Windows decimal separator
(on a decimal-comma system, 0,5%). A table is a sheet name with '$', quoted
when the name has a space: 'My Sheet$'. Field maps are matched to their
table by the sheet name, so the quoting of either does not matter.

A symbol library named in a row is looked up next to the DbLib and on its
search path, and below them only when the DbLib asks for sub-directories
(SearchSubDirectories=1), a bounded number of folders deep.
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


# Field map AddMode ("Add To Design"); 0 is Default, the library's own
ADD_DEFAULT, ADD_NEVER, ADD_ALWAYS, ADD_IF_NOT_BLANK = 0, 1, 2, 3
# Sub-directory search: at most this many folders, this deep
SEARCH_MAX_DIRS, SEARCH_MAX_DEPTH = 2000, 8


class DbLibError(ValueError):
    pass


class Row(dict):
    """A table row, {column: text}, knowing its Excel row number and each
    column's letter, so an error can name the cell."""
    def __init__(self, values, number=0, letters=None):
        super().__init__(values)
        self.number = number
        self.letters = letters or {}

    def cell(self, column: str) -> str:
        return f"{self.letters.get(column, '?')}{self.number}" if self.number else column


def sheet_name(table: str) -> str:
    """The Excel sheet a DbLib table names: 'My Sheet$' -> My Sheet,
    Resistor$ -> Resistor (the OLE DB driver quotes names with spaces and
    doubles an apostrophe)."""
    t = table.strip()
    if len(t) > 1 and t[0] == t[-1] == "'":
        t = t[1:-1].replace("''", "'")
    return t[:-1] if t.endswith("$") else t


def _int(text: str, default: int) -> int:
    try:
        return int(text)
    except (TypeError, ValueError):
        return default


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
    Each map: field (column), parameter, visible, key (FieldType 0) and add
    (its AddMode, Default resolved to the library's)."""
    p = Path(path)
    if not p.is_file():
        raise DbLibError(f"DbLib not found: {path}")
    raw = p.read_bytes()
    try:
        text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", "replace")
        ini = configparser.ConfigParser(interpolation=None, strict=False)
        ini.optionxform = str
        ini.read_string(text.replace("\x00", ""))
    except (configparser.Error, UnicodeDecodeError) as e:
        raise DbLibError(f"cannot read {p.name}: {e}")
    links = ini["DatabaseLinks"] if ini.has_section("DatabaseLinks") else {}
    add_default = _int(links.get("AddMode"), ADD_ALWAYS) or ADD_ALWAYS
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
                "visible": o.get("VisibleOnAdd", "False") == "True", "key": o.get("FieldType") == "0",
                "add": _int(o.get("AddMode"), ADD_DEFAULT) or add_default})
    return {"path": str(p), "name": p.name, "database": db, "tables": tables, "fields": fields,
            "search_path": links.get("LibrarySearchPath", ""), "add": add_default,
            "search_subdirectories": links.get("SearchSubDirectories", "0") == "1"}


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


def read_xlsx_sheet(path: str, sheet: str) -> List[Row]:
    """The rows of one sheet as {header: value}, the first row as headers."""
    try:
        z = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as e:
        raise DbLibError(f"cannot read {path}: {e}")
    try:
        return _read_sheet(z, path, sheet)
    except DbLibError:
        raise
    except Exception as e:
        # Whatever a damaged workbook raises (bad XML, a shared-string index
        # past the end, a part that will not decompress), the tool answers
        # with an error rather than a traceback
        raise DbLibError(f"cannot read sheet '{sheet}' of {path}: {type(e).__name__}: {e}")


def _read_sheet(z: zipfile.ZipFile, path: str, sheet: str) -> List[Row]:
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
        rows.append((_int(row.get("r"), len(rows) + 1), cells))
    if not rows:
        return []
    headers = {i: h.strip() for i, h in rows[0][1].items() if h.strip()}
    letters = {h: _letters(i) for i, h in headers.items()}
    return [Row({h: r.get(i, "") for i, h in headers.items()}, n, letters)
            for n, r in rows[1:] if any(v for v in r.values())]


def _letters(column: int) -> str:
    """Excel's column letters of a 0-based column: 0 -> A, 26 -> AA."""
    out, n = "", column + 1
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def field_maps(lib: dict, table: str) -> List[dict]:
    """The field maps of a table, however the DbLib spells its name: 'My
    Sheet$', My Sheet$ and My Sheet are the same table."""
    want = sheet_name(table).lower()
    return [m for t, maps in lib["fields"].items() if sheet_name(t).lower() == want for m in maps]


def key_field(lib: dict, table: str, rows: List[Dict[str, str]]) -> str:
    """The table's key column: the field mapped as key, else the first."""
    for f in field_maps(lib, table):
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
        tables = [t for t in tables if sheet_name(t).lower() == sheet_name(table).lower()]
        if not tables:
            raise DbLibError(f"no enabled table '{table}' in {lib['name']}; tables: {', '.join(lib['tables'])}")
    found = []
    for t in tables:
        rows = read_xlsx_sheet(lib["database"], sheet_name(t))
        key = key_field(lib, t, rows)
        found += [(t, r) for r in rows if r.get(key, "").strip() == part.strip()]
    if not found:
        raise DbLibError(f"'{part}' is not a key in {lib['name']}" + (f" table {tables[0]}" if table else ""))
    if len(found) > 1:
        raise DbLibError(f"'{part}' is in several tables ({', '.join(t for t, _ in found)}); give table")
    return found[0]


def _find_below(root: Path, name: str, budget: List[int]) -> Optional[str]:
    """The first file called name below root, breadth first, visiting at most
    budget[0] folders (shared between roots) and SEARCH_MAX_DEPTH levels."""
    level = [str(root)]
    for _ in range(SEARCH_MAX_DEPTH):
        deeper = []
        for folder in level:
            if budget[0] <= 0:
                return None
            budget[0] -= 1
            try:
                with os.scandir(folder) as it:
                    entries = list(it)
            except OSError:
                continue
            for e in entries:
                try:
                    if e.is_file() and e.name.lower() == name.lower():
                        return os.path.normpath(e.path)
                    if e.is_dir(follow_symlinks=False):
                        deeper.append(e.path)
                except OSError:
                    pass
        if not deeper:
            return None
        level = deeper
    return None


def _find_file(lib: dict, name: str) -> Optional[str]:
    """A library file named in a row: relative to the DbLib's folder or its
    search path, or, when the DbLib searches sub-directories, found by name
    below them (bounded, so a large network tree cannot stall the server)."""
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
    if lib.get("search_subdirectories"):
        budget = [SEARCH_MAX_DIRS]
        for root in roots:
            hit = _find_below(root, Path(name).name, budget) if root.is_dir() else None
            if hit:
                return hit
    return None


def place_operation(op: dict) -> dict:
    """A place_component operation given as dblib + part_number turned into
    the SchLib placement plus the DbLib link (keys db_*)."""
    lib = read_dblib(op["dblib"])
    table, row = find_part(lib, str(op["part_number"]), op.get("table"))
    maps = {f["field"]: f for f in field_maps(lib, table)}

    def checked(field):
        # The spec to Altium is one line of '|'-separated fields
        value = row.get(field, "")
        if any(c in value for c in "|\r\n"):
            raise DbLibError(f"the {table} cell {row.cell(field)} (column '{field}') of '{op['part_number']}' "
                             f"holds a '|' or a line break, which cannot be placed; edit the cell")
        return value

    def system(name):
        for field, m in maps.items():
            if m["parameter"].lower() == f"[{name}]".lower():
                return checked(field).strip()
        return checked(name).strip()

    symbol, library = system("Library Ref"), system("Library Path")
    if not symbol or not library:
        raise DbLibError(f"the row of '{op['part_number']}' has no Library Ref or Library Path")
    library_file = _find_file(lib, library)
    if not library_file:
        raise DbLibError(f"symbol library '{library}' of '{op['part_number']}' not found next to {lib['name']}"
                         + (" or on its search path" if lib.get("search_path") else "")
                         + ("" if lib.get("search_subdirectories") else
                            " (the DbLib does not search sub-directories)"))
    params = []
    for field in row:
        m = maps.get(field)
        name = m["parameter"] if m and m["parameter"] else field
        if name.startswith("["):
            continue
        # The field map's Add To Design: do not add, or only if not blank
        add = m["add"] if m else lib.get("add", ADD_ALWAYS)
        if add == ADD_NEVER or (add == ADD_IF_NOT_BLANK and not row[field].strip()):
            continue
        params.append((name, checked(field), bool(m and m["visible"])))
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
