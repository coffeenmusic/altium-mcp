# get_schematic_connectivity live verification

Date: 2026-11-03. Altium Designer Enterprise 24.4.1.
Open project: `SALT-FMUV6X-JETSON`.
Runner: `dev/test_schematic_connectivity_live.py`. No schematic objects were
edited and no sheets were saved.

## Results

| Sheet | Active editor pins | Model pins | Resolved nets |
| --- | --- | --- | --- |
| `Display.SchDoc` | 201 | 198 | 107 |
| `Interfaces.SchDoc` | 179 | 179 | 114 |

For 120 resolved pins of unmirrored Display components, Workspace Manager
electrical-end coordinates matched an independent editor inventory obtained
through `edit_schematic(path, [])`.

Three placed Display pins had no compiler record: `C1.1`, `C1.2`, `TP8.1`.
They remained in the response with `resolution=not_in_compiled_model`,
`net_name=null`, `complete=false`. C1/TP8 did not have `CompilationMasked` set;
the reason for their absence was not established. No net was inferred from
geometry, and an expected GND check for an unresolved pin correctly failed.

Cross-sheet counts were returned: GND has 14 local Display pins and 415 pin
records in the flattened project model; `3V3_SYS` has 4 and 36 respectively.
Exact membership checks use the target sheet, excluding remote endpoints.

| Check | Result |
| --- | --- |
| `compiled=True` | Owning project compiled in memory; actual net names returned |
| `compiled=False` | Same map without explicit compilation; stale-cache warning |
| Independent expectation `C2.1 -> TMDS_D2_N` | Passed |
| Common net and exact local membership | Passed |
| Wrong expected net name | Rejected |
| Expected connection across different nets | Rejected |
| Forbidden existing connection | Rejected |
| Incomplete list with `exact=true` | Extra members detected |
| Missing designator/pin | Rejected |
| Unknown net | Failed; unknown data did not prove disconnection |
| Two different known nets with `connected=false` | Passed |
| Switch to another sheet, then request Display | Original Display map returned |
| MCP registration | Required `sheet_path`; defaults `compiled=True`, `expected_connections=None` |

17 new unit tests cover validation, unknown/ambiguous pins, contradictory model
records, shared pins and repeated channels using synthetic data, compiler
coordinate priority and expectation types. Regression run: **58 tests,
57 passed, 1 skipped**. The existing CP1252 test was skipped because the system
uses another ANSI code page.

Repeated channels were tested with synthetic data, not a separate live
multichannel project. Missing cache and multiple project ownership have explicit
failure paths but were not created in the open project.

## Existing project diagnostics

`get_erc_violations(compile=False)` returned 2 errors, 249 warnings and no fatal
errors. Both errors describe floating inputs:

- `NetU6_6`: pin `U6-6`.
- `NetU11_3`: pin `U11-3`.

These describe the current project, not a retrieval failure. Electrical errors
and components missing from the compiler model were not repaired.

## No-change verification

Display object snapshots matched before/after. Its Modified flag and the set
of modified project documents were unchanged: Display was already modified
before this task. Modified was never cleared.

SHA-256 hashes matched for all **12 project `.SchDoc` files**. A final check
also matched all **14 `.SchDoc` files in the project directory** against the
baseline captured before this task's first diagnostic script.
The executor probe succeeded with `ProcessDepth=0`.

Diagnostic `run_altium_script` calls inspected Workspace Manager coordinates,
identities, pin ownership, coverage, `CompilationMasked` and executor state.
All connectivity maps and expectation checks used the permanent
`get_schematic_connectivity(sheet_path, compiled, expected_connections)` tool.
Diagnostic scripts did not save documents.

Local snapshots and machine report:
`.altium-mcp/schematic_connectivity_20261103_003059/report.json`.
The same directory holds `compiled.json`, positive/negative checks,
`second_sheet.json`, `compiler_errors.json` and `final_current_code.json`.
Artifacts are Git-ignored and stored in the toolkit checkout, outside the PCB
project. See the [API documentation](SCHEMATIC_CONNECTIVITY.md).
