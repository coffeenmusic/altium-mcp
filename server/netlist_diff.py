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

import json
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
    (not matched by name) it shares most pins with."""
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


def snapshot_path(folder: Path, project_path: str, name: str) -> Path:
    stem = Path(project_path).stem or "project"
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name) or "last"
    return folder / f"{stem}__{safe}.json"


def save(path: Path, project_path: str, netlist: Netlist, when: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"project_path": project_path, "saved": when, "pins": netlist}, indent=0),
                    encoding="utf-8")


def load(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def limit(diff: dict, n: int) -> List[str]:
    """Cut every list to n items in place; the keys that were cut."""
    cut = []
    for k, v in diff.items():
        if len(v) > n:
            diff[k] = v[:n]
            cut.append(k)
    return cut
