"""Persistence policy, semantic comparisons, STEP evidence and failure handling."""

import asyncio
import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib
import pythoncom
from win32com import storagecon as sc

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import library_roundtrip as rt
import library_roundtrip_files as assets
from server.tests.test_pcblib_file import body,write_library,CREATE


class RoundtripTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name).resolve()
        self.paths=[str(self.folder/'Round_\u03a9.SchLib'),str(self.folder/'Round_\u0416.PcbLib')]
        for name in self.paths:
            Path(name).write_bytes(b'original')
        self.content=dict(graphics=dict(library_name='Name',symbols=[dict(symbol_name='LED',parameters=[dict(name='MP',value='Exact')],
                            primitives=[dict(type='pin',pin_number='01',x=0,y=0,length=100,orientation=0)])]),
                          members=[dict(component_name='LED',metadata=dict(parameters={'_id':'parameter','library_path':'value'},
                            models=[dict(model_type='PCBLIB',model_name='FP',map_as_string='(01:1,2),(3:4)')]),
                            supplement=[dict(type='pin',_id='0',unique_id='TEMP',pin_number='01',x=0)])])

    def native(self, pcb=False, dirty=False):
        content=dict(graphics=dict(footprints=[dict(footprint_name='FP',height_mm=1,primitives=[],bodies_3d=[])])) if pcb else self.content
        index=int(pcb)
        return dict(success=True,save_requested=False,saved=False,started=True,saved_paths=[],save_attempted_paths=[],reopened_paths=[self.paths[index]],
                    view_restored=True,selections_restored=True,project_restored=True,unrelated_documents_preserved=True,restoration_errors=[],
                    libraries=[dict(library_path=self.paths[index],kind='PCBLIB' if pcb else 'SCHLIB',was_open=True,is_open=True,
                                    was_modified=dirty,is_modified=False,before=copy.deepcopy(content),after=copy.deepcopy(content))])

    def execute(self, native=None, response=None, save=False, pcb=False, change=None, **kwargs):
        path=self.paths[int(pcb)]
        class Bridge:
            async def execute_command(inner,command,params):
                self.assertEqual(command,'verify_library_roundtrip')
                self.request=Path(params['spec_file'])
                self.spec=self.request.read_text(encoding='utf-8-sig')
                if change:
                    Path(path).write_bytes(change)
                return response if response is not None else {'result':native}
        result=json.loads(asyncio.run(rt.execute(Bridge(),self.folder,path,save=save,**kwargs)))
        if result.get('report_path'):
            report_root=Path(result['report_path']).parent
            allowed=Path(rt.__file__).resolve().parents[1]/'.altium-mcp'/'roundtrip_runs'
            self.assertTrue(report_root.resolve().is_relative_to(allowed.resolve()))
            self.assertNotEqual(report_root.resolve(),allowed.resolve())
            self.addCleanup(lambda: __import__('shutil').rmtree(report_root))
        return result

    def test_default_reopen_and_no_file_writes(self):
        result=self.execute(self.native())
        self.assertTrue(result['success']);self.assertTrue(result['equivalent']);self.assertFalse(result['saved'])
        self.assertEqual(result['difference_count'],0);self.assertEqual(result['backups'],{})
        self.assertIn('save=0',self.spec);self.assertFalse(self.request.exists())
        self.assertTrue(Path(result['report_path']).exists())

    def test_explicit_save_backup_and_dirty_nonstep_library(self):
        native=self.native(dirty=True);native.update(save_requested=True,saved=True,saved_paths=[self.paths[0]],save_attempted_paths=[self.paths[0]])
        result=self.execute(native,save=True,change=b'persisted')
        self.assertTrue(result['equivalent']);self.assertTrue(result['saved'])
        self.assertEqual(Path(result['backups'][self.paths[0]]).read_bytes(),b'original')
        self.assertIn('save=1',self.spec)

    def test_no_save_refusal_is_known_not_unknown_completion(self):
        native=dict(success=False,saved=False,started=False,error='save=False requires saved clean libraries',libraries=[],
                    saved_paths=[],save_attempted_paths=[],reopened_paths=[],view_restored=True,selections_restored=True,unrelated_documents_preserved=True)
        result=self.execute(native)
        self.assertFalse(result['success']);self.assertIsNone(result['equivalent']);self.assertNotIn('outcome_unknown',result)
        self.assertIn('clean',result['error']);self.assertFalse(self.request.exists())

    def test_map_order_iterators_and_temporary_uids_are_ignored(self):
        a=copy.deepcopy(self.content);b=copy.deepcopy(a)
        b['members'][0]['metadata']['models'][0]['map_as_string']='(3:4),(01:2,1)'
        b['members'][0]['supplement'][0].update(_id='99',unique_id='REOPENED')
        b['graphics']['library_name']='Different display label'
        self.assertEqual(rt.differences(a,b,0,10)[1],0)
        b['members'][0]['metadata']['parameters']['_id']='Changed parameter'
        self.assertEqual(rt.differences(a,b,0,10)[1],1)

    def test_vertex_order_and_duplicate_object_multiplicity_survive(self):
        a={'primitives':[dict(type='polygon',vertices=[{'x':0,'y':0},{'x':10,'y':0},{'x':10,'y':5}])]}
        b=copy.deepcopy(a);b['primitives'][0]['vertices'].reverse()
        self.assertGreater(rt.differences(a,b,0,10)[1],0)
        b=copy.deepcopy(a);b['primitives']*=2
        self.assertGreater(rt.differences(a,b,0,10)[1],0)

    def test_numeric_tolerance_applies_to_geometry_not_enums_or_parameters(self):
        self.assertEqual(rt.differences({'x':0,'height_mm':1},{'x':.0009,'height_mm':1.00001},.001,10)[1],0)
        self.assertEqual(rt.differences({'orientation':0},{'orientation':1},1,10)[1],1)
        self.assertEqual(rt.differences({'parameters':{'x':'0'}},{'parameters':{'x':'0.0001'}},1,10)[1],1)
        self.assertEqual(rt.differences({'rotation_x':0},{'rotation_x':.1},1,10)[1],1)

    def test_differences_are_counted_beyond_output_cap(self):
        native=self.native();native['libraries'][0]['after']['graphics']['symbols'][0]['primitives'][0].update(x=50,y=100,length=200)
        result=self.execute(native,max_differences=1)
        self.assertTrue(result['success']);self.assertFalse(result['equivalent']);self.assertEqual(result['difference_count'],3)
        self.assertTrue(result['differences_truncated']);self.assertEqual(len(result['libraries'][0]['differences']),1)

    def test_unknown_geometry_never_gives_full_equivalence(self):
        native=self.native();native['libraries'][0]['before']['graphics']['symbols'][0]['primitives'].append({'type':'unknown','object_id':999})
        native['libraries'][0]['after']=copy.deepcopy(native['libraries'][0]['before'])
        result=self.execute(native)
        self.assertIsNone(result['equivalent']);self.assertFalse(result['coverage_complete'])
        self.assertTrue(rt.gaps({'type':'pad','mode':2}));self.assertTrue(rt.gaps({'type':'region','hole_count':1}))

    def test_step_dirty_buffer_is_distinct_from_saved_file_evidence(self):
        native=self.native(pcb=True,dirty=True);native.update(saved=True,save_requested=True,saved_paths=[self.paths[1]],save_attempted_paths=[self.paths[1]])
        for side in ('before','after'):
            native['libraries'][0][side]['graphics']['footprints'][0]['bodies_3d']=[dict(model_file='m.step',model_embedded=True)]
        state=dict(readable=True,footprints={'FP':[dict(model_name='m.step',embedded=True,rotation_x=0,rotation_y=0,rotation_z=0,model_z_offset=0,
                                                      payload={'sha256':'a','size_bytes':1,'encoding':'zlib'})]})
        with patch.object(rt,'saved_step_state',return_value=state):
            result=self.execute(native,save=True,pcb=True)
        self.assertTrue(result['success']);self.assertIsNone(result['equivalent']);self.assertFalse(result['coverage_complete'])
        self.assertIn('dirty native buffer',str(result['libraries'][0]['coverage_gaps']))

    def test_step_payload_and_placement_changes_are_detected(self):
        native=self.native(pcb=True)
        for side in ('before','after'):
            native['libraries'][0][side]['graphics']['footprints'][0]['bodies_3d']=[dict(model_file='m.step',model_embedded=True)]
        a=dict(readable=True,footprints={'FP':[dict(model_name='m.step',embedded=True,rotation_x=0,rotation_y=0,rotation_z=0,model_z_offset=0,
                                                  payload={'sha256':'a','size_bytes':10,'encoding':'zlib'})]})
        b=copy.deepcopy(a);b['footprints']['FP'][0].update(rotation_z=90);b['footprints']['FP'][0]['payload']['sha256']='b'
        with patch.object(rt,'saved_step_state',side_effect=[a,b]):
            result=self.execute(native,pcb=True)
        self.assertFalse(result['equivalent']);self.assertEqual(result['difference_count'],2)
        self.assertTrue(all(d['scope']=='STEP' for d in result['libraries'][0]['differences']))

    def test_protocol_rejects_saving_by_default_wrong_identity_and_disk_changes(self):
        native=self.native();native.update(saved=True,saved_paths=[self.paths[0]],save_attempted_paths=[self.paths[0]])
        self.assertFalse(self.execute(native)['success'])
        native=self.native();native['libraries'][0]['library_path']=self.paths[1]
        self.assertFalse(self.execute(native)['success'])
        self.assertFalse(self.execute(self.native(),change=b'unexpected save')['success'])

    def test_incomplete_transport_stops_and_retains_evidence(self):
        result=self.execute(response={'error':'timeout'},save=True)
        self.assertTrue(result['outcome_unknown']);self.assertTrue(result['save_outcome_unknown']);self.assertTrue(self.request.exists())
        result=self.execute(response={'error':'busy','command_started':False})
        self.assertFalse(result['outcome_unknown']);self.assertFalse(self.request.exists())

    def test_invalid_inputs_do_not_authorize_saves(self):
        cases=[([],False,100,.001),(self.paths,1,100,.001),([self.paths[0]]*2,False,100,.001),
               (self.paths,False,0,.001),(self.paths,False,100,float('nan')),(self.paths,False,100,-1)]
        for args in cases:
            with self.subTest(args=args),self.assertRaises(ValueError): rt.prepare(*args)

    def test_saved_step_parsing_hashes_uncompressed_payload_and_missing_evidence(self):
        source=b'ISO-10303-21;\nDATA;\nENDSEC;'
        self.assertEqual(assets.model_digest(zlib.compress(source,1))['sha256'],assets.model_digest(zlib.compress(source,9))['sha256'])
        raw=b'ID=model|EMBED=TRUE\x00';self.assertEqual(assets.records(struct.pack('<I',len(raw))+raw)[0]['ID'],'model')
        with self.assertRaises(ValueError): assets.records(b'\xff\x00')
        self.assertTrue(rt.step_gaps({'readable':False},1));self.assertEqual(rt.step_gaps({},0),[])
        self.assertFalse(assets.saved_step_state(self.folder/'Missing.PcbLib')['readable'])

    def test_saved_embedded_step_association_and_placement(self):
        path=Path(self.paths[1]);write_library(path,{'FP':('FP',body('model.step',rotx='90.000'))})
        root=pythoncom.StgOpenStorage(str(path),None,sc.STGM_READWRITE|sc.STGM_SHARE_EXCLUSIVE)
        lib=root.CreateStorage('Library',CREATE,0,0);models=lib.CreateStorage('Models',CREATE,0,0)
        raw=b'ID={81E1FACD-F016-4B2C-BF65-3CCE43694708}|EMBED=TRUE\x00'
        stream=models.CreateStream('Data',CREATE,0,0);stream.Write(struct.pack('<I',len(raw))+raw);stream=None
        stream=models.CreateStream('0',CREATE,0,0);stream.Write(zlib.compress(b'ISO-10303-21; MODEL'));stream=None
        models.Commit(0);models=None;lib.Commit(0);lib=None;root.Commit(0);root=None
        state=assets.saved_step_state(path)
        self.assertTrue(state['readable']);record=state['footprints']['FP'][0]
        self.assertEqual(record['rotation_x'],90)
        self.assertEqual(record['payload']['sha256'],assets.model_digest(zlib.compress(b'ISO-10303-21; MODEL'))['sha256'])

    def test_saved_external_step_and_unicode_field_decoding(self):
        path=Path(self.paths[1]);write_library(path,{'FP':('FP',body('local.step',embed='FALSE'))})
        root=pythoncom.StgOpenStorage(str(path),None,sc.STGM_READWRITE|sc.STGM_SHARE_EXCLUSIVE)
        lib=root.CreateStorage('Library',CREATE,0,0);lib.Commit(0);lib=None;root.Commit(0);root=None
        (self.folder/'local.step').write_bytes(b'ISO-10303-21; EXTERNAL')
        state=assets.saved_step_state(path);self.assertTrue(state['readable'])
        self.assertEqual(state['footprints']['FP'][0]['payload']['encoding'],'external')
        encoded='Model_\u03a9\u0416.step'.encode('utf8').decode('latin1')
        self.assertEqual(assets.stored_text({'%UTF8%MODEL.NAME':encoded},'MODEL.NAME'),'Model_\u03a9\u0416.step')


if __name__=='__main__':
    unittest.main()
