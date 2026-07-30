"""Verify scripted DbLib placement against real database-placed components.

For each reference component taken from a live schematic, this places a new
component with the SAME part number via the spec pipeline, then compares every
parameter, the footprint model, symbol and comment - so the check is
field-by-field rather than "looks right".

Usage:
    python dev/verify_placement.py                 # compare an existing run
    python dev/verify_placement.py --place N       # place N test parts first
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEV = REPO / "dev"
EXCHANGE = Path("C:/Users/Public/altium_mcp")
REFERENCE = EXCHANGE / "reference_components.txt"
PLACED = EXCHANGE / "placed_components.txt"


def parse_dump(path):
    """Parse a COMP/PARAM/MODEL dump into {designator: {...}}."""
    comps = {}
    if not path.exists():
        return comps
    for line in path.read_text(encoding="cp1252", errors="replace").splitlines():
        s = line.strip()
        if s.startswith("COMP|"):
            f = s.split("|")
            comps[f[1]] = dict(pn=f[2], sym=f[3], table=f[4], dblib=f[5],
                               comment=f[6] if len(f) > 6 else "",
                               params={}, models=[])
        elif s.startswith("PARAM|"):
            f = s.split("|")
            if f[1] in comps:
                comps[f[1]]["params"][f[2]] = f[3] if len(f) > 3 else ""
        elif s.startswith("MODEL|"):
            f = s.split("|")
            if f[1] in comps:
                comps[f[1]]["models"].append((f[2], f[3]))
    return comps


def db_row_for(part_number):
    """Current database row for a part, used to tell a defect apart from a
    stale reference component (placed before the database changed)."""
    try:
        sys.path.insert(0, str(DEV))
        import make_part_spec as mps
        _table, row = mps.find_part(part_number)
        return {k.lower(): v for k, v in (row or {}).items()}
    except Exception:
        return {}


def compare(ref, got, label, db=None):
    """Compare one reference component against its scripted counterpart."""
    issues, stale = [], []
    if ref["sym"] != got["sym"]:
        issues.append(f"symbol: reference={ref['sym']!r} placed={got['sym']!r}")
    if ref["comment"] != got["comment"]:
        issues.append(f"comment: reference={ref['comment']!r} placed={got['comment']!r}")

    ref_models = sorted(ref["models"])
    got_models = sorted(got["models"])
    if ref_models != got_models:
        issues.append(f"models: reference={ref_models} placed={got_models}")

    # Parameters: compare case-insensitively by name
    rp = {k.lower(): (k, v) for k, v in ref["params"].items()}
    gp = {k.lower(): (k, v) for k, v in got["params"].items()}
    for key in sorted(set(rp) | set(gp)):
        if key not in gp:
            issues.append(f"MISSING param {rp[key][0]!r} (reference value {rp[key][1]!r})")
        elif key not in rp:
            issues.append(f"EXTRA param {gp[key][0]!r} = {gp[key][1]!r}")
        elif rp[key][1] != gp[key][1]:
            dbval = (db or {}).get(key)
            if dbval is not None and dbval == gp[key][1]:
                stale.append(f"param {rp[key][0]!r}: reference={rp[key][1]!r} is STALE; "
                             f"placed={gp[key][1]!r} matches the current database")
            else:
                issues.append(f"param {rp[key][0]!r}: reference={rp[key][1]!r} "
                              f"placed={gp[key][1]!r} db={dbval!r}")

    if not issues:
        status = "MATCH" if not stale else f"MATCH (+{len(stale)} stale reference field(s))"
    else:
        status = f"{len(issues)} difference(s)"
    print(f"\n=== {label}: {status}")
    print(f"    part {ref['pn']}  table {ref['table']}  "
          f"params ref={len(ref['params'])} placed={len(got['params'])}")
    for i in issues:
        print(f"    - DIFF  {i}")
    for i in stale:
        print(f"    - stale {i}")
    return not issues


def main():
    ref = parse_dump(REFERENCE)
    got = parse_dump(PLACED)
    if not ref:
        raise SystemExit("no reference dump; run the reference dump experiment first")
    if not got:
        raise SystemExit("no placed dump; place the test parts first")

    # Pair each placed component to its reference by part number
    ref_by_pn = {}
    for des, c in ref.items():
        ref_by_pn.setdefault(c["pn"], (des, c))

    ok = fail = 0
    for des, g in sorted(got.items()):
        pair = ref_by_pn.get(g["pn"])
        if not pair:
            print(f"\n=== {des}: no reference component with part number {g['pn']}")
            fail += 1
            continue
        rdes, r = pair
        if compare(r, g, f"{des} vs reference {rdes}", db_row_for(g["pn"])):
            ok += 1
        else:
            fail += 1

    print(f"\n{'=' * 60}\nRESULT: {ok} matched, {fail} with differences "
          f"({len(got)} parts compared)")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
