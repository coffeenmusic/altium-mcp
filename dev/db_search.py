"""Search the component database behind a .DbLib - read-only.

Why this exists: the Altium API (IDatabaseLibDocument) exposes the schema
fully - GetTableCount/GetTableNameAt/TableEnabled, GetFieldNameAt and
GetParameterNameAt - but it cannot enumerate parts. A DbLib table can be a
parameterised single-part lookup (UserWhereText=[<key>] = '{<key>}'), and
GetAllComponentKeys against one returns 0 rows because nothing is bound.
Searching therefore queries the database directly, by default in the tables
the DbLib enables (the ones Altium places parts from).

Nothing here is specific to one database: the part-number field comes from
the tables' Key= or UserWhereText, and the DbLib itself from Altium's
IntegratedLibraryManager unless given with --dblib or ALTIUM_DBLIB.

Read-only by construction: only SELECT is issued, and the connection string
comes straight from the .DbLib. The database may live on a shared drive that
must never be written to.

Usage:
    python dev/db_search.py --tables
    python dev/db_search.py --columns <TABLE>
    python dev/db_search.py --find "LED DRIVER" [--table <TABLE>] [--all-tables] [--max 40]
    python dev/db_search.py --part <PART NUMBER>
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

DBLIB_ENV = "ALTIUM_DBLIB"

# Lists the .DbLib files installed in Altium, '|'-separated
_INSTALLED_DBLIBS = """IntMan := IntegratedLibraryManager;
S1 := '';
for I1 := 0 to IntMan.InstalledLibraryCount - 1 do
begin
    S2 := IntMan.InstalledLibraryPath(I1);
    if (UpperCase(ExtractFileExt(S2)) = '.DBLIB') then
        S1 := S1 + S2 + '|';
end;
ResultText := S1;"""


def installed_dblibs():
    """The .DbLib files installed in Altium, from its IntegratedLibraryManager
    (Altium must be running). Altium's own preference files are not read:
    where they keep the list differs between versions."""
    import sandbox_runner as sr
    sr.inject(_INSTALLED_DBLIBS)
    if not sr.run(timeout=60, quiet=True):
        return []
    text = sr.SANDBOX_RESULT.read_text(encoding="utf-8", errors="replace").strip()
    return [Path(p) for p in text.split("|") if p.strip()]


def resolve_dblib(explicit=None):
    """Locate the .DbLib: explicit path, the ALTIUM_DBLIB environment
    variable, or the one DbLib installed in Altium."""
    import os
    for cand in (explicit, os.environ.get(DBLIB_ENV)):
        if cand and Path(cand).is_file():
            return Path(cand)
    found = [p for p in installed_dblibs() if p.is_file()]
    if len(found) == 1:
        return found[0]
    if found:
        listing = "\n  ".join(str(p) for p in found)
        raise SystemExit(f"Several DbLibs are installed in Altium; pass --dblib PATH "
                         f"or set {DBLIB_ENV} to one of:\n  {listing}")
    raise SystemExit(
        f"Could not locate a .DbLib (none installed in a running Altium). "
        f"Pass --dblib PATH or set {DBLIB_ENV}."
    )


def table_info(dblib):
    """The DbLib's tables: [{name, enabled, key}]. key is the field a part
    number is looked up by - Key=, or the field in a UserWhereText of the
    form "[<field>] = '{<field>}'" - or "" for a plain table."""
    txt = dblib.read_text(errors="replace")
    tables = []
    for m in re.finditer(r"\[Table\d+\]\s*\n((?:(?!\[).*\n)*)", txt):
        body = m.group(1)
        name = re.search(r"^TableName=(.*)$", body, re.M)
        if not name:
            continue
        enabled = re.search(r"^Enabled=(.*)$", body, re.M)
        key = (re.search(r"^Key=\[?([^\]\r\n]+?)\]?\s*$", body, re.M)
               or re.search(r"^UserWhereText=\[([^\]]+)\]\s*=", body, re.M))
        tables.append({
            "name": name.group(1).strip(),
            "enabled": bool(enabled) and enabled.group(1).strip().lower() == "true",
            "key": key.group(1).strip() if key else "",
        })
    return tables


def part_key(tables):
    """The part-number field: the one the lookup tables use most, or ""."""
    keys = [t["key"] for t in tables if t["key"]]
    return max(set(keys), key=keys.count) if keys else ""


def dblib_config(dblib):
    txt = dblib.read_text(errors="replace")
    conn = re.search(r"^ConnectionString=(.+)$", txt, re.M).group(1).strip()
    return conn, [(t["name"], t["enabled"]) for t in table_info(dblib)]


def run_sql(conn_str, sql, limit=200):
    """Execute a SELECT via OLEDB, returning a list of dict rows.

    Guarded: anything that is not a SELECT is refused outright, so this module
    cannot be used to modify the shared database.
    """
    if not sql.lstrip().lower().startswith("select"):
        raise ValueError("run_sql only issues SELECT statements")
    ps = r"""
$ErrorActionPreference = 'Stop'
$conn = New-Object System.Data.OleDb.OleDbConnection($env:AMCP_CONN)
$conn.Open()
$cmd = $conn.CreateCommand()
$cmd.CommandText = $env:AMCP_SQL
$rdr = $cmd.ExecuteReader()
$n = 0
while ($rdr.Read() -and $n -lt [int]$env:AMCP_LIMIT) {
  $parts = @()
  for ($i = 0; $i -lt $rdr.FieldCount; $i++) {
    $v = $rdr[$i]
    if ($v -is [System.DBNull]) { $v = '' }
    $v = ([string]$v) -replace "[`r`n`t]", ' '
    $parts += ($rdr.GetName($i) + '=' + $v)
  }
  Write-Output ($parts -join "`t")
  $n++
}
$rdr.Close(); $conn.Close()
"""
    import os
    env = dict(os.environ, AMCP_CONN=conn_str, AMCP_SQL=sql, AMCP_LIMIT=str(limit))
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True, text=True, timeout=300, env=env)
    if r.returncode != 0:
        sys.stderr.write(r.stderr.strip()[:1500] + "\n")
        return []
    rows = []
    for line in r.stdout.splitlines():
        if "=" not in line:
            continue
        row = {}
        for field in line.split("\t"):
            if "=" in field:
                k, v = field.split("=", 1)
                row[k.strip()] = v.strip()
        if row:
            rows.append(row)
    return rows


def columns_of(conn, table):
    rows = run_sql(conn, f"SELECT TOP 1 * FROM [{table}]", limit=1)
    return list(rows[0].keys()) if rows else []


def is_part_number_column(name):
    n = name.lower().replace(" ", "_")
    return "part_number" in n or "partnumber" in n or n in ("mpn", "pn")


def search(conn, table, term, max_rows, cols=None, key=""):
    """Find parts whose description, class/category or part numbers match
    `term`."""
    if cols is None:
        cols = columns_of(conn, table)
    if not cols:
        return [], []
    hay = [c for c in cols
           if c.lower() in ("description", "class", "category", key.lower())
           or is_part_number_column(c)]
    if not hay:
        return cols, []
    safe = term.replace("'", "''")
    where = " OR ".join(f"[{c}] LIKE '%{safe}%'" for c in hay)
    sql = f"SELECT TOP {max_rows} * FROM [{table}] WHERE {where}"
    return cols, run_sql(conn, sql, limit=max_rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dblib")
    ap.add_argument("--tables", action="store_true", help="list tables")
    ap.add_argument("--columns", metavar="TABLE")
    ap.add_argument("--find", metavar="TERM")
    ap.add_argument("--part", metavar="PART_NUMBER")
    ap.add_argument("--table", help="restrict --find to one table")
    ap.add_argument("--all-tables", action="store_true",
                    help="--find also searches the tables the DbLib does not enable")
    ap.add_argument("--max", type=int, default=40)
    a = ap.parse_args()

    dblib = resolve_dblib(a.dblib)
    conn, tables = dblib_config(dblib)
    info = table_info(dblib)
    key = part_key(info)
    print(f"# dblib: {dblib}\n")

    if a.tables:
        for name, enabled in tables:
            print(f"{'enabled ' if enabled else '        '}{name}")
        return

    if a.columns:
        for c in columns_of(conn, a.columns):
            print(c)
        return

    # The enabled tables are the ones Altium places parts from; the others
    # (often the raw tables behind them) only with --all-tables, or for --part
    # after the enabled ones.
    enabled = [n for n, e in tables if e]
    others = [n for n, e in tables if not e]
    base = enabled + others if a.all_tables else enabled or others

    if a.part:
        if not key:
            raise SystemExit("The DbLib names no part-number field (no Key= or UserWhereText).")
        safe = a.part.replace("'", "''")
        for t in enabled + others:
            # Access reports an unknown column as "no value given for one or
            # more required parameters", so check the schema before filtering.
            if key.lower() not in {c.lower() for c in columns_of(conn, t)}:
                continue
            rows = run_sql(conn, f"SELECT TOP 1 * FROM [{t}] WHERE [{key}]='{safe}'", 1)
            if rows:
                print(f"## {t}")
                for k, v in rows[0].items():
                    if v:
                        print(f"  {k:<26} {v}")
                return
        print("not found")
        return

    if a.find:
        targets = [a.table] if a.table else base
        for t in targets:
            cols, rows = search(conn, t, a.find, a.max, key=key)
            if not rows:
                continue
            print(f"## {t}  ({len(rows)} match)")
            for r in rows:
                # Column names differ between databases: show the key, the
                # description, another part number (e.g. the manufacturer's)
                # and whatever names a symbol and a footprint
                def first(test):
                    return next((v for c, v in r.items() if v and test(c.lower())), "")
                pn = r.get(key, "") if key else ""
                desc = first(lambda c: c == "description")
                mfg = first(lambda c: is_part_number_column(c) and c != key.lower())
                sym = first(lambda c: "symbol" in c)
                fp = first(lambda c: "footprint" in c)
                print(f"  {pn:<12} {desc[:46]:<46} {mfg[:20]:<20} {sym[:20]:<20} {fp}")
            print()
        return

    ap.print_help()


if __name__ == "__main__":
    main()
