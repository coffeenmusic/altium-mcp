# update_symbol_primitives

```python
update_symbol_primitives(library_path, symbol_name, edits)
```

Edit existing SchLib labels, graphics and pins in place. The earlier
`create_symbols_batch` / `EDITSYMBOL` path changes parameters, Comment,
description and selected pin metadata, but leaves existing free graphics alone.
`get_symbol_primitives` reads geometry; creation tools build new objects.
This tool fills the gap without recreating a symbol or replacing its objects.

## Targets and scope

- `library_path`: absolute path to an existing `.SchLib`. The exact open buffer
  is reused, including unsaved edits; a closed library is opened.
- `symbol_name`: exact, case-insensitive LibReference matching one symbol.
- `edits`: up to 500 `{target, properties}` objects. `[]` reads editable objects
  with their current properties, `unique_id`, `owner_part_id` and `display_mode`.
- `target.type` is required. Other selectors are `unique_id`, `owner_part_id`,
  `display_mode` and any current property from the table below. Pins also accept
  `pin_number` as a selector. All selectors are combined with AND; strings match
  exactly except font names. Each target must resolve to exactly one object.
- Prefer a nonempty UID from a fresh read. Legacy objects may have an empty UID;
  use part/display and current text, coordinates or pin number instead. A bare
  type is sufficient only when that type occurs once in the symbol.
- `properties` must contain at least one supported field. Omitted fields remain
  unchanged. One object can be targeted only once per request.

Every selector is resolved before mutation. Missing or ambiguous matches, or
duplicate targets, reject the entire batch without editing earlier targets.
An API failure during application can leave partial changes; there is no
automatic rollback or retry. Updates use one undo group. Identical values are
no-ops and preserve Modified, including on an already dirty library.

## Editable properties

| Type | Properties |
|---|---|
| `label` | `text`, `x`, `y`, `orientation`, `justification`, `color`, `font_name`, `font_size`, `font_bold`, `font_italic`, `font_underline`, `font_strikeout` |
| `line` | `x1`, `y1`, `x2`, `y2`, `line_width`, `color` |
| `rectangle` | Line fields plus `area_color`, `is_solid` |
| `polyline` | `vertices: [{x, y}, ...]`, `line_width`, `color` |
| `polygon` | Polyline fields plus `area_color`, `is_solid` |
| `arc` | `cx`, `cy`, `radius`, `start_angle`, `end_angle`, `line_width`, `color` |
| `ellipse` | `cx`, `cy`, `radius`, `secondary_radius`, `line_width`, `color`, `area_color`, `is_solid` |
| `pin` | `pin_name`, `description`, `x`, `y`, `length`, `orientation`, `electrical`, `show_name`, `show_designator`, `is_hidden`, `color` |

Coordinates and lengths are mils, with Y increasing upward. Coordinate updates
require pairs (`x/y`, `x1/y1`, `x2/y2`, `cx/cy`). Coordinates are limited to
plus/minus 200000 mils; lengths/radii must be positive. Vertices replace the
complete vertex list of the same object: 2..1000 for polylines, 3..1000 for
polygons. Arc angles are degrees in 0..360. Native enum values are used for
`orientation` (0..3), `justification` (0..8), `line_width` (0..3) and
`electrical` (0..7); read the current value before changing it. Font size is an
integer in 1..144 points. Boolean fields require actual booleans.

Colors are native BGR integers: red `0x0000FF`, green `0x008000`, blue
`0xFF0000`. Text and pin names/descriptions accept empty strings and Unicode.
Pin numbers, UIDs and part/display ownership are read-only. Pin parameters,
IEEE symbols and footprint maps are preserved. Use `set_library_component_data`
for model maps and symbol parameters, or `EDITSYMBOL` for its pin metadata
records. Beziers, rounded rectangles, elliptical arcs and other unlisted types
are not editable through this tool.

## E08 color labels

Read first, then select the correct part and current label text:

```python
update_symbol_primitives(
    library_path=r"C:\Design\SQ-CARD.SchLib",
    symbol_name="LTST-C155KGJRKT",
    edits=[],
)

update_symbol_primitives(
    library_path=r"C:\Design\SQ-CARD.SchLib",
    symbol_name="LTST-C155KGJRKT",
    edits=[
        {"target": {"type": "label", "owner_part_id": 1, "text": "RED"},
         "properties": {"text": "Red", "color": 0x0000FF}},
        {"target": {"type": "label", "owner_part_id": 2, "text": "GREEN"},
         "properties": {"text": "Green", "color": 0x008000}},
    ],
)
```

After renaming a label, use its UID or the new text for subsequent calls.
No documents are saved, placed components updated or projects compiled.

## Results and verification

The result includes `objects` with supported object snapshots after the edit,
`results` with each edit's index/type/UID, `changed`, `updated_count`, Modified
states, symbol UID, `metadata_before`/`metadata_after`, `saved:false`, and
`verification:{passed, errors}`. `unsupported_count` counts other native child
objects, including symbol parameters and model containers, not just unsupported
drawing types. Internal interface identities are never public selectors.

Verification checks requested readback, unchanged object identities/counts,
unrequested exposed properties, pin parameters/IEEE symbols, symbol metadata
and footprint maps, Modified behavior and unchanged library file hash/timestamp.
Legacy empty UIDs are preserved; objects receive no new UID. The native registry
holds existing interfaces because schematic `I_ObjectAddress` is an opaque
handle, not a numeric address.

Inspect `may_have_partial_changes` on failures. An incomplete executor reply
returns `outcome_unknown:true` and retains its request spec. Stop and inspect
Altium before retrying an uncertain mutation.

## Verification runs

Unit tests: `server/tests/test_symbol_primitives.py`. Opt-in live harness:

```powershell
python dev/test_symbol_primitives_live.py --library C:\Design\SQ-CARD.SchLib --symbol LTST-C155KGJRKT --exercise --fixtures
```

The harness copies the library into ignored `.altium-mcp`, edits both E08
labels and existing graphics/pins, checks missing/ambiguous/duplicate targets
and idempotency, then discards the copy. `--fixtures` also creates a disposable
symbol using existing creation tools to exercise every supported type. Original
project states and source/project/scratch file hashes/timestamps are compared.
No design documents are saved. Reports remain under the ignored run directory.

The harness invokes production handlers in a fresh process without another
listening MCP server. Restart the connected MCP process to refresh its tool
catalog; Altium does not need restarting.

Passed on 2026-11-08 with the SQ-CARD project open. Run
`symbol_primitives_20261108_105227` completed 21 steps on a disposable SchLib
copy. Both E08 color labels (parts 1/2), a line, a polygon and a pin were edited
without replacement. The additional fixture exercised all eight supported
types, font styles, fill colors, vertex-count changes, pin properties and empty
strings. Missing/ambiguous/duplicate targets and repeated no-ops behaved as
expected. Symbol identities, metadata, pin maps, unedited objects, original
document states and all 10 guarded files were preserved. The dirty scratch
buffer was closed with discard.

Regression suite: 173 tests, 172 passed and one platform-specific skip.
