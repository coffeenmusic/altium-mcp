"""Pad testpoint selection, independent flags, preservation and transport checks."""

import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import footprint_editor as editor


class FootprintPadPropertiesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.library=self.root/'Pads_\u03a9.PcbLib'
        self.library.write_bytes(b'fixture')
        self.before=[dict(name='1',is_testpoint_top=False,is_testpoint_bottom=True,locked=True,
                          layer='Multi-Layer',mask_settings={'mask_mode':2,'paste_mode':2}),
                     dict(name='2',is_testpoint_top=True,is_testpoint_bottom=False,locked=True,
                          layer='Multi-Layer',mask_settings={'mask_mode':1,'paste_mode':2})]

    def spec(self,**options):
        return editor.prepare_pad_properties(str(self.library),'DEBUG',['1','2'],**options)

    def native(self,**edits):
        after=copy.deepcopy(self.before)
        for row in after:
            row.update(edits)
        changed=sum(a!=b for a,b in zip(self.before,after))
        return dict(success=True,saved=False,library_path=str(self.library),footprint_name='DEBUG',
                    before=copy.deepcopy(self.before),after=after,verified_count=2,updated_count=changed,
                    changed=bool(changed),was_modified=False,is_modified=bool(changed))

    def execute(self,native=None,response=None,change_disk=False,**edits):
        class Bridge:
            async def execute_command(inner,command,params):
                self.assertEqual(command,'set_footprint_pad_properties')
                self.request=Path(params['spec_file'])
                self.transport=self.request.read_text(encoding='utf-8-sig')
                if change_disk:
                    self.library.write_bytes(b'unexpected save')
                return response if response is not None else {'success':True,'result':json.dumps(native)}
        return json.loads(asyncio.run(editor.execute(Bridge(),self.root,'set_footprint_pad_properties',
                         editor.prepare_pad_properties,str(self.library),'DEBUG',['1','2'],**edits)))

    def test_independent_flags_and_read_mode(self):
        spec=self.spec(is_testpoint_top=True)
        self.assertEqual(spec['is_testpoint_top'],'1')
        self.assertNotIn('is_testpoint_bottom',spec)
        self.assertEqual(self.spec(is_testpoint_bottom=False)['is_testpoint_bottom'],'0')
        self.assertNotIn('is_testpoint_top',self.spec())
        self.assertEqual(self.spec()['count'],'2')

    def test_invalid_flags_and_selection(self):
        for value in (0,1,'true',[],{},2.3):
            for key in ('is_testpoint_top','is_testpoint_bottom'):
                with self.subTest(key=key,value=value),self.assertRaises(ValueError):
                    self.spec(**{key:value})
        for names in ([],['1','1'],['A','a'],[1],[''],[str(n) for n in range(1001)]):
            with self.assertRaises(ValueError):
                editor.prepare_pad_properties(str(self.library),'DEBUG',names,True)

    def test_roundtrip_top_preserves_bottom(self):
        native=self.native(is_testpoint_top=True)
        result=self.execute(native,is_testpoint_top=True)
        self.assertTrue(result['success'])
        self.assertEqual(result['updated_count'],1)
        self.assertIn('is_testpoint_top=1\n',self.transport)
        self.assertNotIn('is_testpoint_bottom=',self.transport)
        self.assertFalse(self.request.exists())

    def test_both_sides_and_clearing(self):
        for edits in ({'is_testpoint_top':True,'is_testpoint_bottom':True},
                      {'is_testpoint_top':False,'is_testpoint_bottom':False},
                      {'is_testpoint_bottom':False}):
            with self.subTest(edits=edits):
                self.assertTrue(self.execute(self.native(**edits),**edits)['success'])

    def test_read_noop_preserves_modified(self):
        native=self.native()
        native.update(was_modified=True,is_modified=True)
        self.assertTrue(self.execute(native)['success'])
        native['is_modified']=False
        self.assertFalse(self.execute(native)['success'])

    def test_rejects_unrequested_changes_flags_and_wrong_targets(self):
        native=self.native(is_testpoint_top=True)
        cases=[]
        for key,value in (('is_testpoint_bottom',False),('locked',False),('mask_settings',{}),('name','3'),('is_testpoint_top',False)):
            changed=copy.deepcopy(native)
            changed['after'][0][key]=value
            cases.append(changed)
        cases.extend([{**native,'updated_count':2},{**native,'is_modified':False},{**native,'verified_count':1},
                      {**native,'after':native['after'][:1]},{**native,'library_path':str(self.root/'Other.PcbLib')}])
        for changed in cases:
            with self.subTest(changed=changed):
                self.assertFalse(self.execute(changed,is_testpoint_top=True)['success'])

    def test_disk_guard(self):
        result=self.execute(self.native(is_testpoint_bottom=True),is_testpoint_bottom=True,change_disk=True)
        self.assertFalse(result['success'])
        self.assertIn('library file changed unexpectedly',result['verification']['errors'])

    def test_failure_and_read_timeout_semantics(self):
        native=dict(success=False,error='Selector missing or ambiguous',saved=False,may_have_partial_changes=False)
        result=self.execute(native,response={'success':False,'result':native},is_testpoint_top=True)
        self.assertFalse(result['may_have_partial_changes'])
        self.assertFalse(self.request.exists())
        result=self.execute(response={'success':False,'error':'timeout'},is_testpoint_bottom=True)
        self.assertTrue(result['outcome_unknown'])
        self.assertTrue(result['may_have_partial_changes'])
        self.request.unlink()
        result=self.execute(response={'success':False,'error':'timeout'})
        self.assertFalse(result['may_have_partial_changes'])
        self.request.unlink()


if __name__=='__main__':
    unittest.main()
