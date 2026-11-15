"""Verify strict LCSC application on disposable SchLib symbols, without saving."""

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
        print('Pass --exercise to edit a disposable library copy without saving.')
        return
    import main
    logging.getLogger().setLevel(logging.WARNING)
    root = REPO / '.altium-mcp' / ('lcsc_application_' + time.strftime('%Y%m%d_%H%M%S'))
    root.mkdir(parents=True)
    source = args.schlib.resolve()
    scratch = root / 'Supplier_\u03a9.SchLib'
    shutil.copy2(source, scratch)
    extensions = {'.schdoc','.pcbdoc','.schlib','.pcblib','.prjpcb','.libpkg','.outjob','.bomdoc','.pcbdwf'}
    guarded = sorted({p for p in source.parent.iterdir() if p.is_file() and p.suffix.lower() in extensions} | {scratch})
    report = dict(saved=False, steps=[], disk_before={str(p):disk_state(p) for p in guarded})
    opened = unknown = False
    original = previous = None

    async def call(label, function, *values, **options):
        nonlocal unknown
        stable = 0
        for _ in range(40):
            windows = editor_windows()
            stable = stable + 1 if windows and all(w['responding'] for w in windows) else 0
            if stable >= 3: break
            await asyncio.sleep(.5)
        else:
            unknown = True
            raise RuntimeError('Altium not responsive; no command sent')
        result = json.loads(await function(None, *values, **options))
        (root / (label+'.json')).write_text(json.dumps(result, indent=2, ensure_ascii=True), encoding='ascii')
        row = dict(label=label, **{k:result.get(k) for k in ('success','changed','saved','error')})
        report['steps'].append(row)
        print(json.dumps(row), flush=True)
        if result.get('outcome_unknown') or result.get('close_outcome_unknown'):
            unknown = True
            raise RuntimeError('Unknown completion; no more Altium commands will be sent')
        return result

    def require(value):
        assert value.get('success', True) and not value.get('error'), value
        assert value.get('saved', False) is False, value
        if 'verification' in value: assert value['verification']['passed'], value
        return value

    async def read(label, name):
        return require(await call(label, main.set_library_component_data, str(scratch), name))['after']

    async def apply(label, name, **options):
        return await call(label, main.set_library_component_lcsc_data, str(scratch), name, args.lcsc_id, qty=100, **options)

    try:
        original = require(await call('project_before', main.get_project_info))
        lookup = require(await call('catalog', main.get_lcsc_component_info, args.lcsc_id, qty=100, include_historical=False))
        component = lookup['components'][0]
        assert component['manufacturer'] and component['package'], component
        opened_result = require(await call('open_copy', main.open_document, str(scratch)))
        opened = True
        previous = opened_result.get('previous_document_path')
        inventory = require(await call('inventory_before', main.get_symbol_primitives, str(scratch)))
        reference = inventory['symbols'][0]['name']
        reference_before = require(await call('reference_before', main.get_symbol_primitives, str(scratch), reference))
        base = {'Value':'Supplier validation fixture','MF':component['manufacturer'],'MP':component['mpn'],
                'Package':component['package'],'HelpURL':component['product_url'],'Type':'IC',
                'Instructions':'Test only','System':'Supplier validation',
                'User Note':'Keep \u03a9 \u0416 \U0001f680 | = ,'}
        names = []
        async def create(label, **changes):
            name = 'MCP_LCSC_' + label.upper()
            names.append(name)
            require(await call('create_'+label, main.create_schematic_symbol, name,
                               'IC: Disposable supplier validation symbol', [], {**base, **changes}))
            return name

        good = await create('identity')
        before = await read('good_before', good)
        geometry_before = require(await call('geometry_before', main.get_symbol_primitives, str(scratch), good))
        result = require(await apply('apply_verified', good))
        assert result['identity_verified'] and result['package_check']['status'] == 'match'
        assert not result['package_conflict']
        after = result['after']
        for key,value in base.items(): assert after['parameters'][key] == value
        for key in ('description','comment','models'): assert after[key] == before[key]
        fields = after['parameters']
        assert fields['LCSC Source URL'] == component['source_url']
        assert fields['LCSC Data Source'] == 'lcsc_detail_api'
        assert fields['LCSC Fetched At'] == result['lcsc_fetched_at']
        assert fields['LCSC Sales Status'] in {'on_sale','not_for_sale','discontinued','unknown'}
        assert json.loads(fields['LCSC Sales Status Evidence']) == component['sales_status_evidence']
        assert fields['LCSC Sales Region'] == component['sales_region']
        refreshed = require(await apply('refresh_timestamp', good))
        assert refreshed['after']['parameters']['LCSC Fetched At'] != fields['LCSC Fetched At']
        assert refreshed['changed']
        geometry_after = require(await call('geometry_after', main.get_symbol_primitives, str(scratch), good))
        assert geometry_before['primitives'] == geometry_after['primitives']

        for label,changes,field in [('wrong_mf',{'MF':'MCP intentional other manufacturer'},'MF'),
                                     ('wrong_mp',{'MP':component['mpn']+'_WRONG'},'MP'),
                                     ('wrong_package',{'Package':'MCP conflicting package'},'Package')]:
            name = await create(label, **changes)
            prior = await read(label+'_before', name)
            denied = await apply(label+'_reject', name)
            assert not denied['success'] and denied['may_have_partial_changes'] is False, denied
            assert any(c['field'] == field for c in denied['conflicts'])
            assert await read(label+'_after', name) == prior
            if label == 'wrong_package':
                assert denied['package_check']['symbol'] == changes['Package']
                assert denied['package_check']['catalog'] == component['package']
                overridden = require(await apply('package_override', name, allow_package_mismatch=True))
                assert overridden['package_mismatch_allowed'] and overridden['package_conflict']
                assert overridden['warnings'][0]['code'] == 'package_mismatch_allowed'
                assert overridden['after']['parameters']['Package'] == changes['Package']
            if label == 'wrong_mf':
                denied = await apply('manufacturer_not_bypassed', name, allow_package_mismatch=True)
                assert not denied['success'] and not denied['may_have_partial_changes']
                assert await read('manufacturer_still_unchanged', name) == prior
        missing = await create('missing_mf')
        require(await call('clear_mf', main.set_library_component_data, str(scratch), missing, parameters={'MF':''}))
        before_missing = await read('missing_mf_before', missing)
        denied = await apply('missing_mf_reject', missing)
        assert not denied['success'] and denied['identity_check']['manufacturer']['status'] == 'symbol_missing'
        assert await read('missing_mf_after', missing) == before_missing
        ref_after = require(await call('reference_after', main.get_symbol_primitives, str(scratch), reference))
        assert ref_after == reference_before
        final_inventory = require(await call('inventory_after', main.get_symbol_primitives, str(scratch)))
        assert final_inventory['symbol_count'] == inventory['symbol_count'] + len(names)
        report.update(checks_passed=True, identity_conflicts_rejected=True, package_override_verified=True,
                      provenance_readback_verified=True, core_and_geometry_preserved=True)
    except Exception as exc:
        report.update(checks_passed=False, error=str(exc) or repr(exc), outcome_unknown=unknown)
    finally:
        if not unknown:
            try:
                if opened:
                    require(await call('discard_copy', main.close_document, str(scratch), require_clean=False))
                    opened = False
                if previous and Path(previous).suffix.casefold() in {'.schdoc','.pcbdoc','.schlib','.pcblib'}:
                    require(await call('restore_editor', main.open_document, previous))
                if original:
                    if Path(original['project_path']).suffix.casefold() in {'.prjpcb','.libpkg'}:
                        require(await call('restore_project', main.select_project, original['project_path']))
                    final = require(await call('project_after', main.get_project_info))
                    report['original_states_unchanged'] = final == original
            except Exception as exc: report['cleanup_error'] = str(exc) or repr(exc)
        report['disk_after'] = {str(p):disk_state(p) for p in guarded}
        report['disk_unchanged'] = report['disk_before'] == report['disk_after']
        report['scratch_closed'] = not opened
        (root/'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding='ascii')
        print('REPORT '+str(root/'report.json'), flush=True)
    assert all(report.get(k) for k in ('checks_passed','disk_unchanged','scratch_closed','original_states_unchanged')), report.get('error', report)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exercise', action='store_true')
    parser.add_argument('--schlib', type=Path, default=Path(r'C:\workspace_altium\SQ-CARD\SQ-CARD.SchLib'))
    parser.add_argument('--lcsc-id', default='C136648')
    asyncio.run(run(parser.parse_args()))
