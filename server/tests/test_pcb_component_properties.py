"""Placed PCB component properties: strict selection and honest report eligibility."""

import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pcb_component_properties as editor


class PCBComponentPropertiesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.pcb=self.root/'Board_\u03a9.PcbDoc';self.pcb.write_bytes(b'PCB fixture')
        self.calls=0

    def spec(self,**kwargs):return editor.prepare(str(self.pcb),['FID1'],**kwargs)

    def row(self,kind='standard'):
        bom,pick=editor.POLICY[kind]
        return dict(designator='FID1',object_address='123',component_kind=kind,component_kind_code=editor.CODES[kind],
                    graphical=kind=='graphical',include_in_bom=bom,include_in_pick_place=pick,
                    inclusion_source='component_kind_policy',
                    pick_place_policy='depends_on_output_settings' if pick is None else 'eligible' if pick else 'excluded',
                    x_coord=100,y_coord=200,rotation=90,layer_code=1,pattern='FIDUCIAL',source_unique_id='KEEP',
                    moveable=False,primitive_lock=True,children=[dict(object_address='456',net='',x=100,y=200,top_x=393701)])

    def native(self,old='standard',new='graphical'):
        return dict(success=True,saved=False,pcb_path=str(self.pcb),before=[self.row(old)],after=[self.row(new)],
                    changed=old!=new,updated_count=int(old!=new),was_modified=False,is_modified=old!=new)

    def execute(self,native=None,response=None,change_disk=False,**kwargs):
        class Bridge:
            async def execute_command(inner,command,params):
                self.calls+=1;self.assertEqual(command,'set_pcb_component_properties')
                self.request=Path(params['spec_file']);self.request_text=self.request.read_text(encoding='utf-8-sig')
                if change_disk:self.pcb.write_bytes(b'Unexpected save')
                return response if response is not None else {'result':json.dumps(native)}
        return json.loads(asyncio.run(editor.execute(Bridge(),self.root,str(self.pcb),['FID1'],**kwargs)))

    def test_type_aliases_and_contradictions_preflight_without_a_board(self):
        self.assertEqual(self.spec(component_kind='Standard (No BOM)')['component_kind'],'standard_no_bom')
        for kw in (dict(graphical=True,include_in_bom=True),dict(component_kind='mechanical',graphical=True),
                   dict(component_kind='standard_no_bom',include_in_pick_place=False),dict(graphical='true')):
            with self.subTest(kw=kw):
                result=self.execute(**kw);self.assertFalse(result['success']);self.assertFalse(result['may_have_partial_changes'])
        self.assertEqual(self.calls,0)

    def test_exact_designators_and_document_kind(self):
        for names in ([],['FID1','fid1'],['*'],['FID?'],[None],['A\nB']):
            with self.assertRaises(ValueError):editor.prepare(str(self.pcb),names)
        for path in ('relative.PcbDoc',str(self.pcb.with_suffix('.PcbLib')),str(self.root/'Symbols.SchLib')):
            with self.assertRaises(ValueError):editor.prepare(path,['FID1'])
        self.pcb.unlink()
        self.assertEqual(self.spec()['pcb_path'],str(self.pcb))

    def test_resolver_cannot_create_net_ties_or_independent_unsupported_flags(self):
        self.assertEqual(editor.resolve('standard',self.spec(include_in_bom=False,include_in_pick_place=False)),'graphical')
        self.assertEqual(editor.resolve('graphical',self.spec(include_in_bom=True,include_in_pick_place=False)),'mechanical')
        self.assertEqual(editor.resolve('graphical',self.spec(graphical=False)),'standard')
        self.assertEqual(editor.resolve('mechanical',self.spec(graphical=False)),'mechanical')
        self.assertEqual(editor.resolve('net_tie_no_bom',self.spec(include_in_bom=False,include_in_pick_place=False)),'net_tie_no_bom')
        for kw in (dict(include_in_bom=False,include_in_pick_place=True),dict(include_in_bom=False)):
            with self.assertRaises(ValueError):editor.resolve('standard',self.spec(**kw))

    def test_native_graphical_readback_and_unsaved_transport(self):
        result=self.execute(self.native(),graphical=True,include_in_bom=False,include_in_pick_place=False)
        self.assertTrue(result['success']);self.assertTrue(result['verification']['passed'])
        self.assertIn('graphical=1',self.request_text);self.assertFalse(self.request.exists())
        self.assertFalse(result['after'][0]['include_in_bom']);self.assertFalse(result['after'][0]['include_in_pick_place'])

    def test_conditional_pick_place_is_null_and_explicit(self):
        result=self.execute(self.native(new='standard_no_bom'),component_kind='standard_no_bom')
        self.assertTrue(result['success']);self.assertIsNone(result['after'][0]['include_in_pick_place'])
        bad=self.native(new='standard_no_bom');bad['after'][0]['include_in_pick_place']=True
        self.assertFalse(self.execute(bad,component_kind='standard_no_bom')['success'])

    def test_geometry_nets_source_links_and_placement_locks_are_preserved(self):
        for field,value in [('x_coord',101),('rotation',0),('pattern','OTHER'),('source_unique_id','OTHER'),('moveable',True)]:
            native=self.native();native['after'][0][field]=value
            with self.subTest(field=field):self.assertFalse(self.execute(native,graphical=True)['success'])
        native=self.native();native['after'][0]['children'][0]['net']='CHANGED'
        self.assertFalse(self.execute(native,graphical=True)['success'])

    def test_read_and_identical_kind_preserve_modified(self):
        for options in ({},{'graphical':True}):
            native=self.native('graphical','graphical');native.update(was_modified=True,is_modified=True)
            self.assertTrue(self.execute(native,**options)['success'])
            native['is_modified']=False
            self.assertFalse(self.execute(native,**options)['success'])

    def test_malformed_identity_counts_flags_and_unexpected_save_fail_verification(self):
        for changes in ({'pcb_path':str(self.root/'Other.PcbDoc')},{'updated_count':True},{'saved':True},{'is_modified':False}):
            native={**self.native(),**changes}
            self.assertFalse(self.execute(native,graphical=True)['success'])
        native=self.native();native['after'][0]['designator']='FID2'
        self.assertFalse(self.execute(native,graphical=True)['success'])
        self.assertFalse(self.execute(self.native(),graphical=True,change_disk=True)['success'])

    def test_failures_and_unknown_completion_keep_evidence_without_blind_retries(self):
        result=self.execute(response={'result':dict(success=False,saved=False,error='Missing or ambiguous',may_have_partial_changes=False)},graphical=True)
        self.assertFalse(result['may_have_partial_changes']);self.assertFalse(self.request.exists())
        for kw in ({},{'graphical':True}):
            result=self.execute(response={'error':'timeout'},**kw)
            self.assertTrue(result['outcome_unknown']);self.assertEqual(result['may_have_partial_changes'],bool(kw))
            self.assertTrue(self.request.exists());self.request.unlink()
        result=self.execute(response={'command_started':False,'error':'Busy'},graphical=True)
        self.assertFalse(result['may_have_partial_changes']);self.assertFalse(self.request.exists())


if __name__=='__main__':unittest.main()
