"""3D edit selection, no-save guarantees and honest partial verification."""

import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import footprint_model3d as editor


class Model3DTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.library = self.root / 'Parts_\u03a9.PcbLib'
        self.library.write_bytes(b'library fixture')
        self.step = self.root / 'New_\u03a9.step'
        self.step.write_bytes(b'STEP fixture')
        self.calls = 0

    def spec(self, **kwargs):
        return editor.prepare(str(self.library), 'USB-C', **kwargs)

    def native(self):
        old = dict(body_address='123', layer='Mechanical Layer 13', has_model=True, model_type=1,
                   model_file='Old.step', embed=True, x_mm=0, y_mm=1.35,
                   standoff_height_mm=-1.5, overall_height_mm=3.161)
        new = {**old, 'x_mm': 2.54}
        geometry = dict(name='USB-C', height_mm=3.26, description='Keep',
                        primitives=[dict(type='pad', name='1', x=0, y=0)])
        return dict(success=True, saved=False, changed=True, was_modified=False, is_modified=True,
                    library_path=str(self.library), footprint_name='USB-C', body_index=0,
                    before=[old], after=[new], footprint_before=geometry, footprint_after=copy.deepcopy(geometry))

    def run_edit(self, native=None, response=None, changes=None, delete=False, change_disk=False, **kwargs):
        class Bridge:
            async def execute_command(inner, command, params):
                self.calls += 1
                self.assertEqual(command, 'edit_footprint_model3d')
                self.request = Path(params['spec_file'])
                self.request_text = self.request.read_text(encoding='utf-8-sig')
                if change_disk:
                    self.library.write_bytes(b'unexpected save')
                return response if response is not None else {'result': json.dumps(native)}
        return json.loads(asyncio.run(editor.execute(Bridge(), self.root, str(self.library), 'USB-C',
                                                     changes=changes, delete=delete, **kwargs)))

    def test_preflight_preserves_unicode_and_native_mm_values(self):
        spec = self.spec(changes={'x_mm': 2.54, 'embed': False})
        self.assertEqual(spec['x_mm'], '2.54')
        self.assertEqual(spec['embed'], '0')
        self.assertEqual(spec['library_path'], str(self.library))
        self.assertEqual(self.spec()['mutating'], '0')
        self.assertEqual(self.spec(delete=True)['mutating'], '1')

    def test_reject_partial_pose_and_bad_values_before_dispatch(self):
        for changes in ({'rotation_z':90}, {'z_mm':1}, {'model_path':str(self.step)},
                        {'x_mm':True}, {'x_mm':float('nan')}, {'y_mm':1001}, {'x_mm':10**1000},
                        {'embed':1}, {'typo':0}, {'x_mm':None}, []):
            with self.subTest(changes=changes):
                r = self.run_edit(changes=changes)
                self.assertFalse(r['success'])
                self.assertFalse(r['may_have_partial_changes'])
        self.assertEqual(self.calls, 0)
        for options in (dict(body_index=True), dict(body_index=-1), dict(body_index=1.2),
                        dict(delete=1), dict(delete=True, changes={'x_mm':0}),
                        dict(expected_model_file=' ')):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.spec(**options)

    def test_replacement_and_named_unsaved_target_preflight(self):
        pose = dict(rotation_x=0, rotation_y=90, rotation_z=-30, z_mm=.2)
        spec = self.spec(changes={**pose, 'model_path':str(self.step)})
        self.assertNotIn('embed', spec)
        self.assertEqual(spec['model_path'], str(self.step))
        for model in ('relative.step', str(self.root/'Missing.step'), str(self.library)):
            with self.assertRaises(ValueError):
                self.spec(changes={**pose, 'model_path':model})
        self.library.unlink()
        self.assertEqual(self.spec()['library_path'], str(self.library))

    def test_success_and_pose_verification_limit(self):
        result = self.run_edit(self.native(), changes={'x_mm':2.54}, expected_model_file='Old.step')
        self.assertTrue(result['success'])
        self.assertTrue(result['verification']['passed'])
        self.assertFalse(result['verification']['pose_readback_available'])
        self.assertIn('x_mm=2.54', self.request_text)
        self.assertFalse(self.request.exists())
        native = self.native()
        pose = dict(rotation_x=90, rotation_y=0, rotation_z=0, z_mm=.2)
        native['pose_applied'] = pose
        self.assertTrue(self.run_edit(native, changes={'x_mm':2.54, **pose})['success'])
        del native['pose_applied']
        self.assertFalse(self.run_edit(native, changes={'x_mm':2.54, **pose})['success'])

    def test_failed_readback_detects_geometry_identity_and_omitted_changes(self):
        for mutation in ('pad', 'height', 'y', 'embed', 'file', 'body', 'count', 'selector', 'save', 'modified'):
            with self.subTest(mutation=mutation):
                native = self.native()
                if mutation == 'pad': native['footprint_after']['primitives'][0]['x'] = 10
                if mutation == 'height': native['footprint_after']['height_mm'] = 4
                if mutation == 'y': native['after'][0]['y_mm'] = 100
                if mutation == 'embed': native['after'][0]['embed'] = False
                if mutation == 'file': native['after'][0]['model_file'] = 'Other.step'
                if mutation == 'body': native['after'][0]['body_address'] = '456'
                if mutation == 'count': native['after'] = []
                if mutation == 'selector': native['body_index'] = 1
                if mutation == 'save': native['saved'] = True
                if mutation == 'modified': native['is_modified'] = False
                result = self.run_edit(native, changes={'x_mm':2.54}, body_index=0)
                self.assertFalse(result['success'])
                self.assertTrue(result['may_have_partial_changes'])

    def test_delete_preserves_other_body_and_read_mode(self):
        native = self.native()
        other = {**native['before'][0], 'body_address':'456', 'model_file':'Second.step'}
        native['before'].append(other)
        native['after'] = [other]
        self.assertTrue(self.run_edit(native, delete=True, body_index=0)['success'])
        native['after'] = []
        self.assertFalse(self.run_edit(native, delete=True, body_index=0)['success'])
        native = self.native()
        native.update(after=copy.deepcopy(native['before']), changed=False, was_modified=True, is_modified=True)
        self.assertTrue(self.run_edit(native)['success'])
        native['is_modified'] = False
        self.assertFalse(self.run_edit(native)['success'])

    def test_unknown_outcome_is_not_safe_to_retry_and_keeps_evidence(self):
        for changes in (None, {'x_mm':1}):
            result = self.run_edit(response={'error':'timeout'}, changes=changes)
            self.assertTrue(result['outcome_unknown'])
            self.assertEqual(result['may_have_partial_changes'], bool(changes))
            self.assertTrue(self.request.exists())
            self.request.unlink()
        result = self.run_edit(response={'command_started':False, 'error':'busy'}, changes={'x_mm':1})
        self.assertFalse(result['may_have_partial_changes'])
        self.assertFalse(self.request.exists())

    def test_completed_failure_and_disk_guard(self):
        failure = dict(success=False, saved=False, error='Ambiguous target', may_have_partial_changes=False)
        self.assertFalse(self.run_edit(failure, changes={'x_mm':1})['may_have_partial_changes'])
        self.assertFalse(self.request.exists())
        result = self.run_edit(self.native(), changes={'x_mm':2.54}, change_disk=True)
        self.assertFalse(result['success'])
        self.assertIn('library file changed unexpectedly', result['verification']['errors'])

    def test_body_id_survives_iterator_reordering_and_guards_stale_targets(self):
        native = self.native()
        other = {**native['before'][0], 'body_address':'456'}
        native['before'].append(other)
        native['after'].insert(0, other)
        self.assertTrue(self.run_edit(native, changes={'x_mm':2.54}, body_id='123')['success'])
        self.assertFalse(self.run_edit(native, changes={'x_mm':2.54}, body_id='456')['success'])
        native['after'] = [other]
        self.assertTrue(self.run_edit(native, delete=True, body_id='123')['success'])
        for body_id in (123, '', '-1', '123.0', 'abc'):
            with self.assertRaises(ValueError): self.spec(body_id=body_id)


if __name__ == '__main__':
    unittest.main()
