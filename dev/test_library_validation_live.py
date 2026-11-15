"""Opt-in validation of live project libraries and disposable good/bad fixtures."""

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
        print('Pass --exercise to validate open libraries and disposable unsaved fixtures.')
        return
    import main
    from altium_files import write_altium_text
    root=REPO/'.altium-mcp'/('library_validation_'+time.strftime('%Y%m%d_%H%M%S'))
    root.mkdir(parents=True)
    logging.getLogger().setLevel(logging.WARNING)
    paths=[root/'Validate_\u03a9.SchLib',root/'Validate_\u0416.PcbLib']
    for source,target in zip((args.schlib,args.pcblib),paths):
        shutil.copy2(source,target)
    opened=[];original=None;unknown=False;guarded=[]
    report={'saved':False,'steps':[]}

    async def call(label,func,*values,**options):
        nonlocal unknown
        stable=0
        for _ in range(40):
            windows=editor_windows();stable=stable+1 if windows and all(w['responding'] for w in windows) else 0
            if stable>=3:
                break
            await asyncio.sleep(.5)
        else:
            unknown=True;raise RuntimeError('Altium not responsive; no further command sent')
        result=json.loads(await func(None,*values,**options))
        (root/(label+'.json')).write_text(json.dumps(result,indent=2,ensure_ascii=True),encoding='ascii')
        row={'label':label,**{k:result.get(k) for k in ('success','valid','returned_count','issue_counts','error')}}
        report['steps'].append(row);print(json.dumps(row),flush=True)
        if result.get('outcome_unknown') or result.get('close_outcome_unknown'):
            unknown=True;raise RuntimeError('Unknown completion; no further command sent')
        return result

    def require(result):
        assert result.get('success',True) and not result.get('error'),result
        assert result.get('saved',False) is False,result
        return result

    def codes(result):
        return {i['code'] for row in result['components']+result['dependencies'] for i in row['issues']}

    try:
        original=require(await call('project_before',main.get_project_info))
        guarded=list({*paths,Path(args.schlib),Path(args.pcblib),Path(original['project_path']),
                      *(Path(d['path']) for d in original['documents'])})
        report['disk_before']={str(p):disk_state(p) for p in guarded}
        source_paths=[str(args.schlib),str(args.pcblib)]
        baseline=require(await call('project_libraries',main.validate_library_components,source_paths,limit=500))
        assert baseline['returned_count']==50 and baseline['verification']['passed']
        report['original_library_validation_passed']=True
        led=require(await call('led_dependency',main.validate_library_components,source_paths,['LTST-C155KGJRKT']))
        assert led['valid'] is True and len(led['dependencies'])==1
        usb_roles=[dict(library_path=str(args.pcblib),footprint_name='GSWITCH_GT_USB_7010ASV',pad_name='SH'+str(i),role='shield') for i in range(1,5)]
        usb=require(await call('usb_roles',main.validate_library_components,source_paths,['GT-USB-7010ASV'],pad_roles=usb_roles))
        roles=usb['dependencies'][0]['pad_roles']
        assert {r['role'] for r in roles}=={'electrical','shield','npth'}
        assert sum(r['role']=='npth' for r in roles)==2 and sum(r['role']=='shield' for r in roles)==4
        report['m01_roles_passed']=True
        for i,path in enumerate(paths):
            require(await call('open_'+str(i),main.open_document,str(path)));opened.append(path)
        constants=require(await call('constants',main.get_pcb_api_constants,['eRectangular']))
        shape=constants['constants']['eRectangular']
        good='MCP_VALIDATION_GOOD';bad='MCP_VALIDATION_BAD';symbol='MCP_VALIDATION_SYMBOL'
        lines=['FPLIB|'+str(paths[1])]
        def pad(name,x,y,width=1,height=1,layer='Top Layer',hole=0,plated=0):
            mils=lambda mm:mm/.0254
            return '|'.join(map(str,['PAD',name,mils(x),mils(y),0,layer,plated,mils(hole),0,0,0,
                                     mils(width),mils(height),shape,0,0,0,0,0,0,0,0,2,0,2,mils(.05)]))
        lines+=['FOOTPRINT|'+good+'|Disposable validation fixture',pad('1',0,0),pad('2',2,0),pad('SH1',4,0),
                pad('MH1',6,0,0,0,'Multi-Layer',.8)]
        lines+=['FOOTPRINT|'+bad+'|Deliberate pad geometry errors',pad('1',0,0),pad('2',.9,0),
                pad('3',4,0,.01,.01),pad('MH1',.4,0,0,0,'Multi-Layer',.8)]
        spec=root/'footprints.txt';write_altium_text(spec,'\n'.join(lines)+'\n')
        made=require(await call('create_footprints',main.create_footprints_batch,str(spec)))
        assert made['created']==2 and not made.get('problems')
        require(await call('focus_symbol_library',main.open_document,str(paths[0])))
        params=dict(Value='Validation fixture',MF='MCP test fixture',MP='MCP-VALIDATION',HelpURL='https://www.altium.com/documentation',
                    Package='Test fixture',Type='Test',Instructions='DNP',System='Validation tests')
        # Two passive contacts on opposing sides, 100 mil lengths and grid.
        require(await call('create_symbol',main.create_schematic_symbol,symbol,
                           'Disposable library validation fixture',
                           ['1|A|eElectricPassive|eRotate180|-100|0|1|100','2|B|eElectricPassive|eRotate0|100|0|1|100'],
                           parameters=params))
        require(await call('set_designator',main.set_library_component_properties,str(paths[0]),symbol,'J?','standard'))
        require(await call('bind_footprint',main.set_library_component_data,str(paths[0]),symbol,
                           footprint_models=[dict(model_name=good,library_path=str(paths[1]),pin_map={'1':['1'],'2':['2']})]))
        roles=[dict(library_path=str(paths[1]),footprint_name=good,pad_name='SH1',role='shield')]
        both=[str(p) for p in paths]
        valid=require(await call('fixture_good',main.validate_library_components,both,[symbol],pad_roles=roles))
        assert valid['valid'] is True and valid['complete'] and len(valid['dependencies'])==1
        assert all(lib['was_modified'] and lib['is_modified'] for lib in valid['libraries'])
        wrong=require(await call('fixture_bad_geometry',main.validate_library_components,str(paths[1]),[bad]))
        assert wrong['valid'] is False and {'pad_clearance','pad_dimensions','npth_clearance','mask_clearance','paste_clearance'}<=codes(wrong)
        require(await call('remove_parameter',main.set_library_component_data,str(paths[0]),symbol,parameters={'MP':''}))
        require(await call('off_grid_pin',main.update_symbol_primitives,str(paths[0]),symbol,
                           [dict(target=dict(type='pin',pin_number='1',owner_part_id=1,display_mode=0),properties={'x':-150,'y':0})]))
        require(await call('close_paste',main.set_footprint_pad_settings,str(paths[1]),good,['1'],paste_expansion_mm=-.6))
        errors=require(await call('fixture_bad_metadata',main.validate_library_components,both,[symbol],pad_roles=roles))
        assert errors['valid'] is False and {'parameter_required','pin_off_grid','paste_aperture_closed'}<=codes(errors)
        require(await call('restore_parameter',main.set_library_component_data,str(paths[0]),symbol,parameters={'MP':params['MP']}))
        require(await call('restore_grid',main.update_symbol_primitives,str(paths[0]),symbol,
                           [dict(target=dict(type='pin',pin_number='1',owner_part_id=1,display_mode=0),properties={'x':-100,'y':0})]))
        require(await call('rule_masks',main.set_footprint_pad_settings,str(paths[1]),good,['1','2','SH1'],mode='rule'))
        deferred=require(await call('fixture_rules_deferred',main.validate_library_components,both,[symbol],pad_roles=roles))
        assert deferred['valid'] is None and {'mask_rule_unresolved','paste_rule_unresolved'}<=codes(deferred)
        explicit=require(await call('fixture_rules_explicit',main.validate_library_components,both,[symbol],
                                    rules={'rule_mask_expansion_mm':.05,'rule_paste_expansion_mm':0},pad_roles=roles))
        assert explicit['valid'] is True and explicit['complete']
        missing=require(await call('missing_name',main.validate_library_components,both,['MCP_MISSING']))
        assert missing['missing_names']==['MCP_MISSING'] and missing['valid'] is None
        report['fixtures_passed']=True;report['checks_passed']=True
    except Exception as exc:
        report.update(checks_passed=False,error=str(exc) or repr(exc),outcome_unknown=unknown)
    finally:
        if not unknown:
            try:
                for i,path in enumerate(list(opened)):
                    require(await call('discard_'+str(i),main.close_document,str(path),require_clean=False));opened.remove(path)
                if original:
                    require(await call('restore_project',main.select_project,original['project_path']))
                    active=next((d['path'] for d in original['documents'] if d['state']=='open'),None)
                    if active:
                        require(await call('restore_editor',main.open_document,active))
                    last=require(await call('project_after',main.get_project_info))
                    report['original_states_unchanged']=last==original
            except Exception as exc:
                report['cleanup_error']=str(exc) or repr(exc)
        report['disk_after']={str(p):disk_state(p) for p in guarded}
        report['disk_unchanged']=report.get('disk_before')==report['disk_after']
        report['scratch_closed']=not opened
        (root/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=True),encoding='ascii')
        print('REPORT '+str(root/'report.json'),flush=True)
    assert all(report.get(k) for k in ('checks_passed','disk_unchanged','original_states_unchanged','scratch_closed')),report.get('error',report)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exercise',action='store_true')
    parser.add_argument('--schlib',type=Path,default=Path(r'C:\workspace_altium\SQ-CARD\SQ-CARD.SchLib'))
    parser.add_argument('--pcblib',type=Path,default=Path(r'C:\workspace_altium\SQ-CARD\SQ-CARD.PcbLib'))
    asyncio.run(run(parser.parse_args()))
