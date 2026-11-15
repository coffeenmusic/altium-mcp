"""Opt-in no-save checks for the power sequencer report's script replacements."""
import argparse
import asyncio
import hashlib
import json
import logging
from pathlib import Path, PureWindowsPath
import shutil
import sys
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'server'))
from test_footprint_editor_live import disk_state, editor_windows


async def run(args):
    root = REPO / '.altium-mcp' / ('schematic_document_' + time.strftime('%Y%m%d_%H%M%S'))
    root.mkdir(parents=True)
    report = {'root':str(root), 'documents_saved':False, 'projects_saved':False, 'steps':[]}
    if not args.exercise:
        print('Pass --exercise to edit only unique scratch documents; no saves are issued.')
        return
    import main
    from altium_files import write_altium_text
    logging.getLogger().setLevel(logging.WARNING)
    project = str(Path(args.project).resolve())
    sheet = str(Path(args.sheet).resolve())
    source = str(Path(args.source).resolve())
    scratch = root / 'Unsaved_\u03a9_\u0416.SchDoc'
    private_project = root / 'Scratch.PrjPcb'
    private_source = root / 'SavedSource.SchDoc'
    unknown = False
    original = None
    opened_scratch = False
    opened_project = False

    def record(label, data):
        (root / (label + '.json')).write_text(json.dumps(data, indent=2, ensure_ascii=True), encoding='ascii')

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
            raise RuntimeError('Altium did not become responsive; no further command sent')
        result = json.loads(await function(None, *values, **options))
        record(label, result)
        report['steps'].append({'label':label, **{k:result.get(k) for k in
            ('success','error','saved','project_saved','applied_count','cloned_count','complete','pin_count')}})
        print(json.dumps(report['steps'][-1]), flush=True)
        if result.get('outcome_unknown') or result.get('clone_outcome_unknown') or result.get('close_outcome_unknown'):
            unknown = True
            raise RuntimeError('Unknown executor completion; no further Altium commands sent')
        return result

    def require(value):
        assert value.get('success', True) and not value.get('error'), value
        assert value.get('saved', False) is False and value.get('project_saved', False) is False, value
        return value

    try:
        require(await call('select_original', main.select_project, project))
        original = require(await call('original_before', main.get_project_info))
        originals = [Path(project)] + [Path(d['path']) for d in original['documents']]
        report['disk_before'] = {str(p):disk_state(p) for p in originals}
        record('disk_before', report['disk_before'])
        baseline = require(await call('draft_before', main.edit_schematic, sheet, []))
        connectivity = require(await call('compiled_connectivity', main.get_schematic_connectivity, sheet, compiled=True))
        assert connectivity['complete'] and connectivity['pin_count'] == 79
        assert all(p.get('geometry_matches') for p in connectivity['pins'])
        editor = {(o['designator'],p['designator']):(p['x'],p['y'])
                  for o in baseline['objects'] if o['type']=='component' for p in o['pins']}
        for pin in connectivity['pins']:
            x,y = editor[(pin['logical_designator'], pin['pin_number'])]
            assert abs(x-pin['x']) < .01 and abs(y-pin['y']) < .01
        report['editor_compiler_units_match'] = True
        require(await call('create_unsaved', main.create_document, str(scratch)))
        opened_scratch = True
        require(await call('read_unsaved', main.edit_schematic, str(scratch), []))
        require(await call('set_A3', main.set_schematic_sheet_properties, str(scratch), 'A3'))
        repeated = require(await call('set_A3_again', main.set_schematic_sheet_properties, str(scratch), 'A3'))
        assert repeated['changed'] is False
        require(await call('clone_prototypes', main.clone_schematic_components, source, str(scratch), ['R38','C55']))
        changed = require(await call('detach_and_rename', main.edit_schematic, str(scratch), [
            {'op':'update','type':'component','target':{'designator':'R38'},
             'properties':{'designator':'R501','x':1200,'y':1000,'design_item_id':''}},
            {'op':'update','type':'component','target':{'designator':'C55'},
             'properties':{'designator':'C501','x':1600,'y':1000,'design_item_id':''}}], return_mode='changed'))
        assert len(changed['objects']) == 2 and all(o['design_item_id']=='' for o in changed['objects'])
        summary = require(await call('compact_summary', main.edit_schematic, str(scratch), [], return_mode='summary'))
        assert 'objects' not in summary and summary['object_counts']['component']==2
        require(await call('zoom_unsaved', main.zoom_schematic, str(scratch)))
        require(await call('zoom_unsaved_page', main.zoom_schematic, str(scratch), mode='sheet'))
        require(await call('zoom_original', main.zoom_schematic, sheet))
        image_result = await main.get_screenshot(None, 'sch')
        assert isinstance(image_result, list), image_result
        image_metadata = json.loads(image_result[0])
        assert image_metadata['success'] and image_metadata['captured_kind']=='SCH'
        (root/'schematic.png').write_bytes(image_result[1].data)
        record('screenshot', image_metadata)
        report['native_screenshot'] = True
        original_dry = require(await call('save_original_dry_run', main.save_project, project, dry_run=True))
        assert original_dry['dry_run'] and original_dry['source_files_unchanged']
        shutil.copy2(sheet, private_source)
        write_altium_text(private_project, '[Design]\nVersion=1.0\n[Document1]\nDocumentPath=SavedSource.SchDoc\n')
        report['scratch_disk_before'] = {str(p):disk_state(p) for p in (private_source, private_project)}
        require(await call('open_private_project', main.open_project, str(private_project)))
        opened_project = True
        require(await call('save_private_dry_run', main.save_project, str(private_project), dry_run=True))
        require(await call('add_unsaved_to_private', main.add_document_to_project,
                           project_path=str(private_project), document_path=str(scratch)))
        refused = await call('refuse_unsaved_source', main.save_project, str(private_project), dry_run=True)
        assert refused['success'] is False and refused['project_saved'] is False
        report['save_project_live_scope'] = 'dry_run only; no real project save authorized'
        after_draft = require(await call('draft_after', main.edit_schematic, sheet, []))
        assert after_draft['objects'] == baseline['objects']
        report['original_draft_unchanged'] = True
        report['checks_passed'] = True
    except Exception as exc:
        report.update(error=str(exc), checks_passed=False, executor_outcome_unknown=unknown)
    finally:
        if not unknown:
            if opened_project:
                require(await call('discard_private_project', main.close_document, str(private_project), require_clean=False))
            if opened_scratch:
                require(await call('discard_unsaved', main.close_document, str(scratch), require_clean=False))
            if original:
                require(await call('restore_project', main.select_project, project))
                current = require(await call('current_states', main.get_project_info))
                states = {PureWindowsPath(d['path']):d['state'] for d in current['documents']}
                for index,doc in enumerate(original['documents']):
                    if doc['state']=='closed' and states.get(PureWindowsPath(doc['path']),'closed')!='closed':
                        require(await call('restore_closed_'+str(index), main.close_document, doc['path'], require_clean=True))
                active = next((d['path'] for d in original['documents'] if d['state']!='closed'), None)
                if active:
                    require(await call('restore_editor', main.open_document, active))
                final = require(await call('original_after', main.get_project_info))
                report['original_states_unchanged'] = final == original
        if original:
            report['disk_after'] = {str(p):disk_state(p) for p in originals}
            report['original_disk_unchanged'] = report['disk_before']==report['disk_after']
        if report.get('scratch_disk_before'):
            report['scratch_disk_unchanged'] = all(disk_state(Path(p))==state for p,state in report['scratch_disk_before'].items())
        report['unsaved_sheet_not_written'] = not scratch.exists()
        record('report', report)
        print('REPORT '+str(root/'report.json'), flush=True)
    assert report['checks_passed'] and report['original_states_unchanged'] and report['original_disk_unchanged'], report.get('error', report)
    assert report['scratch_disk_unchanged'] and report['unsaved_sheet_not_written']


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--sheet', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--exercise', action='store_true')
    asyncio.run(run(parser.parse_args()))
