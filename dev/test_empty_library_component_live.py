"""Opt-in empty library cleanup on disposable unsaved libraries; never saves."""

import argparse
import asyncio
import json
import logging
from pathlib import Path
import sys
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/'server'))
from test_footprint_editor_live import disk_state, editor_windows


async def run(args):
    if not args.exercise:
        print('Pass --exercise to test disposable unsaved libraries only.')
        return
    import main
    import symbol_metadata
    from altium_files import write_altium_text
    logging.getLogger().setLevel(logging.WARNING)
    root = REPO/'.altium-mcp'/('empty_library_'+time.strftime('%Y%m%d_%H%M%S'))
    root.mkdir(parents=True)
    report={'saved':False,'steps':[]}
    opened=[]; guarded=[]; original=None; unknown=False

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
            raise RuntimeError('Altium is not responsive; no further command sent')
        result=json.loads(await function(None,*values,**options))
        (root/(label+'.json')).write_text(json.dumps(result,indent=2,ensure_ascii=True),encoding='ascii')
        row={'label':label,**{k:result.get(k) for k in ('success','removed','removable','error')}}
        report['steps'].append(row)
        print(json.dumps(row),flush=True)
        if result.get('outcome_unknown') or result.get('close_outcome_unknown'):
            unknown=True
            raise RuntimeError('Unknown command completion; no further command sent')
        return result

    def require(result):
        assert result.get('success',True) and not result.get('error'),result
        assert not result.get('saved',False),result
        return result

    async def spec_call(label,records,pcb=False):
        # Batch path records require saved files. Focus the exact named unsaved
        # buffer through open_document, then use the documented current-library mode.
        library=records[0].split('|',1)[1]
        require(await call('focus_'+label,main.open_document,library))
        path=root/(label+'.txt')
        write_altium_text(path,'\n'.join(records[1:])+'\n')
        return require(await call(label,main.create_footprints_batch if pcb else main.create_symbols_batch,str(path)))

    try:
        original=require(await call('project_before',main.get_project_info))
        guarded=list({Path(original['project_path']),*(Path(d['path']) for d in original['documents'])})
        report['disk_before']={str(p):disk_state(p) for p in guarded}
        scratch={kind:root/('Empty_\u03a9_\u0416.'+kind) for kind in ('SchLib','PcbLib')}
        for kind,path in scratch.items():
            require(await call('create_'+kind,main.create_document,str(path)))
            opened.append(path)
            name='Component_1' if kind=='SchLib' else 'PCBComponent_1'
            baseline=require(await call('inspect_'+kind,main.remove_empty_library_component,str(path),name,True))
            assert baseline['found'] and baseline['assessment']['empty'] and not baseline['removable'],baseline
            refused=await call('last_'+kind,main.remove_empty_library_component,str(path),name)
            assert refused['success'] is False and not refused['removed'] and not refused['may_have_partial_changes']
        if args.probe:
            report['checks_passed']=True
        else:
            require(await call('placement_rules',main.get_symbol_placement_rules))
            metadata=require(await call('source_metadata',main.set_library_component_data,args.library,args.symbol))['after']
            parameters={k:metadata['parameters'].get(k,metadata['comment']) for k in symbol_metadata.REQUIRED_PARAMETERS}
            sch=scratch['SchLib']; pcb=scratch['PcbLib']; kept='MCP_KEPT'
            sch_records=['LIBRARY|'+str(sch),'SYMBOL|'+kept+'|'+metadata['description']+'|1']
            sch_records += ['PARAM|'+k+'|'+v for k,v in parameters.items()]
            sch_records += ['PIN|1|A|eElectricPassive|eRotate180|0|0|1|100|1|1',
                            'GRAPHIC|line|1|1|0|0|100|0']
            require(await spec_call('create_kept_symbol',sch_records))
            # Hidden metadata alone must protect an otherwise blank default symbol.
            await spec_call('set_hidden_parameter',['LIBRARY|'+str(sch),'EDITSYMBOL|Component_1','SYMPARAM|Keep|Important data|0'])
            refused=await call('hidden_parameter_refusal',main.remove_empty_library_component,str(sch),'Component_1')
            assert refused['success'] is False and not refused['removed'] and not refused['assessment']['empty']
            # A separate fresh SchLib supplies the genuinely empty removal case.
            empty_sch=root/'Positive.SchLib'
            require(await call('create_positive_sch',main.create_document,str(empty_sch))); opened.append(empty_sch)
            positive_records=list(sch_records);positive_records[0]='LIBRARY|'+str(empty_sch)
            await spec_call('positive_kept_symbol',positive_records)
            await spec_call('create_kept_footprint',['FPLIB|'+str(pcb),'FOOTPRINT|'+kept+'|Temporary retained footprint',
                            'TRACK|0|0|100|0|10|Top Overlay'],True)
            # A zero-primitive footprint with a description is also nonempty.
            await spec_call('create_metadata_footprint',['FPLIB|'+str(pcb),'FOOTPRINT|MCP_METADATA|Keep this description'],True)
            refused=await call('pcb_metadata_refusal',main.remove_empty_library_component,str(pcb),'MCP_METADATA')
            assert refused['success'] is False and not refused['removed'] and refused['assessment']['child_count']==0
            for kind,path,name,getter in (('SchLib',empty_sch,'Component_1',main.get_symbol_primitives),
                                         ('PcbLib',pcb,'PCBComponent_1',main.get_footprint_primitives)):
                # These legacy getters require a saved explicit path; their
                # current-library mode can inspect the exact focused unsaved buffer.
                require(await call('focus_kept_before_'+kind,main.open_document,str(path)))
                before=require(await call('kept_before_'+kind,getter,'',kept))
                assert before['library_name'].casefold()==path.name.casefold(),before
                refused=await call('content_refusal_'+kind,main.remove_empty_library_component,str(path),kept)
                assert refused['success'] is False and not refused['removed']
                dry=require(await call('dry_'+kind,main.remove_empty_library_component,str(path),name,True))
                assert dry['removable'] and not dry['removed']
                applied=require(await call('remove_'+kind,main.remove_empty_library_component,str(path),name))
                assert applied['removed'] and applied['verification']['passed']
                repeated=require(await call('repeat_'+kind,main.remove_empty_library_component,str(path),name))
                assert not repeated['found'] and not repeated['removed']
                require(await call('focus_kept_after_'+kind,main.open_document,str(path)))
                after=require(await call('kept_after_'+kind,getter,'',kept))
                assert before==after,'retained geometry/metadata changed'
            report['checks_passed']=True
    except Exception as exc:
        report.update(checks_passed=False,error=str(exc),outcome_unknown=unknown)
    finally:
        if not unknown:
            try:
                for index,path in enumerate(list(opened)):
                    require(await call('discard_'+str(index),main.close_document,str(path),require_clean=False))
                    opened.remove(path)
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
        report['scratch_files_absent']=not any(root.glob('*.SchLib')) and not any(root.glob('*.PcbLib'))
        report['scratch_closed']=not opened
        (root/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=True),encoding='ascii')
        print('REPORT '+str(root/'report.json'),flush=True)
    assert all(report.get(k) for k in ('checks_passed','disk_unchanged','original_states_unchanged','scratch_files_absent','scratch_closed')),report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exercise',action='store_true')
    parser.add_argument('--probe',action='store_true',help='Only inspect defaults and last-member refusal')
    parser.add_argument('--library',default=r'C:\workspace_altium\SQ-CARD\SQ-CARD.SchLib')
    parser.add_argument('--symbol',default='LTST-C155KGJRKT')
    asyncio.run(run(parser.parse_args()))
