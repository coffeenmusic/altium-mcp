"""Strict order codes, independent sale status, batch isolation and page fallback."""

import asyncio
import io
import json
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch
from urllib import error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lcsc_catalog as data
import lcsc_history as history
from server.tests import test_lcsc_catalog as fixtures

payload = fixtures.payload


def nxp(**changes):
    return payload(**dict(productCode="C58988", productModel="TJA1051T/3,118", brandNameEn="NXP") | changes)


def script_page(products):
    return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps({"props":{"pageProps":products}}) + '</script>'


def cn_page(code="C907510", mpn="DMT3N4R2U224M3DTA0"):
    return (f'<h1>{mpn}</h1><dl><dt>\u54c1\u724c\u540d\u79f0</dt><dd>muRata(\u6751\u7530)</dd>'
            f'<dt>\u5546\u54c1\u578b\u53f7</dt><dd>{mpn}</dd>'
            f'<dt>\u5546\u54c1\u7f16\u53f7</dt><dd>{code}</dd>'
            '<dt>\u5546\u54c1\u5c01\u88c5</dt><dd>SMD</dd></dl>'
            '<table><tr><th>\u5c5e\u6027</th><th>Value</th></tr><tr><td>Capacitance</td><td>0.22F</td></tr></table>'
            '<button>\u5546\u54c1\u5df2\u505c\u552e</button>'
            '<a href="https://atta.szlcsc.com/file/test.pdf">Datasheet</a>')


class LookupTests(unittest.TestCase):
    def test_brand_filter_precedes_preference_and_detail_is_authoritative(self):
        client = data.LcscClient()
        rows = [dict(productCode="C1", productModel="TJA1051T/3", brandNameEn="Tokmas"),
                dict(productCode="C58988", productModel="TJA1051T/3,118", brandNameEn="NXP")]
        with patch.object(client, "_request_result", side_effect=[{"dataList":rows}, nxp()]):
            result = client.lookup("TJA1051T/3", manufacturer="NXP", include_historical=False)
        self.assertEqual([c["lcsc_id"] for c in result["components"]], ["C58988"])
        self.assertEqual(result["components"][0]["orderable_mpn"], "TJA1051T/3,118")
        with patch.object(client, "_request_result", side_effect=[{"dataList":rows}, nxp(brandNameEn="Tokmas")]):
            result = client.lookup("TJA1051T/3", manufacturer="NXP", include_historical=False)
        self.assertEqual(result["components"], [])

    def test_exact_order_mpn_preserves_punctuation_and_packaging_suffix(self):
        client = data.LcscClient()
        rows = [dict(productCode="C1", productModel="TJA1051T/3", brandNameEn="NXP"),
                dict(productCode="C58988", productModel="TJA1051T/3,118", brandNameEn="NXP")]
        with patch.object(client, "_request_result", side_effect=[{"dataList":rows}, nxp()]) as fetch:
            result = client.lookup("TJA1051T/3", exact_mpn="TJA1051T/3,118", include_historical=False)
        self.assertEqual(fetch.call_args_list[0].kwargs['body']['keyword'], "TJA1051T/3,118")
        self.assertTrue(result["components"][0]["exact_mpn_match"])
        with patch.object(client, "get_product", return_value=data.component_from_payload(nxp())), patch.object(client, "_historical_search") as fallback:
            result = client.lookup("C58988", exact_mpn="TJA1051T3,118")
        self.assertEqual(result["components"], [])
        fallback.assert_not_called()

    def test_filter_limit_applies_after_multiple_search_pages_and_duplicate_codes(self):
        client = data.LcscClient()
        wrong = dict(productCode="C1", productModel="PART", brandNameEn="Other")
        right = dict(productCode="C58988", productModel="TJA1051T/3,118", brandNameEn="NXP")
        with patch.object(client, "_request_result", side_effect=[{"dataList":[wrong]*30, "totalPage":2},
                                                                 {"dataList":[right,right]}, nxp()]) as fetch:
            result = client.lookup("TJA", limit=1, manufacturer="NXP", include_historical=False)
        self.assertEqual(result["search_pages"], 2)
        self.assertEqual(fetch.call_count, 3)
        self.assertEqual(result["result_count"], 1)
        with patch.object(client, "_request_result", return_value={"dataList":[wrong]*30,"totalPage":9}):
            result = client.lookup("TJA", manufacturer="NXP", include_historical=False)
        self.assertTrue(result["search_truncated"])
        self.assertFalse(result["complete"])

    def test_sale_status_is_independent_of_zero_stock_and_regional_visibility(self):
        self.assertEqual(data.component_from_payload(nxp(isForeignOnsale=True, stockNumber=0))["sales_status"], "on_sale")
        card = data.component_from_payload(nxp(isForeignOnsale=False, productCycle="normal"))
        self.assertEqual(card["sales_status"], "not_for_sale")
        self.assertEqual(card["lifecycle_raw"], "normal")
        self.assertEqual(card["sales_region"], "international")
        self.assertEqual(data.component_from_payload(nxp(productCycle="obsolete"))["sales_status"], "discontinued")
        for flag in (None, "false", 0):
            self.assertEqual(data.component_from_payload(nxp(isForeignOnsale=flag))["sales_status"], "unknown")

    def test_manufacturer_aliases_do_not_use_substring_matching(self):
        for a,b in (("TI","Texas Instruments"),("NXP","NXP Semiconductors"),("Murata","muRata(\u6751\u7530)")):
            self.assertEqual(data.manufacturer_key(a), data.manufacturer_key(b))
        self.assertNotEqual(data.manufacturer_key("NXP"), data.manufacturer_key("NXP-compatible"))

    def test_batch_preflight_dedup_order_and_individual_network_errors(self):
        client = data.LcscClient()
        def lookup(**options):
            if options['query'] == 'offline': raise data.LcscError('network unavailable')
            return dict(success=True, query=options['query'], result_count=0, components=[], saved=False)
        with patch.object(client, 'lookup', side_effect=lookup) as fetch:
            result = client.lookup_many([{'id':'K07','query':'C58988'}, 'offline', {'id':'Again','query':'C58988'}])
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual([r['query'] for r in result['results']], ['C58988','offline','C58988'])
        self.assertEqual(result['results'][0]['id'], 'K07')
        self.assertEqual(result['results'][2]['id'], 'Again')
        self.assertTrue(result['partial_success'])
        self.assertEqual(result['failed_count'], 1)
        with patch.object(client, 'lookup') as fetch, self.assertRaises(ValueError):
            client.lookup_many(['C58988', {'query':'C136648', 'qty':0}])
        fetch.assert_not_called()

    def test_batch_has_bounded_parallelism(self):
        client = data.LcscClient()
        lock = threading.Lock()
        active = peak = 0
        def lookup(**options):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(.02)
            with lock: active -= 1
            return dict(success=True, query=options['query'], components=[], result_count=0)
        with patch.object(client, 'lookup', side_effect=lookup):
            result = client.lookup_many([f'C{i}' for i in range(12)])
        self.assertTrue(result['success'])
        self.assertLessEqual(peak, 3)
        self.assertGreater(peak, 1)

    def test_invalid_options_fail_without_http(self):
        client = data.LcscClient()
        for kw in ({'manufacturer':[]}, {'manufacturer':'()'}, {'exact_mpn':True}, {'include_historical':1},
                   {'historical_url':'http://localhost/private'}, {'historical_url':'https://example.com/part'},
                   {'historical_url':'https://item.szlcsc.com/973089.html', 'include_historical':False}):
            with self.subTest(kw=kw), patch.object(client, '_request_result') as fetch, self.assertRaises(ValueError):
                client.lookup('C58988', **kw)
            fetch.assert_not_called()
        for values in ([], ['C1']*26, ['C1', {'query':'x', 'extra':True}]):
            with self.assertRaises(ValueError): client.lookup_many(values)


class HistoricalTests(unittest.TestCase):
    def test_domestic_card_supplies_identity_parameters_and_explicit_sale_stop(self):
        client = data.LcscClient()
        with patch.object(client, 'get_product', side_effect=data.LcscNotFound('missing')), patch.object(client, '_request_page', return_value=cn_page()) as fetch:
            result = client.lookup('C907510', manufacturer='Murata', exact_mpn='DMT3N4R2U224M3DTA0',
                                   historical_url='https://item.szlcsc.com/973089.html')
        self.assertEqual(fetch.call_count, 1)
        card = result['components'][0]
        self.assertTrue(card['historical'])
        self.assertEqual(card['sales_status'], 'discontinued')
        self.assertEqual(card['sales_region'], 'CN')
        self.assertEqual(card['parameters']['Capacitance'], '0.22F')
        self.assertEqual(card['source_url'], 'https://item.szlcsc.com/973089.html')
        self.assertEqual(card['lcsc_parameters']['LCSC URL'], card['source_url'])
        for key in ('stock','moq','unit_price','batch_cost','order_qty'):
            self.assertIsNone(card[key])

    def test_json_page_keeps_full_mpn_but_never_reuses_static_prices_as_live_quote(self):
        client = data.LcscClient()
        with patch.object(client, 'get_product', side_effect=data.LcscNotFound('missing')), patch.object(client, '_request_page', return_value=script_page(nxp(isForeignOnsale=True))):
            result = client.lookup('C58988', exact_mpn='TJA1051T/3,118')
        card = result['components'][0]
        self.assertEqual(card['orderable_mpn'], 'TJA1051T/3,118')
        self.assertIsNone(card['unit_price'])
        self.assertEqual(card['sales_status'], 'on_sale')

    def test_full_detail_wins_over_jsonld_summary_and_recommendations(self):
        html = '<script type="application/ld+json">' + json.dumps({'@type':'Product','sku':'C58988',
                'mpn':'TJA1051T/3,118','brand':{'name':'NXP'}}) + '</script>' + script_page(nxp())
        client = data.LcscClient()
        with patch.object(client, 'get_product', side_effect=data.LcscNotFound('missing')), patch.object(client, '_request_page', return_value=html):
            result = client.lookup('C58988')
        self.assertEqual(result['components'][0]['parameters']['Operating Voltage'], '4.3V~60V')

    def test_search_discovers_and_hydrates_cards_instead_of_returning_summaries(self):
        client = data.LcscClient()
        search = '<a href="https://item.szlcsc.com/973089.html">Card</a>'
        def page(url): return cn_page() if 'item.szlcsc.com' in url else search
        with patch.object(client, '_request_result', return_value={'dataList':[]}), patch.object(client, '_request_page', side_effect=page):
            result = client.lookup('DMT3N4R2U224M3DTA0', manufacturer='Murata')
        self.assertEqual(result['components'][0]['lcsc_id'], 'C907510')
        self.assertIn('Capacitance', result['components'][0]['parameters'])

    def test_blocked_sources_are_incomplete_failure_not_false_not_found(self):
        client = data.LcscClient()
        with patch.object(client, 'get_product', side_effect=data.LcscNotFound('missing')), patch.object(client, '_request_page', side_effect=data.LcscError('HTTP 405')):
            result = client.lookup('C907510')
        self.assertFalse(result['success'])
        self.assertFalse(result['complete'])
        self.assertTrue(all(s['status']=='unavailable' for s in result['historical_search']['sources']))

    def test_wrong_code_brand_or_mpn_in_recommendations_never_passes(self):
        client = data.LcscClient()
        with patch.object(client, 'get_product', side_effect=data.LcscNotFound('missing')), patch.object(client, '_request_page', return_value=script_page(nxp())):
            result = client.lookup('C907510', manufacturer='Murata')
        self.assertFalse(result['success'])
        self.assertEqual(result['components'], [])
        bad = script_page([nxp(), nxp(brandNameEn='Tokmas')])
        with self.assertRaises(ValueError): history.page_products(bad, 'https://www.lcsc.com/product-detail/C58988.html')

    def test_recommendation_never_overrides_missing_or_mismatched_primary_page(self):
        missing = script_page({'webData':None, 'recommended':[nxp()]})
        self.assertEqual(history.page_products(missing, 'https://www.lcsc.com/product-detail/C58988.html'), [])
        wrong = payload(productCode='C1')
        html = script_page({'webData':wrong, 'recommended':[nxp()]})
        with self.assertRaises(ValueError):
            history.page_products(html, 'https://www.lcsc.com/product-detail/C58988.html')
        self.assertEqual([r['productCode'] for r in history.page_products(html, 'https://www.lcsc.com/product-detail/C1.html')], ['C1'])

    def test_page_fetch_limit_invalid_encoding_and_http_failures(self):
        client = data.LcscClient()
        with patch.object(data.request,'urlopen',return_value=io.BytesIO(b'\xff')), self.assertRaises(data.LcscError):
            client._request_page('https://www.lcsc.com/product-detail/C1.html')
        failure = error.HTTPError('https://item.szlcsc.com/1.html',405,'Not Allowed',None,None)
        with patch.object(data.request,'urlopen',side_effect=failure), self.assertRaises(data.LcscError):
            client._request_page('https://item.szlcsc.com/1.html')
        for content in (b'{"l1":"var arg1=challenge","l2":"GET"}opaque',
                        b'<html><title>Just a moment...</title></html>'):
            with patch.object(data.request,'urlopen',return_value=io.BytesIO(content)), self.assertRaises(data.LcscError):
                client._request_page('https://item.szlcsc.com/1.html')
        self.assertEqual(history.discover('<a href="https://attacker.invalid/1.html">Bad</a><a href="//item.szlcsc.com/973089.html">Good</a>',
                                         'https://so.szlcsc.com/global.html'), ['https://item.szlcsc.com/973089.html'])


class LookupToolsTest(unittest.TestCase):
    setUp = fixtures.CatalogToolsTest.setUp
    tool = fixtures.CatalogToolsTest.tool

    def test_batch_handler_is_read_only_and_keeps_input_failures_explicit(self):
        tool = self.tool('get_lcsc_component_info')
        with patch.object(data.LcscClient, 'lookup_many', return_value={'success':True,'results':[]}) as lookup:
            result = json.loads(asyncio.run(tool(None, queries=['C58988','C136648'])))
        self.assertTrue(result['success'])
        lookup.assert_called_once()
        self.metadata_tool.assert_not_awaited()
        with patch.object(data.LcscClient, 'lookup_many') as lookup:
            result = json.loads(asyncio.run(tool(None, query='C1', queries=['C2'])))
        self.assertFalse(result['success'])
        lookup.assert_not_called()


if __name__ == '__main__':
    unittest.main()
