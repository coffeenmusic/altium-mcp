"""Validation, coverage reconciliation and expected-connection checks.

Net membership comes exclusively from Altium's compiled Workspace Manager
model. Geometry is never used to infer electrical connectivity.
"""

from collections import defaultdict
from pathlib import Path, PureWindowsPath


def _string(value, name):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(f"{name} must be a nonempty string without NUL")


def validate_request(sheet_path, compiled, expected_connections):
    _string(sheet_path, "sheet_path")
    if (not PureWindowsPath(sheet_path).is_absolute() or
            Path(sheet_path).suffix.lower() != ".schdoc" or not Path(sheet_path).is_file()):
        raise ValueError("sheet_path must be an absolute path to an existing .SchDoc")
    if type(compiled) is not bool:
        raise ValueError("compiled must be a boolean")
    if expected_connections is None:
        return str(Path(sheet_path).resolve())
    if not isinstance(expected_connections, list) or len(expected_connections) > 1000:
        raise ValueError("expected_connections must be a list of at most 1000 checks")
    for index, check in enumerate(expected_connections):
        if not isinstance(check, dict) or set(check) - {"pins", "net_name", "connected", "exact"}:
            raise ValueError(f"check {index}: unknown keys; use pins, net_name, connected, exact")
        pins = check.get("pins")
        if not isinstance(pins, list) or not 1 <= len(pins) <= 2000:
            raise ValueError(f"check {index}: pins must contain 1 to 2000 selectors")
        for flag in ("connected", "exact"):
            if flag in check and type(check[flag]) is not bool:
                raise ValueError(f"check {index}: {flag} must be a boolean")
        if "net_name" in check:
            _string(check["net_name"], "net_name")
        if len(pins) == 1 and "net_name" not in check and not check.get("exact"):
            raise ValueError(f"check {index}: a single pin needs net_name or exact=true")
        if not check.get("connected", True) and (len(pins) != 2 or "net_name" in check or check.get("exact")):
            raise ValueError(f"check {index}: connected=false requires exactly two pins and no net_name/exact")
        seen = set()
        for pin in pins:
            if not isinstance(pin, dict) or set(pin) - {"designator", "pin_number", "component_uid", "part_id", "instance_id"}:
                raise ValueError(f"check {index}: invalid pin selector")
            for key in ("designator", "pin_number"):
                _string(pin.get(key), key)
            for key in ("component_uid", "instance_id"):
                if key in pin:
                    _string(pin[key], key)
            if "part_id" in pin and (type(pin["part_id"]) is not int or pin["part_id"] < 0):
                raise ValueError("part_id must be a nonnegative integer")
            token = tuple(sorted((k, v.casefold() if k == "designator" else v) for k, v in pin.items()))
            if token in seen:
                raise ValueError(f"check {index}: duplicate pin selector")
            seen.add(token)
    return str(Path(sheet_path).resolve())


def reconcile(raw):
    """Join live placed pins to compiler pins, retaining every coverage gap."""
    model = defaultdict(list)
    for pin in raw.pop("model_pins"):
        model[(pin["component_uid"], pin["pin_number"])].append(pin)
    matched = set()
    pins = []
    missing = 0
    for live in raw.pop("sheet_pins"):
        candidates = [p for p in model.get((live["component_uid"], live["pin_number"]), [])
                      if live["part_id"] == 0 or p["part_id"] == live["part_id"]]
        if not candidates:
            missing += 1
            pins.append({**live, "instance_id": None, "net_name": None, "net_id": None,
                         "resolution": "not_in_compiled_model", "coordinate_source": "schematic_editor"})
            continue
        seen = set()
        identities = defaultdict(set)
        for candidate in candidates:
            identities[(candidate["instance_id"], candidate["part_id"])].add(tuple(sorted(candidate.items())))
        for compiled_pin in candidates:
            # Shared pins can be enumerated repeatedly by the compiler. Preserve
            # contradictory rows rather than choosing one of their net names.
            token = tuple(sorted(compiled_pin.items()))
            matched.add(token)
            if token in seen:
                continue
            seen.add(token)
            name = compiled_pin["net_name"]
            conflict = len(identities[(compiled_pin["instance_id"], compiled_pin["part_id"])]) > 1
            known = name not in ("", "?") and not conflict
            pins.append({**live, **compiled_pin, "net_name": name if known else None,
                         "net_id": name if known else None,
                         "resolution": "ambiguous_model" if conflict else ("resolved" if known else "not_assigned"),
                         "coordinate_source": "compiled_model",
                         "editor_x": live["x"], "editor_y": live["y"],
                         "geometry_matches": abs(live["x"] - compiled_pin["x"]) < 0.01 and
                                             abs(live["y"] - compiled_pin["y"]) < 0.01})
    model_only = [p for values in model.values() for p in values if tuple(sorted(p.items())) not in matched]
    raw["model_only_pins"] = model_only
    raw["pins"] = pins
    raw["pin_count"] = len(pins)
    raw["missing_model_pin_count"] = missing
    raw["unresolved_pin_count"] = sum(p["resolution"] != "resolved" for p in pins)
    raw["complete"] = not raw["unresolved_pin_count"] and not model_only and not raw.get("compile_fatals", 0)
    raw["may_be_stale"] = not raw["compile_performed"]
    raw["net_source"] = "altium_workspace_manager"
    raw["coordinate_units"] = "mils"
    warnings = []
    if raw["may_be_stale"]:
        warnings.append("compiled=false reads the existing compiler cache; it may not reflect current unsaved edits")
    if missing:
        warnings.append(f"{missing} placed pins are absent from the compiled model; their networks are unknown")
    if model_only:
        warnings.append(f"{len(model_only)} compiler pins could not be matched to placed pins on this sheet")
    if any(p["resolution"] == "ambiguous_model" for p in pins):
        warnings.append("the compiler returned contradictory records for the same pin identity")
    if raw.get("compile_errors", 0) or raw.get("compile_fatals", 0):
        warnings.append("the project compiler reports errors; inspect compiler/ERC diagnostics")
    raw["warnings"] = warnings
    members = defaultdict(list)
    for index, pin in enumerate(pins):
        pin["index"] = index
        if pin["net_id"] is not None:
            members[pin["net_id"]].append(index)
    counts = raw.pop("project_net_pin_counts")
    raw["nets"] = [{"net_id": name, "net_name": name, "pin_indexes": indexes,
                    "sheet_pin_count": len(indexes), "project_pin_count": counts.get(name)}
                   for name, indexes in sorted(members.items())]
    raw["net_count"] = len(raw["nets"])
    return raw


def check_connections(result, expected_connections):
    """Compare only explicitly requested relationships, with exact sheet scope."""
    reports = []
    pins = result["pins"]
    for index, expected in enumerate(expected_connections):
        issues, selected = [], []
        for selector in expected["pins"]:
            candidates = [pin for pin in pins if
                          pin["designator"].casefold() == selector["designator"].casefold() and
                          pin["pin_number"] == selector["pin_number"] and
                          all(pin.get(key) == value for key, value in selector.items()
                              if key not in ("designator", "pin_number"))]
            if len(candidates) != 1:
                issues.append({"code": "missing_pin" if not candidates else "ambiguous_pin",
                               "selector": selector, "matches": len(candidates)})
            else:
                selected.append(candidates[0])
        if not issues:
            if len({p["index"] for p in selected}) != len(selected):
                issues.append({"code": "duplicate_resolved_pin"})
            unresolved = [p["index"] for p in selected if p["resolution"] != "resolved"]
            if unresolved:
                issues.append({"code": "unresolved_net", "pin_indexes": unresolved})
            else:
                names = {p["net_id"] for p in selected}
                connected = expected.get("connected", True)
                if connected and len(names) != 1:
                    issues.append({"code": "different_nets", "actual_nets": sorted(names)})
                if not connected and len(names) == 1:
                    issues.append({"code": "unexpected_connection", "actual_net": selected[0]["net_name"]})
                if "net_name" in expected:
                    wrong = [p["index"] for p in selected if p["net_name"].casefold() != expected["net_name"].casefold()]
                    if wrong:
                        issues.append({"code": "unexpected_net", "expected_net": expected["net_name"], "pin_indexes": wrong})
                if expected.get("exact") and len(names) == 1:
                    actual = {p["index"] for p in pins if p["net_id"] == selected[0]["net_id"]}
                    extras = actual - {p["index"] for p in selected}
                    if extras:
                        issues.append({"code": "unexpected_members", "pin_indexes": sorted(extras), "scope": "sheet"})
        reports.append({"index": index, "passed": not issues, "issues": issues,
                        "actual": [{k: p.get(k) for k in ("index", "designator", "pin_number", "net_name", "resolution")}
                                   for p in selected]})
    return {"passed": all(r["passed"] for r in reports), "checked_count": len(reports),
            "failed_count": sum(not r["passed"] for r in reports), "scope": "sheet",
            "compile_performed": result["compile_performed"], "results": reports}
