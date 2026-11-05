"""What Design > Update PCB would change, worked out from the compiled project
and the board without running it.

Pure Python, unit-tested in tests/test_eco_preview.py. The bridge commands
get_eco_source and get_eco_target (other_utils.pas) supply both sides as
'|'-separated strings.

Matching follows the comparator's defaults: a schematic component matches
the board component whose source unique id is its unique id (the sheet-symbol
path plus the component's id), and a net matches by its pins, so a net whose
pins all stay together under another name is a rename. Rooms and classes
match by name.

Not modelled: the comparator's ignore settings (Project Options > Comparator)
and its partial net matching (MinPercent/MinMatch). Every difference is
reported; an ignored kind is not applied by the ECO.

Not compared at all (NOT_COMPARED): net class members (net classes match by
name only), component parameters, differential pairs, rules from parameter
sets, and room scope and members (rooms match by name only). No differences
here means none of the kinds compared, not that an Update PCB would change
nothing.
"""

from collections import defaultdict
from typing import Dict, List, Set, Tuple


def _split(item: str, n: int) -> List[str]:
    parts = item.split("|")
    if len(parts) > n:                 # the last field may hold '|' (comments, descriptors)
        parts = parts[:n - 1] + ["|".join(parts[n - 1:])]
    return parts + [""] * (n - len(parts))


def _members(text: str) -> List[str]:
    return [m for m in text.split(",") if m]


def parse_source(data: dict) -> dict:
    comps = {}
    for item in data.get("components", []):
        uid, des, fp, comment = _split(item, 4)
        comps[uid.lower()] = {"unique_id": uid, "designator": des, "footprint": fp, "comment": comment}
    pins = {}
    for item in data.get("pins", []):
        des, pin, net = _split(item, 3)
        pins[(des.lower(), pin.lower())] = net
    classes = []
    for item in data.get("component_classes", []):
        name, sheet, members = _split(item, 3)
        classes.append({"name": name, "sheet": sheet, "members": _members(members)})
    rooms = [dict(zip(("name", "descriptor"), _split(i, 2))) for i in data.get("rooms", [])]
    net_classes = [{"name": n, "members": _members(m)} for n, m in (_split(i, 2) for i in data.get("net_classes", []))]
    return {"components": comps, "pins": pins, "component_classes": classes, "rooms": rooms,
            "net_classes": net_classes}


def parse_target(data: dict) -> dict:
    comps = {}
    unlinked = []
    for item in data.get("components", []):
        uid, des, fp, comment = _split(item, 4)
        c = {"unique_id": uid, "designator": des, "footprint": fp, "comment": comment}
        if uid:
            comps[uid.lower()] = c
        else:
            unlinked.append(c)
    pads = {}
    for item in data.get("pads", []):
        des, pad, net = _split(item, 3)
        pads.setdefault((des.lower(), pad.lower()), net)
    return {"components": comps, "unlinked": unlinked, "pads": pads,
            "component_classes": [{"name": n, "members": _members(m)}
                                  for n, m in (_split(i, 2) for i in data.get("component_classes", []))],
            "net_classes": [{"name": n, "members": _members(m)}
                            for n, m in (_split(i, 2) for i in data.get("net_classes", []))],
            "rooms": [dict(zip(("name", "scope"), _split(i, 2))) for i in data.get("rooms", [])]}


def _nets(pin_nets: Dict[Tuple[str, str], str]) -> Dict[str, Set[Tuple[str, str]]]:
    out = defaultdict(set)
    for key, net in pin_nets.items():
        if net:
            out[net].add(key)
    return out


def compare(source: dict, target: dict, single_pin_nets: bool = False) -> dict:
    """The differences, grouped as the ECO dialog groups its actions.

    single_pin_nets is the project's "Netlist single pin nets" option: off
    (the default), a net with one pin in the whole design is left out of the
    netlist, so the ECO leaves that pin as it is on the board: it neither
    connects nor disconnects it, and keeps a board net made of such pins.
    Board components with no schematic link (logos, title blocks) are left
    alone too; they are listed as board_only_components. Both checked against
    Altium AD25's own Engineering Change Order.
    """
    sc, tc = source["components"], target["components"]
    out = {k: [] for k in ("add_components", "remove_components", "change_designators", "change_footprints",
                           "change_comments", "add_nets", "remove_nets", "rename_nets", "change_pin_nets",
                           "add_component_classes", "remove_component_classes", "change_component_class_members",
                           "add_net_classes", "remove_net_classes", "add_rooms", "remove_rooms",
                           "duplicate_component_classes", "board_only_components")}

    # Components, matched by unique id. The parts of a multi-part component
    # (U1A, U1B) are one board component: a part whose sibling (same
    # designator) is matched is not an addition, and a new multi-part
    # component is added once
    matched = {s["designator"].lower() for uid, s in sc.items() if uid in tc}
    added = set()
    sch_to_pcb = {}                                   # schematic designator -> board designator
    for uid, s in sc.items():
        t = tc.get(uid)
        if t is None:
            des = s["designator"].lower()
            if des in matched:
                continue
            if "?" not in des:                        # unannotated parts are each their own
                if des in added:
                    continue
                added.add(des)
            out["add_components"].append({"designator": s["designator"], "footprint": s["footprint"],
                                          "comment": s["comment"]})
            continue
        sch_to_pcb[s["designator"].lower()] = t["designator"]
        if s["designator"].lower() != t["designator"].lower():
            out["change_designators"].append({"from": t["designator"], "to": s["designator"]})
        if s["footprint"].lower() != t["footprint"].lower():
            out["change_footprints"].append({"designator": s["designator"], "from": t["footprint"],
                                             "to": s["footprint"]})
        if s["comment"] != t["comment"]:
            out["change_comments"].append({"designator": s["designator"], "from": t["comment"],
                                           "to": s["comment"]})
    for uid, t in tc.items():
        if uid not in sc:
            out["remove_components"].append({"designator": t["designator"], "footprint": t["footprint"],
                                             "reason": "not in the schematic"})
    for t in target["unlinked"]:
        out["board_only_components"].append({"designator": t["designator"], "footprint": t["footprint"]})

    # Nets, over the pins of matched components, keyed by board designator
    linked = {d.lower() for d in sch_to_pcb.values()}
    all_pins = source["pins"]
    untouched = set()                                 # board pins the ECO leaves as they are
    if not single_pin_nets:
        sizes = defaultdict(int)
        for n in all_pins.values():
            sizes[n] += 1
        untouched = {(sch_to_pcb[d].lower(), p) for (d, p), n in all_pins.items()
                     if d in sch_to_pcb and n and sizes[n] == 1}
        # nor does it add such a net for a new part
        all_pins = {k: (n if sizes[n] > 1 else "") for k, n in all_pins.items()}
    sch_pins = {(sch_to_pcb[d].lower(), p): n for (d, p), n in all_pins.items()
                if d in sch_to_pcb and (sch_to_pcb[d].lower(), p) not in untouched}
    pcb_pins = {k: n for k, n in target["pads"].items() if k[0] in linked and k not in untouched}
    sn, pn = _nets(sch_pins), _nets(pcb_pins)
    by_members = {frozenset(m): name for name, m in pn.items()}
    renamed = {}                                      # board name -> schematic name
    for name, members in sn.items():
        if name in pn:
            continue
        old = by_members.get(frozenset(members))
        if old and old not in sn:
            renamed[old] = name
            out["rename_nets"].append({"from": old, "to": name})
    mapped = {k: renamed.get(n, n) for k, n in pcb_pins.items()}
    for key in sorted(set(sch_pins) | set(mapped)):
        s_net, p_net = sch_pins.get(key, ""), mapped.get(key, "")
        if key not in sch_pins and key in pcb_pins:
            continue                                  # a pad with no schematic pin (mechanical, shield)
        if s_net != p_net:
            out["change_pin_nets"].append({"pin": f"{key[0].upper()}-{key[1].upper()}",
                                           "from": pcb_pins.get(key, ""), "to": s_net})
    # Nets by name over every part, so a net on parts still to be added, or
    # only on parts that go, counts too
    new_names = set(renamed.values())
    sch_names = {n for n in all_pins.values() if n}
    pcb_names = {n for n in target["pads"].values() if n}
    out["add_nets"] = sorted(n for n in sch_names if n not in pcb_names and n not in new_names)
    kept = {n for k, n in target["pads"].items() if k in untouched and n}
    out["remove_nets"] = sorted(n for n in pcb_names if n not in sch_names and n not in renamed and n not in kept)

    # Component classes and rooms, by name
    seen = defaultdict(list)
    for c in source["component_classes"]:
        seen[c["name"].lower()].append(c)
    pcb_classes = {}                                  # a board can hold one name twice too
    for c in target["component_classes"]:
        kept = pcb_classes.setdefault(c["name"].lower(), {"name": c["name"], "members": [], "copies": 0})
        kept["members"] += c["members"]
        kept["copies"] += 1
    for name, group in seen.items():
        s = group[0]
        t = pcb_classes.get(name)
        if len(group) > 1:
            # Which sheet's class Altium keeps is not visible from here, so
            # no member changes are claimed for it
            out["duplicate_component_classes"].append({
                "name": s["name"],
                "sheet_members": {g["sheet"]: len(g["members"]) for g in group},
                "board_members": len(set(m.lower() for m in t["members"])) if t else None,
                "board_copies": t["copies"] if t else 0,
                "note": "Several sheets make a class of this name, so compiling warns \"Class already exists\" "
                        "and only one of them is kept. The usual cause is one sheet symbol whose File Name "
                        "lists several sheets (a multi-file sheet symbol): every sheet makes the class "
                        "again. The update may then change this class's members unexpectedly; give the "
                        "sheets their own sheet symbols, or turn off component class generation for all "
                        "but one of them (Project Options > Class Generation), if the class matters."})
            if t is not None:
                continue
        if t is None:
            out["add_component_classes"].append({"name": s["name"], "sheet": s["sheet"],
                                                 "members": len(s["members"])})
            continue
        want = {sch_to_pcb.get(m.lower(), m).lower() for m in s["members"]}
        have = {m.lower() for m in t["members"]}
        if want != have:
            out["change_component_class_members"].append({
                "name": s["name"], "add": sorted(m.upper() for m in want - have),
                "remove": sorted(m.upper() for m in have - want)})
    for name, t in pcb_classes.items():
        if name not in seen:
            out["remove_component_classes"].append({"name": t["name"], "members": len(t["members"])})
    sch_nc = {c["name"].lower() for c in source["net_classes"]}
    pcb_nc = {c["name"].lower(): c["name"] for c in target["net_classes"]}
    out["add_net_classes"] = sorted(c["name"] for c in source["net_classes"] if c["name"].lower() not in pcb_nc)
    out["remove_net_classes"] = sorted(n for k, n in pcb_nc.items() if k not in sch_nc)
    sch_rooms = {r["name"].lower() for r in source["rooms"]}
    pcb_rooms = {r["name"].lower(): r for r in target["rooms"]}
    out["add_rooms"] = sorted(r["name"] for r in source["rooms"] if r["name"].lower() not in pcb_rooms)
    out["remove_rooms"] = [{"name": r["name"], "scope": r["scope"]}
                           for k, r in sorted(pcb_rooms.items()) if k not in sch_rooms]
    return out


# Listed for information: the ECO does not touch them
INFO_KINDS = ("board_only_components",)

# What an Update PCB can change that compare() does not look at
NOT_COMPARED = ("net class members", "component parameters", "differential pairs",
                "rules from parameter sets", "room scope and members")


def no_differences_note() -> str:
    """The note for an empty summary: it covers only what was compared."""
    return ("No differences of the kinds compared. Not compared: " + ", ".join(NOT_COMPARED) +
            "; an Update PCB may still change those.")


def summary(diff: dict) -> Dict[str, int]:
    """Count per kind of change; information-only kinds left out."""
    return {k: len(v) for k, v in diff.items() if v and k not in INFO_KINDS}


def limit(diff: dict, n: int) -> Tuple[dict, List[str]]:
    """The differences with each list cut to n entries, and which were cut."""
    cut = [k for k, v in diff.items() if len(v) > n]
    return {k: v[:n] for k, v in diff.items() if v}, cut
