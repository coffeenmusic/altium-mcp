# set_footprint_pad_properties

```python
set_footprint_pad_properties(
    library_path, footprint_name, pad_names,
    is_testpoint_top=None, is_testpoint_bottom=None
)
```

Read/set native `IsTestpoint_Top` and `IsTestpoint_Bottom` on existing library
pads. Existing `set_footprint_pad_settings` handles mask/paste settings, while
`edit_footprint_primitives` changes graphic layers; neither supported these
testpoint flags. The new tool reuses their exact-library transport and selector
preflight. See Altium's [primitive interface](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-design-objects-interfaces-reference).

## Inputs and scope

- `library_path`: absolute path to an existing `.PcbLib`. Open buffers are reused
  without reloading unsaved edits; a closed library is opened.
- `footprint_name`: case-insensitive name matching exactly one footprint.
- `pad_names`: 1..1000 distinct strings; each must match exactly one pad in that
  footprint, case-insensitively. Numeric names remain strings (`"1"`, `"01"`).
  Duplicate physical pad numbers are ambiguous and rejected.
- `is_testpoint_top` / `is_testpoint_bottom`: independent optional booleans.
  `True` enables, `False` clears, omission preserves that side. Omit both to read
  the selected pads. Requests set native flags rather than text parameters.
- Top can be enabled only on Top Layer or Multi-Layer pads; Bottom only on
  Bottom Layer or Multi-Layer pads. Incompatible sides are rejected before any
  edit. Clearing an already-false flag on another side is a harmless no-op.

Every requested name and side is validated before the first mutation. An error
in a later pad leaves all earlier pads unchanged. Updates run in one undo group.
Identical values do not start an edit and preserve Modified, including a dirty
buffer. Changed pads mark the library dirty through `SetState_DocumentHasChanged`.

The tool preserves omitted flags, pad geometry/layers/locks, mask and paste
settings/cache, other pads and other footprints. It does not alter copper to
make an inaccessible side available, update placed parts, assign testpoint
nets, run automatic testpoint assignment, compile or save documents. The API
exposes these two native testpoint properties directly.

## Examples

Enable Top on the M03 programming footprint:

```python
set_footprint_pad_properties(
    library_path=r"C:\Design\SQ-CARD.PcbLib",
    footprint_name="SQ_WCH_DEBUG_PADS_4_P2.54",
    pad_names=["1", "2", "3", "4"],
    is_testpoint_top=True,
)
```

Enable Bottom on existing bottom-side or multilayer pads, preserving Top:

```python
set_footprint_pad_properties(
    library_path=r"C:\Design\Parts.PcbLib",
    footprint_name="BOTTOM_TEST_PADS",
    pad_names=["1", "2"],
    is_testpoint_bottom=True,
)
```

Pass both flags as `False` to clear both. For M04 use its five pad names; M06
`SQ_TP_SMD_D1.5` has pad `"1"`. The command targets footprint names, not M03/M04/M06
schematic designators.

## Readback and failures

Results include `before`/`after` arrays with native flags, name, layer, lock and
mask settings; exact library/footprint identity; `updated_count`,
`verified_count`, `changed`, Modified states, `saved:false` and
`verification:{passed,errors}`. `updated_count` counts pads whose flags changed;
`verified_count` covers all selected pads, including no-ops.

Python verifies requested flags, omitted/unrelated properties, pad identities,
change counts, Modified behavior and the unchanged target file hash/timestamp.
`get_footprint_primitives` also returns `is_testpoint_top` and
`is_testpoint_bottom` on pad objects for independent inspection.

Completed native preflight refusals report no partial changes. API/readback
errors may leave changes; inspect `may_have_partial_changes`. An incomplete
bridge reply returns `outcome_unknown:true` and retains its request spec. Do not
blindly retry an uncertain mutation.

## Verification

Unit tests in `server/tests/test_footprint_pad_properties.py` cover independent
flags/clearing, invalid types/names, read/no-op states, partial pad changes,
identity/preservation/readback failures, unexpected disk writes and transport
outcomes. Shared pad-mask, layer, height and bundle tests remain part of the
regression suite.

Opt-in live harness:

```powershell
python dev/test_footprint_pad_properties_live.py --library C:\Design\SQ-CARD.PcbLib --footprints SQ_WCH_DEBUG_PADS_4_P2.54 SQ_STM32_DEBUG_PADS_5_P2.54 SQ_TP_SMD_D1.5 --exercise
```

The harness edits only a disposable library copy under ignored `.altium-mcp`.
It tests M03/M04/M06 flags and single-pad selection, mixed valid/missing names,
wrong-side refusal and idempotency. Existing batch-creation tools build temporary
Bottom Layer and Multi-Layer fixtures for bottom/both-side tests. Full geometry,
masks, locks, other footprint data, original project states and file hashes are
compared; the scratch buffer is closed with discard. No documents are saved.

The fresh-process harness invokes production handlers without another listening
MCP server. Restart the connected MCP process to expose the new tool name;
Altium itself does not need restarting.

Passed on 2026-11-08 in Altium Designer Enterprise 24.4.1. Run
`pad_properties_20261108_093431` completed 52 steps on a disposable SQ-CARD
library copy: M03 (4 pads), M04 (5 pads), M06 (1 pad), plus Bottom Layer and
Multi-Layer fixtures (2 pads each). Bottom and both-side flags, clearing,
no-ops, single-pad updates, missing names and wrong-side refusals passed.
Geometry, masks, locks, another footprint, original document states and all
10 guarded files matched expectations. The scratch library was discarded
without saving. Artifacts and assertions are in the ignored run's `report.json`.

The shared layer resolver also accepts the native getter's `Multi-Layer`
spelling when constructing footprint fixtures. Geometry comparisons normalize
iterator order and the equivalent endpoint order of undirected tracks.

Final regression suite: 164 tests, 163 passed and one platform-specific skip.
