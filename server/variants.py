"""Project variants: reading them from a .PrjPcb, naming components by logical
designator and channel, and turning edit operations into the spec the bridge
applies through Altium (ApplyVariantEdits in other_utils.pas).

Pure Python, unit-tested in tests/test_variants.py.

How a .PrjPcb stores a variant
------------------------------
[ProjectVariantN] holds Description (the variant's name), VariationCount,
VariationK = Designator=<physical>|UniqueId=<\\sheet-symbol ids\\component id>|
Kind=<0 none, 1 not fitted, 2 alternate>|AlternatePart=...|AltLibLink_...,
ParamVariationCount, ParamVariationK = ParameterName=..|VariantValue=.. and
ParamDesignatorK = <physical>. Altium writes each variation's parameter
rows right under its Variation line, and its loader goes by that position: a
parameter row belongs to the Variation line above it, and rows are read until
a key is missing, whatever the counts say. Its save, though, updates the keys
where they stand and leaves the ones past the counts. After a variation is
removed the rows no longer sit under their own variation, and the next load
gives parameters to other parts (normalize() regroups them).

Why the edits go through Altium
-------------------------------
The project is usually open in Altium, which keeps its own copy and writes it
back on the next save, so a change made to the file would be lost or would
fight a reload. IProjectVariant.DM_AddComponentVariation and friends change
Altium's copy, and saving the project writes it out (checked on AD25).
remove_param uses IComponentVariation.DM_RemoveParameterVariationByName
(checked on AD25). Removing the Comment variation of an alternate part also
clears its AlternatePart when Altium saves: Altium's own behaviour.
"""

import fnmatch
import os
import re
import tempfile
from typing import Dict, List, Optional, Tuple

KINDS = {0: "none", 1: "not_fitted", 2: "alternate"}
KIND_CODES = {v: k for k, v in KINDS.items()}

OPS = ("add_variant", "rename_variant", "not_fitted", "fitted", "set_param", "remove_param")

_NEWLINE_RE = re.compile(r"\r\n|\r|\n")


def split_lines(text: str) -> List[str]:
    """The lines of text, split at CR LF, CR or LF only. str.splitlines also
    splits at U+0085, U+2028 and others; a .PrjPcb read as latin-1 holds
    U+0085 wherever a UTF-8 character has the byte 0x85 (A ring, Cyrillic
    ha, ...), so splitting there would cut a line in two."""
    lines = _NEWLINE_RE.split(text)
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _fields(value: str) -> Dict[str, str]:
    out = {}
    for part in value.split("|"):
        key, sep, val = part.partition("=")
        if sep:
            out[key] = val
    return out


def parse_variants(text: str) -> List[dict]:
    """The variants of a .PrjPcb file, in file order.

    Each: {name, unique_id, variations [{designator, unique_id, kind,
    alternate_part, params {name: value}}]}.
    """
    variants = []
    section, keys = None, {}

    def close():
        if section is None:
            return
        count = int(keys.get("VariationCount", "0") or 0)
        variations = []
        by_designator = {}
        for k in range(1, count + 1):
            f = _fields(keys.get(f"Variation{k}", ""))
            v = {"designator": f.get("Designator", ""), "unique_id": f.get("UniqueId", ""),
                 "kind": KINDS.get(int(f.get("Kind", "0") or 0), f.get("Kind", "")),
                 "alternate_part": f.get("AlternatePart", ""), "params": {}}
            variations.append(v)
            by_designator.setdefault(v["designator"].lower(), v)
        pcount = int(keys.get("ParamVariationCount", "0") or 0)
        for k in range(1, pcount + 1):
            f = _fields(keys.get(f"ParamVariation{k}", ""))
            owner = by_designator.get(keys.get(f"ParamDesignator{k}", "").lower())
            if owner is not None and f.get("ParameterName"):
                owner["params"][f["ParameterName"]] = f.get("VariantValue", "")
        variants.append({"name": keys.get("Description", ""), "unique_id": keys.get("UniqueId", ""),
                         "variations": variations})

    for raw in split_lines(text):
        line = raw.strip()
        m = re.match(r"^\[(.+)\]$", line)
        if m:
            close()
            section = m.group(1) if re.fullmatch(r"ProjectVariant\d+", m.group(1)) else None
            keys = {}
        elif section is not None and "=" in line:
            k, _, v = line.partition("=")
            keys[k] = v
    close()
    return variants


_SECTION_RE = re.compile(r"^\[(.+)\]\s*$")
_ROW_RE = re.compile(r"^(Variation|ParamVariation|ParamDesignator)(\d+)=(.*)$")
_COUNT_RE = re.compile(r"^(VariationCount|ParamVariationCount)=(\d*)\s*$")


def _layout(block: List[str]) -> dict:
    """The variation rows of one [ProjectVariantN] section (lines without
    their endings): the other keys, the counted variations and parameter
    rows, whether each parameter row sits under its own variation, and what
    lies past the counts."""
    counts, rows = {}, {"Variation": {}, "ParamVariation": {}, "ParamDesignator": {}}
    other, order = [], []
    for ln in block:
        m = _COUNT_RE.match(ln)
        if m:
            counts[m.group(1)] = int(m.group(2) or 0)
            continue
        m = _ROW_RE.match(ln)
        if m:
            rows[m.group(1)][int(m.group(2))] = m.group(3)
            order.append((m.group(1), int(m.group(2))))
            continue
        other.append(ln)
    nv, np_ = counts.get("VariationCount", 0), counts.get("ParamVariationCount", 0)
    variations = [rows["Variation"].get(k, "") for k in range(1, nv + 1)]
    params = [(rows["ParamVariation"].get(k, ""), rows["ParamDesignator"].get(k, "")) for k in range(1, np_ + 1)]
    stale = sum(1 for kind, k in order if k > (nv if kind == "Variation" else np_))
    # Altium reads a parameter row as belonging to the Variation line above it
    misplaced, current = 0, None
    for kind, k in order:
        if kind == "Variation" and k <= nv:
            current = _fields(rows["Variation"][k]).get("Designator", "").lower()
        elif kind == "ParamDesignator" and k <= np_ and rows["ParamDesignator"][k].strip().lower() != current:
            misplaced += 1
    designators = [_fields(v).get("Designator", "").lower() for v in variations]
    description = next((ln.partition("=")[2] for ln in other if ln.startswith("Description=")), "")
    return {"other": other, "variations": variations, "params": params, "stale": stale,
            "misplaced": misplaced, "duplicates": len(designators) != len(set(designators)),
            "description": description}


def _sections(text: str):
    """(line index range, name) of every [ProjectVariantN] section."""
    lines = split_lines(text)
    found, start, name = [], None, None
    for i, ln in enumerate(lines):
        m = _SECTION_RE.match(ln.strip())
        if m:
            if start is not None:
                found.append((start, i, name))
            start, name = (i + 1, m.group(1)) if re.fullmatch(r"ProjectVariant\d+", m.group(1)) else (None, None)
    if start is not None:
        found.append((start, len(lines), name))
    return lines, found


def layout_problems(text: str) -> List[dict]:
    """Variant sections Altium would misread on its next load."""
    lines, found = _sections(text)
    out = []
    for a, b, name in found:
        lay = _layout(lines[a:b])
        if lay["misplaced"] or lay["stale"]:
            out.append({"section": name, "misplaced_rows": lay["misplaced"], "rows_past_counts": lay["stale"],
                        "duplicate_designators": lay["duplicates"]})
    return out


def replace_file(path, data: bytes) -> None:
    """Write data to path through a temporary file in the same folder and
    os.replace, so a failed write leaves the old file whole."""
    path = os.fspath(path)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".~", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def normalize(text: str, only: Optional[List[str]] = None) -> Tuple[str, List[dict]]:
    """Rewrite the variant sections Altium would misread, in the layout it
    writes itself; returns the text and what was rewritten or left alone.
    only: the variant names (Description) to touch, e.g. the ones an edit
    changed; None for every variant. Lines are split at CR and LF only, so a
    file read as latin-1 keeps its bytes outside what is rewritten (given
    one kind of line ending throughout, as Altium writes it).

    Altium's save updates the variation keys where they stand and never
    removes keys past the counts, but its loader reads a parameter row as
    belonging to the Variation line above it and reads keys until one is
    missing. So after a variation is removed through the API and saved,
    reloading the project hands parameters to the wrong parts (seen on AD25:
    RSENSE1_U_CL1 came back with RSENSE1_U_CL2's values). This puts every
    variation's parameter rows (by ParamDesignator) right under it, numbered
    in order, and drops what lies past the counts. A section where two
    variations share a designator (unannotated parts) cannot be regrouped by
    designator and is left alone, misplaced or not.
    """
    wanted = None if only is None else {n.lower() for n in only}
    nl = "\r\n" if "\r\n" in text else "\n"
    lines, found = _sections(text)
    report = []
    for a, b, name in reversed(found):
        block = lines[a:b]
        lay = _layout(block)
        if not (lay["misplaced"] or lay["stale"]):
            continue
        if wanted is not None and lay["description"].lower() not in wanted:
            continue
        if lay["duplicates"]:
            report.append({"section": name, "left_alone": "two variations share a designator, so their "
                                                          "parameter rows cannot be told apart"})
            continue
        trailing = []
        while block and not block[-1].strip():
            trailing.insert(0, block.pop())
        other = [ln for ln in lay["other"] if ln.strip()]
        by_designator: Dict[str, List[Tuple[str, str]]] = {}
        for value, designator in lay["params"]:
            by_designator.setdefault(designator.strip().lower(), []).append((value, designator))
        out = other + [f"VariationCount={len(lay['variations'])}"]
        n = 0
        for k, v in enumerate(lay["variations"], 1):
            out.append(f"Variation{k}={v}")
            for value, designator in by_designator.pop(_fields(v).get("Designator", "").lower(), []):
                n += 1
                out += [f"ParamVariation{n}={value}", f"ParamDesignator{n}={designator}"]
        for rest in by_designator.values():          # rows of no variation: kept, at the end
            for value, designator in rest:
                n += 1
                out += [f"ParamVariation{n}={value}", f"ParamDesignator{n}={designator}"]
        out.append(f"ParamVariationCount={n}")
        lines[a:b] = out + trailing
        report.append({"section": name, "rewritten": True, "misplaced_rows": lay["misplaced"],
                       "rows_past_counts": lay["stale"]})
    return nl.join(lines) + (nl if text.endswith(("\n", "\r")) else ""), list(reversed(report))


def edited_variants(lines: List[str]) -> List[str]:
    """The names, as they are after the edit, of the variants the spec
    lines change (a renamed variant by its new name)."""
    names: List[str] = []
    for ln in lines:
        f = ln.split("|")
        if f[0] == "RENAME" and len(f) > 2:
            names = [f[2] if n.lower() == f[1].lower() else n for n in names]
            if f[2].lower() not in (n.lower() for n in names):
                names.append(f[2])
        elif len(f) > 1 and f[1].lower() not in (n.lower() for n in names):
            names.append(f[1])
    return names


def shared_designators(variants: List[dict]) -> Dict[str, List[str]]:
    """Variant name -> designators used by more than one of its variations."""
    out = {}
    for v in variants:
        seen, dup = set(), []
        for x in v["variations"]:
            d = x["designator"].lower()
            if d in seen and x["designator"] not in dup:
                dup.append(x["designator"])
            seen.add(d)
        if dup:
            out[v["name"]] = dup
    return out


def channel_of(physical: str, logical: str) -> str:
    """The channel part of a physical designator: what the channel naming
    adds to the logical designator ('Rtop1_U_CL2', 'Rtop1' -> 'U_CL2').
    Empty for a component outside any channel."""
    if physical.lower() == logical.lower():
        return ""
    if physical.lower().startswith(logical.lower()):
        return physical[len(logical):].lstrip("_-.")
    return physical


def parse_targets(items: List[str]) -> List[dict]:
    """get_variant_targets' 'physical|logical|unique id|sheet' strings as dicts."""
    out = []
    for item in items:
        physical, logical, uid, sheet = (item.split("|") + ["", "", "", ""])[:4]
        out.append({"designator": physical, "logical": logical, "unique_id": uid, "sheet": sheet,
                    "channel": channel_of(physical, logical)})
    return out


def resolve(targets: List[dict], designator: str, channel: Optional[str] = None) -> Tuple[List[dict], str]:
    """Components an operation means, and an error ('' when found).

    designator: a physical designator ('R1_CH2'), or a logical one ('R1')
    with channel ('CH2', or '*' for every channel). A logical designator used
    in several channels without channel is an error that lists them.
    """
    if not designator:
        return [], "no designator"
    d = designator.lower()
    if channel is None or channel == "":
        exact = [t for t in targets if t["designator"].lower() == d]
        if exact:
            return exact, ""
        logical = [t for t in targets if t["logical"].lower() == d]
        if len(logical) == 1:
            return logical, ""
        if logical:
            chans = sorted({t["channel"] for t in logical})
            return [], (f"{designator} is in {len(logical)} channels ({', '.join(chans[:12])}"
                        f"{', ...' if len(chans) > 12 else ''}); give channel, or channel '*' for all")
        return [], f"no component {designator} in the project"
    logical = [t for t in targets if t["logical"].lower() == d]
    if not logical:
        return [], f"no component with logical designator {designator}"
    hits = [t for t in logical if fnmatch.fnmatch(t["channel"].lower(), channel.lower())]
    if not hits:
        chans = sorted({t["channel"] for t in logical})
        return [], f"{designator} has no channel {channel}; its channels: {', '.join(chans[:12])}"
    return hits, ""


def _clean(value: str, what: str) -> str:
    if "|" in value or "\n" in value or "\r" in value:
        raise ValueError(f"{what} may not contain '|' or a line break")
    return value


def encode_operations(variants: List[dict], targets: List[dict], operations: List[dict],
                      default_variant: str = "") -> Tuple[List[str], List[str], List[dict]]:
    """Spec lines for ApplyVariantEdits, errors, and a plan of what each
    operation does. `variants` (from parse_variants) is updated as the
    operations would change it, so later operations see earlier ones."""
    lines, errors, plan = [], [], []
    names = {v["name"].lower(): v for v in variants}

    for n, op in enumerate(operations or [], 1):
        if not isinstance(op, dict):
            errors.append(f"operation {n}: each operation is a dict with an 'op'")
            continue
        kind = op.get("op", "")
        try:
            if kind not in OPS:
                raise ValueError(f"unknown op '{kind}'; use one of {', '.join(OPS)}")
            if kind == "add_variant":
                name = _clean(str(op.get("name", "")).strip(), "name")
                if not name or name.lower() in names:
                    raise ValueError(f"variant name '{name}' is empty or taken")
                v = {"name": name, "unique_id": "", "variations": []}
                variants.append(v)
                names[name.lower()] = v
                lines.append(f"NEW|{name}")
                plan.append({"op": n, "add_variant": name})
                continue

            vname = str(op.get("variant") or default_variant or "")
            if not vname and len(variants) == 1:
                vname = variants[0]["name"]
            variant = names.get(vname.lower())
            if variant is None:
                raise ValueError(f"no variant '{vname}'; give variant (one of: "
                                 f"{', '.join(v['name'] for v in variants) or 'none'})")
            vname = _clean(variant["name"], "variant")

            if kind == "rename_variant":
                new = _clean(str(op.get("name", "")).strip(), "name")
                if not new or (new.lower() in names and names[new.lower()] is not variant):
                    raise ValueError(f"variant name '{new}' is empty or taken")
                lines.append(f"RENAME|{vname}|{new}")
                del names[variant["name"].lower()]
                variant["name"] = new
                names[new.lower()] = variant
                plan.append({"op": n, "rename_variant": vname, "to": new})
                continue

            comps, err = resolve(targets, str(op.get("designator", "")), op.get("channel"))
            if err:
                raise ValueError(err)
            for c in comps:
                uid, phys = _clean(c["unique_id"], "unique id"), _clean(c["designator"], "designator")
                existing = next((x for x in variant["variations"] if x["unique_id"].lower() == uid.lower()), None)
                if kind == "not_fitted":
                    lines.append(f"KIND|{vname}|{uid}|{phys}|1")
                    if existing is None:
                        existing = {"designator": phys, "unique_id": uid, "kind": "not_fitted",
                                    "alternate_part": "", "params": {}}
                        variant["variations"].append(existing)
                    existing["kind"] = "not_fitted"
                    plan.append({"op": n, "designator": phys, "not_fitted": True})
                elif kind == "fitted":
                    if existing is None:
                        plan.append({"op": n, "designator": phys, "note": "already fitted as designed"})
                        continue
                    lines.append(f"DEL|{vname}|{uid}")
                    variant["variations"].remove(existing)
                    plan.append({"op": n, "designator": phys, "fitted": True,
                                 "dropped": {"kind": existing["kind"], "params": existing["params"]}})
                elif kind == "set_param":
                    pname = _clean(str(op.get("name", "")), "parameter name")
                    value = _clean(str(op.get("value", "")), "value")
                    if not pname:
                        raise ValueError("set_param needs name")
                    lines.append(f"PARAM|{vname}|{uid}|{phys}|{pname}|{value}")
                    if existing is None:
                        existing = {"designator": phys, "unique_id": uid, "kind": "none",
                                    "alternate_part": "", "params": {}}
                        variant["variations"].append(existing)
                    existing["params"][pname] = value
                    plan.append({"op": n, "designator": phys, "set_param": {pname: value}})
                elif kind == "remove_param":
                    pname = str(op.get("name", ""))
                    if existing is None or pname not in existing["params"]:
                        plan.append({"op": n, "designator": phys, "note": f"no variation of {pname}"})
                        continue
                    keep = {k: v for k, v in existing["params"].items() if k != pname}
                    step = {"op": n, "designator": phys, "remove_param": pname}
                    if keep or existing["kind"] != "none":
                        lines.append(f"UNPARAM|{vname}|{uid}|{_clean(pname, 'parameter name')}")
                        existing["params"] = keep
                        if existing["kind"] == "alternate" and pname.lower() == "comment":
                            existing["alternate_part"] = ""
                            step["note"] = "Altium clears the alternate part when its Comment variation goes"
                    else:
                        # nothing left to vary: the variation goes
                        lines.append(f"DEL|{vname}|{uid}")
                        variant["variations"].remove(existing)
                    plan.append(step)
        except ValueError as e:
            errors.append(f"operation {n}: {e}")
    return lines, errors, plan


def describe(variants: List[dict], targets: List[dict], variant: str = "",
             designators: Optional[List[str]] = None) -> List[dict]:
    """Variants with each variation's logical designator, channel and sheet."""
    by_uid = {t["unique_id"].lower(): t for t in targets}
    out = []
    for v in variants:
        if variant and v["name"].lower() != variant.lower():
            continue
        rows = []
        for x in v["variations"]:
            t = by_uid.get(x["unique_id"].lower())
            row = {"designator": x["designator"], "kind": x["kind"]}
            if t:
                row.update({"logical": t["logical"], "channel": t["channel"], "sheet": t["sheet"]})
            else:
                row["note"] = "not in the compiled project"
            if x["alternate_part"] and x["kind"] == "alternate":
                row["alternate_part"] = x["alternate_part"]
            if x["params"]:
                row["params"] = x["params"]
            if designators and not any(fnmatch.fnmatch(row["designator"].lower(), d.lower())
                                       or fnmatch.fnmatch(row.get("logical", "").lower(), d.lower())
                                       for d in designators):
                continue
            rows.append(row)
        out.append({"name": v["name"], "variation_count": len(v["variations"]), "variations": rows})
    return out
