"""Opt-in mixed-library batch reads, comparisons and dirty-buffer checks; no saves."""

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
        print('Pass --exercise to inspect disposable library copies without saving.')
        return
    import main
    import library_components_data as reader
    from library_view import canonical
    root=REPO/'.altium-mcp'/('library_components_'+time.strftime('%Y%m%d_%H%M%S'))
    root.mkdir(parents=True)
    logging.getLogger().setLevel(logging.WARNING)
    paths=[root/'Batch_\u03a9.SchLib',root/'Batch_\u0416.PcbLib']
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
        row={'label':label,**{k:result.get(k) for k in ('success','returned_count','matched_count','error')}}
        report['steps'].append(row);print(json.dumps(row),flush=True)
        if result.get('outcome_unknown') or result.get('close_outcome_unknown'):
            unknown=True;raise RuntimeError('Unknown completion; no further command sent')
        return result

    def require(value):
        assert value.get('success',True) and not value.get('error'),value
        assert value.get('saved',False) is False,value
        return value

    def key(row):
        return row['library_kind'],row['component_name'].casefold()

    try:
        original=require(await call('project_before',main.get_project_info))
        guarded=list({*paths,Path(args.schlib),Path(args.pcblib),Path(original['project_path']),
                      *(Path(d['path']) for d in original['documents'])})
        report['disk_before']={str(p):disk_state(p) for p in guarded}
        for i,path in enumerate(paths):
            require(await call('open_'+str(i),main.open_document,str(path)));opened.append(path)
        path_args=[str(p) for p in paths]
        baseline=require(await call('baseline',main.get_library_components_data,path_args,limit=500))
        assert baseline['complete'] and baseline['view_restored']
        by_id={row['parameters'].get('Component ID'):row for row in baseline['components'] if row['library_kind']=='SCHLIB'}
        symbols=[by_id[n] for n in ('E08','M01','M03','M04','M06')]
        footprints={m['model_name'] for row in symbols for m in row['models'] if m['model_type'].upper()=='PCBLIB'}
        names=list(dict.fromkeys([r['component_name'] for r in symbols]+sorted(footprints)))
        detailed=require(await call('details',main.get_library_components_data,path_args,names,list(reader.FIELDS),limit=500))
        assert detailed['complete'] and not detailed['missing_names']
        rows={key(row):row for row in detailed['components']}
        for i,symbol in enumerate(symbols):
            name=symbol['component_name'];row=rows['SCHLIB',name.casefold()]
            legacy=require(await call('symbol_'+str(i),main.set_library_component_data,str(paths[0]),name))['after']
            assert row['parameters']==legacy['parameters']
            for field in ('designator_template','component_kind','comment','description','part_count'):
                assert row['properties'][field]==legacy[field]
            assert canonical(row['models'])==canonical(legacy['models'])
            dump=require(await call('pins_'+str(i),main.get_symbol_primitives,str(paths[0]),name))
            expected=[p for p in dump['primitives'] if p['type']=='pin']
            project=lambda pin:{k:pin[k] for k in ('pin_number','pin_name','owner_part_id','orientation','electrical','x','y','length')}
            normalize=lambda values:sorted((project(p) for p in values),key=lambda p:json.dumps(p,sort_keys=True))
            assert normalize(row['pins'])==normalize(expected)
        for i,name in enumerate(sorted(footprints)):
            row=rows['PCBLIB',name.casefold()]
            dump=require(await call('footprint_'+str(i),main.get_footprint_primitives,str(paths[1]),name))
            assert abs(row['properties']['height_mm']-dump['height_mm'])<.00001
            expected=[p for p in dump['primitives'] if p['type']=='pad']
            project=lambda pad:{k:pad[k] for k in ('name','layer','is_testpoint_top','is_testpoint_bottom')}
            normalize=lambda values:sorted((project(p) for p in values),key=lambda p:json.dumps(p,sort_keys=True))
            assert normalize(row['pads'])==normalize(expected)
        report['legacy_comparisons_passed']=True
        collected=[];offset=0
        while True:
            page=require(await call('page_'+str(offset),main.get_library_components_data,path_args,fields=['properties'],offset=offset,limit=19))
            collected.extend(key(row) for row in page['components'])
            if not page['has_more']:
                break
            offset=page['next_offset']
        assert collected==[key(row) for row in baseline['components']]
        report['pagination_passed']=True
        missing=require(await call('missing_name',main.get_library_components_data,path_args,[symbols[0]['component_name'],'MCP_MISSING']))
        assert missing['missing_names']==['MCP_MISSING'] and not missing['complete']
        partial=await call('missing_library',main.get_library_components_data,[str(paths[0]),str(root/'Missing.PcbLib')],fields=['properties'],limit=500)
        assert partial['success'] is False and partial['components'] and partial['library_errors']
        # Edits are only fixtures in copied buffers; the batch getter must see them.
        led=symbols[0]['component_name']
        require(await call('dirty_symbol',main.set_library_component_properties,str(paths[0]),led,'Z?','standard_no_bom'))
        require(await call('dirty_parameter',main.set_library_component_data,str(paths[0]),led,parameters={'Batch Read Probe':'\u03a9\u0416'}))
        usb_symbol=by_id['M01'];usb=next(m['model_name'] for m in usb_symbol['models'] if m['model_type'].upper()=='PCBLIB')
        require(await call('dirty_height',main.set_footprint_properties,str(paths[1]),usb,4.56))
        physical=rows['PCBLIB',usb.casefold()]['pads']
        pad=next(p['name'] for p in physical if p['layer'].replace('-',' ').casefold()=='multi layer')
        require(await call('dirty_testpoints',main.set_footprint_pad_properties,str(paths[1]),usb,[pad],True,True))
        dirty=require(await call('dirty_read',main.get_library_components_data,path_args,[led,usb],list(reader.FIELDS),limit=500))
        dirty_rows={key(row):row for row in dirty['components']}
        symbol=dirty_rows['SCHLIB',led.casefold()];footprint=dirty_rows['PCBLIB',usb.casefold()]
        assert symbol['properties']['designator_template']=='Z?' and symbol['properties']['component_kind']=='standard_no_bom'
        assert symbol['parameters']['Batch Read Probe']=='\u03a9\u0416'
        assert abs(footprint['properties']['height_mm']-4.56)<.00001
        flagged=next(p for p in footprint['pads'] if p['name']==pad)
        assert flagged['is_testpoint_top'] and flagged['is_testpoint_bottom']
        assert all(lib['was_modified'] and lib['is_modified'] for lib in dirty['libraries'])
        report['dirty_buffers_passed']=True
        new_paths=[root/'Unsaved.SchLib',root/'Unsaved.PcbLib']
        for i,path in enumerate(new_paths):
            require(await call('create_unsaved_'+str(i),main.create_document,str(path)));opened.append(path)
        unsaved=require(await call('unsaved_read',main.get_library_components_data,[str(p) for p in new_paths],fields=list(reader.FIELDS)))
        assert unsaved['returned_count']==2 and not any(p.exists() for p in new_paths)
        assert {row['component_name'] for row in unsaved['components']}=={'Component_1','PCBCOMPONENT_1'} or \
               {row['component_name'].casefold() for row in unsaved['components']}=={'component_1','pcbcomponent_1'}
        report['unsaved_passed']=True;report['checks_passed']=True
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
                report['cleanup_error']=str(exc)
        report['disk_after']={str(p):disk_state(p) for p in guarded}
        report['disk_unchanged']=report.get('disk_before')==report['disk_after']
        report['scratch_closed']=not opened
        (root/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=True),encoding='ascii')
        print('REPORT '+str(root/'report.json'),flush=True)
    assert all(report.get(k) for k in ('checks_passed','disk_unchanged','original_states_unchanged','scratch_closed')),report.get('error',report)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exercise',action='store_true')
    parser.add_argument('--schlib',default=r'C:\workspace_altium\SQ-CARD\SQ-CARD.SchLib')
    parser.add_argument('--pcblib',default=r'C:\workspace_altium\SQ-CARD\SQ-CARD.PcbLib')
    asyncio.run(run(parser.parse_args()))
