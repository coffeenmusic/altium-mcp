"""Verify native creation properties in one batch; discard scratch buffers."""

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
from test_footprint_editor_live import disk_state,editor_windows


async def run(args):
    if not args.exercise:
        print('Pass --exercise to test creation properties in a disposable library copy.')
        return
    import main
    from altium_files import write_altium_text
    from library_view import canonical
    root=REPO/'.altium-mcp'/('batch_symbol_properties_'+time.strftime('%Y%m%d_%H%M%S'));root.mkdir(parents=True)
    logging.getLogger().setLevel(logging.WARNING)
    scratch=root/'Batch_\u03a9.SchLib';shutil.copy2(args.schlib,scratch)
    report={'saved':False,'steps':[]};original=None;opened=False;unknown=False;guarded=[]

    async def call(label,func,*values,**options):
        nonlocal unknown
        stable=0
        for _ in range(40):
            windows=editor_windows();stable=stable+1 if windows and all(w['responding'] for w in windows) else 0
            if stable>=3: break
            await asyncio.sleep(.5)
        else:
            unknown=True;raise RuntimeError('Altium not responsive; no further command sent')
        result=json.loads(await func(None,*values,**options))
        (root/(label+'.json')).write_text(json.dumps(result,indent=2,ensure_ascii=True),encoding='ascii')
        step={'label':label,**{k:result.get(k) for k in ('success','created','saved','error')}}
        report['steps'].append(step);print(json.dumps(step),flush=True)
        if result.get('outcome_unknown') or result.get('close_outcome_unknown'):
            unknown=True;raise RuntimeError('Unknown completion; no further command sent')
        return result

    def require(result):
        assert result.get('success',True) and not result.get('error'),result
        assert result.get('saved',False) is False,result
        return result

    params=dict(Value='Batch test',MF='MCP test fixture',MP='MCP-BATCH',HelpURL='https://www.altium.com/documentation',
                Package='Test fixture',Type='Test',Instructions='DNP',System='Batch properties',Extra='\u03a9 \u0416 | = "test"')

    def block(name,suffix):
        return [f'SYMBOL|{name}|Disposable batch creation fixture{suffix}',
                *(f'PARAM|{k}|{v}' for k,v in params.items()),
                'PIN|1|A|eElectricPassive|eRotate180|-100|0|1|100',
                'PIN|2|B|eElectricPassive|eRotate0|100|0|1|100']

    try:
        original=require(await call('project_before',main.get_project_info))
        guarded=list({Path(args.schlib),scratch,Path(original['project_path']),*(Path(d['path']) for d in original['documents'])})
        report['disk_before']={str(p):disk_state(p) for p in guarded}
        require(await call('open_copy',main.open_document,str(scratch)));opened=True
        inventory=require(await call('inventory_before',main.get_symbol_primitives,str(scratch)))
        reference=inventory['symbols'][0]['name']
        ref_before=require(await call('reference_before',main.set_library_component_data,str(scratch),reference))['after']
        cases=[('STANDARD','|1|J?|standard','J?','standard'),
               ('NOBOM','|2|D?|Standard (No BOM)','D?','standard_no_bom'),
               ('MECHANICAL','|1|MH?|Mechanical','MH?','mechanical'),
               ('GRAPHICAL','|1|G?|graphical','G?','graphical'),
               ('NETBOM','|1|NT?|Net Tie (In BOM)','NT?','net_tie_bom'),
               ('NETNONE','|1|NT?|Net Tie','NT?','net_tie_no_bom'),
               ('LEGACY','','U?','standard'),
               ('DESIGNATOR','|1|C?','C?','standard'),
               ('KIND','|||standard_no_bom','U?','standard_no_bom')]
        lines=['LIBRARY|'+str(scratch)]
        for name,suffix,*_ in cases: lines.extend(block('MCP_BATCH_'+name,suffix))
        spec=root/'batch.txt';write_altium_text(spec,'\n'.join(lines)+'\n');spec_before=spec.read_bytes()
        created=require(await call('batch_create',main.create_symbols_batch,str(spec)))
        assert created['created']==len(cases) and created['verification']['passed'] and spec.read_bytes()==spec_before
        for item,(_,_,designator,kind) in zip(created['components'],cases):
            meta=item['metadata'];assert meta['designator_template']==designator and meta['component_kind']==kind
            assert 'Designator' not in meta['parameters'] and 'ComponentKind' not in meta['parameters']
            assert meta['parameters']['Extra']==params['Extra']
        names=['MCP_BATCH_'+c[0] for c in cases]
        read=require(await call('batch_readback',main.get_library_components_data,str(scratch),names,['properties','parameters','pins'],limit=100))
        by_name={r['component_name']:r for r in read['components']}
        for name,_,designator,kind in cases:
            row=by_name['MCP_BATCH_'+name]
            assert row['properties']['designator_template']==designator and row['properties']['component_kind']==kind
            assert row['pin_count']==2
        assert by_name['MCP_BATCH_NOBOM']['properties']['part_count']==2
        assert all(lib['was_modified'] and lib['is_modified'] for lib in read['libraries'])
        report['all_six_kinds_verified']=True;report['single_batch_creation_count']=len(cases)
        # The native function's old six-argument entry point must retain defaults.
        single=require(await call('single_compatibility',main.create_schematic_symbol,'MCP_BATCH_SINGLE_COMPAT',
                           'Disposable single compatibility fixture',['1|A|eElectricPassive|eRotate0|0|0'],params))
        assert single['metadata']['designator_template']=='U?' and single['metadata']['component_kind']=='standard'
        for label,suffix in [('bad_designator','|1|J1|standard'),('bad_kind','|1|J?|Unsupported')]:
            invalid=root/(label+'.txt')
            write_altium_text(invalid,'\n'.join(['LIBRARY|'+str(scratch),*block('MCP_MUST_NOT_EXIST','|1|J?|standard'),*block('MCP_MUST_NOT_EXIST_2',suffix)]))
            failure=await call(label,main.create_symbols_batch,str(invalid))
            assert failure['success'] is False and failure['may_have_partial_changes'] is False
        after=require(await call('inventory_after',main.get_symbol_primitives,str(scratch)))
        assert after['symbol_count']==inventory['symbol_count']+len(cases)+1
        assert not any(r['name'].startswith('MCP_MUST_NOT_EXIST') for r in after['symbols'])
        ref_after=require(await call('reference_after',main.set_library_component_data,str(scratch),reference))['after']
        assert canonical(ref_before)==canonical(ref_after)
        report['checks_passed']=True
    except Exception as exc:
        report.update(checks_passed=False,error=str(exc) or repr(exc),outcome_unknown=unknown)
    finally:
        if not unknown:
            try:
                if opened:
                    require(await call('discard_copy',main.close_document,str(scratch),require_clean=False));opened=False
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
    assert all(report.get(k) for k in ('checks_passed','disk_unchanged','original_states_unchanged','scratch_closed')),report.get('error',report)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--exercise',action='store_true')
    parser.add_argument('--schlib',type=Path,default=Path(r'C:\workspace_altium\SQ-CARD\SQ-CARD.SchLib'))
    asyncio.run(run(parser.parse_args()))
