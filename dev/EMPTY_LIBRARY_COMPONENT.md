# remove_empty_library_component

```python
remove_empty_library_component(library_path, component_name, dry_run=False)
```

Remove one verified empty member of a SchLib or PcbLib in memory. Intended for
the native `Component_1` / `PCBComponent_1` placeholders left after creating a
real component, such as K01. Existing inventory/creation tools could inspect or
add members, but did not provide checked library-member removal. This command
uses Altium's native [RemoveSchComponent](https://www.altium.com/documentation/altium-dxp-developer/isch-lib-interface)
and [RemoveComponent](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-system-interfaces-reference)
methods after content preflight.

## Target and behavior

- `library_path`: exact absolute `.SchLib` or `.PcbLib` path. A saved library is
  opened if necessary. A new unsaved library must already be open at that path,
  for example after `create_document`. Dirty buffers are reused without reload.
  Ambiguous document aliases are refused.
- `component_name`: exact member name, matched case-insensitively. The tool can
  check any explicitly named member; default naming alone never proves emptiness.
  Multiple matches are refused. An absent member is a successful no-op.
- `dry_run=True`: return content assessment and `removable` without deletion.
  A nonempty component is a successful inspection with `empty:false` and
  explanatory `blockers`. Actual removal of that component returns a refusal.
- The last library member is retained. Create the real symbol/footprint first;
  `empty:true` with `removable:false` identifies a last empty member.
- Only an actual removal marks the library dirty. Dry runs, absent targets and
  preflight refusals preserve the previous Modified state.

No document or project is saved, no file deleted, no placed component changed,
and no project compiled. Removing a member does not create a replacement.

## Empty-content checks

SchLib checks include all child objects without a part, display-mode or
visibility filter. Any pin, graphic, custom parameter (even hidden or empty),
implementation/model, or unrecognized child blocks deletion. Only the actual
native Designator and Comment objects may be present, with default placeholder
text (`""`, `"*"`, `"?"`). Their identities are checked; a custom parameter with a
similar name is not exempt. The native new SchLib has two such child objects,
so `child_count:2` can still be empty.

A symbol also needs an empty description and design-item ID, Standard kind,
one part and one display mode. Library identifiers are allowed only when empty,
placeholder text or the symbol's own LibReference; external identifiers block
removal. Nondefault Comment/designator values protect otherwise blank symbols.

PcbLib checks every child primitive, including hidden graphics, pads and 3D
bodies. There must be zero children, an empty native Description and zero native
Height. A zero-pad footprint with only text, a body, description or height is
not empty. Unknown primitive types are counted too.

An unreadable API property fails closed. The editor is switched to a surviving
member before removal; contents are checked again after the editor buffer is
flushed. The command never relies on the placeholder's name or visible canvas
alone.

## Examples

```python
# Inspect before removing the default symbol after creating K01.
remove_empty_library_component(
    r"C:\Design\Parts.SchLib", "Component_1", dry_run=True
)
remove_empty_library_component(r"C:\Design\Parts.SchLib", "Component_1")

# The footprint equivalent, including a newly created unsaved PcbLib.
remove_empty_library_component(r"C:\Design\Parts.PcbLib", "PCBComponent_1")
```

## Results and failure handling

Results include exact target/kind, `found`, `dry_run`, `removable`, `removed`,
`changed`, Modified states, `saved:false`, `assessment` and
`verification:{passed, errors}`. Assessment includes `empty`, `blockers`, native
child count and relevant metadata. `component_names_before`/`after` show the
membership change.

Python verifies that only the requested native member disappeared and that
survivor identities, child counts and exposed metadata stayed unchanged. It
also checks Modified behavior and the target file hash/timestamp (or continued
file absence for a new unsaved library). Internal interface IDs are kept inside
the verifier, not returned as selectors. This is a member-removal check, not an
exhaustive geometry comparison of every surviving object.

A preflight refusal has `may_have_partial_changes:false`. A failure after the
native removal began can leave changes. An incomplete bridge reply returns
`outcome_unknown:true` and retains its request spec. Inspect Altium before
retrying; uncertain mutations are not automatically repeated.

## Verification

Unit tests: `server/tests/test_empty_library_component.py`. Opt-in live harness:

```powershell
python dev/test_empty_library_component_live.py --exercise --library C:\Design\SQ-CARD.SchLib --symbol LTST-C155KGJRKT
```

The harness creates disposable unsaved libraries under ignored `.altium-mcp`.
It checks native defaults, last-member refusal, hidden-parameter and
description-only protection, populated-symbol/footprint refusal, dry runs,
actual removal and repeated absent-target no-ops. Retained test-member geometry
and metadata are compared using existing getters. All scratch buffers are
discarded; original project document states and file hashes/timestamps are
compared, and scratch library files must remain absent.

`--probe` limits the run to default inspection and last-member refusals. The
fresh-process harness invokes production handlers without a second listening
MCP server. Restart the connected MCP process to expose the new tool name;
Altium itself does not need restarting.

Passed on 2026-11-08 with the SQ-CARD project open. Run
`empty_library_20261108_115305` completed 44 steps using three disposable
unsaved libraries. Both native placeholders were removed after adding real
members; populated members, a hidden-parameter-only symbol, a description-only
footprint and last members were retained. Dry runs and repeated removal of an
absent target passed. Retained symbol/footprint getter snapshots matched
exactly. All nine original guarded files and document states were unchanged;
all three scratch buffers were discarded and no scratch library file existed.

Regression suite: 181 tests, 180 passed and one platform-specific skip. The live
harness uses the existing batch/getter current-library modes after exact
`open_document` focus because their explicit path modes require saved files.
No arbitrary `run_altium_script` snippets are needed.
