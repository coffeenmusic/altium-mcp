# Project selection live verification

Verified on 2026-11-04 in the user's running Altium Designer 24 session.
The final harness run completed 13 calls using the new Python MCP tool function
and a fresh production DelphiScript project through the normal bridge.

## Observed results

| Check | Result |
| --- | --- |
| Original current project | `SALT-FMUV6X-JETSON.PrjPcb` |
| Select exact already-open `AM_NODE_V2.PrjPcb` | Passed; `changed=true`, exact focused path verified. |
| Subsequent `get_project_info` | Returned AM_NODE_V2, confirming the project-level reader follows selection. |
| Repeat target selection | Passed; `changed=false`. |
| Uppercase path and forward slashes | Passed; `changed=false`. |
| Unopened project with a Unicode filename | Rejected before and after target selection; current project unchanged. |
| Script project extension | Rejected at the Python boundary. |
| Reader after rejected requests | Still returned AM_NODE_V2. |
| Switch back to the original project | Passed; `changed=true`. |
| Repeat original selection and teardown | Passed; original context restored. |
| All target document states | Before/after states identical on every selection. |
| Original project document states | Identical before the first selection and after teardown. |
| Existing design files in both project directories | SHA-256 and modification timestamps unchanged for all 35 checked files. |
| Sheet/project saving, compilation and content editing | Not invoked. |

Machine-readable evidence and individual responses:
`.altium-mcp/project_selection_20261104_095639/report.json`.
The harness is `dev/test_project_selection_live.py`.

Regression verification: 107 tests run, 105 passed and two existing
environment-dependent tests skipped. Eight tests specifically cover selection:
path validation, Unicode BOM transport, exact MCP dispatch, failed-target
readback, preserving modified-state snapshots, detecting unexpected saves/state
changes, and retaining uncertain input after timeouts or exceptions.

Unit tests needed execution outside the restricted sandbox because its blocked
loopback socket prevented asyncio's Windows event loop from starting. The live
harness also ran outside it to communicate with Altium and use the existing
shared exchange directory. No design directory received test helpers or reports.

## Limits

Both live projects were already open and had no modified source documents.
Preservation of preexisting modified-state snapshots is covered by unit tests;
the harness did not deliberately dirty a user sheet. Library packages, empty
projects and positive selection of a Unicode-named open project were not
exercised live. Duplicate open instances of an exact path are explicitly rejected
in the native resolver but were not manufactured for testing.

No custom `run_altium_script` operation was needed for this verification: the
previous workaround is now a dedicated `select_project(project_path)` tool.
