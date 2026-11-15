"""Compare mm/mil batch geometry, Height and both-side flags without saving."""

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
        print('Pass --exercise to create disposable unsaved footprint fixtures.')
        return
    import main
    from altium_files import write_altium_text
    from library_view import canonical
    root=REPO/'.altium-mcp'/('footprint_batch_properties_'+time.strftime('%Y%m%d_%H%M%S'));root.mkdir(parents=True)
    logging.getLogger().setLevel(logging.WARNING)
    scratch=root/'Batch_\u03a9.PcbLib';shutil.copy2(args.pcblib,scratch)
    report={'saved':False,'steps':[]};original=None;opened=False;unknown=False;guarded=[]

    async def call(label,func,*values,**options):
        nonlocal unknown
        stable=0
        for _ in range(40):
            windows=editor_windows();stable=stable+1 if windows and all(w['responding'] for w in windows) else 0
            if stable>=3:break
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

    def require(value):
        assert value.get('success',True) and not value.get('error'),value
        assert value.get('saved',False) is False,value
        return value

    try:
        original=require(await call('project_before',main.get_project_info))
        guarded=list({Path(args.pcblib),scratch,Path(original['project_path']),*(Path(d['path']) for d in original['documents'])})
        report['disk_before']={str(p):disk_state(p) for p in guarded}
        require(await call('open_copy',main.open_document,str(scratch)));opened=True
        inventory=require(await call('inventory_before',main.get_footprint_primitives,str(scratch)))
        reference=inventory['footprints'][0]['name']
        before=require(await call('reference_before',main.get_footprint_primitives,str(scratch),reference))
        constants=require(await call('constants',main.get_pcb_api_constants,['eRectangular']))
        shape=constants['constants']['eRectangular']

        def layout(name,units):
            # Fixture dimensions are defined independently in mm; the mil file
            # uses explicit converted values, not the production field map.
            f=lambda n:format(n if units=='mm' else n/.0254,'.15g')
            def pad(name,x,y,w,h,layer,top,bottom,hole=0,stack=False):
                return '|'.join(map(str,['PAD',name,f(x),f(y),90,layer,int(hole>0),f(hole),0,0,0,
                                        f(w),f(h),shape,0,int(stack),f(1.4) if stack else '',f(1.9) if stack else '',shape if stack else '',
                                        f(1.5) if stack else '',f(2) if stack else '',shape if stack else '',2,f(-.02),2,f(.05),top,bottom]))
            return ['FPLIB|'+str(scratch),'FOOTPRINT|'+name+'|Disposable geometry fixture|'+f(3.26),
                    pad('01',2.54,5.08,.6,1.15,'Top Layer',1,0),pad('B',-2.54,0,.6,1.15,'Bottom Layer',0,1),
                    pad('TH',0,0,1.5,2,'Multi-Layer',1,1,.7,True),
                    pad('R',5.08,0,.4,.8,'Top Layer',1,0),pad('R',7.62,0,.4,.8,'Top Layer',0,0),
                    '|'.join(['TRACK',f(0),f(0),f(2.54),f(1.27),f(.1),'Mechanical 13']),
                    '|'.join(['ARC',f(0),f(0),f(1.27),'0','90',f(.1),'Mechanical 15']),
                    '|'.join(['FILL',f(-1.27),f(-1.27),f(1.27),f(1.27),'30','Mechanical 13']),
                    '|'.join(['TEXT',f(0),f(-2.54),f(1.27),f(.127),'0','Top Overlay','0','0','Unit test 123']),
                    '|'.join(['REGION','Mechanical 15','0',f(0),f(0),f(2.54),f(0),f(2.54),f(2.54),f(0),f(2.54)]),
                    '|'.join(['VIA',f(1.27),f(3.81),f(.8),f(.4),'Top Layer','Bottom Layer'])]

        for units in ('mm','mil'):
            records=layout('MCP_BATCH_'+units.upper(),units)
            if units=='mm':
                records.extend(['FOOTPRINT|MCP_BATCH_DEFAULTS|Default creation fields',
                                f'PAD|DEF|0|0|0|Top Layer|0|0|0|0|0|.5|.8|{shape}'])
            spec=root/(units+'.txt');write_altium_text(spec,'\n'.join(records)+'\n')
            original_spec=spec.read_bytes()
            result=require(await call('create_'+units,main.create_footprints_batch,str(spec),units=units))
            assert result['created']==(2 if units=='mm' else 1) and result['verification']['passed'] and result['input_units']==units
            if units=='mm':
                default=result['footprints'][1]
                assert default['height_mm']==0 and default['pad_count']==1
                assert not default['pads'][0]['is_testpoint_top'] and not default['pads'][0]['is_testpoint_bottom']
            assert spec.read_bytes()==original_spec
        mm=require(await call('read_mm',main.get_footprint_primitives,str(scratch),'MCP_BATCH_MM'))
        mil=require(await call('read_mil',main.get_footprint_primitives,str(scratch),'MCP_BATCH_MIL'))
        assert canonical(mm['primitives'])==canonical(mil['primitives'])
        assert abs(mm['height_mm']-3.26)<.000003 and mm['height_mm']==mil['height_mm']
        pads={p['name']:p for p in mm['primitives'] if p['type']=='pad' and p['name']!='R'}
        assert pads['01']['is_testpoint_top'] and not pads['01']['is_testpoint_bottom']
        assert pads['B']['is_testpoint_bottom'] and not pads['B']['is_testpoint_top']
        assert pads['TH']['is_testpoint_top'] and pads['TH']['is_testpoint_bottom']
        assert abs(pads['01']['x']-100)<.002 and abs(pads['01']['y']-200)<.002
        report['mm_mil_geometry_equal']=True;report['top_bottom_flags_verified']=True
        legacy=root/'legacy.txt'
        write_altium_text(legacy,f'FPLIB|{scratch}\nFOOTPRINT|MCP_BATCH_LEGACY|Legacy format\nPAD|1|100|0|0|Top Layer|0|0|0|0|0|20|40|{shape}\n')
        require(await call('create_legacy',main.create_footprints_batch,str(legacy)))
        old=require(await call('read_legacy',main.get_footprint_primitives,str(scratch),'MCP_BATCH_LEGACY'))
        pad=next(p for p in old['primitives'] if p['type']=='pad')
        assert old['height_mm']==0 and not pad['is_testpoint_top'] and not pad['is_testpoint_bottom'] and pad['x']==100
        for label,record,units in [('bad_height','FOOTPRINT|BAD|Bad height|-1','mm'),
                                   ('bad_side',layout('BAD','mm')[2][:-3]+'0|1','mm'),
                                   ('bad_flag',layout('BAD','mm')[2][:-3]+'True|0','mm'),
                                   ('bad_units','FOOTPRINT|BAD|Bad units','inch')]:
            spec=root/(label+'.txt');write_altium_text(spec,'\n'.join(['FPLIB|'+str(scratch),'FOOTPRINT|MCP_MUST_NOT_EXIST|Earlier valid|1',record]))
            result=await call(label,main.create_footprints_batch,str(spec),units=units)
            assert result['success'] is False and result['may_have_partial_changes'] is False
        after=require(await call('inventory_after',main.get_footprint_primitives,str(scratch)))
        assert after['footprint_count']==inventory['footprint_count']+4
        assert not any(r['name'].startswith('MCP_MUST_NOT_EXIST') for r in after['footprints'])
        other=require(await call('reference_after',main.get_footprint_primitives,str(scratch),reference))
        assert canonical(before)==canonical(other)
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
                    if active:require(await call('restore_editor',main.open_document,active))
                    after=require(await call('project_after',main.get_project_info))
                    report['original_states_unchanged']=after==original
            except Exception as exc:report['cleanup_error']=str(exc) or repr(exc)
        report['disk_after']={str(p):disk_state(p) for p in guarded}
        report['disk_unchanged']=report.get('disk_before')==report['disk_after'];report['scratch_closed']=not opened
        (root/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=True),encoding='ascii')
        print('REPORT '+str(root/'report.json'),flush=True)
    assert all(report.get(k) for k in ('checks_passed','disk_unchanged','original_states_unchanged','scratch_closed')),report.get('error',report)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--exercise',action='store_true')
    parser.add_argument('--pcblib',type=Path,default=Path(r'C:\workspace_altium\SQ-CARD\SQ-CARD.PcbLib'))
    asyncio.run(run(parser.parse_args()))
