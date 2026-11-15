"""Opt-in persistence checks; explicit saves target disposable copies only."""

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
        print('Pass --exercise; only disposable library copies will be saved.')
        return
    import main
    from library_roundtrip_files import saved_step_state
    root=REPO/'.altium-mcp'/('library_roundtrip_'+time.strftime('%Y%m%d_%H%M%S'));root.mkdir(parents=True)
    logging.getLogger().setLevel(logging.WARNING)
    paths=[root/'Round_\u03a9.SchLib',root/'Round_\u0416.PcbLib']
    for source,target in zip((args.schlib,args.pcblib),paths):
        shutil.copy2(source,target)
    report={'steps':[],'original_files_saved':False};original=None;opened=[];unknown=False;guarded=[]

    async def call(label,func,*values,**options):
        nonlocal unknown
        stable=0
        for _ in range(40):
            windows=editor_windows();stable=stable+1 if windows and all(w['responding'] for w in windows) else 0
            if stable>=3: break
            await asyncio.sleep(.5)
        else:
            unknown=True;raise RuntimeError('Altium not responsive; no further command sent')
        value=json.loads(await func(None,*values,**options))
        (root/(label+'.json')).write_text(json.dumps(value,indent=2,ensure_ascii=True),encoding='ascii')
        item={'label':label,**{k:value.get(k) for k in ('success','saved','equivalent','difference_count','error')}}
        report['steps'].append(item);print(json.dumps(item),flush=True)
        if value.get('outcome_unknown') or value.get('close_outcome_unknown') or value.get('save_outcome_unknown'):
            unknown=True;raise RuntimeError('Unknown completion; no further command sent')
        return value

    def require(value):
        assert value.get('success',True) and not value.get('error'),value
        return value

    def equal(value):
        require(value)
        assert value['equivalent'] is True and value['difference_count']==0 and value['coverage_complete'],value

    try:
        original=require(await call('project_before',main.get_project_info))
        guarded=list({Path(args.schlib),Path(args.pcblib),Path(original['project_path']),*(Path(d['path']) for d in original['documents'])})
        report['disk_before']={str(p):disk_state(p) for p in guarded}
        scratch_before={str(p):disk_state(p) for p in paths};path_args=[str(p) for p in paths]
        clean=await call('clean_reopen',main.verify_library_roundtrip,path_args)
        equal(clean);assert clean['saved'] is False and clean['backups']=={}
        assert all(row['was_open'] is False and row['is_open'] is False for row in clean['libraries'])
        assert scratch_before=={str(p):disk_state(p) for p in paths}
        report['clean_reopen_passed']=True
        for i,path in enumerate(paths):
            require(await call('open_'+str(i),main.open_document,str(path)));opened.append(path)
        name='LTST-C155KGJRKT'
        require(await call('dirty_symbol',main.set_library_component_data,str(paths[0]),name,
                           parameters={'Roundtrip Probe':'\u03a9\u0416 "pins" | ='}))
        refused=await call('dirty_refusal',main.verify_library_roundtrip,path_args)
        assert refused['success'] is False and refused['saved'] is False and not refused.get('outcome_unknown')
        assert not refused.get('reopened_paths') and not refused.get('save_attempted_paths')
        assert scratch_before=={str(p):disk_state(p) for p in paths}
        saved=await call('save_and_reopen',main.verify_library_roundtrip,path_args,save=True)
        equal(saved);assert set(saved['saved_paths'])==set(path_args) and len(saved['backups'])==2
        for path,backup in saved['backups'].items():
            assert disk_state(Path(backup))['sha256']==scratch_before[path]['sha256']
        verify=require(await call('read_persisted_parameter',main.set_library_component_data,str(paths[0]),name))
        assert verify['after']['parameters']['Roundtrip Probe']=='\u03a9\u0416 "pins" | ='
        report['explicit_save_passed']=True
        step_before=saved_step_state(paths[1])
        require(await call('dirty_footprint_height',main.set_footprint_properties,str(paths[1]),'GSWITCH_GT_USB_7010ASV',4.56))
        partial=require(await call('dirty_step_coverage',main.verify_library_roundtrip,str(paths[1]),save=True))
        assert partial['equivalent'] is None and partial['difference_count']==0 and partial['saved']
        assert 'dirty native buffer' in str(partial['libraries'][0]['coverage_gaps'])
        assert saved_step_state(paths[1])==step_before
        final=await call('clean_after_save',main.verify_library_roundtrip,path_args)
        equal(final);assert final['saved'] is False
        height=require(await call('read_persisted_height',main.set_footprint_properties,str(paths[1]),'GSWITCH_GT_USB_7010ASV'))
        assert abs(height['after']['height_mm']-4.56)<.00001
        report['step_coverage_passed']=True
        fresh=root/'Unsaved.SchLib';require(await call('create_unsaved',main.create_document,str(fresh)));opened.append(fresh)
        refused=await call('refuse_unsaved',main.verify_library_roundtrip,str(fresh))
        assert refused['success'] is False and not fresh.exists()
        created=await call('save_unsaved',main.verify_library_roundtrip,str(fresh),save=True)
        equal(created);assert fresh.is_file() and created['saved'] and created['backups']=={}
        report['unsaved_passed']=True;report['checks_passed']=True
    except Exception as exc:
        report.update(checks_passed=False,error=str(exc) or repr(exc),outcome_unknown=unknown)
    finally:
        if not unknown:
            try:
                for i,path in enumerate(list(opened)):
                    require(await call('close_'+str(i),main.close_document,str(path),require_clean=False));opened.remove(path)
                if original:
                    require(await call('restore_project',main.select_project,original['project_path']))
                    active=next((d['path'] for d in original['documents'] if d['state']=='open'),None)
                    if active: require(await call('restore_editor',main.open_document,active))
                    after=require(await call('project_after',main.get_project_info))
                    report['original_states_unchanged']=after==original
            except Exception as exc:
                report['cleanup_error']=str(exc) or repr(exc)
        report['disk_after']={str(p):disk_state(p) for p in guarded}
        report['disk_unchanged']=report.get('disk_before')==report['disk_after'];report['scratch_closed']=not opened
        (root/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=True),encoding='ascii')
        print('REPORT '+str(root/'report.json'),flush=True)
    assert all(report.get(k) for k in ('checks_passed','original_states_unchanged','disk_unchanged','scratch_closed')),report.get('error',report)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--exercise',action='store_true')
    parser.add_argument('--schlib',type=Path,default=Path(r'C:\workspace_altium\SQ-CARD\SQ-CARD.SchLib'))
    parser.add_argument('--pcblib',type=Path,default=Path(r'C:\workspace_altium\SQ-CARD\SQ-CARD.PcbLib'))
    asyncio.run(run(parser.parse_args()))
