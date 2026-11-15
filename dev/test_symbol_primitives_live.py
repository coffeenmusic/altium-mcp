"""Opt-in E08 label and existing graphic/pin edits on a disposable SchLib copy."""

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


def target(row):
    result={'type':row['type'],'owner_part_id':row['owner_part_id'],'display_mode':row['display_mode']}
    if row.get('unique_id'):
        result['unique_id']=row['unique_id']
    elif row['type']=='pin':
        result.update(pin_number=row['pin_number'],x=row['x'],y=row['y'])
    elif row['type']=='label':
        result.update(text=row['text'],x=row['x'],y=row['y'])
    else:
        import symbol_primitives as editor
        result.update({k:v for k,v in row.items() if k in editor.FIELDS[row['type']]})
    return result


async def run(args):
    if not args.exercise:
        print('Pass --exercise to edit a disposable library copy without saving.')
        return
    import main
    logging.getLogger().setLevel(logging.WARNING)
    root=REPO/'.altium-mcp'/('symbol_primitives_'+time.strftime('%Y%m%d_%H%M%S'))
    root.mkdir(parents=True)
    source=Path(args.library).resolve()
    scratch=root/'Primitives_\u03a9_\u0416.SchLib'
    shutil.copy2(source,scratch)
    report={'saved':False,'source':str(source),'scratch':str(scratch),'steps':[]}
    original=None
    guarded=[]
    opened=False
    unknown=False

    def record(label,data):
        (root/(label+'.json')).write_text(json.dumps(data,indent=2,ensure_ascii=True),encoding='ascii')

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
            raise RuntimeError('Altium not responsive; no further command sent')
        result=json.loads(await function(None,*values,**options))
        record(label,result)
        row={'label':label,**{k:result.get(k) for k in ('success','changed','updated_count','error','saved')}}
        report['steps'].append(row)
        print(json.dumps(row,ensure_ascii=True),flush=True)
        if result.get('outcome_unknown') or result.get('close_outcome_unknown'):
            unknown=True
            raise RuntimeError('Unknown executor completion; no further command sent')
        return result

    def require(value):
        assert value.get('success',True) and not value.get('error'),value
        assert value.get('saved',False) is False,value
        return value

    try:
        original=require(await call('project_before',main.get_project_info))
        guarded=list({source,scratch,Path(original['project_path']),*(Path(d['path']) for d in original['documents'])})
        report['disk_before']={str(p):disk_state(p) for p in guarded}
        record('disk_before',report['disk_before'])
        require(await call('open_scratch',main.open_document,str(scratch)))
        opened=True
        before=require(await call('read_before',main.update_symbol_primitives,str(scratch),args.symbol,[]))
        objects=before['objects']
        report['supported_types']=sorted({obj['type'] for obj in objects})
        labels=[row for row in objects if row['type']=='label']
        assert len(labels)>=2,'fixture needs E08 color labels on two parts'
        report['labels_before']=[{k:row.get(k) for k in ('text','owner_part_id','display_mode','unique_id')} for row in labels]
        edits=[{'target':target(row),'properties':{'text':row['text']+' MCP \u03a9\u0416',
                'x':row['x']+12.5,'y':row['y'],'font_size':row['font_size']+1,
                'color':0x0000FF if 'RED' in row['text'].upper() else 0x008000}} for row in labels]
        invalid=[edits[0],{'target':{'type':'label','unique_id':'MCP_MISSING_UID'},'properties':{'text':'Must not apply'}}]
        refused=await call('missing_target',main.update_symbol_primitives,str(scratch),args.symbol,invalid)
        assert refused['success'] is False and refused['updated_count']==0 and not refused['may_have_partial_changes']
        state=require(await call('after_refusal',main.update_symbol_primitives,str(scratch),args.symbol,[]))
        assert state['objects']==before['objects']
        duplicate=await call('duplicate_target',main.update_symbol_primitives,str(scratch),args.symbol,[edits[0],edits[0]])
        assert duplicate['success'] is False and duplicate['updated_count']==0
        ambiguous=await call('ambiguous_label',main.update_symbol_primitives,str(scratch),args.symbol,
                             [{'target':{'type':'label'},'properties':{'text':'Must not apply'}}])
        assert ambiguous['success'] is False and ambiguous['updated_count']==0
        applied=require(await call('edit_e08_labels',main.update_symbol_primitives,str(scratch),args.symbol,edits))
        assert applied['updated_count']==len(labels)
        report['both_parts_edited']=len({row['owner_part_id'] for row in applied['objects'] if row['type']=='label'})>=2
        repeated=[]
        for row in applied['objects']:
            if row['type']=='label':
                repeated.append({'target':target(row),'properties':{'text':row['text'],'x':row['x'],'y':row['y'],'font_size':row['font_size']}})
        repeated_result=require(await call('repeat_labels',main.update_symbol_primitives,str(scratch),args.symbol,repeated))
        assert repeated_result['updated_count']==0 and repeated_result['changed'] is False
        graphics=[]
        for kind in ('line','rectangle','polyline','polygon','arc','ellipse','pin'):
            row=next((r for r in applied['objects'] if r['type']==kind),None)
            if row is None:
                continue
            props={}
            if kind in {'line','rectangle'}:
                props={'x2':row['x2']+5,'y2':row['y2']}
            elif kind in {'polyline','polygon'}:
                vertices=[dict(p) for p in row['vertices']]
                vertices[0]['x']+=5
                props={'vertices':vertices}
            elif kind in {'arc','ellipse'}:
                props={'radius':row['radius']+5}
            elif kind=='pin':
                props={'pin_name':row['pin_name']+'_MCP','length':row['length']+10,'show_name':not row['show_name']}
            graphics.append({'target':target(row),'properties':props})
        require(await call('edit_graphics_and_pin',main.update_symbol_primitives,str(scratch),args.symbol,graphics))
        report['edited_types']=['label']+[row['target']['type'] for row in graphics]
        # Inspect the same native objects after editing; cleanup discards the copy.
        final=require(await call('read_final',main.update_symbol_primitives,str(scratch),args.symbol,[]))
        assert final['component_uid_after']==before['component_uid_before']
        assert final['metadata_after']==before['metadata_before']
        report['identity_metadata_verified']=True
        if args.fixtures:
            from altium_files import write_altium_text
            import symbol_metadata
            require(await call('placement_rules',main.get_symbol_placement_rules))
            fixture_name='MCP_PRIMITIVE_FIXTURE'
            metadata=before['metadata_before']
            parameters={k:metadata['parameters'].get(k,metadata['comment'])
                        for k in symbol_metadata.REQUIRED_PARAMETERS}
            records=['LIBRARY|'+str(scratch),'SYMBOL|'+fixture_name+'|'+metadata['description']+'|1']
            records += ['PARAM|'+k+'|'+v for k,v in parameters.items()]
            records += ['PIN|1|A|eElectricPassive|eRotate180|0|0|1|100|1|1']
            records += ['GRAPHIC|'+graphic for graphic in (
                'label|1|0|300|Fixture', 'line|1|1|0|0|100|0',
                'rectangle|1|1|0|0|0|100|100', 'polyline|1|1|0|0|50|50|100|0',
                'polygon|1|1|1|0|0|100|0|50|100', 'arc|1|1|0|0|100|0|180',
                'ellipse|1|1|0|0|0|100|50', 'elliptical_arc|1|1|0|0|100|50|0|180')]
            spec=root/'fixture.txt'
            write_altium_text(spec,'\n'.join(records)+'\n')
            require(await call('create_fixture',main.create_symbols_batch,str(spec)))
            fixture_before=require(await call('fixture_before',main.update_symbol_primitives,str(scratch),fixture_name,[]))
            props={
                'label':dict(text='',font_name='Arial',font_size=12,font_bold=True,font_italic=True,
                             font_underline=True,font_strikeout=True,orientation=1,justification=4),
                'line':dict(x1=5,y1=10,x2=105,y2=10,line_width=2,color=0x0000FF),
                'rectangle':dict(x1=10,y1=10,x2=110,y2=110,is_solid=True,area_color=0x00FF00),
                'polyline':dict(vertices=[dict(x=0,y=5),dict(x=50,y=55),dict(x=100,y=5),dict(x=120,y=5)]),
                'polygon':dict(vertices=[dict(x=0,y=5),dict(x=100,y=5),dict(x=100,y=105),dict(x=0,y=105)],
                               is_solid=False,area_color=0x0000FF),
                'arc':dict(cx=10,cy=20,radius=110,start_angle=15,end_angle=270),
                'ellipse':dict(cx=20,cy=30,radius=120,secondary_radius=60,is_solid=True,area_color=0xFF0000),
                'pin':dict(pin_name='',description='',x=10,y=10,length=150,orientation=1,
                           electrical=0,show_name=False,show_designator=False,is_hidden=True,color=0x008000),
            }
            fixture_edits=[{'target':{'type':kind},'properties':values} for kind,values in props.items()]
            fixture_after=require(await call('fixture_update',main.update_symbol_primitives,str(scratch),fixture_name,fixture_edits))
            assert fixture_after['updated_count']==len(props)
            noop=require(await call('fixture_noop',main.update_symbol_primitives,str(scratch),fixture_name,fixture_edits))
            assert noop['updated_count']==0
            assert fixture_after['unsupported_count']==fixture_before['unsupported_count']
            # Switching symbols must not revert edits in the previously active symbol.
            e08=require(await call('e08_after_fixture',main.update_symbol_primitives,str(scratch),args.symbol,[]))
            assert e08['objects']==final['objects'] and e08['metadata_after']==final['metadata_after']
            report['fixture_edited_types']=sorted(props)
            report['fixture_checks_passed']=True
        report['checks_passed']=report['both_parts_edited'] and 'pin' in report['edited_types']
    except Exception as exc:
        report.update(checks_passed=False,error=str(exc),outcome_unknown=unknown)
    finally:
        if not unknown:
            try:
                if opened:
                    require(await call('discard_scratch',main.close_document,str(scratch),require_clean=False))
                    opened=False
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
        report['scratch_closed']=not opened
        record('report',report)
        print('REPORT '+str(root/'report.json'),flush=True)
    assert report.get('checks_passed') and report.get('disk_unchanged') and report.get('original_states_unchanged') and report['scratch_closed'],report.get('error',report)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library',required=True)
    parser.add_argument('--symbol',required=True)
    parser.add_argument('--exercise',action='store_true')
    parser.add_argument('--fixtures',action='store_true',help='Also exercise all eight supported types on a temporary symbol')
    asyncio.run(run(parser.parse_args()))
