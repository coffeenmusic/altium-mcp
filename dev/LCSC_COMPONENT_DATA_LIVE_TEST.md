# LCSC data live verification

Historical record of the original 15-field, MP-only implementation. Current
manufacturer/package guards and provenance fields are documented in
[strict application validation](LCSC_APPLICATION_LIVE_TEST.md).

Verified on 2026-11-04 with the real LCSC catalog and the user's running Altium
session. The final run completed 18 calls against current Python tool functions
and a fresh copy of the production script project through the normal bridge.

The catalog fixture was
[`C136648 / LMR16030SDDAR`](https://www.lcsc.com/product-detail/C136648.html).
No stock or price is hard-coded into these checks.

| Check | Result |
| --- | --- |
| Exact C-number lookup | Returned one hydrated detail card, technical parameters, links and price breaks. |
| Exact MPN lookup | Search followed by detail fetch returned the matching MPN. |
| Create a new symbol with required core metadata | Passed in the unsaved scratch SchLib only. |
| Fill supplier metadata after creation | All 15 requested fields verified by native readback. |
| Preserve core parameters and user note | All values, including Unicode/punctuation, unchanged. |
| Preserve Comment, native Description, models and symbol geometry | Passed. |
| Repeat supplier update | `changed=false`; existing parameters reused. |
| Independent metadata reread | Matched the second update's after snapshot. |
| Symbol with an intentionally different MP | Rejected before supplier writes; full metadata snapshot unchanged. |
| Modified state for data-only symbols | True after creation/update; no save requested. |
| Original project/editor context | Restored; original document open/modified states unchanged. |
| Source and scratch disk state | SHA-256 and modification timestamps unchanged throughout the Altium operations. |

Evidence is in the ignored toolkit directory:
`.altium-mcp/lcsc_catalog_20261104_121701/report.json` with adjacent per-call
snapshots. The runner is `dev/test_lcsc_catalog_live.py`. Its source library was
copied from AM_NODE_V2; only the scratch copy received two new fixture symbols.
It did not open, edit or save the original source library. Scratch symbols remain
open for inspection.

The initial live iteration exposed an existing issue: `SetModified(True)` could
leave metadata-only/empty symbols' library unmarked despite successful readback.
The existing `MarkDocumentModified` property-based helper now marks metadata
edits after editor refresh, symbol creation uses the same property setter, and
the Python metadata writer rejects changed data lacking Modified. The final run
passed this check without saving any document.

Regression: 130 tests run, 128 passed, two existing environment-dependent tests
skipped. Thirteen new tests cover catalog HTTP/JSON contracts, exact selection,
detail identity, technical parameter units, MOQ/price tiers, unknown values,
supplier-only writes, core-MP mismatch rejection and MCP dispatch. The existing
metadata writer's regression also now checks Modified readback failures.

No custom `run_altium_script` operation, new Altium command, dependency install or
user configuration change was needed. The source toolkit's `lcsc_tool.py` hash
remained unchanged after extraction.

## Limits

The live lookup covers one real chip by code and exact MPN. Missing metadata,
ambiguous candidates and HTTP/schema/rate-limit failures have isolated unit
coverage. No PDF is downloaded or parsed, and no electrical compatibility or
JLCPCB assembly inventory check is performed. Keep source symbols stable during
the separate read/write operation; there is no atomic rollback across network
lookup and Altium calls.
