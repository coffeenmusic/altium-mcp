"""Existing SchLib object validation, preservation and transport regression tests."""

import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import symbol_primitives as editor


class SymbolPrimitivesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.library=self.root/'Parts_\u03a9.SchLib'
        self.library.write_bytes(b'fixture')
        self.objects=[dict(_id='101',type='label',unique_id='LABEL',owner_part_id=1,display_mode=0,
                           text='RED',x=0,y=100,color=128,font_name='Arial',font_size=10),
                      dict(_id='102',type='pin',unique_id='PIN',owner_part_id=1,display_mode=0,
                           pin_number='2',pin_name='A',x=-100,y=0,length=100,color=128),
                      dict(_id='103',type='unsupported',native_type=45)]
        self.edit={'target':{'type':'label','unique_id':'LABEL','owner_part_id':1},'properties':{'text':'Red'}}

    def native(self, edits):
        after=copy.deepcopy(self.objects)
        rows=[]
        for i,edit in enumerate(edits):
            obj=next(obj for obj in after if obj.get('unique_id')==edit['target']['unique_id'])
            obj.update(edit['properties'])
            rows.append({'index':i,'_id':obj['_id'],'unique_id':obj['unique_id']})
        changed=sum(a!=b for a,b in zip(self.objects,after))
        return dict(success=True,saved=False,library_path=str(self.library),symbol_name='LED',
                    metadata_before={'symbol_name':'LED','models':['Keep'],'part_count':2},
                    metadata_after={'symbol_name':'LED','models':['Keep'],'part_count':2},
                    component_uid_before='',component_uid_after='',_component_id_before='200',_component_id_after='200',
                    objects_before=copy.deepcopy(self.objects),objects_after=after,results=rows,
                    updated_count=changed,changed=bool(changed),was_modified=False,is_modified=bool(changed))

    def execute(self, result=None,response=None,edits=None,change_disk=False):
        edits=[self.edit] if edits is None else edits
        class Bridge:
            async def execute_command(inner,command,params):
                self.assertEqual(command,'update_symbol_primitives')
                self.request=Path(params['spec_file'])
                self.spec=self.request.read_text(encoding='utf-8-sig')
                if change_disk:
                    self.library.write_bytes(b'unexpected save')
                return response if response is not None else {'success':True,'result':json.dumps(result)}
        return json.loads(asyncio.run(editor.execute(Bridge(),self.root,str(self.library),'LED',edits)))

    def test_scope_and_unicode_transport(self):
        edit=copy.deepcopy(self.edit)
        edit['properties']['text']='Red | = \u0416\u03a9\nnext'
        result=self.execute(self.native([edit]),edits=[edit])
        self.assertTrue(result['success'])
        self.assertIn('\\nnext',self.spec)
        self.assertNotIn('_id',result['objects'][0])
        self.assertNotIn('_component_id_before',result)
        self.assertEqual(result['unsupported_count'],1)
        self.assertFalse(self.request.exists())

    def test_supported_geometry_and_style_fields(self):
        for kind,props in {
            'label':{'text':'Green','color':0x008000,'font_bold':True,'font_size':11},
            'line':{'x1':0,'y1':0,'x2':100,'y2':100,'line_width':1},
            'rectangle':{'is_solid':True,'area_color':0xFFFFFF},
            'polyline':{'vertices':[{'x':0,'y':0},{'x':10,'y':5}]},
            'polygon':{'vertices':[{'x':0,'y':0},{'x':10,'y':0},{'x':10,'y':5}]},
            'arc':{'radius':10,'start_angle':0,'end_angle':270},
            'ellipse':{'radius':10,'secondary_radius':5},
            'pin':{'pin_name':'ANODE','length':150,'show_name':True,'electrical':0},
        }.items():
            with self.subTest(kind=kind):
                spec=editor.prepare(str(self.library),'LED',[{'target':{'type':kind,'owner_part_id':1},'properties':props}])
                self.assertEqual(spec['count'],1)

    def test_rejects_unsafe_or_unsupported_edits(self):
        for props in ({'pin_number':'3'},{'unique_id':'NEW'},{'owner_part_id':2},{'x':5},
                      {'length':0},{'orientation':90},{'show_name':1}):
            with self.subTest(props=props),self.assertRaises(ValueError):
                editor.prepare(str(self.library),'LED',[{'target':{'type':'pin','pin_number':'2'},'properties':props}])
        for props in ({'font_size':0},{'color':-1},{'x':float('nan'),'y':0},{'unexpected':1}):
            with self.assertRaises(ValueError):
                editor.prepare(str(self.library),'LED',[{'target':{'type':'label'},'properties':props}])
        for edits in (None,[{'op':'add'}],[{'target':{'type':'bezier'},'properties':{'color':1}}],
                      [{'target':{'type':'label','_id':'101'},'properties':{'text':'X'}}]):
            with self.assertRaises(ValueError):
                editor.prepare(str(self.library),'LED',edits)

    def test_identity_preservation_and_read_mode(self):
        result=self.execute(self.native([]),edits=[])
        self.assertTrue(result['success'])
        self.assertFalse(result['changed'])
        self.assertEqual(result['component_uid_before'],'')

    def test_empty_strings_require_explicit_readback(self):
        edits=[{'target':{'type':'label','unique_id':'LABEL'},'properties':{'text':''}},
               {'target':{'type':'pin','unique_id':'PIN'},'properties':{'pin_name':'','description':''}}]
        editor.prepare(str(self.library),'LED',edits)
        native=self.native(edits)
        self.assertTrue(self.execute(native,edits=edits)['success'])
        del native['objects_after'][0]['text']
        self.assertFalse(self.execute(native,edits=edits)['success'])

    def test_noop_preserves_dirty_buffer(self):
        edit=copy.deepcopy(self.edit)
        edit['properties']['text']='RED'
        native=self.native([edit])
        native.update(was_modified=True,is_modified=True)
        self.assertTrue(self.execute(native,edits=[edit])['success'])
        native['is_modified']=False
        self.assertFalse(self.execute(native,edits=[edit])['success'])

    def test_readback_and_unrequested_object_changes(self):
        native=self.native([self.edit])
        cases=[]
        for key,value in (('unique_id','REPLACED'),('owner_part_id',2),('display_mode',1),('color',0)):
            changed=copy.deepcopy(native);changed['objects_after'][0][key]=value;cases.append(changed)
        for key,value in (('pin_number','3'),('symbol_inside','clock'),('pin_parameters',{'parameters':[]})):
            changed=copy.deepcopy(native);changed['objects_after'][1][key]=value;cases.append(changed)
        for changes in ({'component_uid_after':'NEW'},{'_component_id_after':'201'},
                        {'metadata_after':{'models':[]}},{'objects_after':native['objects_after'][:1]},
                        {'updated_count':0},{'is_modified':False},{'saved':True}):
            cases.append({**copy.deepcopy(native),**changes})
        changed=copy.deepcopy(native)
        for key in ('metadata_before','metadata_after'):
            changed[key]['symbol_name']='ANOTHER_SYMBOL'
        cases.append(changed)
        for case in cases:
            with self.subTest(case=case):
                self.assertFalse(self.execute(case)['success'])

    def test_disk_guard(self):
        result=self.execute(self.native([self.edit]),change_disk=True)
        self.assertFalse(result['success'])
        self.assertIn('library file changed unexpectedly',result['verification']['errors'])

    def test_failure_and_unknown_outcomes(self):
        failed=dict(success=False,error='Target must match exactly once',saved=False,updated_count=0,may_have_partial_changes=False)
        result=self.execute(response={'success':False,'result':failed})
        self.assertFalse(result['may_have_partial_changes'])
        self.assertFalse(self.request.exists())
        result=self.execute(response={'success':False,'error':'timeout'})
        self.assertTrue(result['outcome_unknown'])
        self.assertTrue(self.request.exists())
        self.request.unlink()
        result=self.execute(response={'success':False,'command_started':False,'error':'busy'})
        self.assertFalse(result['may_have_partial_changes'])
        self.assertFalse(self.request.exists())


if __name__=='__main__':
    unittest.main()
