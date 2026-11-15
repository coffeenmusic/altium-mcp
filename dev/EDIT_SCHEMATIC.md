# edit_schematic(sheet_path, operations, unit="mil", save=False, return_mode="full")

The UID operation format below adds, updates and deletes objects in memory.
It always uses mils and requires `save=False`; it never clears Modified. A sheet may retain its `*` marker
after test objects have been removed.

`build_schematic` creates a new sheet. Use `edit_schematic` with an exact path
to continue working on an existing sheet.

## Upstream action format

The same tool also accepts `move_component`, `delete_component`,
`place_component`, `set_parameter`, `move_text`, `add_wire`, `add_net_label`,
`add_power_port`, `add_junction`, `add_no_erc` and `delete_at` operations.
These accept `unit="mil"` or `unit="mm"` and optional `save=True`, preserving
upstream's interface for applying `check_schematic` suggestions. For example:

```python
edit_schematic(
    sheet_path=r"C:\Projects\Board\Display.SchDoc",
    operations=[{"op": "move_component", "designator": "R1", "x": 20, "y": 30}],
    unit="mm",
    save=False,
)
```

Each action includes `op`; x/y are always a pair. For example:

```python
edit_schematic(sheet_path, [
    {"op": "add_wire", "points": [[1000, 1000], [1500, 1000]]},
    {"op": "move_text", "designator": "R1", "name": "Comment", "x": 1200, "y": 900},
], unit="mil", save=False)
```

Action operations run in order in one undo group; inspect `errors` for partial
failures. UID operations preflight all targets and report `applied_count`,
`failed_index` and `may_have_partial_changes`. Do not mix formats in a batch.
An empty list continues to read objects and UIDs. Use `save_document` to save
after UID edits. Both formats isolate request files and retain uncertain inputs
after a bridge timeout; inspect the document before retrying.

## Reading and targeting

```python
edit_schematic(
    sheet_path=r"C:\Projects\Board\Display.SchDoc",
    operations=[]
)
```

The path must be absolute and identify a saved `.SchDoc` or an already-open named
unsaved SchDoc created by `create_document`. A missing closed target is rejected
without creation. A saved closed sheet is opened; an already open sheet is reused.
Focus on
another sheet/library does not change the target. Libraries and other file
types are rejected.

`objects` contains supported top-level objects with `type`, `unique_id`, `x`,
`y` and type-specific properties. Components include `designator`, `symbol`,
`comment`, `description`, `design_item_id`, `orientation`, `mirror`, `parameters` and `pins`.
Pin records contain numbers, names and absolute **electrical connection
points**, not their body-end coordinates. All pin coordinates are in mils,
including editor fallback and clone hot-end coordinates. Wires include their full `points` array.

UID operations accept `return_mode="summary"` (status, results and object counts)
or `"changed"` (only surviving objects affected by this call). Default `"full"`
preserves the complete response. Native verification snapshots are still built;
compact modes reduce client output. Action-format calls require `"full"`.
Use `design_item_id=""` to explicitly detach a copied prototype's DesignItemID.

Use `target={"unique_id": "..."}` from a fresh response for that sheet.
Components also accept `target={"designator": "R1"}`. A selector must have
exactly one key and match exactly one object of the specified type. For
ambiguous designators, including multipart components, use the instance UID.

## Operations

```python
{"op": "add", "type": "wire", "properties": {"points": [1000, 1000, 1500, 1000]}}
{"op": "update", "type": "component", "target": {"designator": "R1"},
 "properties": {"comment": "10k"}}
{"op": "delete", "type": "net_label", "target": {"unique_id": "ABCDEFGH"}}
```

- `op`: `add`, `update` or `delete`.
- `type`: required for every operation; see the table.
- `target`: required only for `update`/`delete`; omit for `add`.
- `properties`: required fields for `add`, a nonempty subset for `update`,
  omitted or empty for `delete`.
- Coordinates and sizes are **mils**, including fractions within +/-200000 mil.
  Supply `x` and `y` together.
- Orientation is an Altium API integer 0-3, not degrees.
- Unknown keys, NaN, infinity, invalid types and NUL characters are rejected.
- At most 500 operations per call, with one edit per existing target per batch,
  even when different selectors identify it.

| type | properties | Required for add |
| --- | --- | --- |
| `component` | `x`, `y`, `designator`, `comment`, `description`, `design_item_id`, `orientation`, `mirror` (bool), `parameters` (string dictionary) | `x`, `y`, `designator`, symbol source |
| `wire` | `points`: flat `[x1,y1,x2,y2,...]` array, 2-1000 points | `points` |
| `net_label` | `x`, `y`, `text`, `orientation` | `x`, `y`, `text` |
| `port` | `x`, `y`, `name`, `io_type` (0-3), `style` (0-7), `width` > 0 | `x`, `y`, `name` |
| `power_port` | `x`, `y`, `text`, `orientation`, `style` (0-10), `show_net_name` (bool) | `x`, `y`, `text` |
| `junction` | `x`, `y` | `x`, `y` |
| `text` | `x`, `y`, `text`, `orientation` | `x`, `y`, `text` |

Updates change only supplied properties. `wire.points` replaces every vertex,
including when shortening the list. Component parameters are added/updated
case-insensitively; other parameters remain. Use dedicated `comment` and
`designator` properties for those fields. Parameter deletion and symbol
replacement are not supported.

Choose exactly one component source:

```python
# Clone a placed instance with a new UID and designator.
{"op": "add", "type": "component", "properties": {
    "source_designator": "R1", "designator": "R200",
    "x": 8000, "y": 4000, "comment": "47k", "orientation": 0
}}

# Place from an existing SchLib; IntLib is unsupported.
{"op": "add", "type": "component", "properties": {
    "symbol_library": r"C:\Libraries\Passives.SchLib", "symbol": "Resistor",
    "designator": "R201", "x": 8500, "y": 4000
}}
```

`source_designator`, `symbol_library` and `symbol` are add-only. Sources are
resolved before editing; components added in a batch become available as sources
in the next call. Existing or already reserved designators cannot be reused for
addition/renaming. Adding another part with the same designator and swapping
designators in one batch are unsupported.

New components receive fresh UIDs and source geometry, pins and metadata.
Source orientation/mirroring is retained unless specified. Libraries are read
only; the held target sheet remains the edit destination even if opening a
SchLib changes the active editor.

## Results and failures

A successful response contains `success=true`, `sheet_path`, `saved=false`,
`was_modified`, `applied_count`, `results` and `objects`. Each operation result
contains zero-based `index`, `op`, `type` and `unique_id`. Deletion returns the
removed object's UID.

Python validates argument shape/types before dispatch. DelphiScript resolves
all targets, symbol sources and designator conflicts before the first edit.
Preflight failures return `applied_count=0`, `failed_index` and
`may_have_partial_changes=false`. An unfinished editor transaction blocks edits.

Edits use `PreProcess`/`PostProcess` and undo notifications. API failures report
completed operations, `failed_index` and `may_have_partial_changes=true`.
There is no atomic rollback guarantee: the failing operation may also have
partially applied. Bridge timeouts report uncertain state and the input spec
path. Do not blindly retry; recover the executor, read the sheet and inspect
which UIDs exist. Uncertain requests retain their spec for diagnosis/late
execution; completed bridge responses remove the unique input spec.

## Electrical and display behavior

Moving a component does not reroute wires. Read measured `pins` first, then
route from their electrical connection points. Labels must touch their wires;
junctions and ports are explicit. Full ERC/project compilation is not automatic.
See [schematic conventions](SCHEMATIC_CONVENTIONS.md).

`port.io_type`: 0 unspecified, 1 output, 2 input, 3 bidirectional. Derive port
and power-port styles from existing objects. Altium may expand a port's width
on redraw if its name does not fit. `show_net_name=false` does not guarantee
that Altium hides a GND label.

Deletion uses `RemoveSchObject` followed by `SCHM_PrimitiveRegistration` to
retain the object for undo. `UnRegisterSchObjectFromContainer` frees it
immediately and is not used. See the official
[Schematic API](https://www.altium.com/documentation/altium-dxp-developer/schematic-api-design-objects-interfaces-reference)
and [ISch_Document](https://www.altium.com/documentation/altium-dxp-developer/isch-document-interface).

## Verification

```powershell
python -m unittest server.tests.test_schematic_editor server.tests.test_sandbox server.tests.test_altium_files server.tests.test_outjob_parser -v
python dev/test_edit_schematic_live.py --sheet 'C:\Projects\Board\Display.SchDoc'
python dev/test_edit_schematic_live.py --sheet 'C:\Projects\Board\Display.SchDoc' --exercise --library 'C:\Projects\Board\Symbols.SchLib'
```

Use the MCP Python environment with dependencies installed and the target
project open in Altium. Without `--exercise`, the runner only reads.
`--exercise` adds temporary objects away from the circuit, updates them, checks
batch rejection for an invalid target, then removes test objects by returned
UIDs. Original objects are not edited. Before/after object snapshots and SHA-256
hashes of all project `.SchDoc` files are compared.

Snapshots, script copies and reports stay under the toolkit's
`.altium-mcp/schematic_edit_<timestamp>` directory. A fresh PrjScr copy avoids
cached code during development. User settings and project files are not rewritten.

Restart the MCP connection to refresh its tool catalog. If Altium retains old
script buffers, reopen `Altium_API.PrjScr` without saving stale buffers over
updated files. See the [live test report](EDIT_SCHEMATIC_LIVE_TEST.md).
