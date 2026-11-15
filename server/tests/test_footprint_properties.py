"""Native footprint height validation, no-op behavior and outcome verification."""

import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import footprint_editor as editor


class FootprintPropertiesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.library=self.root/'Parts_\u03a9.PcbLib'
        self.library.write_bytes(b'fixture')

    def native(self, before=1, after=3.2):
        return dict(success=True,saved=False,library_path=str(self.library),footprint_name='USB-C',
                    verified_count=1,updated_count=int(before!=after),changed=before!=after,
                    was_modified=False,is_modified=before!=after,
                    before=dict(height_mm=before,description='Keep'),after=dict(height_mm=after,description='Keep'))

    def execute(self, native=None, response=None, height=3.2, change_disk=False):
        class Bridge:
            async def execute_command(inner,command,params):
                self.assertEqual(command,'set_footprint_properties')
                self.request=Path(params['spec_file'])
                self.spec=self.request.read_text(encoding='utf-8-sig')
                if change_disk:
                    self.library.write_bytes(b'unexpected save')
                return response if response is not None else {'success':True,'result':json.dumps(native)}
        return json.loads(asyncio.run(editor.execute(Bridge(),self.root,'set_footprint_properties',
                         editor.prepare_properties,str(self.library),'USB-C',height)))

    def test_finite_mm_values_and_read_mode(self):
        for value in (0,3.2,.123456,1000):
            spec=editor.prepare_properties(str(self.library),'USB-C',value)
            self.assertEqual(float(spec['height_mm']),value)
            self.assertEqual(spec['count'],'1')
        self.assertNotIn('height_mm',editor.prepare_properties(str(self.library),'USB-C'))

    def test_invalid_inputs(self):
        for value in (-.1,1000.1,float('nan'),float('inf'),True,'3.2',10**1000,[],{}):
            with self.subTest(value=str(value)[:40]),self.assertRaises(ValueError):
                editor.prepare_properties(str(self.library),'USB-C',value)
        for path,name in ((str(self.library),' '),('Parts.PcbLib','USB-C'),(str(self.library.with_suffix('.SchLib')),'USB-C')):
            with self.assertRaises(ValueError):
                editor.prepare_properties(path,name,3.2)

    def test_roundtrip_and_native_precision(self):
        result=self.execute(self.native(after=3.200001))
        self.assertTrue(result['success'])
        self.assertTrue(result['verification']['passed'])
        self.assertIn('height_mm=3.2\n',self.spec)
        self.assertFalse(self.request.exists())

    def test_read_and_identical_values_preserve_modified(self):
        for height in (None,3.2):
            native=self.native(before=3.2,after=3.2)
            native.update(was_modified=True,is_modified=True)
            self.assertTrue(self.execute(native,height=height)['success'])
            native['is_modified']=False
            self.assertFalse(self.execute(native,height=height)['success'])

    def test_bad_readback_and_unrequested_changes(self):
        native=self.native()
        for changes in ({'after':{'height_mm':3.5,'description':'Keep'}},
                        {'after':{'height_mm':3.2,'description':'Unexpected'}},
                        {'is_modified':False},{'changed':None}, {'saved':True},
                        {'library_path':str(self.root/'Other.PcbLib')},{'footprint_name':'Other'},
                        {'verified_count':0}):
            with self.subTest(changes=changes):
                self.assertFalse(self.execute({**copy.deepcopy(native),**changes})['success'])

    def test_disk_guard(self):
        result=self.execute(self.native(),change_disk=True)
        self.assertFalse(result['success'])
        self.assertIn('library file changed unexpectedly',result['verification']['errors'])

    def test_completed_failure_and_unknown_outcome(self):
        native=dict(success=False,saved=False,error='Footprint missing or ambiguous',may_have_partial_changes=False)
        result=self.execute(native,response={'success':False,'result':native})
        self.assertFalse(result['may_have_partial_changes'])
        self.assertFalse(self.request.exists())
        result=self.execute(response={'success':False,'error':'timeout'})
        self.assertTrue(result['outcome_unknown'])
        self.assertTrue(result['may_have_partial_changes'])
        self.request.unlink()
        result=self.execute(response={'success':False,'error':'timeout'},height=None)
        self.assertFalse(result['may_have_partial_changes'])
        self.request.unlink()
        result=self.execute(response={'success':False,'command_started':False,'error':'busy'})
        self.assertFalse(result['may_have_partial_changes'])
        self.assertFalse(self.request.exists())


if __name__=='__main__':
    unittest.main()
