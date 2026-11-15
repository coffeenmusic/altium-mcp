# Placed PCB component properties

```python
set_pcb_component_properties(
    pcb_path, designators, component_kind=None, graphical=None,
    include_in_bom=None, include_in_pick_place=None,
)
```

Reads/sets native `IPCB_Component.ComponentKind` on existing placed components
in one exact `.PcbDoc` buffer. This operation does not edit the PcbLib footprint,
SchLib symbol, schematic, variants or output settings, and never saves anything.
No custom Graphical/BOM/Pick & Place parameter is created.

## M08 example

```python
set_pcb_component_properties(
    pcb_path=r"C:\project\PCB.PcbDoc", designators=["FID1", "FID2", "FID3"],
    graphical=True, include_in_bom=False, include_in_pick_place=False,
)
```

The fiducials must already be placed and have those exact designators. The tool
changes their native type to Graphical. It does not create or place components.
The existing `place_components` command only moves already-placed components.

## Component type and report eligibility

The script API exposes ComponentKind rather than independent BOM/Pick & Place
switches. Returned inclusion values describe **component-type eligibility**,
not an independently read checkbox or an actual exported file.

| `component_kind` | Graphical | BOM eligibility | Pick & Place eligibility |
|---|---|---|---|
| `standard` | false | true | true |
| `mechanical` | false | true | false |
| `graphical` | true | false | false |
| `net_tie_bom` | false | true | false |
| `net_tie_no_bom` | false | false | false |
| `standard_no_bom` | false | false | null: depends on output settings |

Altium's [component-type reference](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-design-objects-interfaces-reference)
describes the native type. The [Pick & Place documentation](https://www.altium.com/documentation/altium-designer/preparing-for-manufacture/output-jobs/assembly-data)
describes standard-type inclusion and the separate **Include Standard (No BOM)
Items** output option. This tool neither reads nor changes that output option.
`standard_no_bom` therefore reports
`include_in_pick_place=null` and `pick_place_policy="depends_on_output_settings"`.
Requesting a definite true/false value for that conditional policy is rejected.
Variants, filters and the chosen BOM source can further affect generated files;
the tool does not modify a schematic-sourced BOM.

`inclusion_source="component_kind_policy"` makes this distinction explicit.
Unknown future native kinds have unknown inclusion values.

## Selection and resolution

`pcb_path` must be an absolute saved or already-open named unsaved PcbDoc path.
An open dirty buffer is reused without reload; a missing closed target fails.
PcbLib/SchDoc/SchLib paths are rejected before dispatch. The target becomes the
active editor; project context is not inferred from another open board.

`designators` is a list of 1..1000 exact nonempty names, case-insensitively unique,
without wildcards. Each must match exactly one component. All targets and their
requested kinds are preflighted before any component changes. Duplicate board
designators and missing later targets reject the whole batch.

Omit edits to read current properties. `component_kind` accepts the six names
above or UI labels such as `Standard (No BOM)` and `Net Tie (In BOM)`.
`graphical=True` selects Graphical. `graphical=False` resets a Graphical component
to Standard; other matching current kinds remain unchanged.

The inclusion booleans are constraints on the chosen native type. A current kind
that satisfies them is preserved. Otherwise, the tool requires a unique choice
among Standard, Mechanical, Graphical and Standard (No BOM); ambiguous or
contradictory requests fail. Net-tie types are never introduced automatically.
Specify `component_kind` when the intended non-Graphical semantics matter.
Omitted inclusion fields follow the chosen kind, since they are coupled rather
than independent properties. A type change can therefore affect more than one
eligibility value. Component placement locks remain unchanged.

## Verification and failures

One native undo group surrounds actual changes. Read/no-op requests preserve
Modified. The result includes ordered `before`/`after`, `updated_count`, `changed`,
Modified flags and `saved=false`. Native snapshots verify kind, identity, pose,
pattern, source links, locks, child addresses/bounds, pad geometry/drill/mask
fields and exposed pad/track/arc nets. Region vertices, complete 3D payloads and
other unexposed child attributes are not independently compared.
Snapshot positions, bounds and dimensions use raw Altium coordinates (10,000
units per mil); rotations are degrees. This tool has no geometry-edit arguments.

The saved PCB hash/mtime must remain unchanged. Neither the BOM nor Pick & Place
output is generated or independently checked. API/readback errors can leave
partial in-memory edits; inspect `may_have_partial_changes`. On
`outcome_unknown=true`, inspect Altium and the retained request before any retry.
No automatic rollback or save is performed.

## Tests

```powershell
python -m unittest server.tests.test_pcb_component_properties server.tests.test_script_bundle
python dev/test_pcb_component_properties_live.py --exercise --pcb C:\project\PCB.PcbDoc
```

The opt-in harness creates three temporary components/pads and a test net only
in a disposable board copy. Fixtures are not loaded from or written into the
production M08 footprint. It tests locked placement, Graphical/eligibility,
all six kinds, refusals, idempotence, source/net/geometry preservation, then
discards the copy and checks original context and disk hashes. Custom fixture
creation is required because this catalog has no tool that places new PCB
components from a library.

### 2026-11-12 validation

298 regression tests: 297 passed, one skipped for the host ANSI code page.
Evidence: `.altium-mcp/pcb_component_properties_regression.log`.

The final live run passed 24 steps in the open SQ-CARD session:
`.altium-mcp/pcb_component_properties_20261112_124029/report.json`. All six native
kinds, Graphical selection/clearing, inclusion constraints, batch refusals,
no-ops, placement locks, child geometry, source links and an assigned pad net were
checked. All 12 guarded source/scratch files retained their hashes/mtimes; original
project/document states were restored and the scratch board was closed with
explicit discard. No document was saved and no output report was generated.

In this AD24 scripting session `IPCB_Board.IsLibrary` returned true even for a
fresh unsaved board created by `create_document`. Exact targeting therefore
checks `IServerDocument.Kind == PCB`, uniqueness of the open buffer and matching
board filename. PcbLib targets are rejected before dispatch and by native document
kind. An initial fixture guard exited before result writing and timed out; the
sandbox confirmed executor recovery before cleanup. The final fixture script
explicitly focuses its target and reports refusals without an early exit.

| Custom script operation | Reason | Permanent operation / outcome |
|---|---|---|
| Inspect PCB kind/flag API and exact document identity | No dedicated property tool existed; independent BOM/Pick & Place getters were unavailable | Implemented `set_pcb_component_properties(pcb_path, designators, ...)`; native kind changes and readback passed |
| Create disposable component/pad/net fixtures | The board copy was empty and `place_components` only moves existing components | Tested three fixtures and discarded the copy; a future `place_pcb_components(pcb_path, components)` could cover production placement, but is not implemented here |

The new Python tool was exercised through fresh production imports without
starting another MCP listener. Reconnect/restart the existing MCP connection to
load its new signature.
