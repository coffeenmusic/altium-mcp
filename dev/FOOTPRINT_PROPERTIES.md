# set_footprint_properties

```python
set_footprint_properties(library_path, footprint_name, height_mm=None)
```

Read/set the native **Height** field of one existing PcbLib footprint, using
`IPCB_LibComponent.Height`. This is the field in the Footprint properties panel.
Existing pad/layer tools cannot change it; 3D body generation edits different
objects. The new command reuses the existing exact-library edit transport.

## Contract

- `library_path`: absolute path to an existing `.PcbLib`. An open buffer is
  reused without reloading its unsaved edits; a closed library is opened.
- `footprint_name`: case-insensitive name matching exactly one footprint.
- `height_mm`: finite number within 0..1000 **millimeters**, independent of the
  editor's display units or Windows decimal separator. Zero resets the stored
  height to zero. Omit it to read without changing the document.
- Identical native coordinate values are no-ops and preserve Modified, including
  an already-dirty buffer. A changed height is one undo group and marks the PCB
  library dirty through `SetState_DocumentHasChanged`.
- The command changes only Height. It preserves footprint description, pads,
  primitives, 3D models/bodies and other footprints. It does not update placed
  PCB components, change project membership, save documents or generate outputs.

Example for an existing USB-C footprint (height is an illustrative test value,
not a dimension established for the user's connector):

```python
set_footprint_properties(
    library_path=r"C:\Design\Parts.PcbLib",
    footprint_name="USB_C",
    height_mm=3.2,
)
```

This does not resize a 3D model or change its standoff. Altium can prioritize
existing 3D bodies when evaluating height rules, ahead of the stored footprint
Height field. See the [native Height interface](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-design-objects-interfaces-reference)
and Altium's [height-rule explanation](https://www.altium.com/documentation/knowledge-base/altium-designer/how-to-create-an-area-on-the-pcb-where-component-footprint-placement-is-restricted-based-upon-footprint-height).

## Results

Replies include exact library/footprint identity, `saved:false`, `changed`,
`was_modified`, `is_modified`, `updated_count`, `verified_count`, before/after
property snapshots and `verification:{passed,errors}`. Each snapshot has
`height_mm` and description. `get_footprint_primitives` now also reports
`height_mm` in both inventory and geometry modes, using the live buffer.

The native setter verifies the internal coordinate value. Millimeter readback
divides by `MilsToCoord(1)` instead of using `CoordToMils`, which rounds decimal
mils. Python checks the requested height within 0.000003 mm (native coordinate
conversion tolerance), unchanged description, Modified/no-op behavior, exact
identity and an unchanged target disk file (hash and timestamp).

Validation/target refusals precede edits. Inspect `may_have_partial_changes`
after an API/readback error. An incomplete bridge result returns
`outcome_unknown:true` and retains its unique spec file; do not blindly retry
an uncertain mutation. Completed native refusals clean up their specs.

## Verification

Unit tests: `server/tests/test_footprint_properties.py`, shared footprint editor
tests and script bundle tests. They cover finite/range/type validation, read
mode, precision tolerance, preservation/identity/Modified failures, disk writes
and completed versus unknown outcomes.

Opt-in live checks:

```powershell
python dev/test_footprint_properties_live.py --library C:\Design\Parts.PcbLib --footprint USB_C --exercise
```

The harness copies the library into ignored `.altium-mcp` storage and edits only
that copy in memory. It tests 3.2 mm, a fractional value and zero; repeated calls;
independent getter readback; invalid input and a missing footprint. It compares
all geometry/3D body data and another footprint, discards the scratch buffer,
and verifies original project states plus guarded file hashes/timestamps.

The fresh-process harness calls production handlers without starting another
MCP listener. Restart the connected MCP process to expose the new tool name;
Altium itself does not need restarting.

Passed on 2026-11-08 in Altium Designer Enterprise 24.4.1, using a disposable
copy of SQ-CARD.PcbLib and USB-C footprint `GSWITCH_GT_USB_7010ASV`.
Run `footprint_properties_20261108_005638` completed 29 steps, including
3.2 mm, 0.123456 mm, zero, no-op repeats and missing/invalid targets.
All 34 primitives and the existing 3D body were unchanged, as was another
footprint. Original document states and all 10 guarded files matched the
baseline. The scratch buffer was closed with discard; no documents were saved.
The ignored run's `report.json` records the assertions and file checks.

Final regression suite: 156 tests, 155 passed and one platform-specific skip.
