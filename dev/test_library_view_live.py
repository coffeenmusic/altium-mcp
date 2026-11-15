"""Opt-in exact library navigation and view captures on disposable copies."""

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
        print('Pass --exercise to navigate disposable copies without saving.')
        return
    import main
    root=REPO/'.altium-mcp'/('library_view_'+time.strftime('%Y%m%d_%H%M%S'))
    root.mkdir(parents=True)
    logging.getLogger().setLevel(logging.WARNING)
    sch=root/'View.SchLib';pcb=root/'View_\u03a9_\u0416.PcbLib'
    shutil.copy2(args.schlib,sch);shutil.copy2(args.pcblib,pcb)
    report={'saved':False,'steps':[],'captures':[]}
    original=None;guarded=[];opened=[];unknown=False
    native_execute=main.altium_bridge.execute_command
    native_count=0

    async def trace(command,params):
        nonlocal native_count
        result=await native_execute(command,params)
        if command=='set_library_view':
            native_count+=1
            record('native_'+str(native_count),result)
        return result

    main.altium_bridge.execute_command=trace

    def record(label,value):
        (root/(label+'.json')).write_text(json.dumps(value,indent=2,ensure_ascii=True),encoding='ascii')

    async def ready():
        nonlocal unknown
        stable=0
        for _ in range(40):
            windows=editor_windows()
            stable=stable+1 if windows and all(w['responding'] for w in windows) else 0
            if stable>=3:
                return
            await asyncio.sleep(.5)
        unknown=True
        raise RuntimeError('Altium not responsive; no further command sent')

    async def call(label,function,*values,**options):
        nonlocal unknown
        await ready()
        result=json.loads(await function(None,*values,**options))
        record(label,result)
        row={'label':label,'success':result.get('success'),'error':result.get('error'),
             'selected':result.get('selected')}
        report['steps'].append(row);print(json.dumps(row,ensure_ascii=True),flush=True)
        if result.get('outcome_unknown') or result.get('close_outcome_unknown'):
            unknown=True
            raise RuntimeError('Unknown command completion; no further command sent')
        return result

    def require(value):
        assert value.get('success',True) and not value.get('error'),value
        assert value.get('saved',False) is False,value
        return value

    async def capture(label,kind,path):
        await ready()
        result=await main.get_screenshot(None,kind)
        assert isinstance(result,list),result
        meta=json.loads(result[0]);require(meta)
        assert meta['captured_document'].casefold()==path.name.casefold(),meta
        (root/(label+'.png')).write_bytes(result[1].data)
        record(label+'_capture',meta);report['captures'].append(label+'.png')
        print('CAPTURE '+label,flush=True)

    try:
        original=require(await call('project_before',main.get_project_info))
        guarded=list({sch,pcb,Path(args.schlib),Path(args.pcblib),Path(original['project_path']),
                      *(Path(d['path']) for d in original['documents'])})
        report['disk_before']={str(p):disk_state(p) for p in guarded}
        for path in (sch,pcb):
            require(await call('open_'+path.suffix[1:],main.open_document,str(path)));opened.append(path)
        for part in (1,2):
            selected=require(await call('part_'+str(part),main.set_library_view,str(sch),args.symbol,part_id=part,display_mode=0))
            assert selected['selected']['part_id']==part
            await capture('part_'+str(part),'schlib',sch)
        for label,options in (('missing_symbol',{'component_name':'MCP_MISSING'}),
                               ('invalid_part',{'component_name':args.symbol,'part_id':255}),
                               ('invalid_display',{'component_name':args.symbol,'display_mode':255})):
            refused=await call(label,main.set_library_view,str(sch),**options)
            assert refused['success'] is False and not refused['actions']
        # Only this fixture preparation needs an arbitrary script: existing
        # creation tools do not expose alternate display-mode creation.
        from altium_files import write_altium_text
        import symbol_metadata
        metadata=require(await call('fixture_metadata',main.set_library_component_data,str(sch),args.symbol))['after']
        parameters={k:metadata['parameters'].get(k,metadata['comment']) for k in symbol_metadata.REQUIRED_PARAMETERS}
        fixture_name='MCP_VIEW_FIXTURE'
        records=['LIBRARY|'+str(sch),'SYMBOL|'+fixture_name+'|Disposable library view fixture based on '+args.symbol+'|1']
        records+=['PARAM|'+k+'|'+v for k,v in parameters.items()]
        records+=['GRAPHIC|line|1|1|0|0|100|0']
        fixture_spec=root/'fixture.txt';write_altium_text(fixture_spec,'\n'.join(records)+'\n')
        require(await call('create_fixture_symbol',main.create_symbols_batch,str(fixture_spec)))
        require(await call('fixture_focus',main.set_library_view,str(sch),fixture_name,part_id=1,fit=False))
        path_literal=str(sch).replace("'","''")
        symbol_literal=fixture_name
        script=f"""SandboxLog('Verify disposable display-mode fixture');
Obj1 := SchServer.GetCurrentSchDocument;
if Obj1 = nil then begin ResultText := 'No SchLib'; Exit; end;
if Obj1.ObjectId <> 33 then begin ResultText := 'Wrong document kind'; Exit; end;
if not SameText(Obj1.DocumentName,'{path_literal}') then begin ResultText := 'Wrong scratch path'; Exit; end;
Obj2 := Obj1.CurrentSchComponent;
if not SameText(Obj2.LibReference,'{symbol_literal}') then begin ResultText := 'Wrong fixture symbol'; Exit; end;
if Obj2.DisplayModeCount <> 1 then begin ResultText := 'Fixture needs exactly one initial display mode'; Exit; end;
SandboxLog('Add one display mode in disposable copy');
SchServer.ProcessControl.PreProcess(Obj1,'');
try Obj1.SetState_CurrentSchComponentAddDisplayMode;
finally SchServer.ProcessControl.PostProcess(Obj1,''); end;
ResultText := IntToStr(Obj1.CurrentSchComponent.DisplayModeCount);"""
        fixture=require(await call('create_alternate_display',main.run_altium_script,script,timeout_seconds=40))
        assert fixture['result'].strip()=='2',fixture
        for mode in (0,1,0):
            selected=require(await call('display_'+str(len(report['steps'])),main.set_library_view,str(sch),fixture_name,display_mode=mode,fit=False))
            assert selected['selected']['display_mode']==mode and selected['is_modified']
        report['alternate_display_verified']=True
        invalid=await call('missing_footprint',main.set_library_view,str(pcb),'MCP_MISSING',view_mode='3d')
        assert invalid['success'] is False and not invalid['actions']
        require(await call('m01_2d',main.set_library_view,str(pcb),args.footprint,view_mode='2d'))
        await capture('m01_2d','pcblib',pcb)
        require(await call('m01_first_iso',main.set_library_view,str(pcb),args.footprint,camera='isometric'))
        await capture('m01_first_iso','pcblib',pcb)
        for camera in ('top','bottom','front','back','left','right','isometric','flipped_isometric'):
            require(await call('m01_'+camera,main.set_library_view,str(pcb),args.footprint,camera=camera))
            await capture('m01_'+camera,'pcblib',pcb)
        # Reject silent camera no-ops even when process dispatch succeeds.
        from PIL import Image,ImageChops
        top=Image.open(root/'m01_top.png').convert('RGB')
        w,h=top.size;crop=(int(w*.22),int(h*.14),int(w*.72),int(h*.84))
        top=top.crop(crop)
        ratios={}
        for camera in ('bottom','front','back','left','right','isometric','flipped_isometric'):
            view=Image.open(root/('m01_'+camera+'.png')).convert('RGB').crop(crop)
            histogram=ImageChops.difference(top,view).convert('L').histogram()
            ratios[camera]=sum(histogram[33:])/(top.width*top.height)
            assert ratios[camera]>.05,('camera may be a silent no-op',camera,ratios[camera])
        report['camera_image_change_ratios']=ratios
        require(await call('m01_return_2d',main.set_library_view,str(pcb),args.footprint,view_mode='2d'))
        current=require(await call('read_current',main.set_library_view,str(pcb),fit=False))
        assert current['selected']['component_name']==args.footprint and current['actions']==[]
        report['checks_passed']=True
    except Exception as exc:
        report.update(checks_passed=False,error=str(exc),outcome_unknown=unknown)
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
        report['scratch_closed']=not opened;record('report',report)
        print('REPORT '+str(root/'report.json'),flush=True)
    assert all(report.get(k) for k in ('checks_passed','disk_unchanged','original_states_unchanged','scratch_closed')),report.get('error',report)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--exercise',action='store_true')
    p.add_argument('--schlib',default=r'C:\workspace_altium\SQ-CARD\SQ-CARD.SchLib')
    p.add_argument('--pcblib',default=r'C:\workspace_altium\SQ-CARD\SQ-CARD.PcbLib')
    p.add_argument('--symbol',default='LTST-C155KGJRKT')
    p.add_argument('--footprint',default='GSWITCH_GT_USB_7010ASV')
    asyncio.run(run(p.parse_args()))
