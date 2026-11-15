"""Explicit input units, native creation fields and whole-batch preflight."""

import ast
import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock,Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import footprint_editor as editor
from altium_files import read_altium_text,write_altium_text


class FootprintBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.library=self.root/'Parts_\u03a9.PcbLib';self.library.touch()

    def pad(self,layer='Top Layer',top='1',bottom='0'):
        fields=['PAD','01','2.54','5.08','90',layer,'0','0','0','0','45','.6','1.15','2','25','1',
                '.5','1','2','.7','1.2','2','2','-.05','2','.05',top,bottom]
        return '|'.join(fields)

    def source(self,body):
        p=self.root/'batch.txt';write_altium_text(p,'FPLIB|'+str(self.library)+'\n'+body+'\n');return p

    def prepare(self,body,units='mil'):
        source=self.source(body);original=source.read_bytes()
        prepared=editor.prepare_batch_file(source,self.root,units)
        self.assertEqual(source.read_bytes(),original)
        return prepared,read_altium_text(prepared)

    def tool(self,response):
        tree=ast.parse((Path(__file__).resolve().parents[1]/'main.py').read_text(encoding='utf8'))
        fn=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='create_footprints_batch');fn.decorator_list=[]
        bridge=type('Bridge',(),{'execute_command':AsyncMock(return_value=response)})()
        scope=dict(Context=object,logger=Mock(),json=json,footprint_editor=editor,altium_bridge=bridge,EXCHANGE_DIR=self.root)
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'batch','exec'),scope)
        return scope['create_footprints_batch'],bridge

    def native(self,expected):
        rows=copy.deepcopy(expected)
        for row in rows: row['pad_count']=len(row['pads'])
        return dict(success=True,saved=False,created=len(rows),edited=0,primitive_errors=0,failed=[],problems=[],footprints=rows)

    def test_mm_pad_stack_holes_expansions_height_and_flags(self):
        prepared,text=self.prepare('FOOTPRINT|USB|Test footprint|3.26\n'+self.pad(),units='MM')
        rows=text.splitlines();pad=rows[2].split('|')
        for position,mm in {2:2.54,3:5.08,7:0,9:0,11:.6,12:1.15,16:.5,17:1,19:.7,20:1.2,23:-.05,25:.05}.items():
            self.assertAlmostEqual(float(pad[position])*.0254,mm)
        for position,unchanged in {1:'01',4:'90',6:'0',8:'0',10:'45',13:'2',14:'25',15:'1',18:'2',21:'2',22:'2',24:'2',26:'1',27:'0'}.items():
            self.assertEqual(pad[position],unchanged)
        expected=editor.batch_expectations(prepared)
        self.assertAlmostEqual(expected[0]['height_mm'],3.26)
        self.assertEqual(expected[0]['pads'],[dict(name='01',is_testpoint_top=True,is_testpoint_bottom=False)])

    def test_mm_conversion_covers_every_other_record_without_touching_angles_text(self):
        records=['TRACK|2.54|5.08|7.62|10.16|.254|Mechanical 13',
                 'ARC|2.54|5.08|1.27|90|180|.254|Mechanical 15',
                 'FILL|0|2.54|5.08|7.62|45|Top Layer',
                 'TEXT|2.54|5.08|1.27|.127|90|Top Overlay|0|1|12345_\u03a9',
                 'VIA|2.54|5.08|.8|.4|Top Layer|Bottom Layer',
                 'REGION|Mechanical 15|0|0|0|2.54|0|2.54|5.08',
                 'MODEL3D|test.step|Mechanical 13|90|180|270|2.54|5.08|7.62|1']
        _,text=self.prepare('FOOTPRINT|ALL|All dimensions\n'+'\n'.join(records),'mm')
        converted=[line.split('|') for line in text.splitlines()[2:]]
        self.assertEqual(converted[0][1:6],['100','200','300','400','10'])
        self.assertEqual(converted[1][1:7],['100','200','50','90','180','10'])
        self.assertEqual(converted[2][1:6],['0','100','200','300','45'])
        self.assertEqual(converted[3][1:6],['100','200','50','5','90']);self.assertEqual(converted[3][9],'12345_\u03a9')
        self.assertAlmostEqual(float(converted[4][3])*.0254,.8)
        self.assertEqual(converted[5][2:],['0','0','0','100','0','100','200'])
        self.assertEqual(converted[6][3:],['90','180','270','100','200','300','1'])

    def test_legacy_mil_defaults_and_layer_aliases(self):
        legacy='PAD|1|100|0|0|Top Layer|0|0|0|0|0|20|40|2'
        prepared,text=self.prepare('FOOTPRINT|OLD|Legacy\n'+legacy)
        self.assertIn(legacy,text)
        row=editor.batch_expectations(prepared)[0]
        self.assertEqual(row['height_mm'],0);self.assertFalse(row['pads'][0]['is_testpoint_top'])
        prepared,text=self.prepare('FOOTPRINT|NEW|New|100\n'+self.pad('eMultiLayer','1','1'))
        self.assertIn('|Multi-Layer|',text)
        self.assertEqual(editor.batch_expectations(prepared)[0]['height_mm'],2.54)

    def test_invalid_later_properties_and_units_never_dispatch(self):
        tool,bridge=self.tool({})
        records=['FOOTPRINT|BAD|Invalid|-1','FOOTPRINT|BAD|Invalid|1001','FOOTPRINT|BAD|Invalid|nan',
                 'FOOTPRINT|BAD|Invalid|inf','EDITFOOTPRINT|OLD|1',self.pad(top='True'),
                 self.pad(top='0',bottom='1'),self.pad('Bottom Layer',top='1',bottom='0')]
        for record in records:
            p=self.source('FOOTPRINT|MUST_NOT_EXIST|Earlier valid|1\nFOOTPRINT|BAD|Later\n'+record)
            with self.subTest(record=record):
                result=json.loads(asyncio.run(tool(None,str(p),units='mm')))
                self.assertFalse(result['success']);self.assertFalse(result['saved']);self.assertFalse(result['may_have_partial_changes'])
                bridge.execute_command.assert_not_awaited()
        for units in ('mils','inch',None,True):
            with self.subTest(units=units),self.assertRaises(ValueError):editor.prepare_batch_file(self.source('FOOTPRINT|F|Test'),self.root,units)
        with self.assertRaises(ValueError): self.prepare('UNITS|mm','mm')

    def test_repeated_pad_names_and_native_flag_height_schema_checks(self):
        prepared,_=self.prepare('FOOTPRINT|F|Test|3.26\n'+self.pad()+'\n'+self.pad(top='0'),'mm')
        expected=editor.batch_expectations(prepared);native=self.native(expected)
        native['footprints'][0]['pads'].reverse()
        self.assertEqual(editor.verify_batch(native,expected),[])
        cases=[]
        for change in ({'height_mm':3.5},{'pad_count':1},{'pad_count':True},{'footprint_name':'wrong'},
                       {'library_path':str(self.root/'Wrong.PcbLib')},{'library_path':None}):
            wrong=copy.deepcopy(native);wrong['footprints'][0].update(change);cases.append(wrong)
        wrong=copy.deepcopy(native);wrong['footprints'][0]['pads'][0]['is_testpoint_top']=1;cases.append(wrong)
        wrong=copy.deepcopy(native);del wrong['footprints'][0]['pads'][0]['is_testpoint_bottom'];cases.append(wrong)
        wrong=copy.deepcopy(native);wrong['footprints'][0]['pads'][0]['is_testpoint_top']=True;cases.append(wrong)
        cases.append({**copy.deepcopy(native),'success':False})
        for wrong in cases:
            with self.subTest(wrong=wrong):self.assertTrue(editor.verify_batch(wrong,expected))

    def test_runtime_single_call_readback_and_prepared_mil_file(self):
        source=self.source('FOOTPRINT|F|Test|3.26\n'+self.pad())
        prepared=editor.prepare_batch_file(source,self.root,'mm');expected=editor.batch_expectations(prepared);prepared.unlink()
        tool,bridge=self.tool({'success':True,'result':self.native(expected)})
        async def dispatch(command,params):
            self.assertEqual(command,'create_footprints_batch')
            spec=Path(params['spec_file']);self.assertIn('PAD|01|100|200|90',read_altium_text(spec))
            return {'success':True,'result':self.native(expected)}
        bridge.execute_command.side_effect=dispatch
        result=json.loads(asyncio.run(tool(None,str(source),units='MM')))
        self.assertTrue(result['success']);self.assertEqual(result['input_units'],'mm');self.assertTrue(result['verification']['passed'])
        self.assertEqual(bridge.execute_command.await_count,1)
        bad=self.native(expected);bad['footprints'][0]['height_mm']=9
        bridge.execute_command.side_effect=None;bridge.execute_command.return_value={'success':True,'result':bad}
        result=json.loads(asyncio.run(tool(None,str(source),units='mm')))
        self.assertFalse(result['success']);self.assertTrue(result['may_have_partial_changes'])

    def test_guard_refusal_and_edit_only_remain_compatible(self):
        tool,bridge=self.tool({'success':False,'command_started':False,'error':'busy'})
        source=self.source('EDITFOOTPRINT|EXISTING\nMODEL3D|test.step|Mechanical 13|0|0|0|1')
        result=json.loads(asyncio.run(tool(None,str(source))))
        self.assertFalse(result['may_have_partial_changes']);self.assertNotIn('outcome_unknown',result)
        self.assertFalse(Path(bridge.execute_command.call_args.args[1]['spec_file']).exists())
        bridge.execute_command.return_value={'success':True,'result':dict(saved=False,created=0,edited=1,models_added=1,footprints=[],failed=[],problems=[],primitive_errors=0)}
        self.assertTrue(json.loads(asyncio.run(tool(None,str(source))))['success'])


if __name__=='__main__':unittest.main()
