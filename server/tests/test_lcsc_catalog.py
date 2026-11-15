"""LCSC extraction, HTTP contracts and supplier-only SchLib writes."""

import ast
import asyncio
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from typing import Optional
from unittest.mock import AsyncMock, patch
from urllib import error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lcsc_catalog as data
import library_component_data
import symbol_metadata


def payload(**changes):
    return {"productCode": "C136648", "productModel": "LMR16030SDDAR", "brandNameEn": "TI",
            "wmCatalogNameEn": "DC-DC Converters", "firstWmCatalogNameEn": "Power management",
            "productIntroEn": "Buck converter, 4.3V~60V, 3A, SO-8-EP", "encapStandard": "SO-8-EP",
            "stockNumber": 0, "minBuyNumber": 10, "pdfUrl": "https://example.com/datasheet.pdf",
            "productPriceList": [{"ladder": 1, "currencyPrice": 0.6}, {"ladder": 100, "currencyPrice": 0.4053}],
            "paramVOList": [{"paramNameEn": "Operating Voltage", "paramValueEn": "4.3V~60V"},
                            {"paramNameEn": "Temperature", "paramValueEn": "-40\u2103~+125\u2103"},
                            {"paramNameEn": "Unknown", "paramValueEn": "-"}], **changes}


class CatalogTest(unittest.TestCase):
    def test_full_card_preserves_units_zero_stock_and_moq_price_breaks(self):
        card = data.component_from_payload(payload(), 100)
        self.assertEqual(card["parameters"]["Operating Voltage"], "4.3V~60V")
        self.assertEqual(card["parameters"]["Temperature"], "-40\u2103~+125\u2103")
        self.assertNotIn("Unknown", card["parameters"])
        self.assertEqual(card["stock"], 0)
        self.assertEqual(card["order_qty"], 100)
        self.assertEqual(card["unit_price"], 0.4053)
        self.assertEqual(card["batch_cost"], 40.53)
        self.assertEqual(data.component_from_payload(payload(), 1)["order_qty"], 10)

    def test_unknown_metadata_and_unavailable_price_tiers_are_not_invented(self):
        card = data.component_from_payload(payload(stockNumber=None, minBuyNumber=None), 1)
        for key in ("stock", "moq", "order_qty", "unit_price", "batch_cost"):
            self.assertIsNone(card[key])
        parameters = data.library_parameters(card)
        self.assertEqual(parameters["LCSC Stock"], "")
        self.assertEqual(parameters["LCSC Unit Price"], "")
        card = data.component_from_payload(payload(productPriceList=[{"ladder": 100, "currencyPrice": 0.4}]), 1)
        self.assertIsNone(card["unit_price"])

    def test_zero_price_numeric_fallbacks_and_nonfinite_values(self):
        card = data.component_from_payload(payload(productPriceList=[{"quantity": "1", "currencyPrice": 0, "usdPrice": 999}]), 1)
        self.assertEqual(card["unit_price"], 0)
        self.assertEqual(data.library_parameters(card)["LCSC Unit Price"], "0 USD")
        for value in (True, "nan", "inf", -1, "bad", None):
            self.assertIsNone(data.number(value))
        self.assertIsNone(data.number(1.5, integer=True))

    def test_only_supplier_fields_are_emitted_and_creation_accepts_them(self):
        card = data.component_from_payload(payload(), 100)
        fields = data.library_parameters(card)
        self.assertTrue(all(name.startswith("LCSC ") or name == "JLCPCB Part#" for name in fields))
        self.assertTrue(all(isinstance(value, str) for value in fields.values()))
        self.assertEqual(fields["LCSC Batch Cost"], "40.53 USD")
        self.assertEqual(fields["LCSC Datasheet URL"], card["datasheet_url"])
        core = {"Value": "Buck regulator", "MF": "TI", "MP": card["mpn"], "HelpURL": card["datasheet_url"],
                "Package": "SO-8-EP", "Type": "IC", "Instructions": "Fit", "System": "Power"}
        self.assertEqual(symbol_metadata.validate(card["mpn"], card["description"], {**core, **fields}), {**core, **fields})

    def test_lookup_prefers_exact_mpn_and_hydrates_detail_instead_of_search_summary(self):
        client = data.LcscClient()
        summary = [{"productCode": "C9", "productModel": "OTHER"},
                   {"productCode": "C136648", "productModel": "LMR16030SDDAR"}]
        with patch.object(client, "_request_result", side_effect=[{"dataList": summary}, payload()]) as request:
            result = client.lookup("lmr16030sddar", 100, 5)
        self.assertEqual(result["result_count"], 1)
        self.assertEqual(result["components"][0]["parameters"]["Operating Voltage"], "4.3V~60V")
        self.assertEqual(request.call_args.kwargs["query"], {"productCode": "C136648"})

    def test_direct_code_avoids_search_empty_results_are_valid_and_codes_are_deduplicated(self):
        client = data.LcscClient()
        with patch.object(client, "_request_result", return_value=payload()) as request:
            self.assertEqual(client.lookup("c136648")["result_count"], 1)
            self.assertEqual(request.call_count, 1)
        with patch.object(client, "_request_result", return_value={"dataList": []}):
            self.assertEqual(client.lookup("no-such-part", include_historical=False)["components"], [])
        row = {"productCode": "C136648", "productModel": "LMR16030SDDAR"}
        with patch.object(client, "_request_result", side_effect=[{"dataList": [row, row]}, payload()]) as request:
            self.assertEqual(client.lookup("regulator")["result_count"], 1)
            self.assertEqual(request.call_count, 2)

    def test_invalid_inputs_identity_or_schema_fail_explicitly(self):
        client = data.LcscClient()
        with patch.object(client, "_request_result") as request:
            for query, qty, limit in (("", 1, 5), ("a\x00b", 1, 5), (None, 1, 5), ("IC", True, 5),
                                      ("IC", 0, 5), ("IC", 1, 11), ("IC", 1, False)):
                with self.subTest(query=query, qty=qty, limit=limit), self.assertRaises(ValueError):
                    client.lookup(query, qty, limit)
            request.assert_not_called()
        for bad in ({"productCode": "C9", "productModel": "OTHER"}, None):
            with patch.object(client, "_request_result", return_value=bad), self.assertRaises(data.LcscError):
                client.get_product("C136648")
        for bad in (payload(productModel=""), payload(productCode="wrong"), payload(paramVOList={"bad": 1})):
            with self.subTest(bad=bad), self.assertRaises(data.LcscError):
                data.component_from_payload(bad)

    def test_http_contract_envelopes_invalid_json_and_rate_limit(self):
        response = io.BytesIO(json.dumps({"code": 200, "result": payload()}).encode())
        with patch.object(data.request, "urlopen", return_value=response) as request:
            self.assertEqual(data.LcscClient().get_product("C136648")["lcsc_id"], "C136648")
            sent = request.call_args.args[0]
            self.assertEqual(sent.full_url, data.BASE_URL + "/product/detail?productCode=C136648")
            self.assertEqual(sent.get_header("Cookie"), "currencyCode=USD")
        for raw in (b"not json", b"[]", b'{"code":403,"msg":"denied"}', b'{"code":200}'):
            with self.subTest(raw=raw), patch.object(data.request, "urlopen", return_value=io.BytesIO(raw)), self.assertRaises(data.LcscError):
                data.LcscClient().get_product("C136648")
        limited = error.HTTPError(data.BASE_URL, 429, "Rate limit", None, None)
        with patch.object(data.request, "urlopen", side_effect=limited) as request, self.assertRaises(data.LcscError):
            data.LcscClient().get_product("C136648")
        self.assertEqual(request.call_count, 1)

    def test_transient_http_errors_retry_once_and_body_is_read_only_search(self):
        transient = error.HTTPError(data.BASE_URL, 503, "Unavailable", None, None)
        response = io.BytesIO(b'{"code":200,"result":{"dataList":[]}}')
        with patch.object(data.request, "urlopen", side_effect=[transient, response]) as request, patch.object(data.time, "sleep"):
            self.assertTrue(data.LcscClient().lookup("LMR16030", include_historical=False)["success"])
            self.assertEqual(request.call_count, 2)
            sent = request.call_args.args[0]
            self.assertEqual(sent.get_method(), "POST")
            self.assertEqual(json.loads(sent.data)["keyword"], "LMR16030")


class CatalogToolsTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2] / ".altium-mcp" / "unit_tests"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.library = Path(self.temp.name) / "Parts.SchLib"
        self.library.touch()
        self.component = data.component_from_payload(payload(), 100)
        self.snapshot = {"parameters": {"MP": self.component["mpn"], "MF": "Texas Instruments", "Package": "SO-8-EP", "Value": "Regulator"},
                         "description": "Manual native component description", "comment": "Manual comment", "models": []}
        self.metadata_tool = AsyncMock(return_value=json.dumps({"success": True, "after": self.snapshot, "saved": False}))

    def tool(self, name):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8"))
        function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == name)
        function.decorator_list = []
        scope = {"Context": object, "json": json, "asyncio": asyncio, "lcsc_catalog": data, "Optional": Optional,
                 "library_component_data": library_component_data, "set_library_component_data": self.metadata_tool}
        exec(compile(ast.Module(body=[function], type_ignores=[]), name, "exec"), scope)
        return scope[name]

    def apply(self, code="C136648", **options):
        return json.loads(asyncio.run(self.tool("set_library_component_lcsc_data")(None, str(self.library), "TEST", code, 100, **options)))

    def test_info_has_no_altium_dependency_and_reports_network_failure(self):
        tool = self.tool("get_lcsc_component_info")
        with patch.object(data.LcscClient, "lookup", return_value={"success": True, "components": [self.component]}):
            self.assertTrue(json.loads(asyncio.run(tool(None, "LMR16030SDDAR")))["success"])
        self.metadata_tool.assert_not_awaited()
        with patch.object(data.LcscClient, "lookup", side_effect=data.LcscError("offline")):
            result = json.loads(asyncio.run(tool(None, "C136648")))
        self.assertFalse(result["success"])
        self.metadata_tool.assert_not_awaited()

    def test_apply_sends_only_supplier_fields_to_existing_verified_writer(self):
        read = {"success": True, "saved": False, "after": self.snapshot}
        write = {"success": True, "saved": False, "changed": False, "verification": {"passed": True},
                 "before": self.snapshot, "after": self.snapshot}
        self.metadata_tool.side_effect = [json.dumps(read), json.dumps(write)]
        with patch.object(data.LcscClient, "get_product", return_value=self.component):
            result = self.apply()
        self.assertTrue(result["success"])
        self.assertFalse(result["changed"])
        self.assertEqual(self.metadata_tool.await_count, 2)
        self.assertEqual(self.metadata_tool.await_args_list[0].kwargs, {})
        self.assertEqual(self.metadata_tool.await_args_list[1].kwargs, {"parameters": data.library_parameters(self.component)})
        self.assertEqual(result["lcsc_id"], "C136648")

    def test_mismatched_or_ambiguous_mp_never_reaches_write(self):
        for parameters in ({"MP": "OTHER"}, {}, {"MP": self.component["mpn"], "mp": self.component["mpn"]}):
            snapshot = {**self.snapshot, "parameters": parameters}
            self.metadata_tool.reset_mock()
            self.metadata_tool.return_value = json.dumps({"success": True, "saved": False, "after": snapshot})
            with patch.object(data.LcscClient, "get_product", return_value=self.component):
                result = self.apply()
            self.assertFalse(result["success"])
            self.assertFalse(result["may_have_partial_changes"])
            self.assertEqual(self.metadata_tool.await_count, 1)

    def test_same_mpn_wrong_missing_or_ambiguous_manufacturer_never_writes(self):
        for mf in ({"MF":"Tokmas"}, {}, {"MF":""}, {"MF":"unknown"}, {"MF":"TI","mf":"TI"}):
            parameters = {k:v for k,v in self.snapshot['parameters'].items() if k!='MF'} | mf
            self.metadata_tool.reset_mock()
            self.metadata_tool.return_value = json.dumps({'success':True,'saved':False,'after':{**self.snapshot,'parameters':parameters}})
            with patch.object(data.LcscClient, 'get_product', return_value=self.component):
                result = self.apply(allow_package_mismatch=True)
            self.assertFalse(result['success'])
            self.assertFalse(result['may_have_partial_changes'])
            self.assertEqual(result['conflicts'][0]['field'], 'MF')
            self.assertEqual(result['identity_check']['mpn']['status'], 'match')
            self.assertEqual(self.metadata_tool.await_count, 1)

    def test_package_conflict_blocks_and_explicit_override_preserves_core_values(self):
        snapshot = {**self.snapshot, 'parameters':{**self.snapshot['parameters'],'Package':'SOT-23'}}
        read = {'success':True,'saved':False,'after':snapshot}
        self.metadata_tool.return_value = json.dumps(read)
        with patch.object(data.LcscClient, 'get_product', return_value=self.component):
            result = self.apply()
        self.assertFalse(result['success'])
        self.assertTrue(result['identity_verified'])
        self.assertTrue(result['package_conflict'])
        self.assertEqual(result['package_check']['symbol'], 'SOT-23')
        self.assertEqual(result['package_check']['catalog'], 'SO-8-EP')
        self.assertEqual(self.metadata_tool.await_count, 1)
        self.metadata_tool.reset_mock()
        self.metadata_tool.side_effect = [json.dumps(read), json.dumps({'success':True,'saved':False,'after':snapshot})]
        with patch.object(data.LcscClient, 'get_product', return_value=self.component):
            result = self.apply(allow_package_mismatch=True)
        self.assertTrue(result['success'])
        self.assertTrue(result['package_mismatch_allowed'])
        self.assertEqual(result['warnings'][0]['code'], 'package_mismatch_allowed')
        fields = self.metadata_tool.await_args.kwargs['parameters']
        self.assertNotIn('Package', fields)
        self.assertNotIn('MP', fields)
        self.assertNotIn('MF', fields)

    def test_unknown_packages_are_explicit_and_duplicates_always_block(self):
        for parameters, package, expected in (({'MP':self.component['mpn'],'MF':'TI'},'SO-8-EP','symbol_missing'),
                                               (self.snapshot['parameters'],'','catalog_missing')):
            component = {**self.component,'package':package}
            snapshot = {**self.snapshot,'parameters':parameters}
            report = data.check_target(snapshot, component)
            self.assertEqual(report['package_check']['status'], expected)
            self.assertEqual(report['warnings'][0]['code'], 'package_unverified')
        snapshot = {**self.snapshot,'parameters':{**self.snapshot['parameters'],'package':'SO-8-EP'}}
        with self.assertRaises(data.LcscTargetConflict):
            data.check_target(snapshot, self.component, True)

    def test_provenance_date_and_regional_status_are_supplier_fields(self):
        fields = data.library_parameters(self.component)
        self.assertEqual(fields['LCSC Source URL'], self.component['source_url'])
        self.assertEqual(fields['LCSC Data Source'], 'lcsc_detail_api')
        self.assertEqual(fields['LCSC Fetched At'], self.component['fetched_at'])
        self.assertEqual(fields['LCSC Sales Status'], 'unknown')
        self.assertEqual(fields['LCSC Sales Region'], 'international')
        self.assertEqual(json.loads(fields['LCSC Sales Status Evidence']), {})
        updated = data.component_from_payload(payload(isForeignOnsale=False, stockNumber=100, productCycle='normal'))
        fields = data.library_parameters(updated)
        self.assertEqual(fields['LCSC Sales Status'], 'not_for_sale')
        self.assertEqual(fields['LCSC Lifecycle Raw'], 'normal')
        self.assertEqual(json.loads(fields['LCSC Sales Status Evidence'])['isForeignOnsale'], False)

    def test_manufacturer_and_package_normalization_are_conservative(self):
        snapshot = {**self.snapshot,'parameters':{**self.snapshot['parameters'],'Package':'so - 8 - ep'}}
        self.assertTrue(data.check_target(snapshot, self.component)['identity_verified'])
        for changes in ({'manufacturer':''},{'mpn':'LMR16030SDDA'}):
            with self.assertRaises(data.LcscTargetConflict):
                data.check_target(self.snapshot, {**self.component,**changes}, True)
        with patch.object(data.LcscClient, 'get_product') as get:
            self.assertFalse(self.apply(allow_package_mismatch='true')['success'])
            get.assert_not_called()

    def test_bad_target_or_catalog_failure_never_reaches_altium(self):
        with patch.object(data.LcscClient, "get_product") as get:
            self.assertFalse(self.apply("not-a-code")["success"])
            get.assert_not_called()
        with patch.object(data.LcscClient, "get_product", side_effect=data.LcscError("offline")):
            self.assertFalse(self.apply()["success"])
        self.metadata_tool.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
