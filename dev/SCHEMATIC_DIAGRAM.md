# Sheet hierarchy and block diagrams

Two tools cover different Altium objects:

| Tool | Native objects | Purpose |
| --- | --- | --- |
| `edit_sheet_symbols(sheet_path, operations)` | Sheet Symbol, Sheet Entry | Electrical hierarchy referencing child sheets |
| `draw_block_diagram(sheet_path, blocks, links, notes)` | Rectangle, Polyline, Label | Graphical architecture diagrams |

Existing `edit_schematic` covers components, wires, ports and labels, but cannot
create these hierarchy objects or free graphical links. `check_schematic` can
inspect existing sheet-symbol layout in saved files; it cannot author it.
SchLib symbol graphics and PCB silkscreen graphics target different editors.

Both tools require an absolute `.SchDoc` path. They reuse an open buffer,
including a named unsaved sheet, or open an existing saved sheet. Coordinates
are **mils**, y increases upward, and public x/y denotes the lower-left corner
of a symbol or block. Neither tool saves, compiles, creates child documents or
changes project membership. Mutations mark the target Modified and use one
PreProcess/PostProcess undo group. Saving requires a separate authorized call.

## Native Sheet Symbols and Sheet Entries

Create a symbol:

```json
{
  "sheet_path": "C:\\design\\Top.SchDoc",
  "operations": [{
    "op": "add",
    "properties": {
      "designator": "Control",
      "filename": "Control.SchDoc",
      "x": 1000, "y": 1400, "width": 1200, "height": 900,
      "entries": [
        {"name": "EN", "side": "left", "offset": 200, "io_type": "input"},
        {"name": "READY", "side": "right", "offset": 300, "io_type": "output"},
        {"name": "BUS", "side": "top", "offset": 400, "io_type": "bidirectional"},
        {"name": "GND", "side": "bottom", "offset": 500}
      ]
    }
  }]
}
```

- `operations=[]` reads symbols and entries, with UIDs and actual positions.
- `add` requires designator, filename, x/y and positive width/height; entries
  default to empty. Filename references one `.SchDoc`, relative or absolute.
- `update` requires `unique_id` and nonempty properties. Supply x/y together.
  Omitted properties remain unchanged. Geometry changes reposition entries
  and the symbol's name/filename text.
- Omit `entries` to preserve entries and their UIDs. Supply the **complete**
  desired list to replace entries; replacement creates fresh entry UIDs.
  `entries=[]` removes all entries. The symbol UID is preserved.
- `delete` requires `unique_id` and omits properties. It removes the symbol
  together with its entries.
- Each entry requires name, side and positive offset, strictly within the edge.
  Left/right offsets run downward from the top; top/bottom offsets run rightward
  from the left. `io_type` defaults to `unspecified`; other values are `input`,
  `output`, `bidirectional`. Entry names are case-insensitively distinct per
  symbol; two entries cannot share a side/offset pair.
- At most 100 operations and 100 entries per symbol. Only one operation per
  existing symbol in a batch. Designator collisions and invalid resulting entry
  bounds are rejected before mutation, including a resize that strands entries.

For example, append an entry by reading the current list, adding the entry in
the caller and sending that whole list in an update. Use returned fresh UIDs
for later operations. Matching child Ports and parent wires are authored with
`edit_schematic`; creating a Sheet Entry alone does not connect a child Port.

Native API notes: Altium's Sheet Symbol origin is top-left, so the bridge
normalizes it to the public lower-left convention. Entry side/offset and
ownership alone do not establish its rendered position: the bridge also places
each entry explicitly and checks actual `Location` against the expected edge.
See Altium's [object interfaces](https://www.altium.com/documentation/altium-dxp-developer/schematic-api-design-objects-interfaces-reference)
and [enum reference](https://www.altium.com/documentation/altium-dxp-developer/schematic-api-types-reference).

## Graphical diagrams

```json
{
  "sheet_path": "C:\\design\\Architecture.SchDoc",
  "blocks": [
    {"id": "power", "text": "Power input", "x": 1000, "y": 3400, "width": 1200, "height": 600},
    {"id": "control", "text": "Sequencer", "x": 3200, "y": 3400, "width": 1200, "height": 600},
    {"id": "load", "text": "Payload", "x": 5400, "y": 3400, "width": 1200, "height": 600}
  ],
  "links": [
    {"from": "power", "to": "control", "text": "VIN"},
    {"from": "control", "to": "load", "text": "ENABLE"},
    {"from": "load", "to": "control", "from_side": "bottom", "to_side": "bottom",
     "text": "READY", "points": [[6000,3400],[6000,3000],[3800,3000],[3800,3400]]}
  ],
  "notes": [{"x": 1000, "y": 4400, "text": "Power sequencing architecture"}]
}
```

- All three lists are required and may be empty. Limits: 100 blocks, 200 links,
  100 notes and 1000 resulting primitives. Block IDs are case-sensitive and
  unique within this request; block interiors must not overlap.
- Links require distinct existing `from`/`to` block IDs. Sides are left/right/
  top/bottom; defaults choose facing sides. Anchors are side centers.
- Default routes are orthogonal, with up to two bends. This is a simple layout
  helper, not a general obstacle router. A route crossing a block interior is
  rejected. Supply `points` for a detour: the full orthogonal route, including
  both anchors, with 2..100 coordinate pairs. Duplicate consecutive points are
  collapsed. Links crossing other links are allowed and have no electrical
  junctions. `arrow` defaults to true; false omits its graphical arrowhead.
- Blocks have outline rectangles and centered text. Link labels are placed
  above the longest segment; notes use their explicit location. Text uses Arial
  10 pt. There is no automatic text wrapping or text collision solver.
- Results include routes and `{index, role, type, unique_id}` for each created
  primitive. Roles identify block rectangles/text, links, arrowheads, labels and
  notes. Mutation replies include only newly created objects after verification.
- Repeated calls **append** objects. They do not update/replace a prior diagram.
  Use Altium Undo or normal editor selection to remove graphical objects.

These links are `ePolyline`, never `eWire`. They do not form nets or hierarchy.
To inspect the result visually, call `zoom_schematic(sheet_path)` followed by
`get_screenshot(view_type="sch")`. Saved-file `check_schematic` cannot inspect
unsaved changes.

## Results and failures

Successful replies include `saved:false`, `was_modified`, `is_modified`,
`applied_count`, results, objects and `verification:{passed,errors}`. Readback
checks target identity, dimensions/text, full polyline vertices, entry properties,
actual entry coordinates, ownership, Modified and an unchanged target disk file.
Fractional coordinates use locale-safe parsing.

Request validation and native target/name/bounds preflight run before editing.
Native API errors may leave partial changes: inspect `failed_index`,
`applied_count` and `may_have_partial_changes`. An incomplete bridge reply returns
`outcome_unknown:true` and retains its unique request spec for investigation.
Do not blindly retry a mutation with an unknown outcome. Completed native
refusals are distinguished from transport timeouts and clean up their specs.

## Verification

Local regression suite: 141 tests, 140 passed and one platform-specific skip.
Tests cover malformed input, Unicode transport, UID rules, routing/obstacles,
readback ownership/coordinate failures, partial/unknown outcomes and script
bundle integration.

Opt-in live harness:

```powershell
python dev/test_schematic_diagram_live.py --project C:\design\Board.PrjPcb --exercise
```

The harness selects an already-open project, snapshots its buffer states and
file hashes/timestamps, and edits a separate named unsaved sheet in Free
Documents. It never adds the scratch sheet to that project. It exercises all
four entry sides and I/O types, Unicode names/path, fractional coordinates,
symbol move/resize/rename, entry replacement/clearing and symbol deletion.
It also checks native preflight refusal, a three-block diagram with three
arrowed links including a detour, notes, absence of electrical wires and native
screenshot output. The scratch sheet is closed with discard; original project
states and files must match the baseline. Artifacts stay in ignored
`.altium-mcp/schematic_diagram_<timestamp>/`.

Passed on 2026-11-07 in Altium Designer Enterprise 24.4.1, with the already-open
`AM_NODE_V2.PrjPcb` as the test context. Run `schematic_diagram_20261107_211124`
completed 26 steps, created 17 graphical primitives and preserved all 15 guarded
files (SHA-256 and timestamps), project membership and document states.
Its `report.json` confirms `checks_passed`, `original_states_unchanged`,
`original_disk_unchanged`, `unsaved_sheet_not_written` and `scratch_closed`.
The native `diagram.png` was visually inspected: entries sit on the symbol's
four edges and diagram arrows/labels render correctly.

Live validation uses the production Python handlers in a fresh process without
starting another MCP listener. Restart the connected MCP process to expose the
new tool names in its catalog. Altium itself does not need restarting.
The live check does not compile a parent/child hierarchy or exercise file saving.
