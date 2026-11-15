"""Opt-in no-save property checks on a disposable copy of an existing SchLib."""

import argparse
import asyncio
import json
import logging
from pathlib import Path
import shutil
import sys
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'server'))
from test_footprint_editor_live import disk_state, editor_windows


async def run(args):
    if not args.exercise:
        print('Pass --exercise to edit only a disposable library copy without saving.')
        return
    import main
    import library_component_properties as properties
    logging.getLogger().setLevel(logging.WARNING)
    root = REPO / '.altium-mcp' / ('library_properties_' + time.strftime('%Y%m%d_%H%M%S'))
    root.mkdir(parents=True)
    source = Path(args.library).resolve()
    scratch = root / 'Properties_\u03a9_\u0416.SchLib'
    shutil.copy2(source, scratch)
    report = {'saved':False, 'source':str(source), 'scratch':str(scratch), 'steps':[]}
    original = None
    opened = False
    unknown = False
    guarded = []

    def record(label, value):
        (root / (label + '.json')).write_text(json.dumps(value, indent=2, ensure_ascii=True),encoding='ascii')

    async def call(label, function, *values, **options):
        nonlocal unknown
        stable = 0
        for _ in range(40):
            windows = editor_windows()
            stable = stable + 1 if windows and all(w['responding'] for w in windows) else 0
            if stable >= 3:
                break
            await asyncio.sleep(.5)
        else:
            unknown = True
            raise RuntimeError('Altium not responsive; no more commands sent')
        result = json.loads(await function(None,*values,**options))
        record(label,result)
        row = {'label':label, **{k:result.get(k) for k in ('success','changed','error','saved')}}
        report['steps'].append(row)
        print(json.dumps(row,ensure_ascii=True),flush=True)
        if result.get('outcome_unknown') or result.get('close_outcome_unknown'):
            unknown = True
            raise RuntimeError('Unknown executor completion; no more commands sent')
        return result

    def require(result):
        assert result.get('success',True) and not result.get('error'),result
        assert result.get('saved',False) is False,result
        return result

    try:
        original = require(await call('project_before',main.get_project_info))
        guarded = list({source,scratch,Path(original['project_path']),*(Path(d['path']) for d in original['documents'])})
        report['disk_before'] = {str(p):disk_state(p) for p in guarded}
        record('disk_before',report['disk_before'])
        require(await call('open_scratch',main.open_document,str(scratch)))
        opened = True
        inventory = require(await call('inventory',main.get_symbol_primitives,str(scratch)))
        symbols = inventory['symbols']
        symbol = args.symbol or next(s['name'] for s in symbols if s.get('pins',0)>0)
        report['symbol'] = symbol
        untouched = next((s['name'] for s in symbols if s['name'] != symbol),None)
        baseline = require(await call('properties_before',main.set_library_component_properties,str(scratch),symbol))
        assert baseline['changed'] is False
        other_before = require(await call('other_before',main.set_library_component_properties,str(scratch),untouched)) if untouched else None
        geometry_before = require(await call('geometry_before',main.get_symbol_primitives,str(scratch),symbol))
        for i, kind in enumerate(properties.KINDS):
            template = ('J?','D?','C?')[i % 3]
            value = require(await call('kind_'+kind,main.set_library_component_properties,str(scratch),symbol,
                                       designator_template=template,component_kind=properties.KINDS[kind]))
            assert value['after']['component_kind']==kind and value['after']['designator_template']==template
            repeated = require(await call('repeat_'+kind,main.set_library_component_properties,str(scratch),symbol,
                                          designator_template=template,component_kind=kind))
            assert repeated['changed'] is False and repeated['was_modified']==repeated['is_modified']
        # Updating only one property preserves the other, including a dirty buffer.
        value = require(await call('designator_only',main.set_library_component_properties,str(scratch),symbol,designator_template='TP?'))
        assert value['after']['component_kind']=='standard_no_bom'
        value = require(await call('kind_only',main.set_library_component_properties,str(scratch),symbol,component_kind='Standard'))
        assert value['after']['designator_template']=='TP?'
        for i, edits in enumerate(({'designator_template':'J1'}, {'component_kind':5}, {'component_kind':'No BOM'})):
            refused = await call('invalid_'+str(i),main.set_library_component_properties,str(scratch),symbol,**edits)
            assert refused['success'] is False and refused['may_have_partial_changes'] is False
        refused = await call('missing_symbol',main.set_library_component_properties,str(scratch),'MCP_MISSING_SYMBOL',designator_template='J?')
        assert refused['success'] is False and refused['may_have_partial_changes'] is False
        geometry_after = require(await call('geometry_after',main.get_symbol_primitives,str(scratch),symbol))
        report['geometry_unchanged'] = geometry_before==geometry_after
        if untouched:
            other_after = require(await call('other_after',main.set_library_component_properties,str(scratch),untouched))
            report['other_symbol_unchanged'] = other_before['after']==other_after['after']
        report['checks_passed'] = report['geometry_unchanged'] and report.get('other_symbol_unchanged',True)
    except Exception as exc:
        report.update(checks_passed=False,error=str(exc),outcome_unknown=unknown)
    finally:
        if not unknown:
            try:
                if opened:
                    require(await call('discard_scratch',main.close_document,str(scratch),require_clean=False))
                    opened = False
                if original:
                    require(await call('restore_project',main.select_project,original['project_path']))
                    active = next((d['path'] for d in original['documents'] if d['state']=='open'),None)
                    if active:
                        require(await call('restore_editor',main.open_document,active))
                    final = require(await call('project_after',main.get_project_info))
                    report['original_states_unchanged'] = final==original
            except Exception as exc:
                report['cleanup_error'] = str(exc)
        report['disk_after'] = {str(p):disk_state(p) for p in guarded}
        report['disk_unchanged'] = report.get('disk_before')==report['disk_after']
        report['scratch_closed'] = not opened
        record('report',report)
        print('REPORT '+str(root/'report.json'),flush=True)
    assert report.get('checks_passed') and report.get('disk_unchanged') and report.get('original_states_unchanged') and report['scratch_closed'],report.get('error',report)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library',required=True)
    parser.add_argument('--symbol')
    parser.add_argument('--exercise',action='store_true')
    asyncio.run(run(parser.parse_args()))
