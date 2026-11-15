"""Creation metadata preflight, transport, dispatch and readback regression tests."""

import ast
import asyncio
import json
import logging
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import symbol_metadata as data
from altium_files import read_altium_text, write_altium_text


DESCRIPTION = "Resistor, 10k, 1%, 100mW, 0603 SMD"
PARAMETERS = {"Value": "10k", "MF": "Example", "MP": "R-10K", "HelpURL": "https://example.com/datasheet.pdf",
              "Package": "0603", "Type": "SMD", "Instructions": "Fit", "System": "Power"}


class SymbolMetadataTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2] / ".altium-mcp" / "unit_tests"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def prepare(self, parameters=None, description=DESCRIPTION):
        return data.prepare("R-10K", description, [], PARAMETERS if parameters is None else parameters)

    def block(self, name="R-10K", parameters=None):
        parameters = PARAMETERS if parameters is None else parameters
        return "\n".join([f"SYMBOL|{name}|{DESCRIPTION}|1"] +
                         [f"PARAM|{key}|{value}" for key, value in parameters.items()] +
                         ["PIN|1|A|eElectricPassive|eRotate0|0|0", "PIN|2|B|eElectricPassive|eRotate180|600|0"])

    def batch(self, contents):
        path = self.root / "batch.txt"
        write_altium_text(path, contents)
        return path

    def result(self, name="R-10K", description=DESCRIPTION, parameters=None):
        return {"success": True, "saved": False, "component_name": name,
                "metadata": {"description": description, "parameters": dict(PARAMETERS if parameters is None else parameters),
                             "comment": PARAMETERS["MP"], "designator_template": "U?", "component_kind": "standard"}}

    def tool(self, name, execute):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8"))
        function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == name)
        function.decorator_list = []
        bridge = type("Bridge", (), {})()
        bridge.execute_command = AsyncMock(side_effect=execute)
        scope = {"Context": object, "json": json, "uuid": uuid, "logger": logging.getLogger(__name__),
                 "symbol_metadata": data, "altium_bridge": bridge, "EXCHANGE_DIR": self.root,
                 "write_altium_text": write_altium_text}
        exec(compile(ast.Module(body=[function], type_ignores=[]), name, "exec"), scope)
        return scope[name], bridge

    def test_each_required_field_rejects_missing_empty_and_placeholder_values(self):
        for name in data.REQUIRED_PARAMETERS:
            for value in (None, "", "  ", "TBD", "None", "*"):
                parameters = dict(PARAMETERS)
                if value is None:
                    del parameters[name]
                else:
                    parameters[name] = value
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    self.prepare(parameters)

    def test_rejects_legacy_reserved_duplicate_and_malformed_parameters(self):
        for changes in ({"Pacage": "0603"}, {"pacage metric": "1608"}, {"Description": DESCRIPTION},
                        {"Comment": "x"}, {"Designator": "R?"}, {"mp": "duplicate"},
                        {"Extra": 1}, {"Extra": "bad\x00value"}, {"Bad=name": "value"}, {"MP ": "x"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.prepare({**PARAMETERS, **changes})

    def test_description_requires_searchable_text_and_helpurl_requires_http(self):
        for description in ("", " ", "New Component", "R-10K", "Component", "Resistor"):
            with self.subTest(description=description), self.assertRaises(ValueError):
                self.prepare(description=description)
        for url in ("datasheet.pdf", "file:///datasheet.pdf", "https://", "https://example.com/a b"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.prepare({**PARAMETERS, "HelpURL": url})

    def test_canonical_names_optional_supplier_data_and_unicode_transport(self):
        description = "Resistor, 10k\u03a9, \u00b11%, 0603 | \"precision\"\nsearch text"
        parameters = {name.lower(): value for name, value in PARAMETERS.items()}
        parameters.update({"JLCPCB Part#": "C12345", "Extra": "\u0416 \U0001f680 | = , \"'\nnext"})
        spec, normalized = self.prepare(parameters, description)
        fields = dict(line.split("=", 1) for line in spec.splitlines())
        self.assertEqual(json.loads('"' + fields["description"] + '"'), description)
        self.assertEqual(set(normalized), set(PARAMETERS) | {"JLCPCB Part#", "Extra"})
        self.assertEqual(json.loads('"' + fields["p9.value"] + '"'), parameters["Extra"])

    def test_batch_validates_every_block_preserves_pipes_and_requires_metadata(self):
        text = self.block(parameters={**PARAMETERS, "Extra": "one|two=three"}) + "\n" + self.block("OTHER")
        prepared, symbols = data.prepare_batch(str(self.batch(text)))
        self.assertEqual(len(symbols), 2)
        self.assertEqual(symbols[0]["parameters"]["Extra"], "one|two=three")
        self.assertIn("one|two=three", prepared)
        bad = {key: value for key, value in PARAMETERS.items() if key != "System"}
        for text in (self.block() + "\n" + self.block("OTHER", bad), "SYMBOL|EMPTY|" + DESCRIPTION,
                     self.block() + "\nPARAM|mp|duplicate", "PARAM|Value|10k", self.block() + "\nUNKNOWN|x",
                     self.block() + "\n" + self.block("r-10k")):
            with self.subTest(text=text), self.assertRaises(ValueError):
                data.prepare_batch(str(self.batch(text)))

    def test_single_dispatch_metadata_readback_and_no_call_on_bad_input(self):
        captured = []

        async def execute(command, params):
            self.assertEqual(command, "create_schematic_symbol")
            path = Path(params["metadata_file"])
            captured.append(path)
            self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
            self.assertIn("description=Resistor, 10k", read_altium_text(path))
            return {"success": True, "result": self.result()}

        tool, bridge = self.tool("create_schematic_symbol", execute)
        result = json.loads(asyncio.run(tool(None, "R-10K", DESCRIPTION, [], PARAMETERS)))
        self.assertTrue(result["verification"]["passed"])
        self.assertFalse(result["saved"])
        self.assertFalse(captured[0].exists())
        bridge.execute_command.reset_mock()
        result = json.loads(asyncio.run(tool(None, "R-10K", DESCRIPTION, [], {})))
        self.assertFalse(result["may_have_partial_changes"])
        bridge.execute_command.assert_not_awaited()
        bridge.execute_command.side_effect = None
        wrong = self.result()
        wrong["metadata"]["description"] = "Wrong description"
        bridge.execute_command.return_value = {"success": True, "result": wrong}
        result = json.loads(asyncio.run(tool(None, "R-10K", DESCRIPTION, [], PARAMETERS)))
        self.assertFalse(result["success"])
        self.assertTrue(result["may_have_partial_changes"])
        bridge.execute_command.return_value = {"success": False, "error": "timeout"}
        result = json.loads(asyncio.run(tool(None, "R-10K", DESCRIPTION, [], PARAMETERS)))
        self.assertTrue(Path(result["metadata_file"]).exists())

    def test_batch_dispatch_checks_native_description_and_preflights_all_symbols(self):
        async def execute(command, params):
            self.assertEqual(command, "create_symbols_batch")
            self.assertNotEqual(params["spec_file"], str(path))
            return {"success": True, "result": {"created": 2, "failed": [], "saved": False,
                    "components": [self.result(), self.result("OTHER")]}}

        path = self.batch(self.block() + "\n" + self.block("OTHER"))
        original = path.read_bytes()
        tool, bridge = self.tool("create_symbols_batch", execute)
        result = json.loads(asyncio.run(tool(None, str(path))))
        self.assertTrue(result["success"])
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(list(self.root.glob("symbols_batch_*.txt")))
        bridge.execute_command.reset_mock()
        bad = {key: value for key, value in PARAMETERS.items() if key != "MF"}
        path = self.batch(self.block() + "\n" + self.block("OTHER", bad))
        result = json.loads(asyncio.run(tool(None, str(path))))
        self.assertFalse(result["may_have_partial_changes"])
        bridge.execute_command.assert_not_awaited()

    def test_readback_checks_all_parameters_and_comment(self):
        for field in ("description", "comment", *PARAMETERS):
            result = self.result()
            if field in PARAMETERS:
                result["metadata"]["parameters"][field] = "WRONG"
            else:
                result["metadata"][field] = "WRONG"
            with self.subTest(field=field):
                self.assertFalse(data.verify(result, DESCRIPTION, PARAMETERS)["success"])

    def test_edit_only_batch_and_native_failures_keep_upstream_results(self):
        path = self.batch("EDITSYMBOL|EXISTING\nPINDESC|1|Input pin\n")

        async def execute(command, params):
            self.assertIn("EDITSYMBOL|EXISTING", read_altium_text(params["spec_file"]))
            return {"success": True, "result": {"created": 0, "edited": 1, "failed": [], "problems": []}}

        tool, bridge = self.tool("create_symbols_batch", execute)
        result = json.loads(asyncio.run(tool(None, str(path))))
        self.assertTrue(result["success"])
        self.assertEqual(result["edited"], 1)
        bridge.execute_command.side_effect = None
        bridge.execute_command.return_value = {"success": False, "error": "native operation failed",
            "result": {"success": False, "created": 0, "edited": 0, "failed": ["EXISTING"],
                       "problems": ["EXISTING: pin not found"]}}
        result = json.loads(asyncio.run(tool(None, str(path))))
        self.assertFalse(result["success"])
        self.assertEqual(result["failed"], ["EXISTING"])
        self.assertEqual(result["problems"], ["EXISTING: pin not found"])

    def test_batch_creation_properties_defaults_blank_fields_and_aliases(self):
        cases=[('',1,'U?','standard'),('|2',2,'U?','standard'),('|1|J?',1,'J?','standard'),
               ('|||Standard (No BOM)',1,'U?','standard_no_bom'),('|2|D?|Net Tie (In BOM)',2,'D?','net_tie_bom')]
        for suffix,parts,designator,kind in cases:
            text=self.block().replace('SYMBOL|R-10K|'+DESCRIPTION+'|1','SYMBOL|R-10K|'+DESCRIPTION+suffix)
            with self.subTest(suffix=suffix):
                prepared,symbols=data.prepare_batch(str(self.batch(text)))
                self.assertEqual(symbols[0]['designator_template'],designator)
                self.assertEqual(symbols[0]['component_kind'],kind)
                self.assertEqual(symbols[0]['part_count'],parts)
                self.assertIn(f'SYMBOL|R-10K|{DESCRIPTION}|{parts}|{designator}|{kind}',prepared)

    def test_batch_creation_properties_accept_all_supported_native_kinds(self):
        from library_component_properties import KINDS
        for kind,label in KINDS.items():
            text=self.block().replace('|'+DESCRIPTION+'|1','|'+DESCRIPTION+'|1|J?|'+label,1)
            with self.subTest(kind=kind):
                _,symbols=data.prepare_batch(str(self.batch(text)))
                self.assertEqual(symbols[0]['component_kind'],kind)

    def test_invalid_later_creation_properties_preflight_without_a_native_call(self):
        tool,bridge=self.tool('create_symbols_batch',lambda *_:None)
        bad=['|1|J1|standard','|1|1J?|standard','|1|J??|standard','|1|\u0416?|standard',
             '|1|'+'A'*64+'?|standard','|1|J?|unknown','|1|J?|5','|1|J?|standard|extra']
        for suffix in bad:
            second=self.block('SECOND').replace('|'+DESCRIPTION+'|1','|'+DESCRIPTION+suffix,1)
            with self.subTest(suffix=suffix):
                path=self.batch(self.block()+'\n'+second)
                result=json.loads(asyncio.run(tool(None,str(path))))
                self.assertFalse(result['success']);self.assertFalse(result['may_have_partial_changes'])
                bridge.execute_command.assert_not_awaited()
        with self.assertRaises(ValueError):
            data.prepare_batch(str(self.batch('EDITSYMBOL|EXISTING|J?|standard')))

    def test_batch_properties_dispatch_once_and_verify_native_readback(self):
        path=self.batch(self.block().replace('|'+DESCRIPTION+'|1','|'+DESCRIPTION+'|1|D?|Standard (No BOM)',1))
        original=path.read_bytes()
        async def execute(command,params):
            self.assertEqual(command,'create_symbols_batch')
            self.assertIn('|1|D?|standard_no_bom',read_altium_text(params['spec_file']))
            item=self.result();item['metadata'].update(designator_template='D?',component_kind='standard_no_bom')
            return {'success':True,'result':dict(created=1,edited=0,saved=False,components=[item],failed=[],problems=[])}
        tool,bridge=self.tool('create_symbols_batch',execute)
        result=json.loads(asyncio.run(tool(None,str(path))))
        self.assertTrue(result['success']);self.assertEqual(bridge.execute_command.await_count,1)
        self.assertEqual(path.read_bytes(),original)
        for field in ('designator_template','component_kind'):
            item=self.result();item['metadata'].update(designator_template='D?',component_kind='standard_no_bom')
            item['metadata'][field]='wrong'
            bridge.execute_command.side_effect=None
            bridge.execute_command.return_value={'success':True,'result':dict(created=1,components=[item],failed=[],problems=[])}
            result=json.loads(asyncio.run(tool(None,str(path))))
            self.assertFalse(result['success']);self.assertTrue(result['may_have_partial_changes'])
            self.assertIn('native '+field+' mismatch',result['verification']['errors'])


if __name__ == "__main__":
    unittest.main()
