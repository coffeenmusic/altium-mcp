# Library script replacements

For existing STEP body position, pose, embedding, replacement and deletion, see
[`edit_footprint_model3d`](FOOTPRINT_MODEL3D.md). Batch creation still adds bodies.
For STEP full-path/filename matching, per-constant availability and shared layer
aliases, see [PCB readback fixes](PCB_READBACK_FIXES.md).

Analysis of the supplied `1-operation_report.md` (TPS3808G01DBVR, 2026-11-03).
The report describes historical results. Subsequent live validation and fixes are
recorded in [FOOTPRINT_EDITOR_LIVE_TEST.md](FOOTPRINT_EDITOR_LIVE_TEST.md).

| Additional script operation | MCP replacement / finding |
| --- | --- |
| Read PCB shape and cache enums | New `get_pcb_api_constants(names)`: a small explicit whitelist, evaluated by Altium rather than assumed numeric values. |
| Repair slash/backslash library aliases | Existing document tools and `OpenLibraryDocument` already compare normalized paths. Library opening now refuses multiple matching buffers; it never merges, saves or discards dirty aliases. Close reviewed aliases explicitly with `close_document`. |
| Show and position VALUE / PKG_STYLE; hide PLACE / Comment | Already supported by `create_symbols_batch` with `EDITSYMBOL`, `SYMPARAM` and `COMMENT`. Read existing values first. A duplicate `style_library_component` tool is unnecessary. |
| Move nine existing footprint lines to Mechanical 13 / 15 | New `edit_footprint_primitives`: layer updates of existing tracks, arcs and fills selected by their geometry. |
| Set six existing pads to manual mask +0.05 mm / paste 0 mm | New `set_footprint_pad_settings`. The batch creator already supports these options for new pads; the missing capability was editing existing pads. |
| Save exactly the two libraries using `SaveDoc` | Existing `save_document(document_path)` targets one document. It was absent from the older connected catalog, not the current source. No save operation is used by the new editors or live harness. |
| Read symbol formatting, footprint origin and pad cache | Existing symbol getter already reports parameter visibility, position, justification, autoposition and font. Comment now also reports those properties. Footprint getter now reports library origin and nested pad cache settings. |
| Capture a native library view | Existing `get_screenshot` now accepts `schlib` / `pcblib` as well as `sch` / `pcb`. Both library modes passed live image inspection after the compositor capture fix. Focus the exact library with `open_document` first. |

PowerShell/Python backup, spec-file creation, datasheet download/PDF rendering and
independent geometry previews remain ordinary file/PDF operations. They do not
require Altium MCP commands. A reconstructed preview is not an Altium screenshot.
The report's referenced scripts were not attached, so this analysis covers the
operations described in the report rather than a line-by-line audit of those files.

## Existing symbol styling

Pass a UTF-8 BOM spec file to `create_symbols_batch`, for example:

```text
LIBRARY|C:\project\Parts.SchLib
EDITSYMBOL|TPS3808G01DBVR
COMMENT|=VALUE|0
SYMPARAM|VALUE|TPS3808G01DBVR|1|-200|-400
SYMPARAM|PKG_STYLE|SOT-23-6|1|-200|-500
SYMPARAM|PLACE|<existing value>|0
```

Replace the example values/coordinates with actual readback and desired layout.
`SYMPARAM` upserts values as well as formatting. Coordinates are mils; specifying
them disables autoposition. The library remains modified and unsaved.

## Existing pad settings

```python
set_footprint_pad_settings(
    library_path=r"C:\project\Parts.PcbLib",
    footprint_name="TI_DBV0006A_SOT23_6",
    pad_names=["1", "2", "3", "4", "5", "6"],
    mask_expansion_mm=0.05,
    paste_expansion_mm=0.0,
)
```

- `mode="manual"` requires at least one expansion. An omitted expansion is preserved.
- `mode="rule"` with no expansion arguments resets both settings to rules.
- Names must each identify exactly one existing pad; repeated pad numbers are
  ambiguous and rejected. All requested names are checked before editing.
- Uses `TPadCache`, avoiding the unsupported direct expansion setters.
- Returns before/after settings, cache validity and `verified_count`. Native
  readback checks both expansion modes and manual values. Expansions are in mm;
  geometry and locks are preserved. Mode 0 / no mask is unsupported.

## Existing primitive layers

Read the footprint with `get_footprint_primitives`, then copy the target geometry:

```python
edit_footprint_primitives(
    library_path=r"C:\project\Parts.PcbLib",
    footprint_name="TI_DBV0006A_SOT23_6",
    operations=[{
        "target": {"type": "track", "layer": "No Layer",
                   "x1": -80, "y1": -70, "x2": 80, "y2": -70, "width": 5},
        "layer": "Mechanical 15",
    }],
)
```

Targets require all of these geometry fields plus `type` and current `layer`:

| Type | Geometry fields |
| --- | --- |
| `track` | `x1`, `y1`, `x2`, `y2`, `width` |
| `arc` | `cx`, `cy`, `radius`, `start_angle`, `end_angle`, `width` |
| `fill` | `x1`, `y1`, `x2`, `y2`, `rotation` |

Coordinates/widths are mils relative to the footprint origin, angles are degrees.
Every selector must match one object. Missing, ambiguous or overlapping selectors
reject the batch before edits. Object addresses are retained only during the call
so an earlier layer update cannot retarget a later selector. Geometry comparison
tolerance is 0.0001 mil / degree to accommodate getter rounding.

This tool changes layers only. It does not add/delete primitives, modify text,
change geometry or edit pads. Mechanical layers 1..16 are supported, including
`Mechanical Layer 13` / `Mechanical13` / `eMechanical13` aliases. `No Layer` is
accepted only as the current selector layer, never as a destination.

## Creation validation and readback

Batch creation now accepts explicit mm/mil input, native Height and new-pad
testpoint flags. See [creation schema and verification](FOOTPRINT_CREATION.md).

`create_footprints_batch` normalizes mechanical aliases in a private spec copy;
it does not rewrite the caller's file. Altium preflights all layer-bearing records
before creating any object. Unknown layers and `No Layer` destinations reject
the whole batch. Mechanical layers 1..16 have explicit enum fallbacks; higher
layer names still depend on Altium's resolver. `EDITFOOTPRINT` continues to add
3D bodies only; use the new editors for existing graphics or pads.

`get_footprint_primitives` preserves its existing mil-valued fields and adds:

- `library_origin_x` / `library_origin_y`: library board origin in mils. Primitive
  coordinates remain relative to each footprint origin.
- Each pad's `pad_settings`: `name`, modes, expansions in mm,
  `mask_cache_valid` and `paste_cache_valid` native enum values. If supplementary
  cache access fails, `cache_readable=false` is reported instead.

The editors require an exact existing `.PcbLib` and an unambiguous footprint.
They focus that library and footprint. They never save, reload an already-open
buffer or modify project membership. A mid-edit API error can leave partial
changes. Unknown completion retains the unique request spec and reports
`outcome_unknown` / `may_have_partial_changes`; inspect before retrying.

## Verification

Offline tests cover request validation, units, mechanical aliases, UTF-8 transport,
MCP wrapper dispatch, wrong-document screenshot refusal, completed native errors,
readback mismatches, retained timeout requests and full script-bundle ordering.
These tests do not compile DelphiScript inside Altium.

2026-11-05 regression result: 86 tests, 85 passed and one skipped because the
machine does not use ANSI code page 1252. Included footprint editor, script
bundle, library metadata, symbol metadata, document management/closing,
schematic cloning, server identity, screenshot capture and Altium text-file tests.

Opt-in live harness, using the MCP environment's Python:

```powershell
python dev/test_footprint_editor_live.py --exercise `
  --project C:\project\Project.PrjPcb --library C:\project\Parts.PcbLib
```

The source library must already be open in the specified open project. The harness
copies its saved file into a unique `.altium-mcp` directory and edits only that
copy in memory. It checks enums, layer edits, pad overrides/reset, batch refusals,
original buffer snapshots/states and original/scratch disk hashes/mtimes. It
closes its scratch buffer with explicit discard. It invokes no save command.
If executor completion becomes unknown, it sends no cleanup commands and retains
recovery artifacts. Window responsiveness is checked before any Altium command.

2026-11-05 live attempt: **blocked before dispatch**. Altium PID 21532 remained
unresponsive in the earlier multipart clone request; no native compilation or
execution of these additions was possible. Evidence:
`.altium-mcp/footprint_editor_20261105_230035/report.json` (`steps=[]`,
`documents_saved=false`). New library screenshots are also unverified.
After the user's machine restart, the complete footprint harness passed in
`.altium-mcp/footprint_editor_20261105_232835/`. Symbol styling, Unicode transport
and both library screenshots also passed. See the
[live verification record](FOOTPRINT_EDITOR_LIVE_TEST.md) for fixes and evidence.
No Altium document was saved or forcibly closed/restarted by the agent.

After Altium is responsive, restart the MCP connection to load the changed
Python tool catalog. `get_server_status` reports the loaded source fingerprint;
changing files alone does not update an already-running MCP process. Run the
live harness when changing the native implementations.
