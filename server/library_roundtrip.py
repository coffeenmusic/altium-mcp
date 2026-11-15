"""Whole-library persistence checks with explicit save policy and STEP coverage."""

import hashlib
import json
import math
from pathlib import Path, PureWindowsPath
import shutil
import uuid

from altium_files import write_altium_text
from document_management import absolute_path, writable_local_path
from library_component_data import parse_pin_map
from library_roundtrip_files import saved_step_state
from schematic_document import disk_state


def prepare(library_paths, save, max_differences, coordinate_tolerance_mils):
    if isinstance(library_paths,str):
        library_paths=[library_paths]
    if not isinstance(library_paths,list) or not 1<=len(library_paths)<=8:
        raise ValueError("library_paths requires one path or 1..8 paths")
    paths=[str(absolute_path(p,{'.schlib','.pcblib'})) for p in library_paths]
    if len({PureWindowsPath(p) for p in paths})!=len(paths):
        raise ValueError("library_paths must be distinct")
    if type(save) is not bool:
        raise ValueError("save must be an explicit boolean")
    if type(max_differences) is not int or not 1<=max_differences<=10000:
        raise ValueError("max_differences must be within 1..10000")
    if (type(coordinate_tolerance_mils) not in (int,float) or not math.isfinite(coordinate_tolerance_mils)
            or not 0<=coordinate_tolerance_mils<=1):
        raise ValueError("coordinate_tolerance_mils must be finite within 0..1")
    if save:
        for p in paths:
            writable_local_path(Path(p))
    return paths


IGNORED={'_id','library_name','library_path','library_origin_x','library_origin_y'}


def normalize(value,key=''):
    if isinstance(value,dict):
        # Custom parameter names/values are data, including names like _id.
        if key=='parameters' and all(isinstance(v,str) for v in value.values()):
            return value.copy()
        source=value
        value={k:normalize(v,k) for k,v in value.items()
               if not (key=='graphics' and k in IGNORED) and not (k in {'_id','unique_id'} and 'type' in value)}
        if 'map_as_string' in value and 'model_type' in source:
            try: value['map_as_string']=normalize(parse_pin_map(value['map_as_string']))
            except ValueError: pass
        if value.get('type')=='track':
            a,b=sorted(((value['x1'],value['y1']),(value['x2'],value['y2'])))
            value.update(x1=a[0],y1=a[1],x2=b[0],y2=b[1])
        if value.get('model_embedded') and value.get('model_file'):
            value['model_file']=PureWindowsPath(value['model_file']).name.casefold()
        if value.get('embedded') and value.get('model_name'):
            value['model_name']=PureWindowsPath(value['model_name']).name.casefold()
        return value
    if isinstance(value,list):
        rows=[normalize(v) for v in value]
        return rows if key=='vertices' else sorted(rows,key=lambda row:json.dumps(row,sort_keys=True,ensure_ascii=True))
    return value


def digest(value):
    return hashlib.sha256(json.dumps(normalize(value),sort_keys=True,ensure_ascii=True,separators=(',',':')).encode('ascii')).hexdigest()


def differences(a,b,tolerance,cap):
    result=[];count=0
    def add(path,left,right):
        nonlocal count
        count+=1
        if len(result)<cap:
            result.append(dict(path=path,before=left,after=right))
    def visit(left,right,path,key=''):
        if type(left) in (int,float) and type(right) in (int,float):
            metric={'x','y','x1','x2','y1','y2','cx','cy','radius','secondary_radius','length','width','height','size',
                    'hole_size','hole_width','top_x_size','top_y_size','mid_x_size','mid_y_size','bot_x_size','bot_y_size',
                    'standoff_height','overall_height','model_x','model_y','model_z_offset','mask_expansion','paste_expansion',
                    'corner_x_radius','corner_y_radius'}
            angles={'rotation','rotation_x','rotation_y','rotation_z','start_angle','end_angle','hole_rotation'}
            limit=tolerance*.0254 if key.endswith('_mm') else tolerance if key in metric else .001 if key in angles else 0
            if abs(left-right)>limit+1e-10:
                add(path,left,right)
        elif type(left)!=type(right):
            add(path,left,right)
        elif isinstance(left,dict):
            for k in sorted(set(left)|set(right)):
                if k not in left or k not in right:
                    add(path+'.'+k,left.get(k),right.get(k))
                else:
                    visit(left[k],right[k],path+'.'+k,k)
        elif isinstance(left,list):
            if len(left)!=len(right):
                add(path+'.length',len(left),len(right))
            for index,(x,y) in enumerate(zip(left,right)):
                visit(x,y,f'{path}[{index}]',key)
        elif left!=right:
            add(path,left,right)
    visit(normalize(a),normalize(b),'$')
    return result,count


def gaps(value,path='$'):
    """Unknown fields must not turn an equal exposed snapshot into full proof."""
    result=[]
    if isinstance(value,dict):
        if value.get('type') in {'unknown','unreadable','unsupported'}:
            result.append(dict(path=path,reason='Unsupported/unreadable primitive'))
        if value.get('type')=='pad' and value.get('mode')==2:
            result.append(dict(path=path,reason='Full/custom pad stack is not fully exposed'))
        if value.get('type')=='region' and value.get('hole_count',0):
            result.append(dict(path=path,reason='Region hole contours are not exposed'))
        for k,v in value.items():
            result.extend(gaps(v,path+'.'+k))
    elif isinstance(value,list):
        for i,v in enumerate(value):
            result.extend(gaps(v,f'{path}[{i}]'))
    return result


def body_count(snapshot):
    return sum(len(fp.get('bodies_3d',[])) for fp in snapshot.get('graphics',{}).get('footprints',[]))


def step_gaps(state,expected):
    if not expected:
        return []
    if not state.get('readable'):
        return [dict(reason='Saved STEP evidence is unreadable',detail=state.get('reason'))]
    bodies=[b for values in state['footprints'].values() for b in values]
    problems=[]
    if len(bodies)!=expected:
        problems.append(dict(reason='Saved STEP body count does not match native bodies',native_count=expected,saved_count=len(bodies)))
    if any(b.get('payload') is None for b in bodies):
        problems.append(dict(reason='Embedded payload or linked STEP file is unavailable'))
    if any(any(b.get(k) is None for k in ('rotation_x','rotation_y','rotation_z','model_z_offset')) for b in bodies):
        problems.append(dict(reason='STEP placement fields are missing'))
    return problems


async def execute(bridge,exchange_dir,library_paths,save=False,max_differences=100,coordinate_tolerance_mils=.001):
    root=None;backups={}
    try:
        paths=prepare(library_paths,save,max_differences,coordinate_tolerance_mils)
        before={p:disk_state(p) for p in paths}
        root=Path(__file__).resolve().parents[1]/'.altium-mcp'/'roundtrip_runs'/uuid.uuid4().hex;root.mkdir(parents=True)
        steps_before={p:saved_step_state(p) for p in paths if PureWindowsPath(p).suffix.lower()=='.pcblib'}
        backups={}
        if save:
            (root/'backups').mkdir()
            for index,p in enumerate(paths):
                if Path(p).is_file():
                    target=root/'backups'/(str(index)+'_'+Path(p).name);shutil.copy2(p,target);backups[p]=str(target)
                    if disk_state(target)[0]!=before[p][0]:
                        raise ValueError('Library changed while creating its backup')
        if {p:disk_state(p) for p in paths}!=before:
            raise ValueError('Library files changed during preparation; no native command sent')
        request=root/'request.txt'
        spec={'path_count':len(paths),'save':int(save),**{f'path.{i}':p for i,p in enumerate(paths)}}
        write_altium_text(request,'\n'.join(f'{k}={json.dumps(str(v),ensure_ascii=False)[1:-1]}' for k,v in spec.items())+'\n')
    except (ValueError,TypeError,OSError) as exc:
        result=dict(success=False,error=str(exc),saved=False,equivalent=None)
        if root:
            result.update(report_path=str(root/'report.json'),backups=backups)
            (root/'report.json').write_text(json.dumps(result,indent=2,ensure_ascii=True),encoding='ascii')
        return json.dumps(result)
    try:
        try:
            response=await bridge.execute_command('verify_library_roundtrip',{'spec_file':str(request)})
        except Exception as exc:
            response={'error':str(exc)}
        native=response.get('result')
        if isinstance(native,str):
            try: native=json.loads(native)
            except ValueError: native=None
        if not isinstance(native,dict) or type(native.get('success')) is not bool:
            result=dict(success=False,error=response.get('error','Invalid native reply'),equivalent=None,saved=False,
                        command_started=response.get('command_started'),outcome_unknown=response.get('command_started') is not False,
                        save_outcome_unknown=save and response.get('command_started') is not False,
                        may_have_view_changes=response.get('command_started') is not False)
            if response.get('command_started') is False:
                request.unlink(missing_ok=True)
        else:
            (root/'native.json').write_text(json.dumps(native,indent=2,ensure_ascii=True),encoding='ascii')
            after={p:disk_state(p) for p in paths}
            steps_after={p:saved_step_state(p) for p in steps_before}
            (root/'step_before.json').write_text(json.dumps(steps_before,indent=2,ensure_ascii=True),encoding='ascii')
            (root/'step_after.json').write_text(json.dumps(steps_after,indent=2,ensure_ascii=True),encoding='ascii')
            result={k:v for k,v in native.items() if k!='libraries'}
            result.update(equivalent=None,libraries=[],difference_count=0,differences_truncated=False,coverage_complete=False,
                          disk_before=before,disk_after=after,coordinate_tolerance_mils=coordinate_tolerance_mils)
            protocol=[];remaining=max_differences;coverage=True
            if native.get('success') and native.get('save_requested') is not save:
                protocol.append('Native save policy does not match the explicit request')
            native_paths=[r.get('library_path') for r in native.get('libraries',[])]
            if native.get('success') and [PureWindowsPath(p) for p in native_paths]!=[PureWindowsPath(p) for p in paths]:
                protocol.append('Snapshot library identities/count do not match requested targets')
            for key in ('saved_paths','save_attempted_paths','reopened_paths'):
                if any(PureWindowsPath(p) not in {PureWindowsPath(x) for x in paths} for p in native.get(key,[])):
                    protocol.append('Native operation touched an unrequested library')
            if not save and (native.get('saved') or native.get('saved_paths') or native.get('save_attempted_paths')):
                protocol.append('Default mode unexpectedly attempted saving')
            if native.get('success'):
                if (set(PureWindowsPath(p) for p in native.get('reopened_paths',[]))!={PureWindowsPath(p) for p in paths}
                        or not all(native.get(k) is True for k in ('view_restored','selections_restored','project_restored','unrelated_documents_preserved'))):
                    protocol.append('Reopen/selection/context preservation was not confirmed')
                if save and {PureWindowsPath(p) for p in native.get('saved_paths',[])}!={PureWindowsPath(p) for p in paths}:
                    protocol.append('Explicit save did not confirm every requested file')
            for row in native.get('libraries',[]):
                path=row['library_path'];entry={k:v for k,v in row.items() if k not in ('before','after')}
                left,right=row['before'],row.get('after')
                missing=gaps(left)+gaps(right)
                diffs,count=differences(left,right,coordinate_tolerance_mils,remaining) if right is not None else ([],0)
                entry.update(differences=diffs,difference_count=count,before_sha256=digest(left),after_sha256=digest(right) if right is not None else None)
                result['difference_count']+=count;remaining=max(0,remaining-len(diffs))
                if PureWindowsPath(path).suffix.lower()=='.pcblib':
                    bodies=body_count(left)
                    entry['step_body_count']=bodies
                    missing.extend(step_gaps(steps_after[path],body_count(right or {})))
                    if row['was_modified'] and bodies:
                        missing.append(dict(reason='Pre-save STEP rotations/offsets/payload are unavailable from a dirty native buffer'))
                        entry['step_comparison']='saved_file_after_and_native_bindings_only'
                    elif bodies:
                        missing.extend(step_gaps(steps_before[path],bodies))
                        sd,sc=differences(steps_before[path],steps_after[path],coordinate_tolerance_mils,remaining)
                        entry['differences'].extend(dict(d,scope='STEP') for d in sd)
                        entry['difference_count']+=sc;result['difference_count']+=sc;remaining=max(0,remaining-len(sd))
                        entry['step_comparison']='saved_placements_and_used_payload_hashes'
                    else:
                        entry['step_comparison']='no_native_3d_bodies'
                entry['coverage_gaps']=missing;coverage=coverage and not missing and right is not None
                entry['equivalent']=False if entry['difference_count'] else None if missing or right is None else True
                if (native.get('success') or right is not None) and (row.get('is_modified') is not False or row.get('is_open')!=row.get('was_open')):
                    protocol.append('Target editor open/Modified restoration failed')
                result['libraries'].append(entry)
            if not save and before!=after:
                protocol.append('Library file hash/timestamp changed without save permission')
            # Detect changes even on a known preflight refusal/partial failure.
            attempted={PureWindowsPath(p) for p in native.get('save_attempted_paths',[])}
            if any(before[p]!=after[p] and PureWindowsPath(p) not in attempted for p in paths):
                protocol.append('An unattempted library file changed')
            result['verification']={'passed':native.get('success') and not protocol,'errors':protocol}
            result['coverage_complete']=coverage and len(native.get('libraries',[]))==len(paths) and native.get('success') and not protocol
            result['equivalent']=False if result['difference_count'] else True if result['coverage_complete'] else None
            result['differences_truncated']=sum(len(r['differences']) for r in result['libraries'])<result['difference_count']
            if protocol:
                result.update(success=False,error='Roundtrip protocol/preservation verification failed')
            request.unlink(missing_ok=True)
        result.update(report_path=str(root/'report.json'),backups=backups,save_requested=save)
        (root/'report.json').write_text(json.dumps(result,indent=2,ensure_ascii=True),encoding='ascii')
        return json.dumps(result,indent=2,ensure_ascii=False)
    except (ValueError,TypeError,KeyError,OSError) as exc:
        result=dict(success=False,error=str(exc),equivalent=None,save_requested=save,
                    save_outcome_unknown=save,may_have_view_changes=True,report_path=str(root/'report.json'),backups=backups)
        (root/'report.json').write_text(json.dumps(result,indent=2,ensure_ascii=True),encoding='ascii')
        return json.dumps(result)
