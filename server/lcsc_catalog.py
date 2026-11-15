"""Small LCSC search/detail client adapted from the user's lcsc_tool.py.

Origin: altium-delphiscript-toolkit/lcsc_tool.py, LcscClient and
component_from_payload. Only catalog HTTP requests, field parsing and quantity
pricing are retained; this module has no Altium, CLI, cache or job dependencies.
"""

from datetime import datetime, timezone
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import math
import re
import time
from urllib import error, parse, request

import lcsc_history
from lcsc_history import manufacturer_key


BASE_URL = "https://wmsc.lcsc.com/ftps/wm"
MAX_RESPONSE_BYTES = 20 * 1024 * 1024
CODE = re.compile(r"C[0-9]+", re.IGNORECASE)
MAX_BATCH = 25
SEARCH_PAGE_SIZE = 30
MAX_SEARCH_PAGES = 3


class LcscError(RuntimeError):
    """An HTTP, upstream schema or missing-product failure."""


class LcscNotFound(LcscError):
    """The detail endpoint returned no identity-bearing product."""


class LcscTargetConflict(ValueError):
    """A symbol/card mismatch, carrying all preflight evidence without writes."""

    def __init__(self, message, report):
        super().__init__(message)
        self.report = report


def validate_text(value, label, optional=False):
    if (not isinstance(value, str) or len(value) > 256 or any(ord(c) < 32 for c in value)
            or (not optional and not value.strip())):
        raise ValueError(f"{label} must be {'an optional' if optional else 'a nonempty'} string of at most 256 characters without control characters")
    return value.strip()


def lookup_options(query, qty=1, limit=5, manufacturer="", exact_mpn="", include_historical=True, historical_url=""):
    validate_quantity(qty)
    query = validate_text(query, "query", True)
    exact_mpn = validate_text(exact_mpn, "exact_mpn", True)
    manufacturer = validate_text(manufacturer, "manufacturer", True)
    if not query and not exact_mpn:
        raise ValueError("query or exact_mpn is required")
    if type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError("limit must be an integer from 1 to 10")
    if type(include_historical) is not bool:
        raise ValueError("include_historical must be a boolean")
    if manufacturer and not manufacturer_key(manufacturer):
        raise ValueError("manufacturer must contain a name")
    if historical_url:
        historical_url = lcsc_history.product_url(historical_url)
        if not include_historical:
            raise ValueError("historical_url requires include_historical=True")
    elif not isinstance(historical_url, str):
        raise ValueError("historical_url must be a string")
    return dict(query=query or exact_mpn, qty=qty, limit=limit, manufacturer=manufacturer,
                exact_mpn=exact_mpn, include_historical=include_historical, historical_url=historical_url)


def sales_state(payload):
    evidence = {key:payload[key] for key in ("isForeignOnsale", "isForeignDisplay", "productCycle", "isPreSale") if key in payload}
    status = payload.get("_page_sales_status", "unknown")
    if status == "unknown":
        if clean_text(payload.get("productCycle")).casefold() in {"discontinued", "obsolete", "eol", "end_of_life"}:
            status = "discontinued"
        elif payload.get("isForeignOnsale") is False:
            status = "not_for_sale"
        elif payload.get("isForeignOnsale") is True:
            status = "on_sale"
    if "_page_sales_status" in payload:
        evidence["page_status"] = payload["_page_sales_status"]
    return status, evidence


def validate_quantity(qty):
    if type(qty) is not int or not 1 <= qty <= 1_000_000_000:
        raise ValueError("qty must be an integer from 1 to 1000000000")


def validate_code(value):
    if not isinstance(value, str) or not CODE.fullmatch(value.strip()):
        raise ValueError("lcsc_id must be an exact LCSC code such as C136648")
    return value.strip().upper()


def clean_text(value):
    return re.sub(r"\s+", " ", str(value)).strip() if value is not None else ""


def number(value, integer=False):
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        result = float(value)
        if not math.isfinite(result) or result < 0 or (integer and not result.is_integer()):
            return None
        return int(result) if integer else result
    except (ValueError, TypeError, OverflowError):
        return None


def first_value(values, keys):
    for key in keys:
        if values.get(key) is not None and values[key] != "":
            return values[key]
    return None


def http_url(value):
    value = clean_text(value)
    if value.startswith("//"):
        value = "https:" + value
    try:
        url = parse.urlsplit(value)
        return value if url.scheme.lower() in {"http", "https"} and url.netloc else ""
    except ValueError:
        return ""


def component_from_payload(payload, qty=1):
    """Retain catalog parameter names/units; do not infer electrical ratings."""
    validate_quantity(qty)
    if not isinstance(payload, dict):
        raise LcscError("LCSC product is not a JSON object")
    try:
        lcsc_id = validate_code(payload.get("productCode"))
    except ValueError as exc:
        raise LcscError("LCSC product has no valid productCode") from exc
    mpn = payload.get("productModel")
    if not isinstance(mpn, str) or not clean_text(mpn):
        raise LcscError("LCSC product has no manufacturer part number")
    mpn = clean_text(mpn)
    raw_prices = payload.get("productPriceList") or payload.get("priceList") or []
    if not isinstance(raw_prices, list):
        raise LcscError("LCSC product price list has an unsupported schema")
    prices = []
    for row in raw_prices:
        if not isinstance(row, dict):
            continue
        quantity = number(first_value(row, ("ladder", "startPurchasedNumber", "quantity")), integer=True)
        price = number(first_value(row, ("currencyPrice", "productPrice", "usdPrice")))
        if quantity and price is not None:
            prices.append({"quantity": quantity, "unit_price": price, "currency": "USD"})
    prices.sort(key=lambda row: row["quantity"])
    raw_parameters = payload.get("paramVOList") or payload.get("paramPOList") or []
    if not isinstance(raw_parameters, list):
        raise LcscError("LCSC product parameters have an unsupported schema")
    parameters = {}
    for row in raw_parameters:
        if not isinstance(row, dict):
            continue
        name = clean_text(row.get("paramNameEn") or row.get("paramName"))
        value = clean_text(first_value(row, ("paramValueEn", "paramValue")))
        if name and value and value != "-":
            parameters[name] = value
    category_path = []
    for level in ("first", "second", "third", "fourth", "fifth", "sixth"):
        name = clean_text(payload.get(level + "WmCatalogNameEn"))
        if name and name not in category_path:
            category_path.append(name)
    category = clean_text(payload.get("wmCatalogNameEn") or payload.get("catalogName") or
                          (category_path[-1] if category_path else ""))
    if category and (not category_path or category_path[-1] != category):
        category_path.append(category)
    moq = number(first_value(payload, ("minBuyNumber", "minPacketNumber")), integer=True)
    moq = max(1, moq) if moq is not None else None
    order_qty = max(qty, moq) if moq is not None else None
    applicable = [row for row in prices if order_qty is not None and row["quantity"] <= order_qty]
    unit_price = applicable[-1]["unit_price"] if applicable else None
    batch_cost = float(Decimal(str(unit_price)) * order_qty) if unit_price is not None else None
    if batch_cost is not None and not math.isfinite(batch_cost):
        batch_cost = None
    status, evidence = sales_state(payload)
    return {
        "lcsc_id": lcsc_id, "mpn": mpn, "orderable_mpn": mpn,
        "manufacturer": clean_text(payload.get("brandNameEn") or payload.get("brandName")),
        "category": category, "category_path": category_path,
        "description": clean_text(payload.get("productIntroEn") or payload.get("productNameEn") or payload.get("productDescEn")),
        "package": clean_text(payload.get("encapStandard")),
        "stock": number(payload.get("stockNumber"), integer=True), "moq": moq,
        "prices": prices, "currency": "USD", "requested_qty": qty, "order_qty": order_qty,
        "unit_price": unit_price, "batch_cost": batch_cost,
        "datasheet_url": http_url(payload.get("pdfUrl") or payload.get("pdfURL")),
        "product_url": f"https://www.lcsc.com/product-detail/{lcsc_id}.html",
        "parameters": parameters, "fetched_at": datetime.now(timezone.utc).isoformat(),
        "sales_status": status, "sales_status_evidence": evidence, "sales_region": "international",
        "lifecycle_raw": payload.get("productCycle"), "historical": False,
        "data_source": "lcsc_detail_api", "source_url": BASE_URL + "/product/detail?productCode=" + lcsc_id,
        "commercial_data": "api_snapshot",
    }


def library_parameters(component):
    """Only supplier-owned fields; never emit Pacage, core fields or UI state."""
    values = {"JLCPCB Part#": component["lcsc_id"], "LCSC URL": component["product_url"],
              "LCSC MPN": component["mpn"], "LCSC Manufacturer": component["manufacturer"],
              "LCSC Category": component["category"], "LCSC Description": component["description"],
              "LCSC Package": component["package"], "LCSC Datasheet URL": component["datasheet_url"],
              "LCSC Stock": component["stock"], "LCSC MOQ": component["moq"],
              "LCSC Requested Qty": component["requested_qty"], "LCSC Order Qty": component["order_qty"],
              "LCSC Currency": component["currency"],
              "LCSC Source URL": component.get("source_url", ""),
              "LCSC Data Source": component.get("data_source", ""),
              "LCSC Fetched At": component.get("fetched_at", ""),
              "LCSC Sales Status": component.get("sales_status", "unknown"),
              "LCSC Sales Region": component.get("sales_region", ""),
              "LCSC Sales Status Evidence": json.dumps(component.get("sales_status_evidence", {}),
                                                       sort_keys=True, separators=(",", ":"), ensure_ascii=False),
              "LCSC Lifecycle Raw": component.get("lifecycle_raw")}
    values = {name: "" if value is None else str(value) for name, value in values.items()}
    for field, key in (("LCSC Unit Price", "unit_price"), ("LCSC Batch Cost", "batch_cost")):
        value = component[key]
        values[field] = "" if value is None else f"{format(Decimal(str(value)).normalize(), 'f')} {component['currency']}"
    return values


def _target_field(parameters, name, catalog, normalize):
    values = [value for key, value in parameters.items() if key.casefold() == name.casefold()]
    symbol = values[0] if len(values) == 1 else None
    left = normalize(symbol) if isinstance(symbol, str) else ""
    right = normalize(catalog) if isinstance(catalog, str) else ""
    if len(values) > 1:
        status = "ambiguous"
    elif not left:
        status = "symbol_missing"
    elif not right:
        status = "catalog_missing"
    else:
        status = "match" if left == right else "mismatch"
    return dict(field=name, status=status, symbol=symbol, symbol_values=values, catalog=catalog,
                normalized_symbol=left, normalized_catalog=right)


def _identity_value(value):
    value = value.strip().casefold()
    return "" if value in {"", "-", "?", "unknown", "n/a"} else value


def check_target(snapshot, component, allow_package_mismatch=False):
    """Require one matching MP and MF; report packages without guessing aliases."""
    if type(allow_package_mismatch) is not bool:
        raise ValueError("allow_package_mismatch must be a boolean")
    parameters = snapshot.get("parameters")
    if not isinstance(parameters, dict) or any(not isinstance(key, str) for key in parameters):
        raise ValueError("symbol parameter snapshot is missing or invalid")
    checks = {
        "mpn": _target_field(parameters, "MP", component.get("mpn"), _identity_value),
        "manufacturer": _target_field(parameters, "MF", component.get("manufacturer"),
                                      lambda value: manufacturer_key(_identity_value(value))),
        "package": _target_field(parameters, "Package", component.get("package"),
                                 lambda value: re.sub(r"\s+", "", _identity_value(value))),
    }
    conflicts = [checks[key] for key in ("mpn", "manufacturer") if checks[key]["status"] != "match"]
    package = checks["package"]
    if package["status"] == "ambiguous" or (package["status"] == "mismatch" and not allow_package_mismatch):
        conflicts.append(package)
    warnings = []
    if package["status"] in {"symbol_missing", "catalog_missing"}:
        warnings.append(dict(code="package_unverified", **package))
    elif package["status"] == "mismatch" and allow_package_mismatch:
        warnings.append(dict(code="package_mismatch_allowed", **package))
    report = dict(identity_check=checks, package_check=package, conflicts=conflicts, warnings=warnings,
                  identity_verified=not any(checks[k]["status"] != "match" for k in ("mpn", "manufacturer")),
                  package_conflict=package["status"] == "mismatch",
                  package_mismatch_allowed=package["status"] == "mismatch" and allow_package_mismatch)
    if conflicts:
        message = "; ".join(f"{row['field']} {row['status']}: symbol={row['symbol_values']!r}, catalog={row['catalog']!r}"
                            for row in conflicts)
        raise LcscTargetConflict("LCSC target preflight failed: " + message, report)
    return report


class LcscClient:
    """The source toolkit's public-web search/detail endpoints, without caching."""

    def __init__(self, timeout=15.0, retries=1):
        self.timeout = timeout
        self.retries = retries

    def _request_result(self, method, path, *, query=None, body=None):
        url = BASE_URL + path
        if query:
            url += "?" + parse.urlencode(query)
        data = json.dumps(body, separators=(",", ":")).encode("utf-8") if body is not None else None
        headers = {"Accept": "application/json", "User-Agent": "altium-mcp-lcsc/0.1", "Cookie": "currencyCode=USD"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        for attempt in range(self.retries + 1):
            try:
                with request.urlopen(request.Request(url, data=data, headers=headers, method=method), timeout=self.timeout) as response:
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise LcscError("LCSC response is unexpectedly large")
                payload = json.loads(raw.decode("utf-8-sig"))
                if not isinstance(payload, dict):
                    raise LcscError("LCSC response is not a JSON object")
                if payload.get("code") != 200:
                    raise LcscError(f"LCSC API error {payload.get('code')}: {clean_text(payload.get('msg'))}")
                if "result" not in payload:
                    raise LcscError("LCSC response schema changed: result is missing")
                return payload["result"]
            except error.HTTPError as exc:
                if exc.code == 404 and path == "/product/detail":
                    raise LcscNotFound("LCSC detail endpoint returned HTTP 404") from exc
                if exc.code not in {408, 500, 502, 503, 504} or attempt == self.retries:
                    raise LcscError(f"LCSC HTTP {exc.code}: {exc.reason}") from exc
            except (error.URLError, TimeoutError, OSError) as exc:
                if attempt == self.retries:
                    raise LcscError(f"LCSC request failed: {exc}") from exc
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise LcscError("LCSC returned invalid JSON; the endpoint may have changed") from exc
            time.sleep(min(2.0, 0.5 * (2 ** attempt)))

    def get_product(self, lcsc_id, qty=1):
        lcsc_id = validate_code(lcsc_id)
        validate_quantity(qty)
        payload = self._request_result("GET", "/product/detail", query={"productCode": lcsc_id})
        if not isinstance(payload, dict) or not payload.get("productCode"):
            raise LcscNotFound(f"LCSC component {lcsc_id} was not found")
        component = component_from_payload(payload, qty)
        if component["lcsc_id"] != lcsc_id:
            raise LcscError("LCSC detail identity does not match the requested code")
        return component

    def _request_page(self, url):
        headers = {"User-Agent": "altium-mcp-lcsc/0.1", "Accept": "text/html", "Cookie": "currencyCode=USD"}
        try:
            with request.urlopen(request.Request(url, headers=headers), timeout=min(self.timeout, 10)) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise LcscError("Historical page is unexpectedly large")
            html = raw.decode("utf-8-sig")
            head = html.lstrip()[:8192].casefold()
            if (head.startswith(("{", "[")) or "var arg1=" in head or "cf-chl-" in head
                    or "verify you are human" in head or "just a moment..." in head):
                raise LcscError("Historical page returned a browser verification challenge or non-HTML response")
            return html
        except (error.URLError, OSError, UnicodeError) as exc:
            raise LcscError(f"Historical page unavailable: {exc}") from exc

    def _historical_search(self, options):
        query = options["query"]
        keyword = options["exact_mpn"] or query
        wanted_code = validate_code(query) if CODE.fullmatch(query) else None
        urls = []
        if options["historical_url"]: urls.append(options["historical_url"])
        if wanted_code: urls.append(f"https://www.lcsc.com/product-detail/{wanted_code}.html")
        urls.extend(("https://www.lcsc.com/search?q=" + parse.quote(keyword, safe=""),
                     "https://so.szlcsc.com/global.html?k=" + parse.quote(keyword, safe="")))
        components = []
        attempted = []
        seen_codes = set()
        seen_urls = set()
        # Two discovery pages plus at most ten public product cards; no crawling.
        while urls and len(attempted) < 12 and len(components) < options["limit"]:
            url = urls.pop(0)
            if url in seen_urls: continue
            seen_urls.add(url)
            try:
                html = self._request_page(url)
                candidates = lcsc_history.page_products(html, url)
                row = dict(url=url, status="no_match")
                discovery = parse.urlsplit(url).path in {"/search", "/global.html"}
                if discovery:
                    for raw in candidates:
                        code = clean_text(raw.get("productCode")).upper()
                        mpn = clean_text(raw.get("productModel"))
                        if wanted_code and code != wanted_code: continue
                        if not wanted_code and mpn.casefold() != keyword.casefold(): continue
                        urls.append(f"https://www.lcsc.com/product-detail/{code}.html")
                        if type(raw.get("productId")) is int and raw["productId"] > 0:
                            urls.append(f"https://item.szlcsc.com/{raw['productId']}.html")
                    candidates = []
                for raw in candidates:
                    code = clean_text(raw.get("productCode")).upper()
                    mpn = clean_text(raw.get("productModel"))
                    if wanted_code and code != wanted_code: continue
                    if not wanted_code and mpn.casefold() != keyword.casefold(): continue
                    if code in seen_codes: continue
                    # Static/domestic pages cannot provide a fresh USD quote.
                    raw = dict(raw, stockNumber=None, minBuyNumber=None, minPacketNumber=None,
                               productPriceList=[], priceList=[])
                    card = component_from_payload(raw, options["qty"])
                    if not self._matches(card, options): continue
                    card.update(historical=True, data_source="lcsc_product_page", source_url=url, product_url=url,
                                sales_region="CN" if parse.urlsplit(url).hostname == "item.szlcsc.com" else "international",
                                commercial_data="unavailable")
                    card["lcsc_parameters"] = library_parameters(card)
                    components.append(card)
                    seen_codes.add(code)
                    row["status"] = "found"
                    if len(components) >= options["limit"]: break
                attempted.append(row)
                if wanted_code and components: break
                if discovery:
                    urls.extend(lcsc_history.discover(html, url))
            except (LcscError, ValueError) as exc:
                attempted.append(dict(url=url, status="unavailable", error=str(exc)))
        return components, dict(attempted=bool(attempted), sources=attempted,
                                complete=not any(row["status"] == "unavailable" for row in attempted))

    @staticmethod
    def _matches(card, options):
        return ((not options["manufacturer"] or manufacturer_key(card["manufacturer"]) == manufacturer_key(options["manufacturer"]))
                and (not options["exact_mpn"] or card["mpn"].casefold() == options["exact_mpn"].casefold()))

    def lookup(self, query, qty=1, limit=5, manufacturer="", exact_mpn="", include_historical=True, historical_url=""):
        options = lookup_options(query, qty, limit, manufacturer, exact_mpn, include_historical, historical_url)
        query = options["query"]
        components = []
        warnings = []
        seen = set()
        pages = 0
        truncated = False
        missing_code = False
        current_identity_verified = False
        if CODE.fullmatch(query):
            try:
                card = self.get_product(query, qty)
                current_identity_verified = True
                if self._matches(card, options): components.append(card)
            except LcscNotFound:
                missing_code = True
        else:
            keyword = options["exact_mpn"] or query
            for page in range(1, MAX_SEARCH_PAGES + 1):
                result = self._request_result("POST", "/product/query/list", body={"keyword": keyword, "currentPage": page, "pageSize": SEARCH_PAGE_SIZE})
                pages += 1
                if not isinstance(result, dict) or not isinstance(result.get("dataList"), list):
                    raise LcscError("LCSC search response schema changed: dataList is missing")
                rows = [r for r in result["dataList"] if isinstance(r, dict)]
                if not options["exact_mpn"]:
                    exact = [r for r in rows if clean_text(r.get("productModel")).casefold() == query.casefold()
                             and (not manufacturer or manufacturer_key(clean_text(r.get("brandNameEn") or r.get("brandName"))) == manufacturer_key(manufacturer))]
                    rows = exact or rows
                for row in rows:
                    if manufacturer and (row.get("brandNameEn") or row.get("brandName")):
                        if manufacturer_key(clean_text(row.get("brandNameEn") or row.get("brandName"))) != manufacturer_key(manufacturer): continue
                    if exact_mpn and row.get("productModel"):
                        if clean_text(row["productModel"]).casefold() != options["exact_mpn"].casefold(): continue
                    code = validate_code(row.get("productCode"))
                    if code in seen: continue
                    seen.add(code)
                    try:
                        card = self.get_product(code, qty)
                        if self._matches(card, options): components.append(card)
                    except LcscError as exc:
                        warnings.append(dict(lcsc_id=code, error=str(exc)))
                    if len(components) >= limit: break
                total_pages = number(result.get("totalPage"), integer=True)
                last_page = (total_pages is not None and page >= total_pages) or len(result["dataList"]) < SEARCH_PAGE_SIZE
                if len(components) >= limit or last_page: break
                if page == MAX_SEARCH_PAGES: truncated = True
        history = dict(attempted=False, sources=[], complete=True)
        if not components and include_historical and not current_identity_verified:
            components, history = self._historical_search(options)
        for card in components:
            card["manufacturer_match"] = self._matches(card, {**options, "exact_mpn":""}) if manufacturer else None
            card["exact_mpn_match"] = card["mpn"].casefold() == options["exact_mpn"].casefold() if exact_mpn else None
            card["lcsc_parameters"] = library_parameters(card)
        complete = history["complete"] and not warnings and not truncated
        success = bool(components) or (not missing_code and complete)
        result = dict(success=success, query=query, manufacturer=options["manufacturer"], exact_mpn=options["exact_mpn"],
                      result_count=len(components), components=components, saved=False, source="LCSC web catalog",
                      complete=complete, warnings=warnings, historical_search=history, search_pages=pages, search_truncated=truncated)
        if not success:
            result["error"] = "No verified card; historical sources unavailable or catalog detail missing" if not complete or missing_code else "No matching component"
        return result

    def lookup_many(self, queries, qty=1, limit=5, manufacturer="", exact_mpn="", include_historical=True, historical_url=""):
        if not isinstance(queries, list) or not 1 <= len(queries) <= MAX_BATCH:
            raise ValueError(f"queries must contain 1 to {MAX_BATCH} strings or request objects")
        defaults = dict(qty=qty, limit=limit, manufacturer=manufacturer, exact_mpn=exact_mpn,
                        include_historical=include_historical, historical_url=historical_url)
        # Validate the entire batch before making even one network request.
        lookup_options("batch", **defaults)
        requests = []
        for value in queries:
            if isinstance(value, str): value = {"query":value}
            if not isinstance(value, dict) or not set(value) <= {"id", "query", *defaults}:
                raise ValueError("each batch entry must be a query string or a supported lookup request")
            label = value.get("id")
            if label is not None: validate_text(label, "id")
            options = lookup_options(**{**defaults, **{k:v for k,v in value.items() if k != "id"}, "query":value.get("query", "")})
            requests.append((label, options))
        futures = {}
        def run(options):
            try:
                return self.lookup(**options)
            except (LcscError, ValueError, TypeError) as exc:
                return dict(success=False, query=options["query"], error=str(exc), components=[], result_count=0, saved=False, complete=False)
        with ThreadPoolExecutor(max_workers=3) as pool:
            for _,options in requests:
                key = json.dumps(options, sort_keys=True)
                if key not in futures: futures[key] = pool.submit(run, options)
            results = []
            for index,(label, options) in enumerate(requests):
                result = copy.deepcopy(futures[json.dumps(options, sort_keys=True)].result())
                result["request_index"] = index
                if label is not None: result["id"] = label
                results.append(result)
        succeeded = sum(row["success"] for row in results)
        return dict(success=succeeded == len(results), partial_success=0 < succeeded < len(results), saved=False,
                    query_count=len(results), unique_query_count=len(futures), succeeded_count=succeeded,
                    failed_count=len(results)-succeeded, results=results)
