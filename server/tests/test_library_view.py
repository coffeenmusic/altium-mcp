"""Library view validation, finite dispatch, preservation and transport checks."""

import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import library_view as editor


class LibraryViewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.path=self.root/'Library_\u03a9.SchLib'
        self.path.write_bytes(b'original')

    def native(self, **options):
        spec=editor.prepare(str(self.path),'LED',**options)
        content={'metadata':{'models':['Keep']},'objects':[{'_id':'1','type':'label','text':'RED'}]}
        return dict(success=True,saved=False,library_path=str(self.path),was_modified=False,is_modified=False,
                    selected={'component_name':'LED','part_id':options.get('part_id',1),
                              'display_mode':options.get('display_mode',0)},actions=editor.actions(spec),
                    inventory_before=[{'_id':'1','name':'LED','child_count':2}],
                    inventory_after=[{'_id':'1','name':'LED','child_count':2}],
                    content_before=content,content_after=copy.deepcopy(content))

    def execute(self, native=None, response=None, disk_change=False, **options):
        class Bridge:
            async def execute_command(inner, command, params):
                self.assertEqual(command,'set_library_view')
                self.request=Path(params['spec_file'])
                self.spec=self.request.read_text(encoding='utf-8-sig')
                if disk_change:
                    self.path.write_bytes(b'unexpected save')
                return response if response is not None else {'result':json.dumps(native)}
        return json.loads(asyncio.run(editor.execute(Bridge(),self.root,str(self.path),'LED',**options)))

    def test_exact_selection_part_display_and_unicode_transport(self):
        result=self.execute(self.native(part_id=2,display_mode=1),part_id=2,display_mode=1)
        self.assertTrue(result['success'])
        self.assertIn('\u03a9',self.spec)
        self.assertNotIn('content_before',result)
        self.assertEqual(result['verification']['fit_mode_camera'],'dispatch_only')
        self.assertFalse(self.request.exists())

    def test_preserve_dirty_current_member_and_unsaved_paths(self):
        native=self.native(fit=False);native.update(was_modified=True,is_modified=True)
        self.assertTrue(self.execute(native,fit=False)['success'])
        spec=editor.prepare(str(self.root/'New.PcbLib'),fit=False)
        self.assertNotIn('component_name',spec)
        self.assertEqual(editor.actions(spec),[])

    def test_pcb_camera_infers_explicit_3d_and_only_finite_actions(self):
        path=str(self.root/'Parts.PcbLib')
        for camera,parameter in editor.CAMERAS.items():
            spec=editor.prepare(path,'M01',camera=camera)
            self.assertEqual(editor.actions(spec),[
                {'command':'PCB:SwitchTo3D','parameters':''},
                {'command':'IPCB_Board.GraphicalView_ZoomOnRect','parameters':'physical_bounds'},
                {'command':'PCB:ResetCamera','parameters':'To='+parameter}])
        self.assertEqual(editor.actions(editor.prepare(path,view_mode='2D',fit=False)),
                         [{'command':'PCB:SwitchTo2D','parameters':''}])

    def test_validation_before_focus(self):
        for options in ({'part_id':0},{'part_id':True},{'display_mode':-1},{'fit':1},
                        {'view_mode':'toggle'},{'camera':'isometric'},{'view_mode':'3d'},
                        {'camera':{'yaw':30}},{'component_name':''},{'component_name':'x\nnext'}):
            with self.subTest(options=options),self.assertRaises((ValueError,TypeError)):
                editor.prepare(str(self.path),**options)
        for options in ({'part_id':1},{'display_mode':0},{'camera':'top','view_mode':'2d'}):
            with self.assertRaises(ValueError):
                editor.prepare(str(self.root/'Parts.PcbLib'),**options)
        with self.assertRaises(ValueError):
            editor.prepare(str(self.root/'Parts.SchDoc'))

    def test_readback_content_membership_dirty_and_dispatch_failures(self):
        cases=[]
        for values in ({'part_id':1},{'component_name':'Wrong'}):
            native=self.native(part_id=2);native['selected'].update(values);cases.append(native)
        for changes in ({'saved':True},{'is_modified':True},{'actions':[]},{'inventory_after':[]},
                        {'content_after':{'models':[]}},{'library_path':str(self.root/'Other.SchLib')}):
            cases.append({**self.native(part_id=2),**changes})
        for native in cases:
            with self.subTest(native=native):
                self.assertFalse(self.execute(native,part_id=2)['success'])

    def test_equivalent_iteration_and_track_endpoints_but_ordered_vertices(self):
        a=[{'_id':'1','type':'track','x1':0,'y1':1,'x2':5,'y2':2},
           {'_id':'2','type':'polygon','vertices':[{'x':0,'y':0},{'x':1,'y':1}]}]
        b=copy.deepcopy(a);b[0].update(_id='3',x1=5,y1=2,x2=0,y2=1);b.reverse()
        self.assertEqual(editor.canonical(a),editor.canonical(b))
        b[0]['vertices'].reverse()
        self.assertNotEqual(editor.canonical(a),editor.canonical(b))

    def test_disk_and_unknown_outcomes(self):
        self.assertFalse(self.execute(self.native(),disk_change=True)['success'])
        result=self.execute(response={'error':'timeout'})
        self.assertTrue(result['outcome_unknown']);self.assertTrue(self.request.exists())
        self.request.unlink()
        result=self.execute(response={'error':'busy','command_started':False})
        self.assertFalse(result['may_have_view_changes']);self.assertFalse(self.request.exists())
        result=self.execute(response={'result':{'success':False,'error':'missing member','saved':False}})
        self.assertEqual(result['error'],'missing member');self.assertFalse(self.request.exists())

    def test_pin_map_order_is_irrelevant_but_actual_mapping_is_preserved(self):
        before={'map_as_string':'(3:3),(1:1,5),(4:4),(2:2)'}
        equivalent={'map_as_string':'(4:4),(2:2),(1:5,1),(3:3)'}
        changed={'map_as_string':'(4:4),(2:2),(1:5,6),(3:3)'}
        self.assertEqual(editor.canonical(before),editor.canonical(equivalent))
        self.assertNotEqual(editor.canonical(before),editor.canonical(changed))


if __name__=='__main__':
    unittest.main()
