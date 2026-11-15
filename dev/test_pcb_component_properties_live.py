"""Opt-in component-type checks on a disposable board, without saving."""

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


def fixtures(path):
    from altium_files import write_altium_text
    target=Path(path).parent/'fixture_target.txt'
    write_altium_text(target,str(path)+'\n')
    target=str(target).replace("'","''")
    return """var
    Board: IPCB_Board;
    Comp: IPCB_Component;
    Pad: IPCB_Pad;
    Net: IPCB_Net;
    Doc: IServerDocument;
    Module: IServerModule;
begin
    SandboxLog('Read fixture target path');
    List1.LoadFromFile('TARGET_FILE'); S1 := List1[0];
    SandboxLog('Find exact open disposable PCB');
    Doc := nil;
    for I2 := 0 to Client.Count-1 do
    begin
        Module := Client.ServerModule[I2];
        if Module<>nil then
            for I3 := 0 to Module.DocumentCount-1 do
                if UpperCase(Module.Documents[I3].FileName)=UpperCase(S1) then Doc := Module.Documents[I3];
    end;
    if Doc<>nil then
    begin
        SandboxLog('Focus disposable PCB');
        Client.ShowDocument(Doc);
        Sleep(500);
    end;
    SandboxLog('Resolve PCB board');
    Board := PCBServer.GetCurrentPCBBoard;
    ResultText := 'Refused: PCB target is not focused; expected=' + S1;
    if Board=nil then ResultText := ResultText + '; board=nil'
    else ResultText := ResultText + '; actual=' + Board.FileName + '; library=' + BoolToStr(Board.IsLibrary,True);
    if (Doc<>nil) and (Board<>nil) then
    if (UpperCase(Doc.Kind)='PCB') and (UpperCase(Board.FileName)=UpperCase(S1)) then
    begin
    SandboxLog('Create disposable PCB components and pads');
    PCBServer.PreProcess;
    try
        Net := PCBServer.PCBObjectFactory(eNetObject,eNoDimension,eCreate_Default);
        Net.Name := 'MCP_TEST_NET'; Board.AddPCBObject(Net);
        PCBServer.SendMessageToRobots(Board.I_ObjectAddress,c_Broadcast,PCBM_BoardRegisteration,Net.I_ObjectAddress);
        for I1 := 1 to 3 do
        begin
            Comp := PCBServer.PCBObjectFactory(eComponentObject,eNoDimension,eCreate_Default);
            if I1<3 then Comp.Name.Text := 'MCP_FID'+IntToStr(I1) else Comp.Name.Text := 'MCP_U1';
            Comp.Comment.Text := 'Disposable property fixture';
            Comp.Pattern := 'MCP_FIDUCIAL_FIXTURE';
            Comp.SourceUniqueId := 'MCP_TEST_UID_'+IntToStr(I1);
            Comp.SourceDesignator := Comp.Name.Text;
            Comp.X := Board.XOrigin+MM(10*I1); Comp.Y := Board.YOrigin+MM(10);
            Comp.Layer := eTopLayer; Comp.ComponentKind := eComponentKind_Standard;
            Comp.Moveable := I1<>1;
            Pad := PCBServer.PCBObjectFactory(ePadObject,eNoDimension,eCreate_Default);
            Pad.Name := '1'; Pad.X := Comp.X; Pad.Y := Comp.Y; Pad.Layer := eTopLayer;
            Pad.TopXSize := MM(1); Pad.TopYSize := MM(1); Pad.TopShape := eRounded;
            Pad.HoleSize := 0; Pad.Plated := False;
            if I1=3 then Pad.Net := Net;
            Comp.AddPCBObject(Pad);
            Board.AddPCBObject(Comp);
            PCBServer.SendMessageToRobots(Board.I_ObjectAddress,c_Broadcast,PCBM_BoardRegisteration,Comp.I_ObjectAddress);
        end;
    finally PCBServer.PostProcess; end;
    Board.SetState_DocumentHasChanged;
    Board.ViewManager_FullUpdate;
    ResultText := 'Created three disposable PCB component fixtures; no document saved';
    end;
end;""".replace('TARGET_FILE',target)


async def run(args):
    if not args.exercise:
        print('Pass --exercise to create disposable placed-component fixtures.');return
    import main
    root=REPO/'.altium-mcp'/('pcb_component_properties_'+time.strftime('%Y%m%d_%H%M%S'));root.mkdir(parents=True)
    scratch=root/'Board_\u03a9.PcbDoc';shutil.copy2(args.pcb,scratch)
    logging.getLogger().setLevel(logging.WARNING)
    report=dict(saved=False,steps=[]);opened=unknown=False;original=previous=None;guarded=[]

    async def call(label,fn,*values,**options):
        nonlocal unknown
        stable=0
        for _ in range(40):
            windows=editor_windows();stable=stable+1 if windows and all(w['responding'] for w in windows) else 0
            if stable>=3:break
            await asyncio.sleep(.5)
        else:unknown=True;raise RuntimeError('Altium not responsive; no command sent')
        result=json.loads(await fn(None,*values,**options))
        (root/(label+'.json')).write_text(json.dumps(result,indent=2,ensure_ascii=True),encoding='ascii')
        row=dict(label=label,**{k:result.get(k) for k in ('success','changed','saved','error')})
        report['steps'].append(row);print(json.dumps(row),flush=True)
        if result.get('outcome_unknown') or result.get('close_outcome_unknown'):
            unknown=True;raise RuntimeError('Unknown completion; no further commands sent')
        if label=='fixtures' and not result.get('success'):
            if not result.get('recovery',{}).get('recovered'):
                unknown=True;raise RuntimeError('Fixture script failed without confirmed executor recovery')
        return result

    def require(value):
        assert value.get('success',True) and not value.get('error'),value
        assert value.get('saved',False) is False,value
        if 'verification' in value:assert value['verification']['passed'],value
        return value

    async def edit(label,names=None,**options):
        return await call(label,main.set_pcb_component_properties,str(scratch),names or ['MCP_FID1','MCP_FID2','MCP_U1'],**options)

    try:
        original=require(await call('project_before',main.get_project_info))
        guarded=list({args.pcb,scratch,Path(original['project_path']),*(Path(d['path']) for d in original['documents'])})
        report['disk_before']={str(p):disk_state(p) for p in guarded}
        result=require(await call('open_copy',main.open_document,str(scratch)));opened=True;previous=result.get('previous_document_path')
        setup=require(await call('fixtures',main.run_altium_script,fixtures(scratch),timeout_seconds=60))
        assert setup.get('result','').startswith('Created three disposable'),setup
        assert not setup.get('undo_groups_closed')
        before=require(await edit('read_before'));assert len(before['after'])==3
        assert not before['after'][0]['moveable']
        marker=require(await edit('mark_graphical',['MCP_FID1','MCP_FID2'],graphical=True,include_in_bom=False,include_in_pick_place=False))
        assert marker['updated_count']==2 and all(row['component_kind']=='graphical' for row in marker['after'])
        assert not require(await edit('graphical_noop',['MCP_FID1','MCP_FID2'],graphical=True))['changed']
        stable=require(await edit('stable_before_refusals'))['after']
        for label,names,options in [('missing',['MCP_FID1','MCP_MISSING'],dict(component_kind='standard')),
                                   ('conflicting',['MCP_FID1'],dict(graphical=True,include_in_bom=True)),
                                   ('ambiguous',['MCP_FID1'],dict(include_in_bom=True)),
                                   ('output_dependent',['MCP_U1'],dict(component_kind='standard_no_bom',include_in_pick_place=True))]:
            refused=await edit(label,names,**options)
            assert not refused['success'] and not refused['may_have_partial_changes'],refused
        assert require(await edit('stable_after_refusals'))['after']==stable
        no_bom=require(await edit('standard_no_bom',['MCP_U1'],component_kind='Standard (No BOM)',include_in_bom=False))
        row=no_bom['after'][0]
        assert row['include_in_pick_place'] is None and row['pick_place_policy']=='depends_on_output_settings'
        assert any(child.get('net')=='MCP_TEST_NET' for child in row['children'])
        for component_kind in ('mechanical','net_tie_bom','net_tie_no_bom','standard'):
            result=require(await edit(component_kind,['MCP_U1'],component_kind=component_kind))
            assert result['after'][0]['component_kind']==component_kind
        plain=require(await edit('clear_graphical',['MCP_FID1'],graphical=False))
        assert plain['after'][0]['component_kind']=='standard'
        require(await edit('infer_graphical',['MCP_FID1'],include_in_bom=False,include_in_pick_place=False))
        final=require(await edit('read_after'))
        assert final['after'][0]['component_kind']==final['after'][1]['component_kind']=='graphical'
        assert final['after'][2]['component_kind']=='standard'
        report['checks_passed']=True
    except Exception as exc:report.update(checks_passed=False,error=str(exc) or repr(exc),outcome_unknown=unknown)
    finally:
        if not unknown:
            try:
                if opened:require(await call('discard_copy',main.close_document,str(scratch),require_clean=False));opened=False
                if previous and Path(previous).suffix.casefold() in {'.pcbdoc','.schdoc','.pcblib','.schlib'}:
                    require(await call('restore_editor',main.open_document,previous))
                if original:
                    require(await call('restore_project',main.select_project,original['project_path']))
                    report['original_states_unchanged']=require(await call('project_after',main.get_project_info))==original
            except Exception as exc:report['cleanup_error']=str(exc)
        report['disk_after']={str(p):disk_state(p) for p in guarded}
        report['disk_unchanged']=report.get('disk_before')==report['disk_after'];report['scratch_closed']=not opened
        (root/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=True),encoding='ascii')
        print('REPORT '+str(root/'report.json'),flush=True)
    assert all(report.get(k) for k in ('checks_passed','disk_unchanged','scratch_closed','original_states_unchanged')),report.get('error',report)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--exercise',action='store_true')
    parser.add_argument('--pcb',type=Path,default=Path(r'C:\workspace_altium\SQ-CARD\PCB.PcbDoc'))
    asyncio.run(run(parser.parse_args()))
