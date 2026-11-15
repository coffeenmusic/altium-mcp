"""Opt-in STEP/constants/layer checks on disposable copies; never saves Altium documents."""

import argparse
import asyncio
import copy
import json
import ntpath
from pathlib import Path, PureWindowsPath
import shutil
import sys
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'server'))
from test_footprint_editor_live import disk_state, document_states, editor_windows, primitive_state


async def run(args):
    root = REPO / '.altium-mcp' / f'pcb_readback_fixes_{time.strftime("%Y%m%d_%H%M%S")}'
    root.mkdir(parents=True)
    report = dict(saved=False, steps=[], root=str(root))
    opened, unknown, previous, original = [], False, None, None
    guarded = {}

    def record(name, data):
        (root / f'{name}.json').write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding='ascii')

    if not args.exercise:
        print('Not run: pass --exercise.')
        return 0
    if not editor_windows() or any(not w['responding'] for w in editor_windows()):
        raise RuntimeError('Altium is absent or unresponsive; no commands dispatched')
    import main
    import footprint_editor
    import pcblib_file
    from altium_files import write_altium_text

    async def call(label, function, *values, expected=True, **options):
        nonlocal unknown
        for _ in range(40):
            windows = editor_windows()
            if windows and all(w['responding'] for w in windows):
                break
            await asyncio.sleep(.5)
        else:
            unknown = True
            raise RuntimeError('Altium unresponsive; no further command dispatched')
        data = json.loads(await function(None, *values, **options))
        record(label, data)
        report['steps'].append(dict(label=label, success=data.get('success'), error=data.get('error')))
        print(json.dumps(report['steps'][-1]), flush=True)
        if data.get('outcome_unknown') or data.get('close_outcome_unknown'):
            unknown = True
            raise RuntimeError('Unknown command outcome; stop before cleanup/retry')
        if data.get('command_started') is False:
            unknown = True
            raise RuntimeError(data.get('error', 'Command not started'))
        if expected:
            assert data.get('success') is not False and 'error' not in data, data
        else:
            assert data.get('success') is False and not data.get('may_have_partial_changes'), data
        return data

    try:
        original = await call('project_before', main.get_project_info)
        project_path = PureWindowsPath(original.get('project_path', ''))
        if not project_path.is_absolute() or project_path.suffix.lower() not in ('.prjpcb', '.libpkg'):
            original = None
            raise RuntimeError('Select a saved design/library project before running this live test')
        library = Path(args.library)
        source_board = Path(args.board)
        guarded = {d['path']: disk_state(Path(d['path'])) for d in original['documents']}
        guarded[original['project_path']] = disk_state(Path(original['project_path']))
        for source in (library, source_board):
            guarded[str(source)] = disk_state(source)
        constants = await call('all_constants', main.get_pcb_api_constants, sorted(footprint_editor.CONSTANT_NAMES))
        assert constants['constants']['eRound'] == constants['constants']['eRounded'] == 1
        assert constants['constants']['eNoLayer'] == 0
        assert all(v['resolved'] for v in constants['availability'].values())
        scratch = root / 'Parts_\u03a9.PcbLib'
        shutil.copy2(library, scratch)
        guarded[str(scratch)] = disk_state(scratch)
        opened_info = await call('open_library_copy', main.open_document, str(scratch))
        opened.append(scratch)
        previous = opened_info.get('previous_document_path')
        name = args.footprint
        model = await call('m01_saved_model', main.get_footprint_primitives, str(scratch), name)
        assert model['bodies_3d'], 'M01 must have a saved STEP body'
        assert all(b['placement'] == 'saved library file' and all(b[k] is not None for k in pcblib_file._PLACEMENT)
                   for b in model['bodies_3d']), model['bodies_3d']
        # Exercise both reference formats against actual saved M01 records.
        records = pcblib_file.body_placements(scratch, [name])[name.upper()]
        for record_index, body in enumerate(model['bodies_3d']):
            for short in (True, False):
                altered = copy.deepcopy(body)
                base = ntpath.basename(body['model_file'])
                altered['model_file'] = base if short else ntpath.join(r'C:\MCP_reference_check', base)
                saved = copy.deepcopy(records)
                # Opposite form forces the filename fallback without modifying Altium.
                for item in saved:
                    item['model_name'] = (ntpath.join(r'C:\MCP_reference_check', ntpath.basename(item['model_name']))
                                          if short else ntpath.basename(item['model_name']))
                pcblib_file.merge_placements([altered], saved)
                assert altered['placement_match'] == 'file_name'
                assert all(altered[k] == body[k] for k in pcblib_file._PLACEMENT)
                record(f'm01_reference_{record_index}_{short}', altered)
        aliases = ['Multi Layer', 'Multi-Layer', 'eMultiLayer', 'multilayer']
        mechanical = ['eMechanical13', 'Mechanical Layer 13', 'Mechanical13', 'mech-13']
        fixture = '__MCP_LAYER_ALIASES'
        lines = [f'FPLIB|{scratch}', f'FOOTPRINT|{fixture}|Temporary alias verification']
        for index, alias in enumerate(aliases):
            lines.append(f'PAD|{index + 1}|{index * 100}|0|0|{alias}|1|20|0|0|0|50|50|{constants["constants"]["eRound"]}')
        for index, alias in enumerate(mechanical):
            lines.append(f'TRACK|0|{100 + index * 100}|100|{100 + index * 100}|5|{alias}')
        spec = root / 'aliases.txt'
        write_altium_text(spec, '\n'.join(lines) + '\n')
        created = await call('create_alias_fixture', main.create_footprints_batch, str(spec))
        assert created['accepted_layers'] == ['Multi-Layer', 'Mechanical 13']
        before = await call('read_alias_fixture', main.get_footprint_primitives, str(scratch), fixture)
        assert all(p['layer'] == ('Multi-Layer' if p['type'] == 'pad' else 'Mechanical 13') for p in before['primitives'])
        tracks = [p for p in before['primitives'] if p['type'] == 'track']
        operations = []
        for index, track in enumerate(tracks):
            target = {k: track[k] for k in ('type', 'layer', *footprint_editor.GEOMETRY['track'])}
            target['layer'] = mechanical[index]
            operations.append(dict(target=target, layer='eMechanical15'))
        changed = await call('edit_using_alias_selectors', main.edit_footprint_primitives,
                             str(scratch), fixture, operations)
        assert changed['accepted_layers'] == ['Mechanical 15']
        after = await call('read_after_alias_edit', main.get_footprint_primitives, str(scratch), fixture)
        assert primitive_state(before, ('layer',)) == primitive_state(after, ('layer',))
        assert all(p['layer'] == 'Mechanical 15' for p in after['primitives'] if p['type'] == 'track')
        for alias in ('Mechanical17', 'MCP_UNKNOWN_LAYER'):
            write_altium_text(spec, f'FPLIB|{scratch}\nFOOTPRINT|__MCP_REJECT|Must not be created\nTRACK|0|0|1|1|1|{alias}\n')
            await call('reject_' + alias, main.create_footprints_batch, str(spec), expected=False)
        await call('discard_library_copy', main.close_document, str(scratch), require_clean=False)
        opened.remove(scratch)
        board = root / 'Board_\u03a9.PcbDoc'
        shutil.copy2(source_board, board)
        guarded[str(board)] = disk_state(board)
        await call('open_board_copy', main.open_document, str(board))
        opened.append(board)
        layers = await call('board_layers', main.get_pcb_layers)
        number = int(layers['mechanical_layers'][0]['mechanical_number'])
        for index, alias in enumerate(aliases + [f'eMechanical{number}', f'Mechanical Layer {number}', f'mech-{number}']):
            changed = await call(f'visibility_alias_{index}', main.set_pcb_layer_visibility,
                                 [alias], visible=True, exclusive=False)
            assert changed['updated_count'] == 1 and not changed['not_found_layers'], changed
            assert changed['accepted_layers'] == ['Multi-Layer' if index < len(aliases) else f'Mechanical {number}']
        await call('discard_board_copy', main.close_document, str(board), require_clean=False)
        opened.remove(board)
        report['checks_passed'] = True
    except Exception as exc:
        report.update(checks_passed=False, error=str(exc))
    finally:
        if not unknown:
            try:
                for path in reversed(opened):
                    await call('cleanup_' + path.suffix[1:], main.close_document, str(path), require_clean=False)
                opened.clear()
                if previous:
                    if PureWindowsPath(previous).is_absolute():
                        await call('restore_editor', main.open_document, previous)
                    else:
                        # Internal pages have names, not absolute design paths.
                        restored = await main.altium_bridge.execute_command('focus_document',
                            {'path': previous, 'open_if_closed': 'false'})
                        record('restore_internal_page', restored)
                        assert restored.get('success'), restored
                if original:
                    await call('restore_project', main.select_project, original['project_path'])
                    final = await call('project_after', main.get_project_info)
                    report['original_states_unchanged'] = document_states(original) == document_states(final)
            except Exception as exc:
                report.update(checks_passed=False, cleanup_error=str(exc))
        report['disk_unchanged'] = all(disk_state(Path(path)) == state for path, state in guarded.items())
        report['scratch_closed'] = not opened
        report['outcome_unknown'] = unknown
        report['checks_passed'] = bool(report.get('checks_passed') and report['disk_unchanged']
                                     and report.get('original_states_unchanged') and report['scratch_closed'])
        record('report', report)
        print(json.dumps({k: v for k, v in report.items() if k != 'steps'}), flush=True)
    return 0 if report['checks_passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exercise', action='store_true')
    parser.add_argument('--library', default=r'C:\workspace_altium\SQ-CARD\SQ-CARD.PcbLib')
    parser.add_argument('--board', default=r'C:\workspace_altium\SQ-CARD\PCB.PcbDoc')
    parser.add_argument('--footprint', default='GSWITCH_GT_USB_7010ASV')
    raise SystemExit(asyncio.run(run(parser.parse_args())))
