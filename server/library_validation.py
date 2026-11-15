"""Read-only, configurable library checks with explicit deferred coverage."""

from collections import Counter, defaultdict
import json
import math
from pathlib import PureWindowsPath
import re
from urllib.parse import urlsplit

import library_components_data as reader
from pcb_layers import canonical_name
from library_geometry import EPS, annular_ring, gap, hole_shape, pad_shape
from schematic_document import disk_state
from symbol_metadata import REQUIRED_PARAMETERS, PLACEHOLDERS

DEFAULTS = dict(required_parameters=list(REQUIRED_PARAMETERS), pin_grid_mils=100.0,
                grid_tolerance_mils=0.01, min_pad_size_mm=0.05, max_pad_size_mm=100.0,
                min_copper_clearance_mm=0.1, min_npth_clearance_mm=0.2,
                min_annular_ring_mm=0.1, min_mask_web_mm=0.1, min_paste_clearance_mm=0.1,
                max_paste_expansion_mm=0.0, rule_mask_expansion_mm=None, rule_paste_expansion_mm=None,
                require_footprint=True, require_shield_mapping=False)


def prepare(library_paths, component_names, rules, pad_roles, offset, limit, max_issues):
    _, paths, _ = reader.prepare(library_paths, component_names, list(reader.FIELDS), offset, limit)
    if rules is None:
        rules = {}
    if not isinstance(rules, dict) or set(rules)-set(DEFAULTS):
        raise ValueError("rules has unknown keys; see DEFAULTS in the documented schema")
    profile = {**DEFAULTS, **rules}
    params = profile["required_parameters"]
    if (not isinstance(params, list) or len(params)>500 or any(not isinstance(v,str) or not v.strip() for v in params)
            or len({v.casefold() for v in params}) != len(params)):
        raise ValueError("required_parameters must contain at most 500 distinct nonempty names")
    for key,value in profile.items():
        if key == "required_parameters":
            continue
        if key.startswith("require_"):
            if type(value) is not bool:
                raise ValueError(key+" must be boolean")
        elif key.startswith("rule_") and value is None:
            continue
        elif type(value) not in (int,float) or not math.isfinite(value) or abs(value)>10000:
            raise ValueError(key+" must be a finite number within +/-10000")
        elif value<0 and key not in {"rule_mask_expansion_mm","rule_paste_expansion_mm","max_paste_expansion_mm"}:
            raise ValueError(key+" must be nonnegative")
    if not profile["pin_grid_mils"] or profile["grid_tolerance_mils"]>=profile["pin_grid_mils"]/2:
        raise ValueError("pin_grid_mils must be positive and tolerance less than half a grid")
    if profile["min_pad_size_mm"]>profile["max_pad_size_mm"]:
        raise ValueError("min_pad_size_mm exceeds max_pad_size_mm")
    if type(max_issues) is not int or not 1<=max_issues<=10000:
        raise ValueError("max_issues must be an integer within 1..10000")
    if pad_roles is None:
        pad_roles = []
    if not isinstance(pad_roles,list) or len(pad_roles)>5000:
        raise ValueError("pad_roles must be a list of at most 5000 explicit role records")
    roles = {}
    for item in pad_roles:
        required={"library_path","footprint_name","pad_name","role"}
        if not isinstance(item,dict) or not required<=set(item) or set(item)-required-{"paste_required"}:
            raise ValueError("pad_roles entries require library_path, footprint_name, pad_name, role; optional paste_required")
        if not all(isinstance(item[k],str) for k in required) or not item["footprint_name"].strip():
            raise ValueError("pad role identities must be strings with a nonempty footprint_name")
        if "paste_required" in item and type(item["paste_required"]) is not bool:
            raise ValueError("paste_required must be boolean")
        path = PureWindowsPath(reader.absolute_path(item["library_path"],{".pcblib"}))
        if path not in {PureWindowsPath(p) for p in paths} or path.suffix.lower()!=".pcblib":
            raise ValueError("each pad role must refer to an explicit input PcbLib")
        if item["role"] not in {"electrical","shield","npth"}:
            raise ValueError("pad role must be electrical, shield or npth")
        key=(path,item["footprint_name"].casefold(),item["pad_name"].casefold())
        if key in roles:
            raise ValueError("duplicate pad role selector")
        roles[key]={k:item[k] for k in ("role","paste_required") if k in item}
    return paths, profile, roles


def identity(row):
    return PureWindowsPath(row["library_path"]), row["component_name"].casefold()


class Check:
    def __init__(self, row, max_issues):
        self.result={k:row[k] for k in ("library_path","library_kind","component_name")}
        self.issues=[]; self.counts=Counter(); self.deferred=set(); self.max_issues=max_issues

    def add(self, code, message, severity="error", **details):
        self.counts[severity]+=1
        if len(self.issues)<self.max_issues:
            self.issues.append(dict(code=code,severity=severity,message=message,**details))

    def defer(self, code, message, *, key=None, **details):
        # Repeated pairs/pads share one explicit coverage warning per component.
        key=(code,key)
        if key not in self.deferred:
            self.deferred.add(key);self.add(code,message,"warning",**details)

    def finish(self):
        self.result.update(status="fail" if self.counts["error"] else "incomplete" if self.deferred else "pass",
                           validation_complete=not self.deferred, issues=self.issues,
                           issue_counts=dict(self.counts), issues_truncated=sum(self.counts.values())>len(self.issues))
        return self.result


def classify(pad, row, roles):
    explicit=roles.get((*identity(row),pad["name"].casefold()))
    if isinstance(explicit,dict):
        explicit=explicit["role"]
    npth=pad["hole_size_mm"]>EPS and not pad["plated"]
    if npth:
        return "npth", "native", explicit is not None and explicit!="npth"
    if explicit:
        return explicit,"explicit",explicit=="npth"
    if re.fullmatch(r"(?:SH|SHIELD|SHELL|SCREEN)[_\- ]*\d*",pad["name"],re.I):
        return "shield","name_hint",False
    return "electrical","default",False


def symbol_checks(row, rules, check):
    params=defaultdict(list)
    for item in row["parameter_records"]:
        params[item["name"].casefold()].append(item["value"])
    for name,values in params.items():
        if len(values)>1:
            check.add("parameter_duplicate","Duplicate component parameter name",parameter=name,count=len(values))
    for name in rules["required_parameters"]:
        values=params.get(name.casefold(),[])
        if not values or not values[0].strip() or values[0].strip().casefold() in PLACEHOLDERS:
            check.add("parameter_required","Required component parameter is missing, empty or a placeholder",parameter=name)
    if "helpurl" in params:
        url=params["helpurl"][0]
        try:
            parsed=urlsplit(url)
            valid_url=parsed.scheme.lower() in {"http","https"} and bool(parsed.netloc) and not any(c.isspace() for c in url)
        except ValueError:
            valid_url=False
        if not valid_url:
            check.add("parameter_url","HelpURL must be an absolute HTTP(S) URL",parameter="HelpURL")
    props=row["properties"]
    if props["description"].strip().casefold() in PLACEHOLDERS or len(props["description"].split())<3:
        check.add("description_required","Native Description needs a meaningful component description of at least three words")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}\?",props["designator_template"]):
        check.add("designator_template","Native Designator needs a prefix followed by ?")
    if props["component_kind"]=="unknown":
        check.defer("component_kind_unknown","Native ComponentKind is not recognized",value=props["component_kind_code"])
    grid,tolerance=rules["pin_grid_mils"],rules["grid_tolerance_mils"]
    numbers=defaultdict(list);locations=defaultdict(list)
    for pin in row["pins"]:
        details={k:pin.get(k) for k in ("pin_number","owner_part_id","display_mode")}
        if not pin["pin_number"].strip():
            check.add("pin_number_empty","Pin number is empty",**details)
        for field in ("x","y","length"):
            value=pin[field]
            if abs(value-round(value/grid)*grid)>tolerance:
                check.add("pin_off_grid","Pin connection location/length is off the configured library grid",field=field,value_mils=value,grid_mils=grid,**details)
        if pin["length"]<=0:
            check.add("pin_length","Pin length must be positive",**details)
        if pin["orientation"] not in (0,1,2,3):
            check.add("pin_orientation","Unsupported pin orientation",**details)
        ownership=(pin["owner_part_id"],pin["display_mode"])
        numbers[(*ownership,pin["pin_number"])].append(pin)
        locations[(*ownership,pin["x"],pin["y"])].append(pin)
    for (part,mode,number),pins in numbers.items():
        if len(pins)>1:
            check.add("pin_number_duplicate","Pin number is repeated within one part/display mode",pin_number=number,owner_part_id=part,display_mode=mode)
    for (part,mode,x,y),pins in locations.items():
        if len(pins)>1:
            check.add("pin_connections_overlap","Pin connection points overlap within one part/display mode",pins=[p["pin_number"] for p in pins],owner_part_id=part,display_mode=mode,x_mils=x,y_mils=y)
    check.result["pin_count"]=len(row["pins"])


def footprint_checks(row, rules, roles, check):
    pads=row["pad_details"];check.result["pad_count"]=len(pads)
    if not pads:
        check.add("footprint_no_pads","Footprint contains no pads")
    if row["other_copper_count"]:
        check.defer("nonpad_copper_unchecked","Non-pad copper/mask/paste objects are outside pad-pair geometry checks",count=row["other_copper_count"])
    if len(pads)>700:
        check.defer("geometry_limit","More than 700 physical pads; pair geometry deferred",pad_count=len(pads))
    copper={side:[] for side in ("top","middle","bottom")};apertures={kind:{side:[] for side in ("top","bottom")} for kind in ("mask","paste")}
    holes=[];classified=[]
    for index,pad in enumerate(pads):
        role,source,conflict=classify(pad,row,roles)
        target=dict(pad_index=index,pad_name=pad["name"])
        policy=roles.get((*identity(row),pad["name"].casefold()),{})
        paste_required=policy.get("paste_required",not (pad["is_testpoint_top"] or pad["is_testpoint_bottom"] or role=="npth")) if isinstance(policy,dict) else True
        classified.append(dict(**target,role=role,source=source,paste_required=paste_required))
        if conflict:
            check.add("pad_role_conflict","Explicit pad role contradicts native drill/plating properties",**target,role=role)
        if source=="name_hint":
            check.defer("shield_role_inferred","Shield role inferred from pad name; use pad_roles to confirm",**target)
        if role=="electrical" and not pad["name"].strip():
            check.add("electrical_pad_unnamed","Electrical pad has no name",**target)
        hole=hole_shape(pad)
        if pad["hole_size_mm"]>EPS:
            if canonical_name(pad["layer"]) != "Multi-Layer":
                check.add("hole_layer","Drilled pad must be on Multi Layer",**target)
            if hole is None:
                check.defer("hole_geometry_unsupported","Unsupported hole type or invalid slot dimensions",**target)
            elif role=="npth":
                holes.append((index,hole))
        if pad["mode"] not in (0,1):
            check.defer("full_stack_unchecked","Full/custom layer stacks need per-layer inspection",**target)
            continue
        layer=canonical_name(pad["layer"])
        sides=("top","middle","bottom") if layer=="Multi-Layer" else ("top",) if layer=="Top Layer" else ("bottom",) if layer=="Bottom Layer" else ()
        if not sides:
            check.defer("pad_layer_unsupported","Pad is outside supported copper layers",**target,layer=pad["layer"])
        for side_name in sides:
            side=pad["stack"][("top","middle","bottom").index(side_name)]
            width,height=side["width_mm"],side["height_mm"]
            # Pure NPTH drills legitimately have zero copper-land dimensions.
            if role=="npth":
                continue
            if min(width,height)<rules["min_pad_size_mm"]-EPS or max(width,height)>rules["max_pad_size_mm"]+EPS:
                check.add("pad_dimensions","Pad dimensions violate the configured range",**target,side=side_name,width_mm=width,height_mm=height)
            shape=pad_shape(pad,side)
            if shape is None:
                check.defer("pad_geometry_unsupported","Pad shape/dimensions cannot be evaluated",**target,side=side_name)
                continue
            copper[side_name].append((index,shape))
            if pad["plated"] and pad["hole_size_mm"]>EPS:
                ring=annular_ring(pad,side)
                if ring is None:
                    check.defer("annular_ring_unresolved","Annular ring needs unsupported shape inspection",**target)
                elif ring+EPS<rules["min_annular_ring_mm"]:
                    check.add("annular_ring","Annular ring is below the configured minimum",**target,side=side_name,actual_mm=ring,minimum_mm=rules["min_annular_ring_mm"])
            if side_name=="middle":
                continue
            for kind in ("mask","paste"):
                mode=pad["settings"][kind+"_mode"]
                if kind=="mask" and pad["tenting_"+side_name]:
                    if role=="electrical" and pad["hole_size_mm"]<=EPS:
                        check.add("smd_mask_tented","Electrical SMD pad is tented",**target,side=side_name)
                    continue
                if mode==0:
                    if role=="electrical" and pad["hole_size_mm"]<=EPS and (kind!="paste" or paste_required):
                        check.add(kind+"_disabled","Electrical SMD pad has no "+kind+" aperture","warning",**target,side=side_name)
                    continue
                if mode==2 and pad["settings"].get(kind+"_cache_valid",2)!=2:
                    check.defer(kind+"_cache_unresolved","Manual expansion cache is not marked native Manual",**target)
                    continue
                expansion=pad["settings"][kind+"_expansion_mm"] if mode==2 else rules["rule_"+kind+"_expansion_mm"] if mode==1 else None
                if expansion is None:
                    check.defer(kind+"_rule_unresolved",kind.title()+" mode needs an explicit rule expansion",**target,mode=mode)
                    continue
                if kind=="paste" and pad["hole_size_mm"]>EPS:
                    check.add("through_hole_paste","Drilled pad has an enabled paste aperture; confirm pin-in-paste intent","warning",**target,side=side_name)
                    continue
                if kind=="paste" and expansion>rules["max_paste_expansion_mm"]+EPS:
                    check.add("paste_expansion","Paste expansion exceeds the configured maximum",**target,actual_mm=expansion,maximum_mm=rules["max_paste_expansion_mm"])
                aperture=pad_shape(pad,side,expansion)
                if aperture is None:
                    if kind=="paste" and not paste_required:
                        continue
                    if row.get("other_aperture_count",0):
                        check.defer(kind+"_custom_apertures","Collapsed pad aperture may be replaced by separate mask/paste geometry",**target)
                    else:
                        check.add(kind+"_aperture_closed","Expansion collapses the aperture",**target,side=side_name,expansion_mm=expansion)
                else:
                    apertures[kind][side_name].append((index,aperture))
        if role=="npth" and (pad["is_testpoint_top"] or pad["is_testpoint_bottom"]):
            check.add("npth_testpoint","NPTH cannot be an electrical testpoint",**target)
    check.result["pad_roles"]=classified
    if len(pads)>700:
        return

    def compare(items, minimum, code, side, same_name_allowed=False):
        for pos,(index,a) in enumerate(items):
            for other,b in items[pos+1:]:
                if same_name_allowed and pads[index]["name"] and pads[index]["name"]==pads[other]["name"]:
                    continue
                actual=gap(a,b)
                if actual+EPS<minimum:
                    if a["approximate"] or b["approximate"]:
                        check.defer(code+"_unresolved","Conservative shape bounds need exact contour inspection",pad_indices=[index,other],side=side)
                    else:
                        check.add(code,"Pad/aperture edge gap is below the configured minimum",pad_indices=[index,other],pad_names=[pads[index]["name"],pads[other]["name"]],side=side,actual_mm=actual,minimum_mm=minimum)
    for side,items in copper.items():
        compare(items,rules["min_copper_clearance_mm"],"pad_clearance",side,True)
        for index,hole in holes:
            for other,shape in items:
                if index==other:
                    continue
                actual=gap(hole,shape)
                if actual+EPS<rules["min_npth_clearance_mm"]:
                    if shape["approximate"]:
                        check.defer("npth_clearance_unresolved","NPTH clearance requires exact pad contour inspection")
                    else:
                        check.add("npth_clearance","Copper is too close to an NPTH drill",pad_indices=[index,other],side=side,actual_mm=actual,minimum_mm=rules["min_npth_clearance_mm"])
    for kind,sides in apertures.items():
        for side,items in sides.items():
            compare(items,rules["min_mask_web_mm" if kind=="mask" else "min_paste_clearance_mm"],kind+"_clearance",side)


def candidates(symbol, model, footprints):
    matches=[r for r in footprints if r["component_name"].casefold()==model["model_name"].casefold()]
    declared=[]
    for link in model["datafile_links"]:
        if link["file_kind"].casefold()=="pcblib" and link["location"]:
            path=PureWindowsPath(link["location"])
            if not path.is_absolute():
                path=PureWindowsPath(symbol["library_path"]).parent/path
            declared.append(path)
    return [r for r in matches if PureWindowsPath(r["library_path"]) in declared] if declared else matches


def mapping_checks(symbol, footprints, rules, roles, check):
    pins={p["pin_number"] for p in symbol["pins"]}
    models=[m for m in symbol["models"] if m["model_type"].casefold()=="pcblib"]
    if not models and rules["require_footprint"] and symbol["properties"]["component_kind"] not in {"graphical","mechanical"}:
        check.add("footprint_model_missing","Symbol has no PCB footprint implementation")
    checked=[]
    for model in models:
        name=model["model_name"];mapping=model["pin_map"]
        if mapping is None:
            check.add("pin_map_parse","Native pin map could not be parsed",model_name=name,raw=model["map_as_string"])
            continue
        # Altium's empty map means same-name connectivity, not zero connections.
        implicit=not model["map_as_string"].strip()
        if implicit:
            mapping={pin:[pin] for pin in pins}
        for pin in sorted(set(mapping)-pins):
            check.add("pin_map_unknown_pin","Pin map references an absent symbol pin",model_name=name,pin_number=pin)
        for pin in sorted(pins-set(mapping)):
            check.add("pin_map_missing_pin","Symbol pin is not mapped",model_name=name,pin_number=pin)
        resolved=candidates(symbol,model,footprints)
        if len(resolved)!=1:
            check.defer("footprint_unresolved","Footprint must resolve uniquely in the explicit input libraries",key=name,model_name=name,candidate_count=len(resolved))
            continue
        footprint=resolved[0];pads=footprint["pad_details"]
        by_name=defaultdict(list)
        for pad in pads:
            by_name[pad["name"]].append(pad)
        used=defaultdict(list)
        for pin,names in mapping.items():
            if not names or len(set(names))!=len(names):
                check.add("pin_map_pad_list","Pin mapping is empty or repeats a pad",model_name=name,pin_number=pin)
            for pad_name in names:
                used[pad_name].append(pin)
                if pad_name not in by_name:
                    check.add("pin_map_unknown_pad","Pin map references an absent footprint pad",model_name=name,pin_number=pin,pad_name=pad_name)
                elif any(classify(p,footprint,roles)[0]=="npth" for p in by_name[pad_name]):
                    check.add("pin_map_npth","Electrical pin maps to an NPTH drill",model_name=name,pin_number=pin,pad_name=pad_name)
        for pad_name,pin_names in used.items():
            if len(set(pin_names))>1:
                check.add("pin_map_shared_pad","Different symbol pins map to the same pad; review the intended short",model_name=name,pad_name=pad_name,pin_numbers=pin_names)
        for pad_name,physical in by_name.items():
            role=classify(physical[0],footprint,roles)[0]
            if pad_name not in used and (role=="electrical" or (role=="shield" and rules["require_shield_mapping"])):
                check.add("pad_unmapped","Electrical/shield contact is not mapped to a symbol pin",model_name=name,pad_name=pad_name,role=role)
        checked.append(dict(model_name=name,library_path=footprint["library_path"],implicit_same_name_map=implicit))
    check.result["models_checked"]=checked


def analyze(rows, footprints, rules, roles, max_issues):
    components=[];dependencies=[];remaining=max_issues
    roots={identity(r) for r in rows}
    extra=[r for r in footprints if identity(r) not in roots]
    for row in rows+extra:
        check=Check(row,remaining)
        if row["library_kind"]=="SCHLIB":
            symbol_checks(row,rules,check);mapping_checks(row,footprints,rules,roles,check)
        else:
            footprint_checks(row,rules,roles,check)
        result=check.finish()
        (components if identity(row) in roots else dependencies).append(result)
        remaining=max(0,remaining-len(result["issues"]))
    totals=Counter()
    for row in components+dependencies:
        totals.update(row["issue_counts"])
    covered=all(r["validation_complete"] for r in components+dependencies)
    return dict(components=components,dependencies=dependencies,issue_counts=dict(totals),validation_complete=covered,
                valid=False if totals["error"] else True if covered else None,
                issues_truncated=any(r["issues_truncated"] for r in components+dependencies))


async def execute(bridge, exchange_dir, library_paths, component_names=None, rules=None, pad_roles=None,
                  offset=0, limit=50, max_issues=500):
    try:
        paths,profile,roles=prepare(library_paths,component_names,rules,pad_roles,offset,limit,max_issues)
        before={p:disk_state(p) for p in paths}
    except (ValueError,TypeError,OSError) as exc:
        return json.dumps(dict(success=False,error=str(exc),saved=False))
    calls=[]
    try:
        read=json.loads(await reader.execute(bridge,exchange_dir,paths,component_names,list(reader.FIELDS),offset,limit,validation=True))
        calls.append(read)
        if read.get("outcome_unknown") or not read.get("components") and not read.get("success"):
            return json.dumps({**read,"valid":None,"validation_complete":False})
        rows=read.get("components",[]);footprints=[r for r in rows if r["library_kind"]=="PCBLIB"]
        needed=sorted({m["model_name"] for r in rows if r["library_kind"]=="SCHLIB" for m in r["models"] if m["model_type"].casefold()=="pcblib"})
        pcb_paths=[p for p in paths if PureWindowsPath(p).suffix.lower()==".pcblib"]
        # Read dependencies regardless of root-name filtering or the root page.
        # All matching libraries are searched so off-page ambiguity stays visible.
        full_pcb_inventory=all(sum(PureWindowsPath(r["library_path"])==PureWindowsPath(p) for r in footprints)==
                               next((lib.get("component_count",-1) for lib in read.get("libraries",[]) if PureWindowsPath(lib["library_path"])==PureWindowsPath(p)),-1)
                               for p in pcb_paths)
        if needed and pcb_paths and not full_pcb_inventory:
            for start in range(0,len(needed),500):
                page_offset=0
                while True:
                    data=json.loads(await reader.execute(bridge,exchange_dir,pcb_paths,needed[start:start+500],list(reader.FIELDS),page_offset,500,validation=True))
                    calls.append(data)
                    if data.get("outcome_unknown"):
                        return json.dumps({**data,"valid":None,"validation_complete":False})
                    footprints.extend(data.get("components",[]))
                    if not data.get("has_more") or not data.get("success"):
                        break
                    page_offset=data["next_offset"]
        footprints=list({identity(r):r for r in footprints}.values())
        result=analyze(rows,footprints,profile,roles,max_issues)
        result.update(success=all(c.get("success",False) for c in calls),saved=False,rules=profile,
                      matched_count=read.get("matched_count",0),returned_count=len(rows),offset=offset,limit=limit,
                      has_more=read.get("has_more",False),next_offset=read.get("next_offset"),missing_names=read.get("missing_names",[]),
                      libraries=read.get("libraries",[]),read_errors=[dict(error=c.get("error"),libraries=c.get("library_errors",[]),components=c.get("component_errors",[]),verification=c.get("verification")) for c in calls if not c.get("success")],
                      dependency_footprint_count=len(footprints),native_call_count=len(calls),view_restored=all(c.get("view_restored") for c in calls),
                      geometry_scope="Concentric legacy standard pad lands; top/middle/bottom stacks; pad-pair geometry only")
        result["complete"]=bool(read.get("complete")) and result["success"] and result["validation_complete"]
        if not result["success"] or not read.get("complete"):
            result["validation_complete"]=False
            if result["valid"] is True:
                result["valid"]=None
        unchanged={p:disk_state(p) for p in paths}==before
        result["verification"]={"passed":unchanged and result["view_restored"] and result["success"],"disk_unchanged":unchanged}
        if not unchanged:
            result.update(success=False,complete=False,validation_complete=False,valid=None,error="Library files changed during validation")
        return json.dumps(result,indent=2,ensure_ascii=False)
    except (ValueError,KeyError,TypeError,AttributeError,OSError) as exc:
        return json.dumps(dict(success=False,error="Invalid validation snapshot: "+str(exc),saved=False,valid=None,validation_complete=False))
