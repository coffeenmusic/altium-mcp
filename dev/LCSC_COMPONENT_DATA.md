# LCSC component data

The MCP exposes two focused operations:

```python
get_lcsc_component_info(query="LMR16030SDDAR", qty=100)
get_lcsc_component_info(query="C136648", qty=100)

get_lcsc_component_info(
    query="TJA1051T/3", manufacturer="NXP", exact_mpn="TJA1051T/3,118",
)
get_lcsc_component_info(queries=[
    {"id": "K07", "query": "C58988", "manufacturer": "NXP",
     "exact_mpn": "TJA1051T/3,118"},
    {"id": "E04", "query": "C907510", "manufacturer": "Murata",
     "exact_mpn": "DMT3N4R2U224M3DTA0",
     "historical_url": "https://item.szlcsc.com/973089.html"},
])

# After creating the symbol with create_schematic_symbol or create_symbols_batch:
set_library_component_lcsc_data(
    library_path="C:/Design/Parts.SchLib",
    symbol_name="LMR16030SDDAR",
    lcsc_id="C136648",
    qty=100,
)
```

The lookup does not access Altium. The second operation updates only one exact
existing SchLib symbol in memory through `set_library_component_data`; it never
saves the library or updates placed schematic components.

## Extraction scope

`server/lcsc_catalog.py` adapts `LcscClient` and `component_from_payload` from
the user's `C:/workspace_altium/altium-delphiscript-toolkit/lcsc_tool.py`.
The source file was read without edits. Its SHA-256 at extraction was
`f1d71d0baef139f0c0cbc2ef32435af0e0e10636355d9c1a4dcfce7984cabb70`.

Search/detail transport, response parsing and quantity pricing originated there.
Batch lookup, strict filters, sale status and public-page recovery are local
extensions. There is no runtime dependency on that repository, third-party Python
dependency, CLI, SQLite cache, job system, alternative ranking or copied
DelphiScript application layer. The existing native SchLib writer handles edits.

Requests use the source client's public-web endpoints under
`https://wmsc.lcsc.com/ftps/wm`: `POST /product/query/list` and
`GET /product/detail?productCode=C...`, with a USD currency cookie. They are not
a documented authenticated LCSC API. Timeout is 15 seconds per attempt, with one
retry for transient transport/server errors and a 20 MiB response limit. Rate
limits, HTTP errors and changed JSON schemas return explicit failures. Public
page recovery uses read-only HTTPS GETs, a 10-second timeout, the same response
size limit and no retry. It never solves challenges or requires login/cookies
from the user's browser.

## Lookup response

Existing `query`, `qty` and `limit` calls remain compatible. `query` accepts an
exact C-number, MPN or text. `qty` is 1..1,000,000,000; `limit` is 1..10 (default
5), applied after filtering. C-numbers use the detail endpoint. Text/MPN lookup
scans up to three 30-row pages. Exact query-MPN matches are preferred within each
page, but multiple candidates remain explicit and are not silently ranked.

`manufacturer="NXP"` filters by the complete brand name, verified again on the
detail card. Case, spacing/punctuation and parenthetical localized display names
are normalized. The small alias set includes TI/Texas Instruments,
NXP/NXP Semiconductors, Murata/Murata Electronics/Murata Manufacturing,
ST/STMicroelectronics and onsemi/ON Semiconductor. Substring matching is not used.

`exact_mpn` is a **full order code string**, not a boolean. It supplies the search
keyword and strictly filters the hydrated card. Slashes, commas, internal spaces
and packaging suffixes are preserved; only case and surrounding whitespace are
ignored. It can be combined with a C-number or a broader `query`, or supplied
without `query`. `orderable_mpn` returns the catalog's actual full MPN; no suffix
is derived from a family name. A verified current C-number that fails a filter
returns zero candidates; a historical card cannot override that contradiction.

## Batch requests

`queries` replaces `query` and accepts 1..25 strings or request dictionaries.
Dictionary fields are `id`, `query`, `qty`, `limit`, `manufacturer`, `exact_mpn`,
`include_historical` and `historical_url`. Top-level options provide defaults;
individual fields override them. Optional `id` is a caller label. The whole batch
is validated before any HTTP request. Invalid arguments fail the whole batch.

Three workers perform independent lookups. Identical requests, excluding `id`,
are fetched once per call. Results retain input order, duplicate entries and
labels. Each result includes `request_index`; network/missing-card errors affect
only that entry. The envelope reports `query_count`, `unique_query_count`,
`succeeded_count`, `failed_count` and `partial_success`. Batch `success` requires
every lookup to succeed; a completed zero-candidate text search counts as success.
No persistent or cross-call cache is added.

## Card fields and sale status

Every candidate is hydrated from its detail card, rather than returning the
lighter search summary. The response includes:

- C-number, MPN, manufacturer, category/path, description and package.
- `orderable_mpn`, `manufacturer_match` and `exact_mpn_match`; an unrequested
  match check is `null`.
- `sales_status`: `on_sale`, `not_for_sale`, `discontinued` or `unknown`, plus
  `sales_status_evidence`, `sales_region` and unchanged `lifecycle_raw`.
- `parameters`: all available catalog technical names and values with original
  units. No SI inference, electrical compatibility checks or PDF parsing occur.
- Datasheet and canonical LCSC product URLs.
- Stock, MOQ, requested/order quantities, all USD price breaks and the applicable
  unit price/batch cost.
- UTC `fetched_at`, plus `lcsc_parameters`, a string dictionary ready for the
  symbol's creation parameter dictionary.

Missing stock/MOQ/prices stay `null`, not invented zeroes. `order_qty` is at least
the reported MOQ when known; price is selected only when a returned tier applies.
No quote is invented below the lowest returned tier or when MOQ is unknown.
Catalog quantities/prices are snapshots and the order calculation does not model
checkout fees, shipping, taxes or packaging multiples.

Sale status is separate from stock: zero stock does not mean discontinued.
`isForeignOnsale=false` means not for sale in the international catalog, not
manufacturer end-of-life. Unrecognized/missing flags remain unknown. An explicit
sale-stop label on the domestic card is reported as discontinued in region `CN`.
The `lcsc_parameters` dictionary includes supplier provenance, UTC retrieval time
and regional sale status, using the same fields as the symbol writer below.

A completed bounded search with no candidates returns `success=true`,
`result_count=0` and an empty `components` list. Missing exact codes or empty
incomplete recovery return `success=false`. `complete`, `warnings`, `search_pages`
and `search_truncated` expose detail-fetch failures and the page cap; `complete`
does not promise an exhaustive search of the entire catalog. Valid partial cards
remain available with warnings. No Altium changes happen during lookup.

## Historical/public-page recovery

`include_historical=True` is the default. When no current matching card exists,
the client tries the canonical C-number page when known, international/CN search
pages and discovered product cards. Optional `historical_url` supplies a known
card, especially useful for old numeric CN URLs. Only HTTPS product URLs on
`lcsc.com`, `www.lcsc.com` and `item.szlcsc.com` are accepted; arbitrary hosts,
credentials, query strings and fragments are refused. At most 12 pages are read.

Recovery parses identity-bearing Next.js/JSON-LD data or labeled CN card fields
and parameter tables. Full technical payloads take precedence over lightweight
summaries; discovered search results are hydrated as cards. Returned C-number,
manufacturer and complete MPN must pass the same filters. Recommendations and
conflicting identities are never substituted for the requested part. For a text
query, recovery requires a literal MPN match rather than a guessed alternative.

Recovered cards report `historical=true`, `data_source="lcsc_product_page"` and
`source_url`; `product_url` and `lcsc_parameters["LCSC URL"]` point to that recovered
card instead of a potentially missing international URL. This flag identifies the recovery route; it does not itself imply
discontinuation or an archival date. Technical names/units are preserved. Stock,
MOQ and all pricing/order-quantity fields stay null because static/domestic data
cannot establish a fresh USD quote. Retrieval time is not the data's age.

`historical_search.sources` records `found`, `no_match` or `unavailable` and error
details for each attempted URL. Blocked HTTP 403/405, invalid data or transport
failures make recovery incomplete rather than claiming that a historical card
does not exist. `include_historical=False` disables these additional requests.
Browser-verification responses are also classified as unavailable, including
challenges returned with HTTP 200; the client does not attempt to solve them.

## Applying supplier fields

`set_library_component_lcsc_data` accepts an exact C-number, not an implicit
result index. It fetches a fresh detail card, reads the exact symbol and requires
one nonempty `MP` and one nonempty `MF` matching the card's complete MPN and
manufacturer. MPN comparison preserves punctuation and order suffixes and ignores
only case/surrounding whitespace. Manufacturer comparison uses the same complete
brand normalization and documented aliases as lookup. A missing, ambiguous or
mismatched identity fails before writes; matching MPN alone is insufficient.

The symbol's `Package` is compared with the catalog package after case/whitespace
normalization. A known mismatch blocks by default and reports both original
values. `allow_package_mismatch=True` permits this naming conflict only and
returns an explicit warning. It cannot bypass MP/MF checks or duplicate Package
parameters. Missing package data produces `package_unverified`; it is not treated
as verified compatibility. No package synonyms, footprint geometry or land-pattern
compatibility are inferred. The existing core Package and model bindings stay
unchanged, including on override.

Identity/package refusals return `identity_check`, `package_check`, `conflicts` and
`may_have_partial_changes=false`. Successful preflight returns `identity_verified`
for MP/MF, `package_conflict`, `package_mismatch_allowed` and `warnings` alongside
the write result. This is a separate read/write operation: do not concurrently
edit the symbol between its preflight and application.

The library path follows `set_library_component_data`: an absolute path to an
existing SchLib. A newly created symbol may still be unsaved in that library.

The operation upserts 22 supplier-owned string fields. The original 15 are:

| Identity and documentation | Procurement snapshot |
| --- | --- |
| `JLCPCB Part#` | `LCSC Stock` |
| `LCSC URL` | `LCSC MOQ` |
| `LCSC MPN` | `LCSC Requested Qty` |
| `LCSC Manufacturer` | `LCSC Order Qty` |
| `LCSC Category` | `LCSC Unit Price` |
| `LCSC Description` | `LCSC Batch Cost` |
| `LCSC Package` | `LCSC Currency` |
| `LCSC Datasheet URL` | |

Seven provenance/status fields are also written:

| Field | Meaning |
|---|---|
| `LCSC Source URL` | Actual API/page URL used to obtain the card |
| `LCSC Data Source` | Source type, such as `lcsc_detail_api` |
| `LCSC Fetched At` | UTC ISO 8601 retrieval timestamp |
| `LCSC Sales Status` | `on_sale`, `not_for_sale`, `discontinued` or `unknown` |
| `LCSC Sales Region` | Market to which the sale status applies |
| `LCSC Sales Status Evidence` | Compact JSON of the raw flags or page status |
| `LCSC Lifecycle Raw` | Uninterpreted catalog lifecycle field, empty if absent |

`JLCPCB Part#` follows the existing project's convention of storing the LCSC
C-number; this does not check JLCPCB assembly inventory or Basic/Extended class.
Unknown values become empty strings, clearing stale supplier data instead of
retaining an old price or stock value. Unknown sale status is written explicitly
as `unknown`; an empty evidence object is `{}`. Sale status is not inferred from
stock. Refreshing records the new retrieval timestamp even when other values are
equal, so another live fetch normally returns `changed=true`. Identical complete
snapshots still reuse parameter names and remain idempotent in the native writer.

Core fields (`Value`, `MF`, `MP`, `HelpURL`, `Package`, `Type`, `Instructions`,
`System`), Comment, native Description, unrelated parameters, models and geometry
are left alone. `Pacage` is never created. Source-tool workflow fields such as
score, search mode and applied-result index are deliberately not transferred.

The result retains the native writer's before/after snapshots, `changed`,
`saved=false`, Modified state and readback verification, plus `lcsc_id`,
`lcsc_fetched_at`, `lcsc_parameter_names` and the preflight report. Changed metadata must leave the
library marked Modified. A native/API failure can leave partial in-memory edits;
inspect `may_have_partial_changes` before retrying. Do not edit the same symbol
concurrently between its read and write.

For writing at creation time, use the chosen card's dictionary:

```python
parameters = {**core_parameters, **chosen_component["lcsc_parameters"]}
```

The normal creation schema remains unchanged. Choose the matching card explicitly;
the generic creation tool does not independently resolve a C-number. `Value`,
assembly instructions and functional block still need component/project context.

## Verification and loading

```powershell
python -m unittest server.tests.test_lcsc_catalog server.tests.test_library_component_data -v
python -m unittest server.tests.test_lcsc_lookup -v
python dev/test_lcsc_lookup_live.py
python dev/test_lcsc_catalog_live.py --schlib 'C:\Project\Symbols.SchLib' --lcsc-id C136648
python dev/test_lcsc_application_live.py --exercise --schlib 'C:\Project\Symbols.SchLib'
```

The live harness fetches a real part, creates symbols only in an unsaved library
copy under the toolkit's ignored `.altium-mcp` directory, applies supplier fields,
tests repeat/mismatched-MPN behavior and compares core fields, geometry, source
disk hashes and modification timestamps. It leaves scratch symbols available for
inspection and restores the original project/editor. Do not run other Altium
calls concurrently with the harness.

See [live results](LCSC_COMPONENT_DATA_LIVE_TEST.md). Reconnect/restart the MCP
server to expose the two new tools; load updated production scripts when Altium
holds stale buffers. No user configuration or dependency installation is needed.

For the batch/filter/status/recovery extension, see the separate
[2026-11-11 validation record](LCSC_LOOKUP_LIVE_TEST.md). Catalog-only checks do
not require Altium or any library write.

The strict application harness creates only disposable fixture symbols, tests
manufacturer/MPN/package refusals and an explicit package override, verifies
provenance/core fields/geometry, then closes the copy with discard. It stops
dispatching after unknown completion. See
[strict application validation](LCSC_APPLICATION_LIVE_TEST.md).
