"""Native component-kind edits on exact placed PCB components, never libraries."""

import json
from pathlib import PureWindowsPath
import uuid

from altium_files import write_altium_text
from document_management import absolute_path
from library_component_properties import kind
from schematic_document import disk_state


# Component-level eligibility, not a promise about an exported/filtered report.
POLICY = {"standard": (True, True), "mechanical": (True, False), "graphical": (False, False),
          "net_tie_bom": (True, False), "net_tie_no_bom": (False, False), "standard_no_bom": (False, None)}
CODES = {name:index for index,name in enumerate(POLICY)}
FLAGS = ("graphical", "include_in_bom", "include_in_pick_place")
CONTROLLED = {"component_kind", "component_kind_code", *FLAGS, "inclusion_source", "pick_place_policy"}


def matches(name, spec):
    bom, pick = POLICY.get(name, (None, None))
    values = dict(graphical=name == "graphical", include_in_bom=bom, include_in_pick_place=pick)
    return all(values[key] is (spec[key] == "1") for key in FLAGS if key in spec)


def resolve(current, spec):
    if "component_kind" in spec:
        target = spec["component_kind"]
        if matches(target, spec): return target
    elif spec.get("graphical") == "1":
        if matches("graphical", spec): return "graphical"
    elif spec.get("graphical") == "0" and current == "graphical":
        if matches("standard", spec): return "standard"
    elif matches(current, spec):
        return current
    else:
        # Never introduce a net tie automatically: it changes electrical semantics.
        candidates = [name for name in ("standard", "mechanical", "graphical", "standard_no_bom") if matches(name, spec)]
        if len(candidates) == 1: return candidates[0]
    raise ValueError("Inclusion constraints are contradictory, ambiguous or output-dependent; specify component_kind. Standard (No BOM) Pick & Place depends on output settings")


def prepare(pcb_path, designators, component_kind=None, graphical=None, include_in_bom=None, include_in_pick_place=None):
    path = absolute_path(pcb_path, {".pcbdoc"})
    if not isinstance(designators, list) or not 1 <= len(designators) <= 1000:
        raise ValueError("designators must contain 1 to 1000 distinct exact component designators")
    names = []
    for value in designators:
        if (not isinstance(value, str) or not value.strip() or len(value) > 128
                or any(ord(c) < 32 for c in value) or any(c in value for c in '*?')):
            raise ValueError("designators must be nonempty exact strings without wildcards/control characters")
        name = value.strip()
        if name.casefold() in {v.casefold() for v in names}:
            raise ValueError("designators must be distinct, case-insensitively")
        names.append(name)
    spec = dict(pcb_path=str(path), count=str(len(names)))
    spec.update({f"designator.{i}":name for i,name in enumerate(names)})
    if component_kind is not None: spec["component_kind"] = kind(component_kind)
    for key,value in zip(FLAGS, (graphical, include_in_bom, include_in_pick_place)):
        if value is not None:
            if type(value) is not bool: raise ValueError(key + " must be a boolean or omitted")
            spec[key] = "1" if value else "0"
    spec["mutating"] = "1" if set(spec) & CONTROLLED else "0"
    if "component_kind" in spec or spec.get("graphical") == "1": resolve("standard", spec)
    return spec


def verify(result, spec):
    errors = []
    if not isinstance(result.get("pcb_path"), str) or PureWindowsPath(result["pcb_path"]) != PureWindowsPath(spec["pcb_path"]):
        errors.append("PCB identity mismatch")
    if result.get("saved") is not False: errors.append("save was not forbidden")
    before,after = result.get("before"),result.get("after")
    count = int(spec["count"])
    if not isinstance(before,list) or not isinstance(after,list) or len(before) != count or len(after) != count:
        return errors + ["component snapshot count mismatch"]
    changed = 0
    for i,(old,new) in enumerate(zip(before,after)):
        if not isinstance(old,dict) or not isinstance(new,dict): return errors + ["invalid native snapshot"]
        if any(str(row.get("designator", "")).casefold() != spec[f"designator.{i}"].casefold() for row in (old,new)):
            errors.append("component selection/order mismatch")
        try:
            target = resolve(old.get("component_kind"), spec) if spec["mutating"] == "1" else old.get("component_kind")
        except ValueError as exc:
            errors.append(str(exc)); continue
        if new.get("component_kind") != target: errors.append("native component kind mismatch")
        if target in POLICY:
            bom,pick = POLICY[target]
            if (new.get("component_kind_code") != CODES[target] or new.get("graphical") is not (target == "graphical")
                    or new.get("include_in_bom") is not bom or new.get("include_in_pick_place") is not pick):
                errors.append("component inclusion policy mismatch")
            policy = "depends_on_output_settings" if pick is None else "eligible" if pick else "excluded"
            if new.get("pick_place_policy") != policy: errors.append("Pick & Place policy readback mismatch")
        if new.get("inclusion_source") != "component_kind_policy": errors.append("missing inclusion provenance")
        # Child iterators may reorder after a native edit. Identity and values must remain.
        def immutable(row):
            values = {k:v for k,v in row.items() if k not in CONTROLLED}
            if isinstance(values.get("children"), list):
                values["children"] = sorted((json.dumps(v,sort_keys=True) for v in values["children"]))
            return values
        if immutable(old) != immutable(new): errors.append("unrequested component data/child geometry changed")
        changed += old.get("component_kind") != new.get("component_kind")
    if type(result.get("updated_count")) is not int or result.get("updated_count") != changed or result.get("changed") is not bool(changed):
        errors.append("changed component count mismatch")
    if any(type(result.get(k)) is not bool for k in ("was_modified","is_modified")):
        errors.append("missing Modified state")
    elif (changed and not result["is_modified"]) or (not changed and result["was_modified"] != result["is_modified"]):
        errors.append("Modified/no-op state mismatch")
    return errors


async def execute(bridge, exchange_dir, *args, **kwargs):
    try:
        spec = prepare(*args, **kwargs)
        original_disk = disk_state(spec["pcb_path"])
    except (ValueError, TypeError, OSError) as exc:
        return json.dumps(dict(success=False,error=str(exc),saved=False,may_have_partial_changes=False))
    mutating = spec["mutating"] == "1"
    request = exchange_dir / f"pcb_component_properties_{uuid.uuid4().hex}.txt"
    write_altium_text(request, "\n".join(f"{k}={json.dumps(v,ensure_ascii=False)[1:-1]}" for k,v in spec.items())+"\n")
    completed = False
    try:
        try:
            response = await bridge.execute_command("set_pcb_component_properties", {"spec_file":str(request)})
        except Exception as exc: response = {"error":str(exc)}
        if response.get("command_started") is False:
            completed = True
            return json.dumps(dict(success=False,error=response.get("error","Command was not started"),
                                   command_started=False,saved=False,may_have_partial_changes=False))
        result = response.get("result")
        if isinstance(result,str):
            try: result = json.loads(result)
            except ValueError: result = None
        completed = isinstance(result,dict) and type(result.get("success")) is bool
        if not completed:
            return json.dumps(dict(success=False,error=response.get("error","Invalid bridge response"),outcome_unknown=True,
                                   may_have_partial_changes=mutating,saved=False,spec_file=str(request)))
        errors = verify(result,spec) if result["success"] else []
        try:
            if disk_state(spec["pcb_path"]) != original_disk: errors.append("PCB file changed unexpectedly")
        except OSError as exc: errors.append("Cannot verify PCB file: "+str(exc))
        if result["success"] or errors:
            result["verification"] = dict(passed=not errors,errors=errors,
                scope="Native ComponentKind, identity/pose/source links, exposed child geometry/nets and disk; inclusion is type policy, reports not generated")
        if errors: result.update(success=False,error="PCB property verification failed",may_have_partial_changes=mutating)
        return json.dumps(result,indent=2,ensure_ascii=False)
    finally:
        if completed: request.unlink(missing_ok=True)
