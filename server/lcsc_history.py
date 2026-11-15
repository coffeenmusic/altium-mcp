"""Bounded discovery/parsing of public LCSC product pages, without a browser."""

from html.parser import HTMLParser
import json
import re
from urllib import parse


CN_LABELS = {
    "brandNameEn": "\u54c1\u724c\u540d\u79f0", "productModel": "\u5546\u54c1\u578b\u53f7",
    "productCode": "\u5546\u54c1\u7f16\u53f7", "encapStandard": "\u5546\u54c1\u5c01\u88c5",
}
STOP_SALE = "\u5546\u54c1\u5df2\u505c\u552e"
PRODUCT_PATH = re.compile(r"/product-detail/(C[0-9]+)\.html", re.I)
CN_PATH = re.compile(r"/[0-9]+\.html")


def manufacturer_key(value):
    key = re.sub(r"[\W_]+", "", re.sub(r"\([^)]*\)", "", value).casefold())
    return {"texasinstruments": "ti", "texasinstrumentsinc": "ti", "nxpsemiconductors": "nxp",
            "murataelectronics": "murata", "muratamanufacturing": "murata",
            "stmicroelectronics": "st", "onsemiconductor": "onsemi"}.get(key, key)


def product_url(value):
    """Only public product URLs; never accept arbitrary hosts or query strings."""
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 32 for c in value):
        raise ValueError("historical_url must be a public HTTPS LCSC product URL")
    url = parse.urlsplit(value)
    if (url.scheme != "https" or url.username or url.password or url.port not in (None, 443)
            or url.query or url.fragment):
        raise ValueError("historical_url must be a public HTTPS LCSC product URL without query/fragment")
    host = (url.hostname or "").casefold()
    if not ((host in {"lcsc.com", "www.lcsc.com"} and PRODUCT_PATH.fullmatch(url.path)) or
            (host == "item.szlcsc.com" and CN_PATH.fullmatch(url.path))):
        raise ValueError("historical_url must identify an LCSC or item.szlcsc.com product card")
    return parse.urlunsplit(("https", host, url.path, "", ""))


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.texts = []
        self.links = []
        self.json_scripts = []
        self.tables = []
        self._script = None
        self._skip = 0
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "style"}:
            self._skip += 1
            if tag == "script" and (attrs.get("id") == "__NEXT_DATA__" or attrs.get("type") == "application/ld+json"):
                self._script = []
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        if tag == "iframe" and attrs.get("src"):
            self.links.append(attrs["src"])
        if tag == "tr": self._row = []
        if tag in {"td", "th"} and self._row is not None: self._cell = []

    def handle_data(self, text):
        if self._script is not None:
            self._script.append(text)
        if self._skip:
            return
        text = re.sub(r"\s+", " ", text).strip().strip("\u200b")
        if text:
            self.texts.append(text)
            if self._cell is not None: self._cell.append(text)

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self._skip = max(0, self._skip - 1)
            if tag == "script" and self._script is not None:
                self.json_scripts.append("".join(self._script))
                self._script = None
        if tag in {"td", "th"} and self._cell is not None:
            if self._row is not None: self._row.append(" ".join(self._cell))
            self._cell = None
        if tag == "tr" and self._row is not None:
            self.tables.append(self._row)
            self._row = None


def json_products(value):
    """Read only identity-bearing product nodes, not arbitrary page state."""
    pending = [(value, 0)]
    seen = 0
    while pending and seen < 50000:
        node, depth = pending.pop()
        seen += 1
        if depth > 20: continue
        if isinstance(node, dict):
            if re.fullmatch(r"C[0-9]+", str(node.get("productCode", "")), re.I) and node.get("productModel"):
                yield node
            if node.get("@type") == "Product" and re.fullmatch(r"C[0-9]+", str(node.get("sku", "")), re.I) and node.get("mpn"):
                brand = node.get("brand")
                yield dict(productCode=node["sku"], productModel=node["mpn"],
                           brandNameEn=brand.get("name", "") if isinstance(brand, dict) else brand,
                           productIntroEn=node.get("description", ""))
            pending.extend((v, depth + 1) for v in node.values() if isinstance(v, (list, dict)))
        elif isinstance(node, list):
            pending.extend((v, depth + 1) for v in node if isinstance(v, (list, dict)))


def page_products(html, url):
    page = Page()
    page.feed(html)
    products = []
    path = parse.urlsplit(url).path
    detail_page = bool(PRODUCT_PATH.fullmatch(path) or CN_PATH.fullmatch(path))
    primary = None
    primary_missing = False
    for script in page.json_scripts:
        try:
            value = json.loads(script)
            props = value.get("props", {}).get("pageProps") if isinstance(value, dict) and isinstance(value.get("props"), dict) else None
            if detail_page and isinstance(props, dict) and "webData" in props:
                # The actual LCSC Next.js detail node, excluding hot/recommended rows.
                if isinstance(props["webData"], dict) and props["webData"].get("productCode"):
                    primary = props["webData"]
                    products.append(primary)
                else:
                    primary_missing = True
            else:
                products.extend(json_products(value))
        except (ValueError, RecursionError):
            continue
    if primary_missing and primary is None:
        return []
    expected = PRODUCT_PATH.fullmatch(path)
    if expected:
        expected_code = expected[1].upper()
        if primary is not None and str(primary.get("productCode", "")).upper() != expected_code:
            raise ValueError("Historical page primary identity does not match its URL")
        products = [raw for raw in products if str(raw.get("productCode", "")).upper() == expected_code]
    if parse.urlsplit(url).hostname == "item.szlcsc.com":
        raw = {}
        for key, label in CN_LABELS.items():
            matches = [i for i,t in enumerate(page.texts[:-1]) if t.rstrip(":\uff1a") == label]
            values = {page.texts[i + 1] for i in matches}
            if len(values) == 1: raw[key] = values.pop()
        if re.fullmatch(r"C[0-9]+", raw.get("productCode", ""), re.I) and raw.get("productModel") and raw.get("brandNameEn"):
            raw["paramVOList"] = []
            for row in page.tables:
                cells = [c for c in row if c]
                if len(cells) >= 2 and cells[-1] != "-" and cells[-2] not in {"\u5c5e\u6027", "Type"}:
                    raw["paramVOList"].append(dict(paramName=cells[-2], paramValue=cells[-1]))
            if STOP_SALE in page.texts: raw["_page_sales_status"] = "discontinued"
            raw["pdfUrl"] = next((parse.urljoin(url, link) for link in page.links
                                  if parse.urlsplit(parse.urljoin(url, link)).hostname in {"atta.szlcsc.com", "datasheet.lcsc.com"}
                                  and ".pdf" in parse.urlsplit(link).path.casefold()), "")
            products.insert(0, raw)
            if "_page_sales_status" in raw:
                for node in products:
                    if node.get("productCode") == raw["productCode"]:
                        node["_page_sales_status"] = raw["_page_sales_status"]
            if primary is not None and str(primary.get("productCode", "")).upper() != raw["productCode"].upper():
                raise ValueError("Historical page labels conflict with its primary identity")
            products = [node for node in products if str(node.get("productCode", "")).upper() == raw["productCode"].upper()]
        elif detail_page:
            product_id = int(path.strip('/').split('.')[0])
            products = [node for node in products if node.get("productId") == product_id]
    identities = {}
    for raw in products:
        code = str(raw.get("productCode", "")).upper()
        brand = str(raw.get("brandNameEn") or raw.get("brandName") or "")
        if not brand: continue
        identity = (str(raw.get("productModel", "")).strip().casefold(),
                    manufacturer_key(brand))
        if code in identities and identities[code] != identity:
            raise ValueError("Conflicting product identities in historical page")
        identities[code] = identity
    # JSON-LD summaries and recommendation rows often precede the full Next.js
    # detail card. Prefer the actual technical payload for each identity.
    return sorted(products, key=lambda raw: (len(raw.get("paramVOList") or raw.get("paramPOList") or []), len(raw)), reverse=True)


def discover(html, base_url, limit=10):
    page = Page()
    page.feed(html)
    urls = []
    for link in page.links:
        try:
            url = product_url(parse.urljoin(base_url, link))
        except ValueError:
            continue
        if url not in urls:
            urls.append(url)
        if len(urls) >= limit: break
    return urls
