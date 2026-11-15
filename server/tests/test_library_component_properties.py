"""Native property validation, preservation and bridge outcome checks."""

import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import library_component_properties as data


class LibraryPropertiesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / 'Parts.SchLib'
        self.path.write_bytes(b'fixture')
        self.snapshot = dict(symbol_name='Device', designator_template='U?', component_kind='standard',
                             component_kind_code=0, designator_read_only_state=1, comment='Comment', description='Description',
                             part_count=1, parameters={'MP':'Keep'}, models=[])

    def native(self, **changes):
        after = {**copy.deepcopy(self.snapshot), **changes}
        return dict(success=True, saved=False, library_path=str(self.path), symbol_name='Device',
                    changed=after != self.snapshot, was_modified=False, is_modified=after != self.snapshot,
                    before=copy.deepcopy(self.snapshot), after=after)

    def execute(self, result=None, response=None, change_disk=False, **edits):
        class Bridge:
            async def execute_command(inner, command, params):
                self.assertEqual(command, 'set_library_component_data')
                self.request = Path(params['spec_file'])
                self.spec = self.request.read_text(encoding='utf-8-sig')
                self.assertIn('parameter_count=0\n', self.spec)
                self.assertIn('model_count=0\n', self.spec)
                if change_disk:
                    self.path.write_bytes(b'unexpected save')
                return response if response is not None else {'success':True,'result':json.dumps(result)}
        return json.loads(asyncio.run(data.execute(Bridge(),self.root,str(self.path),'Device',**edits)))

    def test_templates_and_ui_kind_names(self):
        for template in ('J?', 'D?', 'C?', 'TP?', 'R_ARRAY2?'):
            spec, requested = data.prepare(str(self.path), 'Device', template, 'Standard (No BOM)')
            self.assertEqual(requested, {'designator_template':template, 'component_kind':'standard_no_bom'})
            self.assertIn('component_kind=standard_no_bom\n', spec)
        for canonical, ui in data.KINDS.items():
            self.assertEqual(data.kind(ui.upper()), canonical)
            self.assertEqual(data.kind(canonical), canonical)

    def test_invalid_requests(self):
        for template in ('', 'J', 'J1', '?', '1J?', 'J??', 'J ?','J?\n', 'J?=X', True, 1, 'A'*64+'?'):
            with self.subTest(template=template), self.assertRaises(ValueError):
                data.prepare(str(self.path), 'Device', template)
        for component_kind in ('No BOM', 'Jumper', '', 5, True, {}, []):
            with self.subTest(component_kind=component_kind), self.assertRaises(ValueError):
                data.prepare(str(self.path), 'Device', component_kind=component_kind)
        for path in ('Parts.SchLib', str(self.root/'Missing.SchLib'), str(self.path.with_suffix('.PcbLib'))):
            with self.assertRaises(ValueError):
                data.prepare(path, 'Device', 'J?', 'standard')

    def test_transport_unicode_and_read_mode(self):
        spec, edits = data.prepare(str(self.path), 'Device_\u03a9_\u0416\n|=', None, None)
        self.assertIn('symbol_name=Device_\u03a9_\u0416\\n|=',spec)
        self.assertEqual(edits,{})
        result=self.execute(self.native())
        self.assertTrue(result['verification']['passed'])
        self.assertFalse(self.request.exists())

    def test_combined_change_and_partial_omissions(self):
        native=self.native(designator_template='J?',component_kind='standard_no_bom',component_kind_code=5)
        result=self.execute(native,designator_template='J?',component_kind='Standard (No BOM)')
        self.assertTrue(result['success'])
        self.assertEqual(result['after']['parameters'],self.snapshot['parameters'])
        native=self.native(designator_template='D?')
        self.assertTrue(self.execute(native,designator_template='D?')['success'])
        native['after']['component_kind']='graphical'
        self.assertFalse(self.execute(native,designator_template='D?')['success'])

    def test_readback_preservation_identity_modified_and_disk(self):
        native=self.native(designator_template='C?')
        for changes in ({'after':{**native['after'],'designator_template':'WRONG?'}},
                        {'after':{**native['after'],'comment':'Unexpected'}},
                        {'after':{**native['after'],'designator_read_only_state':0}},
                        {'library_path':str(self.root/'Other.SchLib')}, {'symbol_name':'Other'},
                        {'saved':True}, {'is_modified':False}):
            with self.subTest(changes=changes):
                result=self.execute({**native,**changes},designator_template='C?')
                self.assertFalse(result['success'])
                self.assertTrue(result['may_have_partial_changes'])

    def test_unexpected_disk_write_is_reported(self):
        native=self.native(designator_template='J?')
        result=self.execute(native,designator_template='J?',change_disk=True)
        self.assertFalse(result['success'])
        self.assertIn('library file changed unexpectedly',result['verification']['errors'])

    def test_noop_does_not_clear_modified(self):
        native=self.native()
        native.update(was_modified=True,is_modified=True)
        self.assertTrue(self.execute(native)['success'])
        native['is_modified']=False
        self.assertFalse(self.execute(native)['success'])

    def test_failure_outcomes(self):
        result=self.execute(response={'success':False,'command_started':False,'error':'busy'},designator_template='D?')
        self.assertFalse(result['may_have_partial_changes'])
        self.assertFalse(self.request.exists())
        result=self.execute(response={'success':False,'error':'timeout'},component_kind='standard_no_bom')
        self.assertTrue(result['outcome_unknown'])
        self.assertTrue(self.request.exists())
        self.request.unlink()
        for prefix in ('ERROR: ', ''):
            result=self.execute(response={'success':False,'error':prefix+'symbol must match exactly once; found 0'},designator_template='D?')
            self.assertFalse(result['may_have_partial_changes'])
            self.assertFalse(self.request.exists())
        result=self.execute(result={'success':False,'saved':False,'error':'Designator is read-only','may_have_partial_changes':False},designator_template='D?')
        self.assertFalse(result['may_have_partial_changes'])
        self.assertFalse(self.request.exists())


if __name__=='__main__':
    unittest.main()
