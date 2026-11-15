"""Cross-sheet component clone validation, transport and preservation readback."""

import json
from collections import Counter
from pathlib import PureWindowsPath
import uuid

from altium_files import write_altium_text
from document_management import absolute_path


def prepare(source_sheet, target_sheet, designators):
    source = absolute_path(source_sheet, {".schdoc"}, require_parent=False)
    target = absolute_path(target_sheet, {".schdoc"}, require_parent=False)
    if PureWindowsPath(source) == PureWindowsPath(target):
        raise ValueError("source_sheet and target_sheet must be different SchDocs")
    if not isinstance(designators, list) or not 1 <= len(designators) <= 500:
        raise ValueError("designators must be a list of 1 to 500 distinct strings")
    names = set()
    for name in designators:
        if not isinstance(name, str) or not name.strip() or any(ord(c) < 32 for c in name):
            raise ValueError("designators must be nonempty strings without control characters")
        if name != name.strip() or name.casefold() in names:
            raise ValueError("designators must be distinct and have no surrounding whitespace")
        names.add(name.casefold())
    spec = {"source_sheet": str(source), "target_sheet": str(target), "count": str(len(designators))}
    spec.update({f"d{i}": name for i, name in enumerate(designators)})
    return spec


def verify(result, source, target, designators):
    errors = []
    for key, want in (("source_sheet", source), ("target_sheet", target)):
        actual = result.get(key)
        if not isinstance(actual, str) or PureWindowsPath(actual) != PureWindowsPath(want):
            errors.append(key + " identity mismatch")
    if result.get("saved") is not False or result.get("source_unchanged") is not True:
        errors.append("source preservation or no-save readback failed")
    if result.get("source_was_modified") != result.get("source_is_modified"):
        errors.append("source Modified flag changed")
    copies = result.get("components", [])
    wanted = {name.casefold() for name in designators}
    if not isinstance(copies, list) or len(copies) != result.get("cloned_count") or not copies:
        errors.append("clone count/readback is incomplete")
        copies = []
    seen, old_uids, new_uids = set(), set(), []
    for item in copies:
        original, clone = item.get("source", {}), item.get("clone", {})
        if original.get("state") != clone.get("state") or not original.get("state"):
            errors.append("component state, pins, primitives or models changed")
        state = clone.get("state", {})
        name = state.get("designator", "").casefold()
        seen.add(name)
        if name not in wanted:
            errors.append("an unrequested designator was cloned")
        mapping = item.get("uid_map", [])
        original_ids = [original.get("unique_id")] + original.get("child_uids", [])
        clone_ids = [clone.get("unique_id")] + clone.get("child_uids", [])
        if len(mapping) != len(original_ids) or len(clone_ids) != len(original_ids):
            errors.append("component UID tree is incomplete")
        if Counter(row.get("source_uid") for row in mapping) != Counter(original_ids) or Counter(row.get("unique_id") for row in mapping) != Counter(clone_ids):
            errors.append("UID mapping does not match component readback")
        old_uids.update(uid for uid in original_ids if uid)
        new_uids.extend(clone_ids)
        item["preserved"] = original.get("state") == clone.get("state")
    if seen != wanted:
        errors.append("requested designator coverage is incomplete")
    if any(not isinstance(uid, str) or not uid for uid in new_uids) or len(new_uids) != len(set(new_uids)) or old_uids.intersection(new_uids):
        errors.append("clone UIDs are missing, duplicated or reused from the source")
    if result.get("target_is_modified") is not True:
        errors.append("target is not marked Modified")
    before_counts, after_counts = result.get("target_counts_before"), result.get("target_counts_after")
    if not isinstance(before_counts, dict) or not isinstance(after_counts, dict):
        errors.append("target object-count readback is missing")
    else:
        expected = dict(before_counts)
        expected["26"] = expected.get("26", 0) + len(copies)
        if expected != after_counts:
            errors.append("target gained unexpected components or non-component primitives")
    if result.get("process_depth") != 0:
        errors.append("schematic undo transaction remains open")
    result["verification"] = {"passed": not errors, "errors": sorted(set(errors))}
    if errors:
        result.update(success=False, error="component clone readback failed", may_have_partial_changes=True)
    else:
        # Keep both snapshots on failure; successful calls need only one copy.
        for item in copies:
            item["source_unique_id"] = item.pop("source")["unique_id"]
            item["component"] = item.pop("clone")
    return result


async def execute(bridge, exchange_dir, source_sheet, target_sheet, designators):
    try:
        spec = prepare(source_sheet, target_sheet, designators)
    except (ValueError, TypeError, OSError) as exc:
        return json.dumps({"success": False, "error": str(exc), "saved": False,
                           "cloned_count": 0, "may_have_partial_changes": False})
    spec_path = exchange_dir / f"schematic_clone_{uuid.uuid4().hex}.txt"
    write_altium_text(spec_path, "\n".join(f"{key}={json.dumps(value, ensure_ascii=False)[1:-1]}"
                                         for key, value in spec.items()) + "\n")
    completed = False
    try:
        try:
            response = await bridge.execute_command("clone_schematic_components", {"spec_file": str(spec_path)})
        except Exception as exc:
            response = {"error": str(exc)}
        result = response.get("result", {})
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                result = {}
        completed = isinstance(result, dict) and type(result.get("success")) is bool
        if not completed:
            return json.dumps({"success": False, "error": response.get("error", "bridge failed"),
                               "saved": False, "clone_outcome_unknown": True,
                               "may_have_partial_changes": True, "spec_file": str(spec_path)})
        if result.get("success"):
            verify(result, spec["source_sheet"], spec["target_sheet"], designators)
        return json.dumps(result, indent=2, ensure_ascii=False)
    finally:
        if completed:
            spec_path.unlink(missing_ok=True)
