# LCSC lookup extension validation

Date: 2026-11-11. No Altium command, library write, project edit or document save
was performed. Live checks invoked the production Python handler without
starting another MCP listener, and asserted that its Altium bridge was unused.

## Offline checks

- 284 selected regression tests: 283 passed; one skipped for the host ANSI code
  page. Evidence: `.altium-mcp/lcsc_extension/regression.log`.
- Included 30 catalog-specific tests and the existing metadata, server identity
  and tool-registration checks.
- Cases include manufacturer collisions, authoritative detail checks, exact
  comma/slash/packaging suffixes, pagination after filtering, independent sale
  status, whole-batch preflight, request deduplication, ordered per-item errors,
  bounded concurrency, URL validation, conflicting page identities and HTTP-200
  browser challenges. Unicode fixture text uses escape sequences.

## Live HTTP checks

Harness: `python dev/test_lcsc_lookup_live.py`.

Final evidence: `.altium-mcp/lcsc_lookup_20261111_171156/report.json`.

| Check | Observed result |
|---|---|
| K07, query `TJA1051T/3`, manufacturer `NXP`, exact `TJA1051T/3,118` | Only C58988, full order code retained, explicit international on-sale flag |
| C58988 with manufacturer Tokmas | Zero matches; historical recovery did not override the current identity |
| C58988 with incomplete order code `TJA1051T/3` | Zero matches |
| Four batch entries with one missing code and one repeated request | Three successful entries, one explicit failure, three unique fetches; input order/labels retained |
| Forced API-miss fixture for C58988, real product-page HTTP fetch | Full Next.js card recovered with parameters and exact identity; static stock/pricing not presented as live USD data |
| E04, C907510, exact Murata MPN and known CN URL | Incomplete recovery explicitly reported; no invented card, stock or price |

The final harness completed successfully. The E04 step intentionally remains a
reported source limitation, not a successful live historical-card recovery.
Initial experiments revealed two issues fixed before the final run: JSON-LD
summaries were preceding full technical payloads, and a browser challenge returned
with HTTP 200 was being classified as an ordinary empty page.

## E04 limitation

The international detail API returned no C907510 card. The public
[domestic product page](https://item.szlcsc.com/973089.html), independently read
through the web reader, identifies `DMT3N4R2U224M3DTA0`, `muRata`, C907510 and an
explicit sale-stop label. Direct server HTTP access encountered 403/405 responses
or browser-verification payloads. The client reports `unavailable`,
`complete=false` and source URLs without trying to solve the challenge.

CN parsing, the discontinued flag, identity checks and null commercial fields
are verified with controlled fixtures; unrestricted CN HTTP retrieval was not
verified. A browser/web reader can inspect the recorded public URLs separately.
No part-specific IDs, assumed sale status or historical parameter values are
hardcoded into the runtime client.

Restart/reconnect the existing MCP connection to load the expanded Python
signature. No new dependency, API key or Altium script is required.

## Revalidation on 2026-11-12

All 30 catalog-specific tests passed again. The six live scenarios also passed
their assertions in `.altium-mcp/lcsc_lookup_20261112_094009/report.json`, including
the explicit unavailable-source outcome for E04. The harness dispatched zero
Altium commands and saved no documents. No implementation changes were needed.

The connected MCP process still reported startup on 2026-11-10 and exposed only
the old `query`/`qty`/`limit` signature. The expanded handler was exercised through
a fresh production import; activating it in that connection requires restarting
or reconnecting the MCP process. Starting a second listener was not attempted.
