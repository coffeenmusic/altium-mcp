"""Opt-in Top/Bottom testpoint checks on a disposable PcbLib copy; no saves."""

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


def geometry(dump, ignore_testpoint_flags=True):
    result=dict(dump)
    ignored={'is_testpoint_top','is_testpoint_bottom'} if ignore_testpoint_flags else set()
    result['primitives']=[{k:v for k,v in row.items() if k not in ignored}
                          for row in dump.get('primitives',[])]
    # Altium may reverse undirected track endpoints while rebuilding an undo group.
    for row in result['primitives']:
        if row.get('type')=='track':
            a,b=sorted(((row['x1'],row['y1']),(row['x2'],row['y2'])))
            row.update(x1=a[0],y1=a[1],x2=b[0],y2=b[1])
    for key in ('primitives','bodies_3d'):
        if key in result:
            result[key]=sorted(result[key],key=lambda row:json.dumps(row,sort_keys=True))
    return result


async def run(args):
    if not args.exercise:
        print('Pass --exercise to edit only a disposable library copy without saving.')
        return
    import main
    logging.getLogger().setLevel(logging.WARNING)
    root=REPO/'.altium-mcp'/('pad_properties_'+time.strftime('%Y%m%d_%H%M%S'))
    root.mkdir(parents=True)
    source=Path(args.library).resolve()
    scratch=root/'Testpoints_\u03a9_\u0416.PcbLib'
    shutil.copy2(source,scratch)
    report={'saved':False,'source':str(source),'scratch':str(scratch),'steps':[],'footprints':[]}
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
        row={'label':label,**{k:result.get(k) for k in ('success','changed','updated_count','error','saved')}}
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

    try:
        original=require(await call('project_before',main.get_project_info))
        guarded=list({source,scratch,Path(original['project_path']),*(Path(d['path']) for d in original['documents'])})
        report['disk_before']={str(p):disk_state(p) for p in guarded}
        record('disk_before',report['disk_before'])
        require(await call('open_scratch',main.open_document,str(scratch)))
        opened=True
        inventory=require(await call('inventory',main.get_footprint_primitives,str(scratch)))
        other=next((row['name'] for row in inventory['footprints'] if row['name'] not in args.footprints),None)
        other_before=require(await call('other_before',main.get_footprint_primitives,str(scratch),other)) if other else None
        for index,name in enumerate(args.footprints):
            prefix=str(index)+'_'
            before=require(await call(prefix+'geometry_before',main.get_footprint_primitives,str(scratch),name))
            names=[row['name'] for row in before['primitives'] if row['type']=='pad']
            assert names and len(set(names))==len(names),'fixture needs unique named pads'
            baseline=require(await call(prefix+'read_before',main.set_footprint_pad_properties,str(scratch),name,names))
            assert baseline['changed'] is False
            if index==0:
                refused=await call('refuse_missing_pad',main.set_footprint_pad_properties,str(scratch),name,
                                   [names[0],'MCP_MISSING_PAD'],is_testpoint_top=not baseline['after'][0]['is_testpoint_top'])
                assert refused['success'] is False and refused['updated_count']==0 and not refused['may_have_partial_changes']
                untouched=require(await call('after_refusal',main.set_footprint_pad_properties,str(scratch),name,names))
                assert baseline['after']==untouched['after']
            require(await call(prefix+'clear_both',main.set_footprint_pad_properties,str(scratch),name,names,False,False))
            require(await call(prefix+'top_only',main.set_footprint_pad_properties,str(scratch),name,names,True))
            repeated=require(await call(prefix+'repeat_top',main.set_footprint_pad_properties,str(scratch),name,names,True))
            assert repeated['changed'] is False
            refused=await call(prefix+'refuse_wrong_side',main.set_footprint_pad_properties,str(scratch),name,names,is_testpoint_bottom=True)
            assert refused['success'] is False and refused['updated_count']==0 and not refused['may_have_partial_changes']
            require(await call(prefix+'clear_top',main.set_footprint_pad_properties,str(scratch),name,names,is_testpoint_top=False))
            require(await call(prefix+'one_pad',main.set_footprint_pad_properties,str(scratch),name,[names[0]],True,False))
            after=require(await call(prefix+'geometry_after',main.get_footprint_primitives,str(scratch),name))
            assert geometry(before)==geometry(after),'pad geometry, masks or other footprint data changed'
            for row in after['primitives']:
                if row['type']=='pad':
                    first=row['name']==names[0]
                    assert row['is_testpoint_top'] is first and row['is_testpoint_bottom'] is False
            report['footprints'].append({'name':name,'pad_count':len(names),'geometry_preserved':True})
        # Bottom and through-hole fixtures stay in the same disposable library.
        constants=require(await call('fixture_constants',main.get_pcb_api_constants,['eRectangular']))
        shape=constants['constants']['eRectangular']
        hole_type=next(row['hole_type'] for row in before['primitives'] if row['type']=='pad')
        lines=['FPLIB|'+str(scratch)]
        fixtures=[('MCP_BOTTOM_TEST','Bottom Layer'),('MCP_MULTILAYER_TEST','Multi-Layer')]
        for fixture,layer in fixtures:
            lines.append('FOOTPRINT|'+fixture+'|Temporary testpoint side fixture')
            for n in (1,2):
                lines.append('|'.join(map(str,['PAD',n,n*100,0,0,layer,int(layer=='Multi-Layer'),
                                               20 if layer=='Multi-Layer' else 0,hole_type,0,0,60,60,shape])))
        from altium_files import write_altium_text
        spec_file=root/'fixtures.txt'
        write_altium_text(spec_file,'\n'.join(lines)+'\n')
        created=require(await call('create_side_fixtures',main.create_footprints_batch,str(spec_file)))
        assert created['created']==2 and not created.get('problems')
        for fixture,layer in fixtures:
            initial=require(await call(fixture+'_before',main.get_footprint_primitives,str(scratch),fixture))
            require(await call(fixture+'_bottom',main.set_footprint_pad_properties,str(scratch),fixture,['1','2'],False,True))
            repeated=require(await call(fixture+'_repeat',main.set_footprint_pad_properties,str(scratch),fixture,['1','2'],False,True))
            assert repeated['changed'] is False
            if layer=='Multi-Layer':
                require(await call(fixture+'_both',main.set_footprint_pad_properties,str(scratch),fixture,['1','2'],True,True))
            else:
                refused=await call(fixture+'_refuse_top',main.set_footprint_pad_properties,str(scratch),fixture,['1','2'],True)
                assert refused['success'] is False and refused['updated_count']==0
            final=require(await call(fixture+'_after',main.get_footprint_primitives,str(scratch),fixture))
            assert geometry(initial)==geometry(final)
            for pad in final['primitives']:
                if pad['type']=='pad':
                    assert pad['is_testpoint_bottom'] is True
                    assert pad['is_testpoint_top'] is (layer=='Multi-Layer')
        report['bottom_and_multilayer_passed']=True
        for index,options in enumerate(({'is_testpoint_top':1},{'is_testpoint_bottom':'true'})):
            refused=await call('invalid_'+str(index),main.set_footprint_pad_properties,str(scratch),args.footprints[0],['1'],**options)
            assert refused['success'] is False and not refused['may_have_partial_changes']
        if other:
            other_after=require(await call('other_after',main.get_footprint_primitives,str(scratch),other))
            report['other_footprint_unchanged']=geometry(other_before,False)==geometry(other_after,False)
        report['checks_passed']=all(row['geometry_preserved'] for row in report['footprints']) and report.get('other_footprint_unchanged',True)
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
    parser.add_argument('--footprints',nargs='+',required=True)
    parser.add_argument('--exercise',action='store_true')
    asyncio.run(run(parser.parse_args()))
