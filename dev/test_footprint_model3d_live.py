"""Opt-in unsaved STEP edits on a disposable copy of the open project's library."""

import argparse
import asyncio
import json
import logging
from pathlib import Path
import shutil
import sys
import time
import zlib

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'server'))
from test_footprint_editor_live import disk_state, editor_windows


async def run(args):
    if not args.exercise:
        print('Pass --exercise to edit a disposable library buffer without saving.')
        return
    import main
    from altium_files import write_altium_text
    from pcblib_file import _open_root, _read_stream, _CHILD
    root = REPO / '.altium-mcp' / ('footprint_model3d_' + time.strftime('%Y%m%d_%H%M%S'))
    root.mkdir(parents=True)
    scratch = root / 'Models_\u03a9.PcbLib'
    shutil.copy2(args.pcblib, scratch)
    # Extract an existing embedded STEP using ordinary file I/O, without a CAD save.
    ole, temporary = _open_root(scratch)
    try:
        library = ole.OpenStorage('Library', None, _CHILD, None, 0)
        models = library.OpenStorage('Models', None, _CHILD, None, 0)
        step = root / 'Replacement_\u03a9.step'
        step.write_bytes(zlib.decompress(_read_stream(models, '0')))
    finally:
        models = library = ole = None
        if temporary: Path(temporary).unlink()
    logging.getLogger().setLevel(logging.WARNING)
    report = dict(saved=False, steps=[])
    opened = unknown = False
    original = None
    guarded = []

    async def call(label, func, *values, **options):
        nonlocal unknown
        stable = 0
        for _ in range(40):
            windows = editor_windows()
            stable = stable + 1 if windows and all(w['responding'] for w in windows) else 0
            if stable >= 3: break
            await asyncio.sleep(.5)
        else:
            unknown = True
            raise RuntimeError('Altium not responsive; no further command sent')
        value = json.loads(await func(None, *values, **options))
        (root / (label + '.json')).write_text(json.dumps(value, indent=2, ensure_ascii=True), encoding='ascii')
        row = dict(label=label, **{k:value.get(k) for k in ('success', 'changed', 'saved', 'error')})
        report['steps'].append(row)
        print(json.dumps(row), flush=True)
        if value.get('outcome_unknown') or value.get('close_outcome_unknown'):
            unknown = True
            raise RuntimeError('Unknown completion; no further command sent')
        return value

    def require(value):
        assert value.get('success', True) and not value.get('error'), value
        assert value.get('saved', False) is False, value
        return value

    async def edit(label, name=args.footprint, **options):
        return await call(label, main.edit_footprint_model3d, str(scratch), name, **options)

    def near(a, b):
        assert abs(a - b) < .000004, (a, b)

    try:
        original = require(await call('project_before', main.get_project_info))
        guarded = list({Path(args.pcblib), scratch, step, Path(original['project_path']),
                        *(Path(d['path']) for d in original['documents'])})
        report['disk_before'] = {str(p):disk_state(p) for p in guarded}
        require(await call('open_copy', main.open_document, str(scratch)))
        opened = True
        base = require(await edit('read_before'))
        assert len(base['before']) == 1 and not base['is_modified'] and not base['changed']
        body = base['before'][0]
        require(await edit('clean_noop', changes=dict(x_mm=body['x_mm'], y_mm=body['y_mm'], embed=body['embed'])))
        assert not json.loads((root/'clean_noop.json').read_text())['is_modified']
        for label, options in [('bad_index', dict(body_index=42, changes={'x_mm':1})),
                               ('bad_expected', dict(expected_model_file='Wrong.step', changes={'x_mm':1})),
                               ('partial_pose', dict(changes={'rotation_z':90})),
                               ('bad_embed', dict(changes={'embed':'true'})),
                               ('missing_source', dict(changes=dict(model_path=str(root/'Absent.step'), rotation_x=0, rotation_y=0, rotation_z=0, z_mm=0))),
                               ('orphan_link', dict(changes={'embed':False}))]:
            failure = await edit(label, **options)
            assert failure['success'] is False and failure['may_have_partial_changes'] is False, failure
        missing = await edit('missing_member', name='MCP_MISSING', changes={'x_mm':1})
        assert missing['success'] is False and not missing['may_have_partial_changes']
        assert not require(await edit('still_clean'))['is_modified']
        shifted = require(await edit('shift_x', changes={'x_mm':2.54}))
        near(shifted['after'][0]['x_mm'], 2.54)
        near(shifted['after'][0]['y_mm'], body['y_mm'])
        assert not require(await edit('dirty_noop', changes={'x_mm':2.54}))['changed']
        flip = require(await edit('rotate_x_z', changes=dict(rotation_x=180, rotation_y=0, rotation_z=0, z_mm=1)))
        near(flip['after'][0]['standoff_height_mm'], 1-body['overall_height_mm'])
        near(flip['after'][0]['overall_height_mm'], 1-body['standoff_height_mm'])
        flip_y = require(await edit('rotate_y_z', changes=dict(rotation_x=0, rotation_y=180, rotation_z=0, z_mm=2)))
        near(flip_y['after'][0]['standoff_height_mm'], 2-body['overall_height_mm'])
        near(flip_y['after'][0]['overall_height_mm'], 2-body['standoff_height_mm'])
        turn = require(await edit('rotate_z', changes=dict(rotation_x=0, rotation_y=0, rotation_z=90, z_mm=0)))
        turned = turn['after'][0]
        near(turned['right_mm']-turned['left_mm'], body['top_mm']-body['bottom_mm'])
        near(turned['top_mm']-turned['bottom_mm'], body['right_mm']-body['left_mm'])
        report['pose_effects_verified_by_bounds'] = True
        replaced = require(await edit('replace_link', changes=dict(model_path=str(step), embed=False,
                             rotation_x=0, rotation_y=0, rotation_z=0, z_mm=.5)))
        assert replaced['replacement_loaded'] and not replaced['after'][0]['embed']
        near(replaced['after'][0]['standoff_height_mm'], body['standoff_height_mm']+.5)
        embedded = require(await edit('embed_existing', changes={'embed':True}))
        assert embedded['after'][0]['embed'] and embedded['changed']
        linked = require(await edit('link_existing', changes={'embed':False}))
        assert not linked['after'][0]['embed']
        require(await edit('embed_again', changes={'embed':True}))
        second = root/'Second.step'
        # A different actual STEP payload tests replacement independently of renaming.
        second.write_bytes(step.read_bytes().replace(b'END-ISO-10303-21;', b'/* replacement fixture */\nEND-ISO-10303-21;'))
        require(await edit('replace_preserve_embed', changes=dict(model_path=str(second),
                           rotation_x=0, rotation_y=0, rotation_z=0, z_mm=0)))
        # The batch tool creates a separate two-body member for ambiguity/ownership checks.
        spec = root/'multi.txt'
        write_altium_text(spec, '\n'.join([f'FPLIB|{scratch}', 'FOOTPRINT|MCP_MODEL_MULTI|Disposable bodies|1',
            f'MODEL3D|{step}|Mechanical 13|0|0|0|0|0|0|1',
            f'MODEL3D|{second}|Mechanical 13|0|0|0|0|15|0|1'])+'\n')
        require(await call('create_multi', main.create_footprints_batch, str(spec), units='mm'))
        multi = require(await edit('read_multi', name='MCP_MODEL_MULTI'))
        assert len(multi['after']) == 2
        ambiguous = await edit('ambiguous', name='MCP_MODEL_MULTI', changes={'y_mm':3})
        assert not ambiguous['success'] and not ambiguous['may_have_partial_changes']
        stale = await edit('stale_id', name='MCP_MODEL_MULTI', body_id='1', delete=True)
        assert not stale['success'] and not stale['may_have_partial_changes']
        require(await edit('edit_second', name='MCP_MODEL_MULTI', body_id=multi['after'][1]['body_address'],
                           expected_model_file=multi['after'][1]['model_file'], changes={'y_mm':3.81}))
        deleted = require(await edit('delete_first', name='MCP_MODEL_MULTI', body_id=multi['after'][0]['body_address'], delete=True))
        assert len(deleted['after']) == 1
        # Switch away and back: current-footprint board ownership must survive.
        require(await edit('show_other', name=args.footprint))
        persisted = require(await edit('read_multi_again', name='MCP_MODEL_MULTI'))
        assert len(persisted['after']) == 1
        near(persisted['after'][0]['y_mm'], 3.81)
        require(await edit('delete_last', name='MCP_MODEL_MULTI', delete=True))
        assert not require(await edit('read_empty', name='MCP_MODEL_MULTI'))['after']
        absent = await edit('delete_absent', name='MCP_MODEL_MULTI', delete=True)
        assert not absent['success'] and not absent['may_have_partial_changes']
        # The original member's edits must also survive switching members.
        final = require(await edit('read_original_member'))
        assert final['after'][0]['embed'] and Path(final['after'][0]['model_file']).name == second.name
        near(final['after'][0]['x_mm'], 2.54)
        require(await edit('delete_original_body', delete=True))
        assert not require(await edit('original_body_removed'))['after']
        report['checks_passed'] = True
    except Exception as exc:
        report.update(checks_passed=False, error=str(exc) or repr(exc), outcome_unknown=unknown)
    finally:
        if not unknown:
            try:
                if opened:
                    require(await call('discard_copy', main.close_document, str(scratch), require_clean=False))
                    opened = False
                if original:
                    require(await call('restore_project', main.select_project, original['project_path']))
                    active = next((d['path'] for d in original['documents'] if d['state']=='open'), None)
                    if active: require(await call('restore_editor', main.open_document, active))
                    report['original_states_unchanged'] = require(await call('project_after', main.get_project_info)) == original
            except Exception as exc:
                report['cleanup_error'] = str(exc) or repr(exc)
        report['disk_after'] = {str(p):disk_state(p) for p in guarded}
        report['disk_unchanged'] = report.get('disk_before') == report['disk_after']
        report['scratch_closed'] = not opened
        (root/'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding='ascii')
        print('REPORT '+str(root/'report.json'), flush=True)
    assert all(report.get(k) for k in ('checks_passed', 'disk_unchanged', 'original_states_unchanged', 'scratch_closed')), report.get('error', report)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exercise', action='store_true')
    parser.add_argument('--pcblib', type=Path, default=Path(r'C:\workspace_altium\SQ-CARD\SQ-CARD.PcbLib'))
    parser.add_argument('--footprint', default='GSWITCH_GT_USB_7010ASV')
    asyncio.run(run(parser.parse_args()))
