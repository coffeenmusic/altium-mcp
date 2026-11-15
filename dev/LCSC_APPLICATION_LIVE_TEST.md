# Strict LCSC application validation

Date: 2026-11-12. This record covers manufacturer/package guards and supplier
provenance added to `set_library_component_lcsc_data`.

## Automated checks

- 289 selected regression tests: 288 passed and one skipped for the host ANSI
  code page. Includes 35 catalog tests plus metadata, creation, tool-registration
  and document/library regressions.
- Evidence: `.altium-mcp/lcsc_application_regression_20261112.log`.
- Tests require matching nonempty unique MP and MF, including supported brand
  aliases; matching MPN with another/missing/ambiguous manufacturer never reaches
  the write call. Full order suffixes remain significant.
- Package conflicts return symbol/catalog values and block by default. An
  explicit override warns and preserves core Package. It cannot bypass MP/MF or
  ambiguous Package checks. Missing package data remains explicitly unverified.
- Supplier source URL/type, UTC timestamp, regional sales status, raw status
  evidence and lifecycle are emitted as strings. Unknown status remains unknown;
  zero stock does not imply discontinuation.

## Real catalog data

A fresh direct API fetch of C58988 verified the production card parser,
MP/MF/package preflight and seven new provenance/status values. Evidence:
`.altium-mcp/lcsc_application_http_20261112.json`. This was an HTTP-only check,
not a native library write; no Altium command or save was involved.

## Native Altium verification

Harness:

```powershell
python dev/test_lcsc_application_live.py --exercise `
  --schlib C:\project\Symbols.SchLib --lcsc-id C136648
```

The harness copies a saved library into the ignored toolkit directory, creates
five disposable symbols and checks valid application, timestamp refresh,
manufacturer/MPN/package refusals, explicit package override, independent
readback and unchanged geometry/core metadata. It closes its scratch buffer with
discard and compares source/scratch disk hashes and original project context.
Unknown completion stops further dispatch, including cleanup.

After the user made Altium ready, the final run passed all 37 steps in
`.altium-mcp/lcsc_application_20261112_100431/report.json`:

| Check | Observed result |
|---|---|
| Matching MP, MF and Package | 22 supplier fields applied and read back; source URL, UTC timestamp and regional status verified |
| Repeated live refresh | New retrieval timestamp persisted and Modified set |
| Same MPN, wrong manufacturer | Rejected; complete symbol metadata unchanged, including when package override was requested |
| Wrong MPN | Rejected; complete symbol metadata unchanged |
| Package conflict | Rejected by default with both original values; metadata unchanged |
| Explicit package override | Warning/conflict remained visible; supplier data written while core Package stayed unchanged |
| Missing MF | Rejected; metadata unchanged |
| Existing reference symbol and fixture geometry/core fields | Preserved |
| Cleanup and original context | Scratch library closed with discard; project/document states restored |
| Disk state | All 12 guarded source/scratch files retained hashes and modification times; no CAD save ran |

The initial attempt was blocked before opening/editing the scratch library:
`.altium-mcp/lcsc_application_20261112_095612/report.json`. Altium did not start
the first read command. Both UI inspection runtimes also failed with a missing
kernel-assets path. No UI input was sent and no user command/script was stopped;
the user resolved the condition before the successful retry.

The existing connected MCP process uses older Python code. Reconnect/restart
that connection to expose the new guard and `allow_package_mismatch` argument.
No native command, new dependency, custom DelphiScript or save option was added.
