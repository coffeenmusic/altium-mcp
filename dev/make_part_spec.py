"""Build a placement spec for a DbLib part - generically, from the database.

Given a part number, this finds which enabled DbLib table contains it, reads
every non-null column, and emits a pipe-delimited spec file that the
placement script consumes. No database-specific code: the part-number field,
the symbol/footprint/description columns and the columns to skip all come
from the .DbLib, and whatever else the table returns becomes the
component's parameters, so every category works.

The DbLib is the one installed in a running Altium (IntegratedLibraryManager),
or the ALTIUM_DBLIB environment variable.

Usage:
    python dev/make_part_spec.py <part_number> <designator> [x_mils] [y_mils]
"""
import re
import subprocess
import sys
from pathlib import Path

from db_search import part_key, resolve_dblib, table_info

SPEC_OUT = Path("C:/Users/Public/altium_mcp/part_spec.txt")

# The .DbLib marks system fields with a bracketed ParameterName in its
# "Options=" mappings; these are the ones that carry the component's
# identity and models rather than plain parameters.
SYSTEM_ROLES = {
    "library ref": "symbol",
    "footprint ref": "footprint",
    "pcb3d ref": "model3d",
    "description": "description",
}
# Columns to skip are NOT hardcoded either: the .DbLib maps them to an empty
# ParameterName, which is how it says "do not create a parameter for this".
# See field_mappings().

_DBLIB = None


def dblib():
    global _DBLIB
    if _DBLIB is None:
        _DBLIB = resolve_dblib()
    return _DBLIB


def dblib_config():
    txt = dblib().read_text(errors="replace")
    conn = re.search(r"^ConnectionString=(.+)$", txt, re.M).group(1).strip()
    search = re.search(r"^LibrarySearchPath=(.+)$", txt, re.M)
    tables = [t for t in table_info(dblib()) if t["enabled"]]
    return conn, tables, (search.group(1) if search else "")


def field_mappings(table):
    """Return (excluded_columns, system_columns) for a table from the .DbLib.

    Each mapped field appears as an "Options=" line. A bracketed
    ParameterName marks a system field ([Description], [Library Ref], ...);
    an EMPTY ParameterName means the column must not become a parameter.
    """
    txt = dblib().read_text(errors="replace")
    excluded, system = set(), {}
    for line in txt.splitlines():
        if not line.startswith("Options="):
            continue
        d = dict(kv.split("=", 1) for kv in line[len("Options="):].split("|") if "=" in kv)
        if d.get("TableNameOnly", "").lower() != table.lower():
            continue
        col = (d.get("FieldNameOnly") or "").lower()
        param = (d.get("ParameterName") or "").strip()
        if not col:
            continue
        if not param:
            excluded.add(col)
        elif param.startswith("[") and param.endswith("]"):
            system[col] = param.strip("[]").lower()
    return excluded, system


def query(conn_str, sql):
    """Run a SELECT via OLEDB and return (columns, first_row) or (None, None)."""
    ps = f"""
$conn = New-Object System.Data.OleDb.OleDbConnection("{conn_str}")
$conn.Open()
$cmd = $conn.CreateCommand()
$cmd.CommandText = "{sql}"
$rdr = $cmd.ExecuteReader()
if ($rdr.Read()) {{
  for ($i = 0; $i -lt $rdr.FieldCount; $i++) {{
    $v = $rdr[$i]
    if ($v -is [System.DBNull]) {{ $v = "" }}
    Write-Output ($rdr.GetName($i) + "`t" + $v)
  }}
}}
$rdr.Close(); $conn.Close()
"""
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    row = {}
    for line in r.stdout.splitlines():
        if "\t" in line:
            k, v = line.split("\t", 1)
            row[k.strip()] = v.strip()
    return row or None


def find_part(part_number):
    """(table, row) of the enabled table holding a part, or (None, None).
    Lookup tables (those with their own part-number field, and with it the
    DbLib's field mappings) are tried first."""
    conn, tables, _ = dblib_config()
    fallback = part_key(table_info(dblib()))
    safe = part_number.replace("'", "''")
    for t in sorted(tables, key=lambda t: not t["key"]):
        key = t["key"] or fallback
        if not key:
            continue
        row = query(conn, f"SELECT TOP 1 * FROM [{t['name']}] WHERE [{key}]='{safe}'")
        if row:
            return t["name"], row
    return None, None


_SYMBOL_INDEX = None


def symbol_library_for(symbol_name, search_paths):
    """Find which .SchLib under the DbLib search paths defines a symbol.

    Component names appear inside the binaries as plain and UTF-16 text, so a
    byte scan is reliable and needs no Altium. The index is built once.
    """
    global _SYMBOL_INDEX
    if _SYMBOL_INDEX is None:
        _SYMBOL_INDEX = []
        for root in search_paths:
            root = root.strip()
            if not root or not Path(root).is_dir():
                continue
            for f in Path(root).rglob("*.SchLib"):
                try:
                    _SYMBOL_INDEX.append((f, f.read_bytes()))
                except OSError:
                    pass
    needle = symbol_name.encode("ascii", "ignore")
    needle16 = symbol_name.encode("utf-16-le")
    # Prefer libraries not in an "Imported"/archive subfolder
    hits = [f for f, data in _SYMBOL_INDEX if needle in data or needle16 in data]
    hits.sort(key=lambda f: ("import" in str(f).lower(), len(str(f))))
    return str(hits[0]) if hits else None


def build_spec(part_number, designator, x=3000, y=3000, new_sheet=True):
    table, row = find_part(part_number)
    if not row:
        raise SystemExit(f"part {part_number} not found in any enabled DbLib table")

    excluded, system = field_mappings(table)
    symbol = footprint = description = None
    params = []
    for col, val in row.items():
        key = col.lower()
        if key in excluded or val == "":
            continue
        role = SYSTEM_ROLES.get(system.get(key, ""))
        if role == "symbol":
            symbol = val
        elif role == "footprint":
            footprint = val
        elif role == "description":
            description = val
        elif role == "model3d":
            continue
        else:
            params.append((col, val))

    if not symbol:
        raise SystemExit(f"no symbol (the [Library Ref] field) for {part_number}")

    _, _, search = dblib_config()
    sym_lib = symbol_library_for(symbol, search.split(";"))
    if not sym_lib:
        raise SystemExit(f"could not locate a .SchLib containing symbol {symbol}")

    lines = [
        f"SYMBOLLIB|{sym_lib}",
        f"SYMBOL|{symbol}",
        f"DESIGNATOR|{designator}",
        f"DESIGNITEMID|{part_number}",
        # A real database-placed part carries the LibReference as its Comment
        f"COMMENT|{symbol}",
        f"TABLE|{table}",
        f"LOCATION|{x}|{y}",
        f"NEWSHEET|{1 if new_sheet else 0}",
    ]
    if footprint:
        lines.append(f"FOOTPRINT|{footprint}")
    if description:
        lines.append(f"DESCRIPTION|{description}")
    for name, val in params:
        lines.append(f"PARAM|{name}|{val}")

    SPEC_OUT.write_text("\n".join(lines) + "\n", encoding="cp1252", errors="replace")
    print(f"table   : {table}")
    print(f"symlib  : {sym_lib}")
    print(f"symbol  : {symbol}")
    print(f"footprint: {footprint}")
    print(f"excluded: {sorted(excluded)}")
    print(f"params  : {len(params)}")
    print(f"spec    : {SPEC_OUT}")
    return SPEC_OUT


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    build_spec(sys.argv[1], sys.argv[2],
               int(sys.argv[3]) if len(sys.argv) > 3 else 3000,
               int(sys.argv[4]) if len(sys.argv) > 4 else 3000)
