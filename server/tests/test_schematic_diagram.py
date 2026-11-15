"""Preflight, routing and outcome verification without an Altium process."""

import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import schematic_diagram as data


class DiagramTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.sheet = str(self.root / 'Unsaved.SchDoc')
        self.props = dict(designator='Control', filename='Control.SchDoc', x=1000, y=1000, width=800, height=600,
                          entries=[dict(name='EN', side='left', offset=100, io_type='input')])
        self.blocks = [dict(id='a', text='Input', x=1000, y=1000, width=600, height=400),
                       dict(id='b', text='Output', x=2400, y=1600, width=600, height=400)]

    def test_symbol_validation_and_unicode(self):
        props = {**self.props, 'designator':'Control \u03a9\u0416',
                 'entries':[dict(name='A=B|C\n\u0416', side='bottom', offset=100)]}
        spec = data.prepare_symbols(self.sheet, [dict(op='add', properties=props)])
        self.assertEqual(spec['0.entry0.name'], 'A=B|C\n\u0416')
        self.assertEqual(spec['0.entry0.io_type'], 'unspecified')
        self.assertEqual(spec['count'], 1)
        for change in ({'width':0}, {'x':float('nan')}, {'y':None}, {'extra':1}, {'filename':'child.txt'},
                       {'entries':[dict(name='A', side='left', offset=600)]},
                       {'entries':[dict(name='A', side='left', offset=100), dict(name='a', side='right', offset=100)]}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                data.prepare_symbols(self.sheet, [dict(op='add', properties={**self.props, **change})])

    def test_uid_edit_rules(self):
        data.prepare_symbols(self.sheet, [dict(op='update', unique_id='ABC', properties={'entries':[]})])
        for ops in ([dict(op='update', unique_id='ABC', properties={'x':5})],
                    [dict(op='delete', unique_id='ABC', properties={})],
                    [dict(op='delete', unique_id='ABC')]*2,
                    [dict(op='update', unique_id='ABC', properties={})]):
            with self.subTest(ops=ops), self.assertRaises(ValueError):
                data.prepare_symbols(self.sheet, ops)

    def test_routes_and_graphic_primitives(self):
        spec, routes = data.prepare_diagram(self.sheet, self.blocks, [{'from':'a','to':'b','text':'Data'}], [])
        self.assertEqual(routes[0]['points'], [(1600,1200),(2000,1200),(2000,1800),(2400,1800)])
        kinds = {v for k,v in spec.items() if k.endswith('.type')}
        self.assertEqual(kinds, {'rectangle','text','polyline'})
        self.assertEqual(spec['count'], 7)

    def test_block_obstacle_requires_detour(self):
        blocks = [{**b, 'y':1000} for b in self.blocks]
        blocks.append(dict(id='obstacle',text='Middle',x=1800,y=1000,width=300,height=400))
        with self.assertRaisesRegex(ValueError, 'crosses a block'):
            data.prepare_diagram(self.sheet, blocks, [{'from':'a','to':'b'}], [])
        _, routes = data.prepare_diagram(self.sheet, blocks, [{'from':'a','to':'b','points':
            [[1600,1200],[1700,1200],[1700,1600],[2300,1600],[2300,1200],[2400,1200]]}], [])
        self.assertEqual(len(routes[0]['points']), 6)

    def test_invalid_diagrams(self):
        for links in ([{'from':'a','to':'missing'}], [{'from':'a','to':'a'}], [{'from':[], 'to':'b'}],
                      [{'from':'a','to':'b','arrow':1}], [{'from':'a','to':'b','points':[[1600,1200],[2400,1800]]}]):
            with self.subTest(links=links), self.assertRaises(ValueError):
                data.prepare_diagram(self.sheet, self.blocks, links, [])
        with self.assertRaisesRegex(ValueError, 'overlap'):
            data.prepare_diagram(self.sheet, [self.blocks[0],{**self.blocks[0],'id':'c'}], [], [])

    def execute(self, spec, result=None, response=None):
        class Bridge:
            async def execute_command(inner, command, params):
                request = Path(params['spec_file']).read_text(encoding='utf-8-sig')
                self.assertIn('sheet_path=', request)
                self.assertIn('count=', request)
                if response is not None:
                    return response
                return {'success':True, 'result':json.dumps(result)}
        return asyncio.run(data.execute(Bridge(), self.root, 'edit_sheet_symbols', spec))

    def test_failure_and_unknown_outcome(self):
        spec = data.prepare_symbols(self.sheet, [dict(op='add',properties=self.props)])
        result = self.execute(spec, response={'success':False, 'error':'timeout'})
        self.assertTrue(result['outcome_unknown'])
        self.assertTrue(Path(result['spec_file']).exists())
        Path(result['spec_file']).unlink()
        result = self.execute(spec, response={'success':False, 'command_started':False, 'error':'busy'})
        self.assertFalse(result['may_have_partial_changes'])
        self.assertFalse(list(self.root.glob('*.txt')))
        result = self.execute(spec, result={'success':False,'error':'not found','saved':False,'may_have_partial_changes':False})
        self.assertNotIn('outcome_unknown', result)
        self.assertFalse(list(self.root.glob('*.txt')))

    def test_readback_mismatch_and_ownership(self):
        spec = data.prepare_symbols(self.sheet, [dict(op='add',properties=self.props)])
        native = dict(success=True, sheet_path=self.sheet, saved=False, is_modified=True, applied_count=1,
                      results=[{'index':0,'unique_id':'NEW'}], objects=[dict(type='sheet_symbol',unique_id='NEW',
                      **{k:v for k,v in self.props.items() if k != 'entries'},
                      entries=[dict(**self.props['entries'][0],unique_id='ENTRY',owner_unique_id='NEW',x=1000,y=1500)])])
        self.assertTrue(self.execute(spec,result=native)['success'])
        native['objects'][0]['entries'][0]['x'] = 0
        self.assertIn('coordinate mismatch', ' '.join(self.execute(spec,result=native)['verification']['errors']))
        native['objects'][0]['entries'][0]['x'] = 1000
        native['objects'][0]['entries'][0]['owner_unique_id'] = 'WRONG'
        result = self.execute(spec,result=native)
        self.assertFalse(result['success'])
        self.assertIn('ownership', ' '.join(result['verification']['errors']))


if __name__ == '__main__':
    unittest.main()
