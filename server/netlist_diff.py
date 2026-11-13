"""Comparing two compiled netlists of a project: the safety net for editing
sheets in place.

Pure Python, unit-tested in tests/test_netlist_diff.py. A netlist is the
compiled project's pins with their nets, as get_eco_source sends them
('designator|pin|net'). Snapshots are saved as JSON so that a later call can
compare against them.

Nets are matched by name first. A net that is gone under its old name is
then matched to the new net it shares most pins with, so an auto-named net
(NetC12_2) that only changed its name is a rename, not a removal and an
addition. A pin "moved" when its net (after that matching) is a different
one.
"""

import hashlib
import json
import ntpath
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set

Netlist = Dict[str, str]          # "U1-3" -> net ("" when unconnected)


def from_source(source: dict) -> Netlist:
    """The pins of get_eco_source's reply as {"U1-3": net}."""
    out = {}
    for item in source.get("pins", []):
        parts = item.split("|")
        if len(parts) < 3:
            continue
        des, pin, net = parts[0], parts[1], "|".join(parts[2:])
        out[f"{des}-{pin}"] = net
    return out


def _nets(netlist: Netlist) -> Dict[str, Set[str]]:
    out = defaultdict(set)
    for pin, net in netlist.items():
        if net:
            out[net].add(pin)
    return dict(out)


def match_nets(before: Dict[str, Set[str]], after: Dict[str, Set[str]]) -> Dict[str, str]:
    """{old net: new net}: by name, then a vanished net to the new net
    (not matched by name) it shares most pins with.

    Greedy: vanished nets are taken one at a time, in the order they come,
    and each takes the free new net it shares most pins with (the first one
    on a tie), which is then no longer free. It is not an optimal
    assignment: when two vanished nets share pins with the same new net, the
    first one gets it and the second may be reported as removed, or matched
    to a net it shares fewer pins with."""
    pairs = {n: n for n in before if n in after}
    free = {n: pins for n, pins in after.items() if n not in pairs.values()}
    for old in (n for n in before if n not in pairs):
        best, overlap = None, 0
        for new, pins in free.items():
            k = len(before[old] & pins)
            if k > overlap:
                best, overlap = new, k
        if best is not None:
            pairs[old] = best
            del free[best]
    return pairs


def compare(before: Netlist, after: Netlist) -> dict:
    """What changed between two netlists."""
    nb, na = _nets(before), _nets(after)
    pairs = match_nets(nb, na)
    back = {new: old for old, new in pairs.items()}
    renamed = [{"from": old, "to": new} for old, new in sorted(pairs.items()) if old != new]
    removed = sorted(n for n in nb if n not in pairs)
    added = sorted(n for n in na if n not in back)
    changed = []
    for new in sorted(na):
        old = back.get(new)
        if old is None:
            continue
        gained, lost = sorted(na[new] - nb[old]), sorted(nb[old] - na[new])
        if gained or lost:
            entry = {"net": new, "pins": len(na[new])}
            if old != new:
                entry["was"] = old
            if gained:
                entry["gained"] = gained
            if lost:
                entry["lost"] = lost
            changed.append(entry)
    moved = []
    for pin in sorted(set(before) & set(after)):
        old, new = before[pin], after[pin]
        expected = pairs.get(old) if old else ""
        if new != expected:
            moved.append({"pin": pin, "from": old or None, "to": new or None})
    single = sorted(n for n, pins in na.items() if len(pins) == 1 and (n not in back or len(nb[back[n]]) > 1))
    return {
        "pins_added": sorted(set(after) - set(before)),
        "pins_removed": sorted(set(before) - set(after)),
        "pins_moved": moved,
        "nets_added": added,
        "nets_removed": removed,
        "nets_renamed": renamed,
        "nets_changed": changed,
        "new_single_pin_nets": single,
    }


def summary(diff: dict) -> Dict[str, int]:
    return {k: len(v) for k, v in diff.items() if v}


def same_project(a: str, b: str) -> bool:
    """Whether two paths name the same project file: compared as Windows
    paths, case, separators and '..' ignored."""
    return _normalised(a) == _normalised(b)


def _normalised(path: str) -> str:
    return ntpath.normcase(ntpath.normpath(str(path or "")))


def snapshot_path(folder: Path, project_path: str, name: str) -> Path:
    """Where a project's snapshot of a name is kept. The file name holds a
    hash of the normalised full project path, so projects of the same name
    in different folders (a project and a copy of it) keep their own."""
    stem = ntpath.splitext(ntpath.basename(ntpath.normpath(str(project_path or ""))))[0] or "project"
    stem = "".join(c if c.isalnum() or c in "-_ " else "_" for c in stem)
    key = hashlib.sha1(_normalised(project_path).encode("utf-8")).hexdigest()[:12]
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name) or "last"
    return folder / f"{stem}_{key}__{safe}.json"


def save(path: Path, project_path: str, netlist: Netlist, when: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"project_path": project_path, "saved": when, "pins": netlist}, indent=0),
                    encoding="utf-8")


# Snapshots kept per project; saving more removes the oldest
KEEP_PER_PROJECT = 20


def prune(folder: Path, project_path: str, keep: Optional[int] = None) -> List[str]:
    """Remove all but the keep (default KEEP_PER_PROJECT) most recently
    saved snapshots of a project; the names of those removed. Other
    projects' snapshots are left alone."""
    keep = KEEP_PER_PROJECT if keep is None else keep
    marker = snapshot_path(folder, project_path, "x").name[:-len("x.json")]   # "<stem>_<hash>__"
    try:
        files = [p for p in folder.glob("*.json") if p.name.startswith(marker)]
    except OSError:
        return []
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    removed = []
    for p in files[keep:]:
        try:
            p.unlink()
            removed.append(p.name[len(marker):-len(".json")])
        except OSError:
            pass
    return removed


def load(path: Path, project_path: str = None) -> Optional[dict]:
    """A saved snapshot, or None when there is none. With project_path, a
    snapshot saved for another project raises ValueError instead of being
    compared with; so does a snapshot that is not valid JSON or whose pins
    are not a {pin: net} object of strings."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        raise ValueError(f"the snapshot {path.name} is not valid JSON; delete it or save a new one")
    pins = data.get("pins") if isinstance(data, dict) else None
    if not isinstance(pins, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in pins.items()):
        raise ValueError(f"the snapshot {path.name} is malformed (its pins are not a {{pin: net}} object); "
                         f"delete it or save a new one")
    if not isinstance(data.get("project_path", ""), str):
        raise ValueError(f"the snapshot {path.name} is malformed (its project_path is not a string)")
    if project_path is not None and not same_project(data.get("project_path", ""), project_path):
        raise ValueError(f"the snapshot {path.name} was saved for {data.get('project_path') or 'another project'}, "
                         f"not {project_path}")
    return data


def limit(diff: dict, n: int) -> List[str]:
    """Cut every list to n items in place; the keys that were cut."""
    cut = []
    for k, v in diff.items():
        if len(v) > n:
            diff[k] = v[:n]
            cut.append(k)
    return cut
