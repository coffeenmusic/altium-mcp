"""Opt-in native height checks on a disposable PcbLib copy; never save in Altium."""

import argparse
import asyncio
import json
import logging
from pathlib import Path
import shutil
import sys
import time

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/'server'))
from test_footprint_editor_live import disk_state, editor_windows


async def run(args):
    if not args.exercise:
        print('Pass --exercise to change a disposable PcbLib copy without saving.')
        return
    import main
    logging.getLogger().setLevel(logging.WARNING)
    root=REPO/'.altium-mcp'/('footprint_properties_'+time.strftime('%Y%m%d_%H%M%S'))
    root.mkdir(parents=True)
    source=Path(args.library).resolve()
    scratch=root/'Height_\u03a9_\u0416.PcbLib'
    shutil.copy2(source,scratch)
    report={'saved':False,'source':str(source),'scratch':str(scratch),'steps':[]}
    original=None
    guarded=[]
    opened=False
    unknown=False

    def record(label,data):
        (root/(label+'.json')).write_text(json.dumps(data,indent=2,ensure_ascii=True),encoding='ascii')

    async def call(label,function,*values,**options):
        nonlocal unknown
        stable=0
        for _ in range(40):
            windows=editor_windows()
            stable=stable+1 if windows and all(w['responding'] for w in windows) else 0
            if stable>=3:
                break
            await asyncio.sleep(.5)
        else:
            unknown=True
            raise RuntimeError('Altium not responsive; no further command sent')
        result=json.loads(await function(None,*values,**options))
        record(label,result)
        row={'label':label,**{k:result.get(k) for k in ('success','changed','error','saved')}}
        report['steps'].append(row)
        print(json.dumps(row,ensure_ascii=True),flush=True)
        if result.get('outcome_unknown') or result.get('close_outcome_unknown'):
            unknown=True
            raise RuntimeError('Unknown executor completion; no further command sent')
        return result

    def require(result):
        assert result.get('success',True) and not result.get('error'),result
        assert result.get('saved',False) is False,result
        return result

    def geometry(dump):
        value={k:v for k,v in dump.items() if k!='height_mm'}
        # Native undo registration can reorder iteration without changing objects.
        for key in ('primitives','bodies_3d'):
            if key in value:
                value[key]=sorted(value[key],key=lambda item:json.dumps(item,sort_keys=True))
        return value

    try:
        original=require(await call('project_before',main.get_project_info))
        guarded=list({source,scratch,Path(original['project_path']),*(Path(d['path']) for d in original['documents'])})
        report['disk_before']={str(p):disk_state(p) for p in guarded}
        record('disk_before',report['disk_before'])
        require(await call('open_scratch',main.open_document,str(scratch)))
        opened=True
        inventory=require(await call('inventory',main.get_footprint_primitives,str(scratch)))
        names=[fp['name'] for fp in inventory['footprints']]
        name=args.footprint or next((n for n in names if 'usb' in n.casefold()),names[0])
        report['footprint']=name
        untouched=next((n for n in names if n!=name),None)
        other_before=require(await call('other_before',main.set_footprint_properties,str(scratch),untouched)) if untouched else None
        baseline=require(await call('height_before',main.set_footprint_properties,str(scratch),name))
        assert baseline['changed'] is False and baseline['was_modified']==baseline['is_modified']
        before=require(await call('geometry_before',main.get_footprint_primitives,str(scratch),name))
        for i,height in enumerate((3.2,.123456,0)):
            changed=require(await call('height_'+str(i),main.set_footprint_properties,str(scratch),name,height))
            assert abs(changed['after']['height_mm']-height)<.000003
            repeated=require(await call('repeat_'+str(i),main.set_footprint_properties,str(scratch),name,height))
            assert repeated['changed'] is False and repeated['was_modified']==repeated['is_modified']
            readback=require(await call('read_'+str(i),main.set_footprint_properties,str(scratch),name))
            assert abs(readback['after']['height_mm']-height)<.000003
            dumped=require(await call('dump_'+str(i),main.get_footprint_primitives,str(scratch),name))
            assert abs(dumped['height_mm']-height)<.000003
            assert geometry(dumped)==geometry(before)
        for i,height in enumerate((-1,True,float('nan'),1001)):
            refused=await call('invalid_'+str(i),main.set_footprint_properties,str(scratch),name,height)
            assert refused['success'] is False and refused['may_have_partial_changes'] is False
        refused=await call('missing_footprint',main.set_footprint_properties,str(scratch),'MCP_MISSING_FOOTPRINT',3.2)
        assert refused['success'] is False and refused['may_have_partial_changes'] is False
        after=require(await call('geometry_after',main.get_footprint_primitives,str(scratch),name))
        report['geometry_and_bodies_unchanged']=geometry(before)==geometry(after)
        if untouched:
            other_after=require(await call('other_after',main.set_footprint_properties,str(scratch),untouched))
            report['other_footprint_unchanged']=other_before['after']==other_after['after']
        report['checks_passed']=report['geometry_and_bodies_unchanged'] and report.get('other_footprint_unchanged',True)
    except Exception as exc:
        report.update(checks_passed=False,error=str(exc),outcome_unknown=unknown)
    finally:
        if not unknown:
            try:
                if opened:
                    require(await call('discard_scratch',main.close_document,str(scratch),require_clean=False))
                    opened=False
                if original:
                    require(await call('restore_project',main.select_project,original['project_path']))
                    active=next((d['path'] for d in original['documents'] if d['state']=='open'),None)
                    if active:
                        require(await call('restore_editor',main.open_document,active))
                    final=require(await call('project_after',main.get_project_info))
                    report['original_states_unchanged']=final==original
            except Exception as exc:
                report['cleanup_error']=str(exc)
        report['disk_after']={str(p):disk_state(p) for p in guarded}
        report['disk_unchanged']=report.get('disk_before')==report['disk_after']
        report['scratch_closed']=not opened
        record('report',report)
        print('REPORT '+str(root/'report.json'),flush=True)
    assert report.get('checks_passed') and report.get('original_states_unchanged') and report.get('disk_unchanged') and report['scratch_closed'],report.get('error',report)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library',required=True)
    parser.add_argument('--footprint')
    parser.add_argument('--exercise',action='store_true')
    asyncio.run(run(parser.parse_args()))
