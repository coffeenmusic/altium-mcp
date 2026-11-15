"""Sheet/view/project validation, no-save guarantees and save verification."""

import ast
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from typing import Optional
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import schematic_document as data
from schematic_editor import compact_result, validate_request
from altium_files import write_altium_text


class SchematicDocumentTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2] / '.altium-mcp' / 'unit_tests'
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sheet = self.root / 'Unsaved_\u03a9.SchDoc'
        self.source = self.root / 'Source.SchDoc'
        self.source.write_bytes(b'original document')
        self.project = self.root / 'Project.PrjPcb'
        write_altium_text(self.project, '[Design]\nVersion=1.0\n[Document1]\nDocumentPath=Source.SchDoc\n')

    def test_sheet_paths_and_sizes(self):
        self.assertEqual(data.prepare_sheet(str(self.sheet), 'a3')['sheet_size'], 'A3')
        self.assertNotIn('sheet_size', data.prepare_sheet(str(self.sheet)))
        for value in ('A5', 'eA3', 3, ''):
            with self.assertRaises(ValueError):
                data.prepare_sheet(str(self.sheet), value)
        for path in ('relative.SchDoc', str(self.sheet.with_suffix('.PcbDoc'))):
            with self.assertRaises(ValueError):
                data.prepare_sheet(path)
        with self.assertRaises(ValueError):
            data.prepare_sheet(str(self.sheet), mode='unknown')

    def test_unsaved_editor_and_detaching_prototype(self):
        spec = validate_request(str(self.sheet), [{'op':'update','type':'component',
                    'target':{'designator':'R38'}, 'properties':{'design_item_id':'', 'designator':'R501'}}])
        self.assertIn('0.design_item_id=\n', spec)

    def test_project_validation_and_membership_parse(self):
        self.assertEqual(data.prepare_project(str(self.project), True)['dry_run'], '1')
        self.assertEqual(data.project_documents(self.project), {str(self.source).casefold()})
        for path, dry_run in ((str(self.sheet), True), (str(self.root/'Missing.PrjPcb'), True), (str(self.project), 1)):
            with self.assertRaises(ValueError):
                data.prepare_project(path, dry_run)

    def run_command(self, command, spec, result, callback=None, **outer):
        async def dispatch(*args):
            preflight = 'dry_run=1' in Path(args[1]['spec_file']).read_text(encoding='utf-8-sig')
            if callback and not preflight:
                callback()
            actual = {**result, 'project_saved':False} if preflight else result
            return {'success': actual.get('success', False), 'result': actual, **outer}
        bridge = type('Bridge', (), {'execute_command': AsyncMock(side_effect=dispatch)})()
        return json.loads(asyncio.run(data.execute(bridge, self.root, command, spec)))

    def test_sheet_and_zoom_readback(self):
        result = self.run_command('set_schematic_sheet_properties', data.prepare_sheet(str(self.sheet), 'A3'),
            {'success':True, 'sheet_path':str(self.sheet), 'sheet_size':'A3', 'changed':True, 'is_modified':True, 'saved':False})
        self.assertTrue(result['verification']['passed'])
        result = self.run_command('zoom_schematic', data.prepare_sheet(str(self.sheet), mode='all'),
            {'success':True, 'sheet_path':str(self.sheet), 'zoom_requested':True, 'was_modified':True, 'is_modified':False, 'saved':False})
        self.assertFalse(result['success'])

    def project_result(self, dry_run):
        return {'success': True, 'project_path':str(self.project), 'saved':False,
                'project_saved':not dry_run, 'source_states_unchanged':True, 'source_paths':[str(self.source)]}

    def test_project_dry_run_and_source_state_guards(self):
        before = self.project.read_bytes()
        result = self.run_command('save_project', data.prepare_project(str(self.project), True), self.project_result(True))
        self.assertTrue(result['verification']['passed'])
        self.assertEqual(self.project.read_bytes(), before)
        result = self.run_command('save_project', data.prepare_project(str(self.project), True),
                                  {**self.project_result(True), 'source_states_unchanged':False})
        self.assertFalse(result['success'])

    def test_actual_save_membership_is_verified_with_mock_bridge(self):
        result = self.run_command('save_project', data.prepare_project(str(self.project)), self.project_result(False))
        self.assertTrue(result['verification']['passed'])
        result = self.run_command('save_project', data.prepare_project(str(self.project)),
                                  {**self.project_result(False), 'source_paths':[]})
        self.assertFalse(result['success'])

    def test_new_model_member_file_is_guarded_before_actual_save(self):
        additional = self.root/'Added.SchDoc'
        additional.write_bytes(b'new member')
        write_altium_text(self.project, '[Design]\nVersion=1.0\n[Document1]\nDocumentPath=Source.SchDoc\n')
        def save_wrong_source():
            write_altium_text(self.project, '[Design]\nVersion=1.0\n[Document1]\nDocumentPath=Source.SchDoc\n[Document2]\nDocumentPath=Added.SchDoc\n')
            additional.write_bytes(b'unexpected source save')
        native = {**self.project_result(False), 'source_paths':[str(self.source),str(additional)]}
        result = self.run_command('save_project', data.prepare_project(str(self.project)), native, callback=save_wrong_source)
        self.assertFalse(result['success'])
        self.assertFalse(result['source_files_unchanged'])
        result = self.run_command('save_project', data.prepare_project(str(self.project)), self.project_result(False),
                                  callback=lambda: self.source.write_bytes(b'unexpected source save'))
        self.assertFalse(result['source_files_unchanged'])
        self.assertFalse(result['success'])

    def test_native_refusal_and_timeout_cleanup(self):
        result = self.run_command('save_project', data.prepare_project(str(self.project), True),
                                  {'success':False, 'error':'dirty source', 'saved':False, 'may_have_partial_changes':False})
        self.assertFalse(result['success'])
        self.assertNotIn('outcome_unknown', result)
        self.assertEqual(list(self.root.glob('save_project_*.txt')), [])
        bridge = type('Bridge', (), {'execute_command':AsyncMock(return_value={'success':False,'error':'timeout'})})()
        result = json.loads(asyncio.run(data.execute(bridge, self.root, 'set_schematic_sheet_properties', data.prepare_sheet(str(self.sheet), 'A3'))))
        self.assertTrue(result['outcome_unknown'])
        self.assertTrue(Path(result['spec_file']).exists())

    def test_compact_modes_preserve_status_and_changed_survivors(self):
        full = {'success':False, 'error':'partial', 'applied_count':1,
                'results':[{'unique_id':'A'}, {'unique_id':'deleted'}],
                'objects':[{'type':'text','unique_id':'A'}, {'type':'component','unique_id':'B'}]}
        summary = compact_result(full, 'summary')
        self.assertNotIn('objects', summary)
        self.assertEqual(summary['object_counts'], {'text':1, 'component':1})
        self.assertFalse(summary['success'])
        self.assertEqual(compact_result(full, 'changed')['objects'], [full['objects'][0]])
        self.assertEqual(len(full['objects']), 2)

    def test_mcp_wrappers_route_exact_targets(self):
        names = {'set_schematic_sheet_properties', 'zoom_schematic', 'save_project'}
        tree = ast.parse((Path(__file__).resolve().parents[1]/'main.py').read_text(encoding='utf-8'))
        nodes = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name in names]
        for n in nodes:
            n.decorator_list = []
        module = SimpleNamespace(prepare_sheet=data.prepare_sheet, prepare_project=data.prepare_project,
                                 execute=AsyncMock(return_value='{}'))
        scope = {'Context':object, 'Optional':Optional, 'schematic_document':module,
                 'altium_bridge':object(), 'EXCHANGE_DIR':self.root, 'json':json}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'wrappers', 'exec'), scope)
        asyncio.run(scope['set_schematic_sheet_properties'](None, str(self.sheet), 'A3'))
        asyncio.run(scope['zoom_schematic'](None, str(self.sheet)))
        asyncio.run(scope['save_project'](None, str(self.project), True))
        self.assertEqual({c.args[2] for c in module.execute.call_args_list}, names)

    def test_catalog_exposes_sheet_zoom_and_explicit_project_save(self):
        from mcp.server.fastmcp import FastMCP, Context
        from mcp.types import ToolAnnotations
        tree = ast.parse((Path(__file__).resolve().parents[1]/'main.py').read_text(encoding='utf-8'))
        names = {'set_schematic_sheet_properties', 'zoom_schematic', 'save_project'}
        nodes = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name=='_tool') or
                 (isinstance(n, ast.AsyncFunctionDef) and n.name in names)]
        mcp = FastMCP('SchematicDocumentTest')
        scope = {'mcp':mcp, 'Context':Context, 'Optional':Optional, 'READ_ONLY':ToolAnnotations(readOnlyHint=True),
                 'schematic_document':data, 'altium_bridge':object(), 'EXCHANGE_DIR':self.root, 'json':json}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'catalog', 'exec'), scope)
        tools = {t.name:t for t in asyncio.run(mcp.list_tools())}
        self.assertEqual(set(tools), names)
        self.assertTrue(tools['zoom_schematic'].annotations.readOnlyHint)
        self.assertEqual(tools['set_schematic_sheet_properties'].inputSchema['required'], ['sheet_path'])
        self.assertIs(tools['save_project'].inputSchema['properties']['dry_run']['default'], False)


if __name__ == '__main__':
    unittest.main()
