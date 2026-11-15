"""Empty-member removal, exact targeting, preservation and uncertain outcomes."""

import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import empty_library_component as editor


class EmptyLibraryComponentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / 'Parts_\u03a9.SchLib'
        self.path.write_bytes(b'original')
        self.before = [dict(_id='1',name='Component_1',child_count=0,metadata={}),
                       dict(_id='2',name='Keep',child_count=4,metadata={'model':'Keep'})]

    def native(self, removed=True, dry_run=False, found=True):
        before = copy.deepcopy(self.before if found else self.before[1:])
        after = copy.deepcopy(before[1:] if removed else before)
        return dict(success=True,library_path=str(self.path),component_name='Component_1',saved=False,
                    dry_run=dry_run,found=found,removable=found,removed=removed,changed=removed,
                    assessment={'empty':found,'blockers':[],'child_count':0},
                    was_modified=False,is_modified=removed,components_before=before,components_after=after)

    def execute(self, native=None, dry_run=False, response=None, disk_change=False):
        class Bridge:
            async def execute_command(inner, command, params):
                self.assertEqual(command,'remove_empty_library_component')
                self.request = Path(params['spec_file'])
                self.spec = self.request.read_text(encoding='utf-8-sig')
                if disk_change:
                    self.path.write_bytes(b'unexpected save')
                return response if response is not None else {'success':True,'result':json.dumps(native)}
        return json.loads(asyncio.run(editor.execute(Bridge(),self.root,str(self.path),'Component_1',dry_run)))

    def test_removes_only_target_and_hides_internal_ids(self):
        result = self.execute(self.native())
        self.assertTrue(result['success'])
        self.assertEqual(result['component_names_after'],['Keep'])
        self.assertNotIn('components_after',result)
        self.assertFalse(self.request.exists())

    def test_saved_and_named_unsaved_paths_unicode_names(self):
        for suffix in ('.SchLib','.PcbLib'):
            path = self.root / ('Unsaved_\u0416'+suffix)
            spec = editor.prepare(str(path),'Part_\u03a9')
            self.assertEqual(spec['component_name'],'Part_\u03a9')
        self.assertTrue(self.execute(self.native())['success'])
        self.assertIn('\u03a9',self.spec)

    def test_validation(self):
        for path,name,dry in (('relative.SchLib','Component_1',False),
                              (str(self.root/'File.SchDoc'),'Component_1',False),
                              (str(self.path),'',False),(str(self.path),'Part\nnext',False),
                              (str(self.path),'Component_1',1)):
            with self.subTest(path=path,name=name,dry=dry),self.assertRaises(ValueError):
                editor.prepare(path,name,dry)

    def test_dry_run_nonempty_and_absent_noop_preserve_dirty_state(self):
        for dry,found in ((True,True),(False,False)):
            native = self.native(False,dry,found)
            native.update(was_modified=True,is_modified=True)
            if dry:
                native['assessment'].update(empty=False,blockers=['hidden parameter'])
                native['removable']=False
            self.assertTrue(self.execute(native,dry_run=dry)['success'])
            native['is_modified']=False
            self.assertFalse(self.execute(native,dry_run=dry)['success'])

    def test_rejects_nonempty_last_or_dry_run_removal(self):
        cases=[]
        for change in ({'empty':False},{'blockers':['has a pin']}):
            native=self.native();native['assessment'].update(change);cases.append(native)
        native=self.native();native['components_before']=native['components_before'][:1];native['components_after']=[];cases.append(native)
        for native in cases:
            self.assertFalse(self.execute(native)['success'])
        native=self.native(dry_run=True)
        self.assertFalse(self.execute(native,dry_run=True)['success'])

    def test_target_and_survivor_verification(self):
        cases=[]
        for key,value in (('_id','replacement'),('child_count',0),('metadata',{}),('name','Renamed')):
            native=self.native();native['components_after'][0][key]=value;cases.append(native)
        for values in ({'found':False},{'removed':False},{'saved':True},{'is_modified':False},
                       {'library_path':str(self.root/'Wrong.SchLib')},{'component_name':'Wrong'}):
            cases.append({**self.native(),**values})
        for native in cases:
            with self.subTest(native=native):
                self.assertFalse(self.execute(native)['success'])

    def test_disk_writes_detected(self):
        self.assertFalse(self.execute(self.native(),disk_change=True)['success'])

    def test_refusal_and_uncertain_outcomes(self):
        refusal={'success':False,'error':'Component contains data','saved':False,'may_have_partial_changes':False}
        self.assertFalse(self.execute(response={'result':refusal})['may_have_partial_changes'])
        self.assertFalse(self.request.exists())
        result=self.execute(response={'error':'timeout'})
        self.assertTrue(result['outcome_unknown'])
        self.assertTrue(result['may_have_partial_changes'])
        self.assertTrue(self.request.exists())
        self.request.unlink()
        result=self.execute(response={'error':'busy','command_started':False})
        self.assertFalse(result['may_have_partial_changes'])
        self.assertFalse(self.request.exists())


if __name__=='__main__':
    unittest.main()
