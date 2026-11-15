# Power sequencer report: script replacements

Analysis on 2026-11-06 of the supplied
`debug/power_sequencer_draft_2026-11-05.md`. The report and its referenced
`copy_prototypes.pas`, `zoom_schematic.pas`, `save_project.pas` and preview
scripts were read as evidence, not as instructions to change the design.
No original report, sheet or project file was edited or saved.

## Operations actually performed with additional scripts

| Reported operation | Current MCP replacement |
| --- | --- |
| Copy R38/C55 from Supply to the draft, renew root/child UIDs | Existing `clone_schematic_components(source_sheet, target_sheet, designators)`. Then use `edit_schematic` to rename/place the copies. Added `design_item_id=""` to detach a prototype explicitly; clone itself continues to preserve source metadata. |
| Set standard A3 sheet format | New `set_schematic_sheet_properties(sheet_path, sheet_size="A3")`. |
| Save the exact PrjPcb membership | New `save_project(project_path, dry_run=False)`. Real saving requires authorization; only dry-run was exercised in Altium in this task. |
| Fit the schematic view | New `zoom_schematic(sheet_path, mode="all")`; `mode="sheet"` fits the page. |
| Reconstruct SVG/PNG from saved records after a black screenshot | Existing fixed `get_screenshot(view_type="sch")` now captures the native schematic canvas. A duplicate preview renderer/tool is unnecessary for this operation. |

Backup copies and saved-record inspection remain file operations. Placing parts,
parameters, wires, labels, junctions and NoERC was already handled by
`edit_schematic`; the report's rejected malformed requests were validation
errors rather than missing wire/text-edit capabilities.

## Sheet properties and view

```python
set_schematic_sheet_properties(sheet_path, sheet_size="A3")
set_schematic_sheet_properties(sheet_path)  # Read current format.
zoom_schematic(sheet_path, mode="all")
get_screenshot(view_type="sch")
```

The exact target must be a saved SchDoc or an already-open named unsaved SchDoc.
Formats A0..A4 select standard sheets and disable `UseCustomSheet`. Changes use
document robot notifications in an undo group, set Modified and verify readback.
The first live attempt exposed why direct property assignment alone was
insufficient: it changed SheetStyle without setting Modified. No save is issued.

Zoom focuses the named document, dispatches native `SCH:Zoom` (`Action=All` or
`Action=Document`), and verifies Modified is preserved. It changes only the view.
Screenshots capture the current visible editor, including any floating panels;
the compiler's Messages panel was closed through the UI for the clean PNG.

The implementation follows Altium's
[document API](https://www.altium.com/documentation/altium-dxp-developer/isch-document-interface)
and [robot notification API](https://www.altium.com/documentation/altium-dxp-developer/schematic-api-design-objects-interfaces-reference).

## Exact project-only save

```python
save_project(project_path, dry_run=True)  # Preflight; writes nothing.
# Only after saving this project is authorized:
save_project(project_path)
```

Supports exact open local `.PrjPcb` / `.LibPkg` files. Source documents must
already exist on disk and have no unsaved changes. A real save first reads the
live member set, snapshots all source files including newly added members, then
rechecks that set before dispatch. It selects the exact project, invokes
`WorkspaceManager:SaveObject` for `FocusedProject`, restores the previous project
focus, and verifies source states/files and the saved membership. No Save All.
Uncertain completion must be inspected before retrying.

**The real Altium write branch was not exercised.** The user prohibited saving
anything in Altium. Live tests covered dry-run on the original and a disposable
project, native membership guards, and refusal with an unsaved source. Offline
mock-bridge tests cover persistence verification and unexpected source writes.
Those checks are not a claim of a successful real project save.

## Editor and coordinate corrections

New sheets can now be edited with UID operations before their first save:

```python
create_document(document_path=new_sheet)
set_schematic_sheet_properties(new_sheet, "A3")
clone_schematic_components(source_sheet, new_sheet, ["R38", "C55"])
edit_schematic(new_sheet, [{
    "op": "update", "type": "component", "target": {"designator": "R38"},
    "properties": {"designator": "R501", "design_item_id": "", "x": 1200, "y": 1000},
}], return_mode="changed")
```

Missing closed paths still fail without creating a sheet. Existing open buffers
are reused. `return_mode="full"` remains the default; `changed` returns surviving
objects named by operation results; `summary` omits snapshots and returns counts.
These compact modes apply only to UID add/update/delete operations. They reduce
client output; the native editor still builds its full verification snapshot.

Placed-pin fallback coordinates, UID editor pin snapshots and clone `hot_x/y`
were incorrectly returning internal units while the contract said mils. All now
convert through `CoordToMils`. Compiler and editor coordinates were compared for
all 79 draft pins and matched within 0.01 mil.

## Other report recommendations

- `get_schematic_connectivity(compiled=True)` already recompiles in memory and
  exposes `complete`, missing pins and compiler diagnostics. It does not promise
  that a never-saved newly attached sheet is ready in Altium's compiler; no
  automatic save/close/reload workaround was added.
- Action examples now explicitly show `op` and paired x/y fields in
  [EDIT_SCHEMATIC.md](EDIT_SCHEMATIC.md). Compact UID replies were added here;
  a separate designator-filtered pin/net export was not required by the scripts.
- A composite create/save/add workflow would write files. The existing lifecycle
  commands plus the new sheet/project operations remain explicit steps.
- `connect_pins` with obstacle/text-aware routing is a separate design feature;
  none of the extra scripts implemented that routing algorithm.
- Electrical hold-up/discharge calculations, MPN selection and integration into
  Supply/SoM are design recommendations in the report, not this automation task.

## Live evidence

Altium Designer Enterprise 24.4.1, open SALT-FMUV6X-JETSON project:

- Native compilation returned 79/79 pins, complete coverage and matching editor
  coordinates. All 23 expected groups from the report's saved readback passed.
- New named unsaved sheet: read/edit, A3, repeated idempotent A3, R38/C55 clone,
  rename/detach, compact replies and both zoom modes passed.
- Project save dry-runs passed; an unsaved source and changed expected membership
  were rejected before any save. A minimal disposable project avoids unrelated
  output-file references in the fixture.
- Original draft object snapshot, complete project state and 20 original file
  hashes/mtimes were unchanged. Scratch sheets/project buffers were discarded,
  scratch-copy files were unchanged, and the new unsaved sheet was never written.
- Existing compiler errors remain 40, with zero fatals; pin coverage and expected
  net checks do not establish clean ERC or validate the sequencer's timing.

Evidence: `.altium-mcp/schematic_document_20261106_000606/`, including
`report.json`, `compiled_connectivity.json`, `expected_connections_check.json`,
`minimal_project_preflight.json` and `schematic_clean.png`.

After the user's Altium restart, a fresh smoke check passed for sheet-property
readback, project save preflight and schematic zoom. Project/document states
were preserved and no save was issued. Evidence: `post_restart_smoke.json` in
the same directory.

Offline regression: 134 tests, 133 passed and one skipped because the current
Windows ANSI code page is not 1252. Includes request schemas, compact replies,
model-only source file protection, save verification/failure cases, both editor
formats, connectivity, cloning, document lifecycle, library operations and full
native bundle ordering. No real Altium save is performed by these tests.

Reproduce with the MCP environment's Python:

```powershell
python dev/test_schematic_document_live.py --exercise `
  --project C:\project\Board.PrjPcb `
  --sheet C:\project\Power-Sequencer-Draft.SchDoc `
  --source C:\project\Supply.SchDoc
```

The harness expects the report's 79-pin draft and R38/C55 prototypes. It calls
production handlers from a fresh Python process, without starting another MCP
listener. Restart an already-running MCP connection to load the new tool catalog
and Python options. Native bundles are content-versioned. No custom
`run_altium_script` calls were needed in this task.
