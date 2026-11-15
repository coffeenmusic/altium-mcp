# set_library_component_data live verification

Date: 2026-11-03. Altium Designer Enterprise 24.4.1.
Source project: `SALT-FMUV6X-JETSON`, libraries `fmuv5X.SchLib` and
`fmuv5X.PcbLib`. Writes were limited to an unsaved SchLib copy in the toolkit
checkout. No original library, PCB or schematic was edited or saved.

## Coverage

Target symbol: `CONN_RCPT_2X25_RECPT_0.4MM_SMD`, 50 pins.
Footprints: `HIROSE_DF40C-50DS-0-4V51` (50 pads) and
`HIROSE_DF40C-50DP-0-4V51` (54 pads).

| Check | Result |
| --- | --- |
| Metadata read without edits | Unchanged snapshots and Modified state |
| Parameter creation/update | Values read back correctly; unrelated parameters retained |
| Comment and description | Empty Comment and new description read back correctly |
| Existing footprint model | Updated without duplication; explicit PcbLib link verified |
| Additional footprint model | Added without removing the existing model |
| Pin swap | Pin 1 mapped to pad 2, pin 2 to pad 1 |
| One-to-many map | Pin 1 mapped to pad 1 and M1/M2/M3/M4 on the alternate model |
| Equivalent repeated request | `changed=false`; no duplicate parameters/models |
| Current footprint selection | Alternate selected; previous PCB model deselected |
| Parameter-only update | All model metadata preserved |
| Missing footprint | Entire request rejected before parameter changes |
| Missing symbol pin | Rejected without changes |
| Missing footprint pad | Rejected without changes |
| Incomplete pin coverage | Rejected without changes |
| Invalid parameter type/missing symbol | Rejected |
| Multipart symbol | Existing map for all 158 pins of two-part `IC_SOM_FMUV5X_FMU_TO_BASE_SMT` validated and written |
| Unicode and escaping | Greek/Cyrillic characters, a supplementary-plane character, quotes, delimiters and newline round-tripped |
| Geometry | Before/after symbol primitives matched |

The final live runner completed **17 command checks**. Its source metadata and
source Modified comparisons passed. Disk hashes matched for **14 schematic
files, both original libraries and the scratch library**. The scratch file on
disk stayed unchanged despite in-memory edits.

The final original project still had exactly its pre-existing modified
`Display.SchDoc`; 382 supported objects were read without saving it.
The executor probe succeeded with `ProcessDepth=0`.

Unit/regression run: **81 tests, 80 passed, 1 skipped**. This includes 14 new
metadata tests plus schematic, sandbox, file, OutJob, server identity and launcher
tests. The existing CP1252 test was skipped on the current CP1251 system.

## Findings and implementation corrections

Altium stores the requested Unicode correctly, but ANSI JSON output replaced
characters outside CP1251 with `?`. The command now ASCII-escapes Unicode after
JSON construction. Live readback verified both BMP and supplementary-plane data.
Input remains BOM-marked UTF-8 to avoid the legacy JSON helper's `Chr` limitation.

An exploratory direct map-definer call failed on the trivial-map path without
wedging the executor. `MapAsString` was then verified for identity, swapped and
one-to-many mappings and used by the permanent implementation.

Writing maps adds `eMapDefiner` objects (ID 44). These are model metadata, not
graphics. `get_symbol_primitives` now excludes them, preventing false geometry
differences after a metadata-only edit.

Earlier exploratory and test runs used separate scratch copies. Source metadata
and disk hashes remained unchanged. The runner permits an initially modified
scratch library and compares its before/after state rather than clearing Modified.

## Limits and artifacts

This test validates stored model links and pin maps, not a schematic-to-PCB ECO,
mechanical fit, manufacturing correctness or the electrical suitability of the
deliberate test swap. Locked/managed-model and read-only-parameter refusal paths
are implemented but were not exercised by changing locks on the source libraries.
The tests did not modify Workspace/IntLib/database model records.

Custom `run_altium_script` calls were API exploration for the missing metadata
operation: model/link inspection, map behavior, Unicode code points, object kinds
and executor state. The reusable replacement is
`set_library_component_data(library_path, symbol_name, parameters, footprint_models, comment, description)`.
All final metadata reads/writes used this implementation through the live runner.

Local artifacts:
`.altium-mcp/library_component_data_20261103_163157/report.json`.
The directory also contains before/after metadata, geometry, footprint data,
negative cases, copied scripts and the unsaved scratch SchLib. Artifacts are
Git-ignored and outside the PCB project. See the [API guide](LIBRARY_COMPONENT_DATA.md).
