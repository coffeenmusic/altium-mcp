# edit_schematic live verification

Date: 2026-11-03. Altium Designer Enterprise 24.4.1.
Open project: `SALT-FMUV6X-JETSON`; target: `Display.SchDoc`.
No documents were saved.

## Results

| Check | Result |
| --- | --- |
| MCP registration | `edit_schematic` registered with required `sheet_path`, `operations` |
| Read with `operations=[]` | 382 supported objects, UIDs, parameters and electrical pin coordinates |
| Add | All 7 types: component, wire, net_label, port, power_port, junction, text |
| Update by UID | All 7 types: position, orientation, mirroring, parameters/Comment, wire vertices and port properties |
| Update by designator | Test component renamed and Comment changed |
| SchLib placement | Real symbol placed from project `fmuv5X.SchLib` |
| Invalid second target | Entire batch rejected; first edit not applied |
| Odd wire coordinate count | Rejected before Altium dispatch |
| Duplicate designator | Rejected without edits |
| Same target through different selectors | Rejected before editing |
| Missing SchLib symbol | Rejected without edits |
| Unicode/escaping | Non-ASCII text, quotes, apostrophes, pipes, equals signs, commas and newlines round-tripped |
| Delete | All 8 test objects removed: 7 initial objects and the library component |
| In-memory restoration | Before/after snapshots of 382 original supported objects matched |
| Disk verification | SHA-256 hashes of all 12 project `.SchDoc` files matched |
| Executor | Final probe succeeded with `ProcessDepth=0` |

Geometry was checked using a copy of two-pin `C1`. After translation its pins
retained offsets of -100/+200 mil from the component origin. Rotation and
mirroring produced measured connection points `(16350,1000)` and `(16350,1300)`
for origin `(16350,1100)`, verifying physical pin movement as well as pose flags.

Regression run: **41 tests, 40 passed, 1 skipped**. The existing CP1252 test
was skipped because that code page is not used by the current system.

Reproduction commands are in the [API documentation](EDIT_SCHEMATIC.md).
Local snapshots and machine report:
`.altium-mcp/schematic_edit_20261103_000855/report.json`.
Artifacts are Git-ignored and stored in the toolkit checkout, outside the PCB
project.

## Fixes found during development

Initial reading exposed an unavailable document-kind constant. Verified numeric
IDs, `32` for SchDoc and `33` for SchLib, replaced it and reading was retested.

The first deletion test exposed an unsupported API notification. Deletion was
changed to `RemoveSchObject` with `SCHM_PrimitiveRegistration`. All seven
remaining test objects were removed by UID and the original snapshot restored.
The interrupted test's transaction was closed; no active transactions remained
before or after the final run.

Altium expanded a narrow test port to fit its name during redraw. Exact geometry
checks then used sufficient width; this behavior is documented in the API guide.

Diagnostic `run_altium_script` calls read UIDs, checked executor/ProcessDepth and
closed the interrupted test transaction. Add/update/delete and final inventory
used the permanent `edit_schematic(sheet_path, operations)` command.

## Final state and limits

No save or Modified reset was called. Only `Display.SchDoc` remained marked
modified after the test transactions; original supported objects were restored
and disk hashes matched. Full ERC, arbitrary circuit correctness and every
symbol style/multipart configuration were not covered by this run.
