# Batch footprint creation

```python
create_footprints_batch(spec_file, units="mil")
```

Create footprints in one native transaction with Height and Top/Bottom pad
testpoint flags set immediately. Existing library/pad property setters remain
the tools for editing existing objects. No library, sheet or project is saved.

## Units

`units="mil"` is the backward-compatible default; `units="mm"` is explicit
metric input. Both accept upper/lowercase. This is a **whole-file input unit**,
not a library UI/display setting. There is no per-record unit switch or `UNITS`
record. The caller's file is preserved; a private UTF-8 BOM copy is normalized
for the existing native reader. Unknown units and nonfinite linear values are
refused.

Every linear field uses the selected unit:

| Record | Linear fields |
|---|---|
| `FOOTPRINT` | Native Height |
| `PAD` | x/y, hole diameter/slot width, Top/Middle/Bottom land dimensions, mask/paste expansions |
| `TRACK` | Endpoints and width |
| `ARC` | Center, radius and line width |
| `FILL` | Corner coordinates |
| `TEXT` | Position, size and stroke width |
| `REGION` | Every contour vertex |
| `VIA` | Position, diameter and hole size |
| `MODEL3D` | Model x/y/z offsets; STEP geometry itself is not rescaled |

Rotations/angles stay in degrees. Shape/hole/mode enums, corner percentages,
booleans, pad names and text remain unchanged. Use decimal points for numbers.
Existing getters keep their documented output units: most geometry is mils,
native footprint Height and supplementary mask/cache fields use mm.

## Creation properties

```text
FOOTPRINT|name|description[|height]
```

Height is `IPCB_LibComponent.Height`, not a text parameter or a STEP-body size.
Omitted/blank Height is zero. Range: 0..1000 mm equivalent in the selected unit.
Setting it does not resize a 3D model. Descriptions cannot contain `|`.

```text
PAD|name|x|y|rot|layer|plated|hole_size|hole_type|hole_width|hole_rot|top_x|top_y|top_shape[|corner_pct[|mode|mid_x|mid_y|mid_shape|bot_x|bot_y|bot_shape[|paste_mode|paste_expansion|mask_mode|mask_expansion[|testpoint_top|testpoint_bottom]]]]
```

The existing PAD columns are unchanged. New trailing fields are at zero-based
positions 26 and 27, counting `PAD` as position 0. Use `1`, `0` or blank; omitted
flags default to False. Leave preceding optional columns empty to reach a later
one. Pad names stay strings, including `01` and duplicate physical pad names.

- Top=True requires Top Layer or Multi-Layer copper.
- Bottom=True requires Bottom Layer or Multi-Layer copper.
- Either/both flags may be cleared on any pad layer. They are independent native
  `IsTestpoint_Top` / `IsTestpoint_Bottom` flags, not custom parameters.

Mask/paste modes retain their existing semantics: 1 uses rules, 2 uses manual
expansion in the selected input unit. Mode 0 cannot be set through this native
writer and remains a reported problem. All newly created pads are locked.
These flags do not assign placed-board testpoint nets or update placed parts.

Example metric specification (illustrative fixture, rectangular shape enum 2):

```text
FPLIB|C:\Design\Parts.PcbLib
FOOTPRINT|TEST_SMD|Illustrative SMD fixture|1.2
PAD|01|0|0|0|Top Layer|0|0|0|0|0|1|1.5|2|0|0|||||||2|0|2|0.05|1|0
```

```python
create_footprints_batch(r"C:\Scratch\footprints.txt", units="mm")
```

Obtain shape/hole enums from `get_footprint_primitives` or the supported
`get_pcb_api_constants` names for the running Altium version.

## Preflight and readback

All Height values, flag syntax/side compatibility and unit conversions are
checked before dispatch. A malformed later record cannot create earlier
footprints. Layer names are normalized; Altium also preflights every layer and
testpoint side before entering the transaction. Unknown layers/No Layer reject
the batch without creation. Native primitive exceptions retain line/record
details in `problems`.

`footprints` in the result contains each created footprint's exact name/library,
`height_mm`, pad count and each pad's Top/Bottom flags. Python verifies identities,
Height within native coordinate precision and pad flags as a multiset, preserving
repeated pad names. No separate property-setting call/transaction is needed.
`verification` and `input_units` describe the readback and selected input unit;
counts, `failed`, `problems` and `primitive_errors` remain available. Any such
error prevents success. A timeout retains the request and reports unknown
completion; a guard refusal before execution does not claim possible edits.

The native readback covers creation Height/flags. It is not a full geometric or
manufacturing audit; use the getter/validator when those checks are needed.
Partial in-memory creation remains possible after native execution errors;
inspect counts and `may_have_partial_changes` before retrying.

`FPLIB|path` still accepts an existing absolute PcbLib path, reusing dirty buffers
without reload. Omit it only when the intended PcbLib is already focused.
`EDITFOOTPRINT|name` keeps its existing STEP-only scope: no Height suffix and no
pad edits. Use `set_footprint_properties` / `set_footprint_pad_properties` for
those existing-object edits. Old mil specifications need no changes.

## Verification

Offline: `server/tests/test_footprint_batch_properties.py` covers every converted
record, untouched angles/enums/text, height bounds/defaults, flag compatibility,
duplicate pad names, native readback failures, one-call dispatch and guard errors.
Existing footprint/editor/bundle tests remain part of the regression suite.

```powershell
python dev/test_footprint_batch_properties_live.py --exercise --pcblib C:\Design\Parts.PcbLib
```

This runner creates only in a disposable library copy, compares independently
specified mm/mil geometry through the existing native getter, verifies Height
and Top/Bottom flags, tests legacy defaults and invalid later records, then
discards the scratch buffer. Project states and file hashes/timestamps are
checked. Diagnostics stay under ignored `.altium-mcp`. A fresh Python process
calls production handlers without a second listening MCP server; restart the
connected MCP process to load its updated argument/schema and validator.

Verified in Altium on 2026-11-10. The successful 21-step run
`.altium-mcp/footprint_batch_properties_20261110_132830/report.json` created four
footprints only in a disposable copy. One metric call created two footprints
with different Height/default properties, confirming separate readback and
no carryover between components. Independently specified mm/mil fixtures had
identical native pad/stack/hole geometry, mask/paste, tracks, arcs, fills, text,
regions and vias. Height 3.26 mm, Top-only/Bottom-only/both-side flags and repeated
physical pad names were checked. Legacy omitted Height/flags retained zero/False.

Invalid later Height/side/flag/unit inputs were refused without creating an
earlier valid footprint. The scratch library was discarded; an existing
footprint, original project states and all 12 guarded file hashes/timestamps
remained unchanged. No Altium document was saved.

Final regression: 245 selected tests, 244 passed and one platform-specific skip,
including seven new batch-property tests and the real DelphiScript bundle check.
Fresh MCP catalog inspection confirmed `units` with default `mil`.
