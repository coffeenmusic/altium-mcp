# Opening targets: live verification

Verified on 2026-11-04 in the user's running Altium session. The final run
completed 25 calls using fresh production scripts and the same bridge as MCP.
Original context: `SALT-FMUV6X-JETSON.PrjPcb`.

| Check | Result |
| --- | --- |
| Open a closed copied SchLib | Passed; `opened=true`. |
| Repeat SchLib open | Reused the in-memory document; `opened=false`, `already_open=true`. |
| Open and repeat a copied PcbLib | Passed. |
| Open a copied SchLib with a Unicode filename | Exact active editor path verified. |
| Open and repeat a copied SchDoc | Passed; no sheet save requested. |
| Add an unsaved parameter to the scratch SchLib, switch editors, reopen with a different path case/separator spelling | Parameter and Modified flag retained. |
| Open a closed Unicode-named PrjPcb | Passed; exact current project verified. |
| Read the opened project through `get_project_info` | Unicode path retained after fixing ANSI response escaping. |
| Read the persisted toolkit context hint | ASCII JSON-escaped path decoded to the exact Unicode filename. |
| Repeat project open | Reused the project; no reload. |
| Open a closed LibPkg referencing the two copied libraries | Passed, including preservation of the already-open dirty SchLib. |
| Repeat LibPkg open | Reused the project; existing source states unchanged. |
| Independent SchLib readback after project/package opening | Unsaved parameter still present and Modified remained true. |
| Missing project/document | Rejected; no file or blank target created. |
| Wrong project extension | Rejected at the Python boundary. |
| Open the already-open user project | Reused it with identical document-state snapshots. |
| Restore original project/editor | Passed; original document states unchanged. |
| Original source disk contents and modification timestamps | Unchanged for all 21 checked files. |
| Scratch disk contents and modification timestamps | Unchanged for all six fixtures after Altium operations. |

The harness creates test project files and copies source documents into the
toolkit's ignored directory before opening them. No Altium save or explicit
compile operation is called. Only a scratch library receives a deliberate
in-memory metadata edit. Original projects/documents are not closed or edited.
Scratch projects/documents remain open for inspection.

Evidence: `.altium-mcp/open_targets_20261104_101201/report.json` and the adjacent
per-call snapshots. Reproduce with `python dev/test_open_targets_live.py` using
the configured server Python environment and an open project containing a
SchLib, PcbLib and schematic source. Do not run other Altium calls concurrently.

Regression results: 117 tests run, 115 passed, two existing environment-dependent
tests skipped. Ten new tests cover open/reuse dispatch, supported extensions,
dirty-state preservation, missing-file responses, exact active-document/project
verification, unexpected save/compile/state changes, Unicode transport and
uncertain bridge failures.

## Limits

Positive PcbDoc opening has boundary coverage but was not exercised live.
The new PrjPcb fixture is intentionally empty; LibPkg verifies opening a project
with actual sources. Loading an arbitrary project's plugins, managed content or
modal warnings remains governed by Altium. A new load may mark a document
modified; the tools preserve and report that state rather than clearing it.

No custom `run_altium_script` operation was needed: the new dedicated tools
invoke the native API directly. The first live iteration exposed Unicode loss
in `get_project_info`; its output and the project context hint were fixed before
the final passing run.
