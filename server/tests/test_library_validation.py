"""Semantic validation cases and geometry boundaries; no live Altium calls."""

import asyncio
import copy
import json
from pathlib import Path, PureWindowsPath
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import library_validation as validation
from library_geometry import annular_ring, capsule, gap, rectangle


class LibraryValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name).resolve()
        self.paths=[str(root/'Parts_\u03a9.SchLib'),str(root/'Parts_\u0416.PcbLib')]
        for name in self.paths:
            Path(name).write_bytes(b'original')
        self.rules=copy.deepcopy(validation.DEFAULTS)
        self.params={name:'Actual data' for name in self.rules['required_parameters']}
        self.params['HelpURL']='https://example.com/datasheet'
        self.symbol=dict(library_path=self.paths[0],library_kind='SCHLIB',component_name='Symbol',
                         properties=dict(designator_template='J?',component_kind='standard',component_kind_code=0,description='Connector two pin header'),
                         parameter_records=[dict(name=k,value=v) for k,v in self.params.items()],
                         pins=[dict(pin_number=str(i),x=i*100,y=0,length=100,orientation=0,owner_part_id=1,display_mode=0) for i in (1,2)],
                         models=[dict(model_type='PCBLIB',model_name='FP',map_as_string='(1:1),(2:2)',pin_map={'1':['1'],'2':['2']},
                                      datafile_links=[dict(file_kind='PCBLIB',location=self.paths[1])])])
        self.footprint=dict(library_path=self.paths[1],library_kind='PCBLIB',component_name='FP',other_copper_count=0,
                            pad_details=[self.pad('1',0),self.pad('2',2)])

    def pad(self, name, x, layer='Top Layer', hole=0, plated=False):
        return dict(name=name,x_mm=x,y_mm=0,rotation=0,layer=layer,mode=0,plated=plated,
                    hole_size_mm=hole,hole_width_mm=0,hole_type=0,hole_rotation=0,
                    is_testpoint_top=False,is_testpoint_bottom=False,tenting_top=False,tenting_bottom=False,
                    settings=dict(mask_mode=2,paste_mode=2,mask_expansion_mm=.05,paste_expansion_mm=0),
                    stack=[dict(width_mm=1,height_mm=1,shape='rectangle') for _ in range(3)])

    def analyze(self, rows=None, footprints=None, rules=None, roles=None, cap=500):
        return validation.analyze(rows if rows is not None else [self.symbol,self.footprint],
                                  footprints if footprints is not None else [self.footprint],
                                  {**self.rules,**(rules or {})},roles or {},cap)

    def codes(self, result):
        return {i['code'] for row in result['components']+result['dependencies'] for i in row['issues']}

    def test_valid_library_and_off_page_dependency_geometry(self):
        self.assertTrue(self.analyze()['valid'])
        self.footprint['pad_details'][1]['x_mm']=1.02
        result=self.analyze(rows=[self.symbol])
        self.assertFalse(result['valid']);self.assertEqual(len(result['dependencies']),1)
        self.assertIn('pad_clearance',self.codes(result))

    def test_required_duplicate_parameters_and_grid(self):
        self.symbol['parameter_records'][0]['value']='TBD'
        self.symbol['parameter_records'].append(dict(name='MP',value='Other'))
        self.symbol['pins'][0]['x']=150
        result=self.analyze();codes=self.codes(result)
        self.assertTrue({'parameter_required','parameter_duplicate','pin_off_grid'}<=codes)
        # Reusing numbers/locations across sections or display modes is legitimate.
        self.symbol=copy.deepcopy(self.symbol)
        self.symbol['pins']=[dict(self.symbol['pins'][1],owner_part_id=p,display_mode=m) for p,m in ((1,0),(2,0),(1,1))]
        self.assertNotIn('pin_number_duplicate',self.codes(self.analyze()))

    def test_map_missing_unknown_shared_and_npth(self):
        self.symbol['models'][0].update(pin_map={'1':['2','N'],'3':['2','ABSENT']})
        self.footprint['pad_details'].append(self.pad('N',5,'Multi Layer',hole=.7))
        codes=self.codes(self.analyze())
        self.assertTrue({'pin_map_missing_pin','pin_map_unknown_pin','pin_map_shared_pad','pin_map_unknown_pad','pin_map_npth','pad_unmapped'}<=codes)

    def test_implicit_map_leading_zero_and_duplicate_physical_pads(self):
        model=self.symbol['models'][0];model.update(map_as_string='',pin_map={})
        self.assertTrue(self.analyze()['valid'])
        self.symbol['pins'][0]['pin_number']='01'
        self.assertIn('pin_map_unknown_pad',self.codes(self.analyze()))
        model.update(map_as_string='(01:1,A),(2:2)',pin_map={'01':['1','A'],'2':['2']})
        self.footprint['pad_details'].extend([self.pad('A',4),self.pad('A',6)])
        self.assertTrue(self.analyze()['valid'])

    def test_link_resolution_respects_library_not_arbitrary_name(self):
        model=self.symbol['models'][0]
        model['datafile_links'][0]['location']=r'C:\External\Other.PcbLib'
        result=self.analyze();self.assertIsNone(result['valid']);self.assertIn('footprint_unresolved',self.codes(result))
        model['datafile_links']=[]
        other=copy.deepcopy(self.footprint);other['library_path']=r'C:\Other\Parts.PcbLib'
        self.assertIsNone(self.analyze(footprints=[self.footprint,other])['valid'])

    def test_shield_and_npth_roles_are_not_signal_contacts(self):
        self.footprint['pad_details'] += [self.pad('SH1',5),self.pad('MH1',8,'Multi Layer',hole=.7)]
        result=self.analyze();self.assertIsNone(result['valid']);self.assertIn('shield_role_inferred',self.codes(result))
        roles={(*validation.identity(self.footprint),'sh1'):'shield'}
        result=self.analyze(roles=roles);self.assertTrue(result['valid'])
        self.assertEqual([p['role'] for p in result['components'][1]['pad_roles']],['electrical','electrical','shield','npth'])
        self.assertIn('pad_unmapped',self.codes(self.analyze(roles=roles,rules={'require_shield_mapping':True})))
        roles[(*validation.identity(self.footprint),'mh1')]='electrical'
        self.assertIn('pad_role_conflict',self.codes(self.analyze(roles=roles)))

    def test_mask_paste_rules_are_deferred_not_assumed(self):
        for pad in self.footprint['pad_details']:
            pad['settings'].update(mask_mode=1,paste_mode=1)
        result=self.analyze();self.assertIsNone(result['valid']);self.assertFalse(result['validation_complete'])
        self.assertTrue({'mask_rule_unresolved','paste_rule_unresolved'}<=self.codes(result))
        self.assertTrue(self.analyze(rules={'rule_mask_expansion_mm':.05,'rule_paste_expansion_mm':0})['valid'])
        self.footprint['pad_details'][0]['settings'].update(mask_mode=2,mask_cache_valid=1)
        self.assertIn('mask_cache_unresolved',self.codes(self.analyze()))

    def test_closed_aperture_web_paste_growth_and_dimensions(self):
        a,b=self.footprint['pad_details'];b['x_mm']=1.15
        a['settings']['paste_expansion_mm']=.2
        b['settings']['mask_expansion_mm']=-.6
        a['settings']['mask_expansion_mm']=.2
        codes=self.codes(self.analyze())
        self.assertTrue({'paste_expansion','mask_aperture_closed','paste_clearance'}<=codes)
        b['settings']['mask_expansion_mm']=.05
        self.assertIn('mask_clearance',self.codes(self.analyze()))
        a['stack'][0]['width_mm']=.01
        self.assertIn('pad_dimensions',self.codes(self.analyze()))

    def test_layers_npth_clearance_and_annular_ring(self):
        a,b=self.footprint['pad_details'];b.update(x_mm=0,layer='Bottom Layer')
        self.assertNotIn('pad_clearance',self.codes(self.analyze()))
        b.update(layer='Multi Layer',hole_size_mm=.95,plated=True)
        self.assertIn('annular_ring',self.codes(self.analyze()))
        b.update(plated=False,x_mm=.5)
        self.assertIn('npth_clearance',self.codes(self.analyze()))
        for side in b['stack']:
            side.update(width_mm=0,height_mm=0)
        self.assertNotIn('pad_dimensions',self.codes(self.analyze()))

    def test_conservative_shapes_and_custom_stacks_never_false_pass(self):
        a,b=self.footprint['pad_details'];a['stack'][0]['shape']='rounded_rectangle';b['x_mm']=1.01
        result=self.analyze();self.assertFalse(result['validation_complete']);self.assertIn('pad_clearance_unresolved',self.codes(result))
        a['mode']=2
        self.assertIn('full_stack_unchecked',self.codes(self.analyze()))
        self.footprint['other_copper_count']=1
        self.assertIn('nonpad_copper_unchecked',self.codes(self.analyze()))

    def test_issue_cap_preserves_analysis_and_counts(self):
        self.symbol['parameter_records']=[]
        full=self.analyze();capped=self.analyze(cap=1)
        self.assertEqual(full['issue_counts'],capped['issue_counts']);self.assertFalse(capped['valid'])
        self.assertTrue(capped['issues_truncated']);self.assertEqual(sum(len(r['issues']) for r in capped['components']),1)

    def test_geometric_edge_distances_rotation_and_slot_ring(self):
        self.assertAlmostEqual(gap(rectangle(0,0,2,1),rectangle(2,0,1,1)),.5)
        self.assertAlmostEqual(gap(capsule(0,0,1,1),capsule(2,0,1,1)),1)
        self.assertAlmostEqual(gap(rectangle(0,0,2,1,90),capsule(2,0,1,1)),1)
        self.assertAlmostEqual(gap(rectangle(0,0,2,.2,45),rectangle(0,1,2,.2,45)),2**-.5-.2)
        pad=self.pad('P',0,'Multi Layer',hole=.4,plated=True)
        pad.update(hole_type=2,hole_width_mm=.8,hole_rotation=90)
        self.assertAlmostEqual(annular_ring(pad,pad['stack'][0]),.1)
        self.footprint['pad_details'][1]['x_mm']=1.09997
        self.assertTrue(self.analyze(rules={'min_mask_web_mm':0,'min_paste_clearance_mm':0})['valid'])
        self.footprint['pad_details'][1]['x_mm']=1.0998
        self.assertFalse(self.analyze(rules={'min_mask_web_mm':0,'min_paste_clearance_mm':0})['valid'])

    def test_prepare_rejects_unknown_rules_roles_and_bad_bounds(self):
        bad=[({'unknown':1},None),({'pin_grid_mils':0},None),({'min_pad_size_mm':-1},None),
             ({'min_pad_size_mm':4,'max_pad_size_mm':1},None),({'required_parameters':['MP','mp']},None),
             ({'require_footprint':1},None),({},[dict(library_path=self.paths[0],footprint_name='FP',pad_name='1',role='shield')])]
        for rules,roles in bad:
            with self.subTest(rules=rules,roles=roles),self.assertRaises(ValueError):
                validation.prepare(self.paths,None,rules,roles,0,50,500)
        role=dict(library_path=self.paths[1],footprint_name='FP',pad_name='SH1',role='shield')
        paths,profile,roles=validation.prepare(self.paths,None,None,[role],0,50,500)
        self.assertEqual(roles[(PureWindowsPath(paths[1]),'fp','sh1')],{'role':'shield'})

    def test_intentional_paste_suppression_and_custom_windows(self):
        self.footprint['pad_details'][0]['settings']['paste_expansion_mm']=-1
        self.assertIn('paste_aperture_closed',self.codes(self.analyze()))
        self.footprint['pad_details'][0]['is_testpoint_top']=True
        self.assertTrue(self.analyze()['valid'])
        self.footprint['pad_details'][0]['is_testpoint_top']=False
        roles={(*validation.identity(self.footprint),'1'):{'role':'electrical','paste_required':False}}
        self.assertTrue(self.analyze(roles=roles)['valid'])
        self.footprint['other_aperture_count']=1
        result=self.analyze()
        self.assertIsNone(result['valid']);self.assertIn('paste_custom_apertures',self.codes(result))

    def read(self, rows, complete=True):
        return dict(success=True,components=rows,complete=complete,matched_count=len(rows),has_more=not complete,
                    next_offset=None if complete else 1,libraries=[dict(library_path=p,component_count=1) for p in self.paths],
                    view_restored=True,missing_names=[],verification={'passed':True})

    def test_transport_dependency_geometry_and_unknown_stop(self):
        calls=[]
        async def execute(*args,**kwargs):
            calls.append((args,kwargs))
            return json.dumps(self.read([self.symbol] if len(calls)==1 else [self.footprint]))
        with patch.object(validation.reader,'execute',execute):
            result=json.loads(asyncio.run(validation.execute(None,Path(self.temp.name),self.paths,['Symbol'])))
        self.assertTrue(result['valid']);self.assertEqual(result['native_call_count'],2)
        self.assertTrue(all(options['validation'] for _,options in calls))
        calls.clear()
        async def unknown(*args,**kwargs):
            calls.append(1);return json.dumps(dict(success=False,outcome_unknown=True,saved=False))
        with patch.object(validation.reader,'execute',unknown):
            result=json.loads(asyncio.run(validation.execute(None,Path(self.temp.name),self.paths)))
        self.assertTrue(result['outcome_unknown']);self.assertEqual(len(calls),1)

    def test_transport_paging_disk_guard_and_full_inventory(self):
        async def read(*args,**kwargs):
            return json.dumps(self.read([self.symbol,self.footprint]))
        with patch.object(validation.reader,'execute',read):
            result=json.loads(asyncio.run(validation.execute(None,Path(self.temp.name),self.paths)))
        self.assertTrue(result['valid']);self.assertEqual(result['native_call_count'],1)
        async def paged(*args,**kwargs):
            return json.dumps(self.read([self.footprint],False))
        with patch.object(validation.reader,'execute',paged):
            result=json.loads(asyncio.run(validation.execute(None,Path(self.temp.name),self.paths)))
        self.assertIsNone(result['valid']);self.assertFalse(result['complete'])
        async def changed(*args,**kwargs):
            Path(self.paths[0]).write_bytes(b'changed');return await read()
        with patch.object(validation.reader,'execute',changed):
            result=json.loads(asyncio.run(validation.execute(None,Path(self.temp.name),self.paths)))
        self.assertFalse(result['success']);self.assertFalse(result['verification']['disk_unchanged'])


if __name__=='__main__':
    unittest.main()
