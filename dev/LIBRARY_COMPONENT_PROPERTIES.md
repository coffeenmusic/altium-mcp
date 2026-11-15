# set_library_component_properties

```python
set_library_component_properties(
    library_path, symbol_name,
    designator_template=None, component_kind=None
)
```

Set the native default Designator and Component Type of one existing SchLib
symbol. This extends the shared `set_library_component_data` native transaction;
it does not create a custom Designator or BOM parameter. The existing metadata
tool still reserves Designator in its parameter dictionary. Symbol creation and
placed-instance editing are not substitutes for editing these library defaults.

```python
set_library_component_properties(
    library_path=r"C:\Libraries\Parts.SchLib",
    symbol_name="ExternalConnector",
    designator_template="J?",
    component_kind="Standard (No BOM)"
)
```

## Inputs

- `library_path`: absolute path to an existing `.SchLib`. Reuses an open buffer
  without reloading unsaved edits; opens the exact file otherwise.
- `symbol_name`: case-insensitive library reference matching exactly one symbol.
- `designator_template`: a prefix followed by one `?`, e.g. `J?`, `D?`, `C?`,
  `TP?`. Prefix starts with an ASCII letter and contains letters, digits or
  underscores; maximum total length is 64 characters. It updates
  `Comp.Designator.Text`, preserving its other attributes.
- `component_kind`: a canonical name or a case-insensitive UI label below.
  Integer enum values and ambiguous `No BOM` alone are rejected.

| Canonical name | UI label | Native constant |
| --- | --- | --- |
| `standard` | Standard | `eComponentKind_Standard` |
| `mechanical` | Mechanical | `eComponentKind_Mechanical` |
| `graphical` | Graphical | `eComponentKind_Graphical` |
| `net_tie_bom` | Net Tie (In BOM) | `eComponentKind_NetTie_BOM` |
| `net_tie_no_bom` | Net Tie | `eComponentKind_NetTie_NoBOM` |
| `standard_no_bom` | Standard (No BOM) | `eComponentKind_Standard_NoBOM` |

Spaces, underscores, hyphens and parentheses are normalized in kind names;
`Net Tie (No BOM)` is also accepted. This initial API supports these six
documented native constants; `Jumper` is not supported.

`Standard (No BOM)` retains standard electrical behavior and synchronization
while excluding the component from BOM output. It is distinct from Graphical
and Net Tie. See Altium's [component interface](https://www.altium.com/documentation/altium-dxp-developer/schematic-api-design-objects-interfaces-reference)
and [symbol properties](https://www.altium.com/documentation/altium-designer/components-libraries/creating-schematic-symbol).

Omit either optional field to preserve it; omit both to read. Repeating identical
values reports `changed:false` and preserves the prior Modified state.

## Scope and results

Only the selected symbol's requested native fields change. Parameters, Comment,
description, models, part count, geometry and other symbols remain intact.
Value-read-only Designator fields and active editor transactions block edits.
The normal name-only protection of the native Designator is preserved and does
not prevent changing its value; identical values need no write.
Neither document saving, placed-component renumbering, library updates to placed
parts, PCB updates, project membership changes nor compilation are performed.

Mutations use the existing single undo group and mark the library Modified.
The result includes `before`/`after`, `designator_read_only_state`, native `component_kind_code`, canonical
`component_kind`, `designator_template`, `changed`, `saved:false`, Modified flags
and `verification:{passed,errors}`. The metadata reader also exposes these three
native property fields in its snapshots.

Verification checks requested values, exact target identity, unchanged omitted
metadata, Modified behavior and the target file's hash/timestamp. It does not
generate a BOM. API failures may leave partial changes; inspect
`may_have_partial_changes`. Incomplete bridge replies return
`outcome_unknown:true` and retain their request spec. Do not blindly retry an
uncertain mutation. Known native preflight refusals are reported without
implying that mutation started.

## Tests

Unit tests: `server/tests/test_library_component_properties.py`, plus the shared
metadata, symbol metadata and script bundle suites. They cover valid/invalid
templates, all kind aliases, omission/read mode, Unicode symbol names, identity,
readback/preservation/Modified failures and completed versus unknown outcomes.

Opt-in live test without saving:

```powershell
python dev/test_library_component_properties_live.py --library C:\Design\Parts.SchLib --exercise
```

The harness copies a saved library into the toolkit's ignored `.altium-mcp`
directory and changes only that disposable copy in memory. It exercises six
kinds with J?/D?/C? templates, idempotency, independent field updates, invalid
requests and a missing symbol. It checks unchanged symbol geometry and another
symbol, closes the scratch buffer with discard and compares original project
states plus all guarded file hashes/timestamps. Reports remain under
`.altium-mcp/library_properties_<timestamp>/`.

The fresh-process harness invokes production handlers without starting another
MCP listener. Restart the connected MCP process to expose the new tool name;
Altium does not need restarting.

Passed on 2026-11-08 in Altium Designer Enterprise 24.4.1 using a disposable
copy of the open SQ-CARD project's SchLib, symbol `SQ_CARD_BOOT0_SOLDER_JUMPER`.
Run `library_properties_20261108_002719` completed 30 steps: all six kinds,
J?/D?/C? templates, no-op repeats and negative cases. Symbol geometry, another
symbol, original document states and all 10 guarded files (hashes/timestamps)
were unchanged. The scratch library was closed with discard; no documents were
saved. See the ignored run's `report.json` for the recorded checks.

Final regression suite: 149 tests, 148 passed and one platform-specific skip.
