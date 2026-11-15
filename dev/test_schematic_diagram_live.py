"""Opt-in diagram/hierarchy checks on a disposable unsaved SchDoc; no design saves."""

import argparse
import asyncio
import json
import logging
from pathlib import Path, PureWindowsPath
import sys
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'server'))
from test_footprint_editor_live import disk_state, editor_windows


async def run(args):
    if not args.exercise:
        print('Pass --exercise to run reversible live checks on an unsaved scratch sheet.')
        return
    import main
    logging.getLogger().setLevel(logging.WARNING)
    root = REPO / '.altium-mcp' / ('schematic_diagram_' + time.strftime('%Y%m%d_%H%M%S'))
    root.mkdir(parents=True)
    sheet = root / 'Diagram_\u03a9_\u0416.SchDoc'
    report = {'root':str(root), 'saved':False, 'steps':[]}
    original = None
    previous = None
    originals = []
    opened = False
    unknown = False

    def record(label, result):
        (root / (label + '.json')).write_text(json.dumps(result, indent=2, ensure_ascii=True), encoding='ascii')

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
            raise RuntimeError('Altium not responsive; no further commands sent')
        result = json.loads(await function(None, *values, **options))
        record(label, result)
        report['steps'].append({'label':label, **{k:result.get(k) for k in ('success','error','saved','applied_count')}})
        print(json.dumps(report['steps'][-1], ensure_ascii=True), flush=True)
        if any(result.get(k) for k in ('outcome_unknown','close_outcome_unknown')):
            unknown = True
            raise RuntimeError('Unknown native completion; no further commands sent')
        return result

    def require(result):
        assert result.get('success', True) and not result.get('error'), result
        assert result.get('saved', False) is False, result
        return result

    try:
        previous = require(await call('previous_project', main.get_project_info))
        require(await call('select_test_project', main.select_project, str(Path(args.project).resolve())))
        original = require(await call('project_before', main.get_project_info))
        originals = [Path(original['project_path'])] + [Path(d['path']) for d in original['documents']]
        report['disk_before'] = {str(p):disk_state(p) for p in originals}
        record('disk_before', report['disk_before'])
        assert original['project_path'].lower() == str(Path(args.project).resolve()).lower(), 'active project changed before test'
        require(await call('create_unsaved', main.create_document, str(sheet)))
        opened = True
        require(await call('size', main.set_schematic_sheet_properties, str(sheet), 'A3'))
        props = dict(designator='Control_\u03a9_\u0416', filename='Control.SchDoc', x=1000.25,y=1400.5,width=1200.5,height=900.25,
                     entries=[dict(name='EN',side='left',offset=200.25,io_type='input'),
                              dict(name='READY',side='right',offset=300,io_type='output'),
                              dict(name='BUS',side='top',offset=400,io_type='bidirectional'),
                              dict(name='GND',side='bottom',offset=500)])
        added = require(await call('add_symbol', main.edit_sheet_symbols, str(sheet), [dict(op='add',properties=props)]))
        uid = added['results'][0]['unique_id']
        symbol = next(o for o in added['objects'] if o['unique_id']==uid)
        assert len(symbol['entries'])==4
        report['entry_geometry'] = symbol['entries']
        missing = await call('missing_target', main.edit_sheet_symbols, str(sheet),
                             [dict(op='add',properties={**props,'designator':'MustNotAppear'}),
                              dict(op='delete',unique_id='MISSING')])
        assert missing['success'] is False and missing['applied_count']==0 and not missing['may_have_partial_changes']
        assert not any(o.get('designator')=='MustNotAppear' for o in missing['objects'])
        duplicate = await call('duplicate_name', main.edit_sheet_symbols, str(sheet), [dict(op='add',properties=props)])
        assert duplicate['success'] is False and duplicate['applied_count']==0
        resize = await call('refuse_stranded_entry', main.edit_sheet_symbols, str(sheet),
                            [dict(op='update',unique_id=uid,properties={'height':100})])
        assert resize['success'] is False and resize['applied_count']==0
        old_entries = {e['unique_id'] for e in symbol['entries']}
        require(await call('resize_symbol', main.edit_sheet_symbols, str(sheet),
                           [dict(op='update',unique_id=uid,properties={'width':1400,'height':1000})]))
        changed = require(await call('move_and_rename', main.edit_sheet_symbols, str(sheet),
                                    [dict(op='update',unique_id=uid,properties={'x':1100,'y':1400,'designator':'Control'})]))
        symbol = next(o for o in changed['objects'] if o['unique_id']==uid)
        assert {e['unique_id'] for e in symbol['entries']}==old_entries
        replaced = require(await call('replace_entries', main.edit_sheet_symbols, str(sheet),
                                     [dict(op='update',unique_id=uid,properties={'entries':props['entries'][:2]})]))
        symbol = next(o for o in replaced['objects'] if o['unique_id']==uid)
        assert len(symbol['entries'])==2 and not old_entries.intersection(e['unique_id'] for e in symbol['entries'])
        require(await call('clear_entries', main.edit_sheet_symbols, str(sheet),
                           [dict(op='update',unique_id=uid,properties={'entries':[]})]))
        require(await call('restore_entries', main.edit_sheet_symbols, str(sheet),
                           [dict(op='update',unique_id=uid,properties={'entries':props['entries']})]))
        second = require(await call('second_symbol', main.edit_sheet_symbols, str(sheet),
                                    [dict(op='add',properties={**props,'designator':'Temporary','x':3000,'entries':[]})]))
        delete_uid = second['results'][0]['unique_id']
        require(await call('delete_symbol', main.edit_sheet_symbols, str(sheet), [dict(op='delete',unique_id=delete_uid)]))
        blocks = [dict(id='power',text='Power input',x=1000,y=3400,width=1200,height=600),
                  dict(id='control',text='Sequencer',x=3200,y=3400,width=1200,height=600),
                  dict(id='load',text='Payload',x=5400,y=3400,width=1200,height=600)]
        links = [{'from':'power','to':'control','text':'VIN'}, {'from':'control','to':'load','text':'ENABLE'},
                 {'from':'load','to':'control','from_side':'bottom','to_side':'bottom','text':'READY','points':
                  [[6000,3400],[6000,3000],[3800,3000],[3800,3400]]}]
        diagram = require(await call('draw_blocks', main.draw_block_diagram, str(sheet), blocks, links,
                                     [{'x':1000,'y':4400,'text':'Block diagram / live no-save check'},
                                      {'x':1000,'y':700,'text':'Native Sheet Symbol with four Sheet Entries'}]))
        assert {o['type'] for o in diagram['objects']} <= {'sheet_symbol','rectangle','polyline','text'}
        assert sum(o['type']=='rectangle' for o in diagram['objects'])==3
        report['diagram_primitive_count'] = diagram['applied_count']
        # Existing electrical editor proves graphic links did not become wires.
        electrical = require(await call('no_electrical_wires', main.edit_schematic, str(sheet), []))
        assert not any(o['type'] in {'wire','port','net_label','component'} for o in electrical['objects'])
        before = require(await call('read_before_invalid', main.edit_sheet_symbols, str(sheet), []))
        invalid = await call('invalid_link', main.draw_block_diagram, str(sheet), [], [{'from':'missing','to':'other'}], [])
        assert invalid['success'] is False and invalid['may_have_partial_changes'] is False
        after = require(await call('read_after_invalid', main.edit_sheet_symbols, str(sheet), []))
        assert before['objects']==after['objects']
        require(await call('zoom', main.zoom_schematic, str(sheet)))
        image_result = await main.get_screenshot(None, 'sch')
        assert isinstance(image_result, list), image_result
        metadata = json.loads(image_result[0])
        assert metadata['success'] and metadata['captured_kind']=='SCH'
        (root / 'diagram.png').write_bytes(image_result[1].data)
        record('screenshot',metadata)
        report['checks_passed'] = True
    except Exception as exc:
        report.update(checks_passed=False,error=str(exc),outcome_unknown=unknown)
    finally:
        if opened and not unknown:
            try:
                require(await call('discard_scratch',main.close_document,str(sheet),require_clean=False))
                opened=False
            except Exception as exc:
                report['cleanup_error']=str(exc)
        if original and not unknown:
            require(await call('restore_project',main.select_project,original['project_path']))
            active = next((d['path'] for d in original['documents'] if d['state']=='open' and d['kind']=='SCH'),None)
            if active:
                require(await call('restore_editor',main.open_document,active))
            final = require(await call('project_after',main.get_project_info))
            report['original_states_unchanged'] = original==final
            if previous and PureWindowsPath(previous['project_path']).is_absolute() and previous['project_path'] != original['project_path']:
                require(await call('restore_previous_project',main.select_project,previous['project_path']))
        if original:
            report['disk_after'] = {str(p):disk_state(p) for p in originals}
            report['original_disk_unchanged'] = report['disk_before']==report['disk_after']
        report['unsaved_sheet_not_written'] = not sheet.exists()
        report['scratch_closed'] = not opened
        record('report',report)
        print('REPORT '+str(root/'report.json'),flush=True)
    assert report.get('checks_passed') and report.get('original_states_unchanged') and report.get('original_disk_unchanged'),report.get('error',report)
    assert report['unsaved_sheet_not_written'] and report['scratch_closed']


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',required=True)
    parser.add_argument('--exercise',action='store_true')
    asyncio.run(run(parser.parse_args()))
