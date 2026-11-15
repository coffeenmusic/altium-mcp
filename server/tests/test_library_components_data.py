"""Batch library fields, mixed kinds, pagination, preservation and transport."""

import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import library_components_data as reader


class LibraryComponentsDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.paths = [self.root/'Parts_\u03a9.SchLib', self.root/'Parts_\u0416.PcbLib']
        for path in self.paths:
            path.write_bytes(b'original')
        self.metadata = dict(symbol_name='LED',designator_template='D?',designator_read_only_state=1,
                             component_kind='standard_no_bom',component_kind_code=5,part_count=2,
                             comment='LED',description='Dual LED',parameters={'Value':'Red / Green'},
                             models=[dict(model_name='FP',model_type='PCBLIB',map_as_string='(01:1,2),(2:3)',datafile_links=[])])
        self.sch = dict(library_index=0,component_name='LED',symbol_data=self.metadata,display_mode_count=1,
                        current_part_id=2,display_mode=0,pin_count=2,
                        pins=[dict(_id=str(n),type='pin',unique_id='P'+str(n),pin_number='01',pin_name='A',
                                   owner_part_id=n,display_mode=0,x=-100,y=0,pin_parameters={}) for n in (1,2)])
        self.pcb = dict(library_index=1,component_name='FP',properties={'description':'USB','height_mm':3.26},pad_count=2,
                        pads=[dict(name='01',layer='Multi-Layer',is_testpoint_top=v,is_testpoint_bottom=True) for v in (False,True)])

    def native(self):
        libraries = [dict(index=i,library_path=str(p),kind='SCHLIB' if i==0 else 'PCBLIB',success=True,
                          component_count=1,matched_count=1,was_modified=True,is_modified=True,
                          active_before='Keep',active_after='Keep',part_before=2,part_after=2,
                          display_before=0,display_after=0) for i,p in enumerate(self.paths)]
        return dict(success=True,saved=False,libraries=libraries,rows=copy.deepcopy([self.sch,self.pcb]),
                    matched_count=2,page_count=2,found_names=['LED','FP'],library_errors=[],component_errors=[],
                    view_restored=True,previous_document_path=str(self.root/'Original.SchDoc'),
                    active_document_path=str(self.root/'Original.SchDoc'))

    def execute(self, native=None, response=None, disk_change=False, **options):
        class Bridge:
            async def execute_command(inner, command, params):
                self.assertEqual(command,'get_library_components_data')
                self.request=Path(params['spec_file'])
                self.spec=self.request.read_text(encoding='utf-8-sig')
                if disk_change:
                    self.paths[1].write_bytes(b'unexpected save')
                return response if response is not None else {'result':json.dumps(native)}
        return json.loads(asyncio.run(reader.execute(Bridge(),self.root,[str(p) for p in self.paths],**options)))

    def test_mixed_batch_full_fields_and_native_relations(self):
        result=self.execute(self.native(),fields=list(reader.FIELDS))
        self.assertTrue(result['success']);self.assertTrue(result['complete'])
        a,b=result['components']
        self.assertEqual(a['models'][0]['pin_map'],{'01':['1','2'],'2':['3']})
        self.assertEqual(a['properties']['component_kind'],'standard_no_bom')
        self.assertEqual([p['owner_part_id'] for p in a['pins']],[1,2])
        self.assertNotIn('_id',a['pins'][0]);self.assertEqual(a['pins'][0]['parameters'],[])
        self.assertEqual(b['properties']['height_mm'],3.26)
        self.assertEqual([p['is_testpoint_top'] for p in b['pads']],[False,True])
        self.assertEqual(len(b['pads']),2)  # Duplicate physical pad names remain separate.
        self.assertNotIn('parameters',b)
        self.assertIn('pads',result['libraries'][0]['unsupported_fields'])
        self.assertNotIn('rows',result);self.assertNotIn('found_names',result)
        self.assertFalse(self.request.exists());self.assertIn('\u03a9',self.spec)

    def test_default_fields_avoid_pin_pad_response_bulk(self):
        result=self.execute(self.native())
        self.assertEqual(result['fields'],list(reader.DEFAULT_FIELDS))
        self.assertNotIn('pins',result['components'][0]);self.assertNotIn('pads',result['components'][1])
        self.assertIn('field.pins=0',self.spec)

    def test_pagination_and_explicit_missing_names(self):
        native=self.native();native.update(rows=native['rows'][:1],page_count=1)
        page=self.execute(native,limit=1)
        self.assertTrue(page['has_more']);self.assertEqual(page['next_offset'],1);self.assertFalse(page['complete'])
        native=self.native();native.update(rows=native['rows'][1:],page_count=1)
        page=self.execute(native,offset=1,limit=1)
        self.assertFalse(page['has_more']);self.assertTrue(page['complete'])
        native=self.native();native.update(rows=native['rows'][:1],matched_count=1,page_count=1,found_names=['LED'])
        result=self.execute(native,component_names=['LED','MISSING'])
        self.assertTrue(result['success']);self.assertEqual(result['missing_names'],['MISSING']);self.assertFalse(result['complete'])

    def test_invalid_requests_and_named_unsaved_paths(self):
        reader.prepare(str(self.root/'Unsaved.SchLib'))
        cases=[([],{}),([str(self.paths[0]),str(self.paths[0])],{}),(['relative.SchLib'],{}),
               ([str(self.paths[0])],{'fields':[]}),([str(self.paths[0])],{'fields':['geometry']}),
               ([str(self.paths[0])],{'component_names':['A','a']}),([str(self.paths[0])],{'component_names':[]}),
               ([str(self.paths[0])],{'component_names':['bad\nname']}),([str(self.paths[0])],{'limit':True}),
               ([str(self.paths[0])],{'offset':-1})]
        for paths,options in cases:
            with self.subTest(paths=paths,options=options),self.assertRaises(ValueError):
                reader.prepare(paths,**options)

    def test_identity_modified_selection_view_and_schema_checks(self):
        cases=[]
        for key,value in (('library_path',str(self.root/'Wrong.SchLib')),('is_modified',False),
                          ('active_after','Wrong'),('part_after',1),('display_after',1)):
            native=self.native();native['libraries'][0][key]=value;cases.append(native)
        for values in ({'saved':True},{'view_restored':False},{'page_count':1},{'rows':['invalid']},
                       {'active_document_path':str(self.root/'Wrong.SchDoc')}):
            cases.append({**self.native(),**values})
        native=self.native();native['rows'][0]['symbol_data']['symbol_name']='Wrong';cases.append(native)
        native=self.native();del native['rows'][1]['pads'][0]['is_testpoint_bottom'];cases.append(native)
        for native in cases:
            with self.subTest(native=native):
                self.assertFalse(self.execute(native,fields=list(reader.FIELDS))['success'])

    def test_partial_row_errors_keep_other_results(self):
        native=self.native();native['success']=False;native['rows']=native['rows'][:1]
        native['component_errors']=[{'library_index':1,'component_name':'FP','error':'unreadable'}]
        result=self.execute(native)
        self.assertFalse(result['success']);self.assertEqual(len(result['components']),1)
        self.assertEqual(result['component_errors'][0]['error'],'unreadable')
        self.assertEqual(result['verification']['errors'],[])

    def test_file_guard_and_uncertain_completion(self):
        self.assertFalse(self.execute(self.native(),disk_change=True)['success'])
        result=self.execute(response={'error':'timeout'})
        self.assertTrue(result['outcome_unknown']);self.assertTrue(self.request.exists());self.request.unlink()
        result=self.execute(response={'error':'busy','command_started':False})
        self.assertFalse(result['may_have_view_changes']);self.assertFalse(self.request.exists())
        result=self.execute(response={'result':dict(success=False,error='Schematic transaction is active',saved=False,rows=[],libraries=[])})
        self.assertEqual(result['error'],'Schematic transaction is active');self.assertFalse(self.request.exists())


if __name__=='__main__':
    unittest.main()
