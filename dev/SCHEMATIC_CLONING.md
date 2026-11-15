# Clone components between schematic sheets

`clone_schematic_components(source_sheet, target_sheet, designators)` copies
placed component instances into a different SchDoc. It never saves either sheet,
compiles a project, changes project membership or updates the PCB.

## Existing alternatives

`edit_schematic` supports `source_designator` only on its target sheet. Rebuilding
an instance from SchLib does not preserve all instance overrides. The development
`duplicate_sheet.pas` script copies an entire sheet's objects. None provides this
combination of exact source/target paths and a selected list of components.

## Contract

- Both paths are absolute Windows `.SchDoc` paths. Each must exist on disk or
  identify an already-open named unsaved sheet. Open buffers are reused without
  reloading; unsaved source edits are included.
- `designators` contains 1 to 500 distinct names, case-insensitively. Empty lists,
  duplicate names, control characters and surrounding whitespace are rejected.
- A multipart designator selects all its placed sections on the source sheet.
  Unplaced sections are not instantiated. Repeated source instances with the
  same designator and part ID are ambiguous and rejected. Expansion is limited
  to 1000 placed instances.
- Source/target must differ. Missing sources, ambiguous UIDs and any target
  component with a requested designator fail the whole preflight before edits.
  Repeating a successful request therefore reports a collision rather than
  creating duplicates or replacing prior copies.
- Copies retain designators, positions in mils, rotation/mirror, current part and
  display mode, parameter values/display settings, pins, symbol primitives and
  library/model links, including footprint pin mappings.
- Every component and every child object with a nonempty UID receives a fresh
  UID. Internal objects whose original UID is empty keep it empty. New IDs are
  checked against both sheets and the rest of the batch.

Only components and their owned content are copied. External wires, labels,
ports, junctions and sheet graphics are excluded. Coordinates are not offset or
fitted to the destination sheet. A copied pin can connect to existing target
wiring at that position. Preserved designators require reannotation when source
and target remain in the same project; no reannotation or ERC is run automatically.
Fresh UIDs make independent copies rather than moving existing PCB-linked parts.

```python
clone_schematic_components(
    source_sheet="C:/Design/Controller/Supply.SchDoc",
    target_sheet="C:/Design/Controller/Sequencer.SchDoc",
    designators=["U6", "R18", "C25"],
)
```

The target may be created first with `create_document(...)` and left unsaved.
Other editing tools retain their own disk-file requirements. The clone tool
does not create a missing closed sheet itself.

## Readback and failures

The result includes exact paths, `saved=false`, `source_unchanged`, source/target
Modified flags, `cloned_count`, `registered_component_uids`,
`target_counts_before/after`, `process_depth`, `components` and `verification`.

Each successful component entry includes `source_unique_id`, `component`,
`preserved` and `uid_map` records (`source_uid`, new `unique_id`, `object_id`).
`component` contains its new root/child UIDs and preserved state: part, pose,
library references, parameters/models, pin coordinates/electrical attributes,
parameter display and primitive counts. Source/clone snapshots are retained
separately when verification fails.

Readback checks selected-source state and Modified preservation, component state,
UID coverage/uniqueness, target object-count changes and a closed undo transaction.
The target gains only the requested component instances, becomes Modified and
retains its existing edits. `source_unchanged` covers selected component snapshots
and the source Modified flag; it is not a whole-document binary serialization.
Native replication copies symbol geometry; readback verifies primitive counts and
pin/parameter geometry rather than serializing every graphic attribute.

The native batch uses one target PreProcess/PostProcess group. Preflight failures
have `cloned_count=0` and `may_have_partial_changes=false`. Native failures after
registration may leave copies: inspect `registered_component_uids`, component
readback and `failed_index` before retrying. A transport failure returns
`clone_outcome_unknown=true` and keeps the unique `spec_file` for a late invocation.
Execution is not an atomic rollback transaction. A failed request may still have
opened/focused its explicit input documents even when no copies were made.

## Implementation and verification

`server/schematic_cloning.py` validates/serializes UTF-8 BOM specs and verifies
native readback. `server/AltiumScript/schematic_cloning.pas` uses exact SchDoc
resolution, sheet-owned component selection, native `Replicate`, fresh UID
assignment and container/robot registration. Unicode responses are escaped before
the legacy ANSI writer. The command bypasses legacy focus guessing and is marked
mutating for timeout handling.

API reference: [Schematic design objects](https://www.altium.com/documentation/altium-dxp-developer/schematic-api-design-objects-interfaces-reference).

Run `python -m unittest server.tests.test_schematic_cloning -v` for offline tests.
See [live verification](SCHEMATIC_CLONING_LIVE_TEST.md) for Altium coverage.
