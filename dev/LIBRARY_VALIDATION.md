# validate_library_components

```python
validate_library_components(
    library_paths, component_names=None, rules=None, pad_roles=None,
    offset=0, limit=50, max_issues=500,
)
```

Read native library buffers and validate their metadata, pin maps, pin grid,
pad dimensions and manufacturing geometry. Existing getters supply the pieces;
this tool combines a batch native snapshot with reusable Python checks. It
never fixes, compiles, saves or reloads dirty documents.

## Selection and dependencies

Paths, names and pagination follow [get_library_components_data](LIBRARY_COMPONENTS_DATA.md):
one absolute SchLib/PcbLib path or 1..16 distinct paths; optionally 1..500 exact
case-insensitive native member names; `offset >= 0`, `limit` 1..500. Named open
unsaved libraries are supported. The input list explicitly defines which
libraries may be opened/read; the validator never follows links outside it.

Every selected symbol's PCB implementations are checked, including noncurrent
models. Their footprints are read and validated even when outside the root
page/name filter. `dependencies` contains these additional footprint results;
they do not consume root page slots. Complete inventories need one native call;
filtered/paged selections can need additional batched dependency reads.

Stored absolute/relative PCB datafile links constrain footprint resolution.
Without a stored location, the name must resolve uniquely across the input
PcbLibs. External/missing/ambiguous models remain unresolved. The caller must
include the relevant PcbLibs; a SchLib-only call cannot certify pad mappings.

## Rules

`rules` overrides the following defaults. Unknown keys, nonfinite values and
invalid ranges are rejected before native execution. These thresholds are
configurable checks, not IPC/fabricator requirements or datasheet validation.

| Key | Default |
|---|---|
| `required_parameters` | `Value, MF, MP, HelpURL, Package, Type, Instructions, System` |
| `pin_grid_mils` | 100 |
| `grid_tolerance_mils` | 0.01 |
| `min_pad_size_mm` / `max_pad_size_mm` | 0.05 / 100 |
| `min_copper_clearance_mm` | 0.1 |
| `min_npth_clearance_mm` | 0.2 |
| `min_annular_ring_mm` | 0.1 |
| `min_mask_web_mm` | 0.1 |
| `min_paste_clearance_mm` | 0.1 |
| `max_paste_expansion_mm` | 0 |
| `rule_mask_expansion_mm` / `rule_paste_expansion_mm` | `None` / `None` |
| `require_footprint` | `True` |
| `require_shield_mapping` | `False` |

Required parameters are case-insensitive, nonempty and cannot contain known
placeholder values. Native component parameter records retain duplicates and
exclude child pin parameters. Pin parameters never satisfy component metadata.
Duplicate names, invalid HelpURL, an invalid native Designator template and an
empty/placeholder native Description are reported. Description needs at least
three words. `required_parameters=[]` disables only the mandatory-name list.

Pin connection coordinates and lengths use library mils, Y up. All stored
sections/display modes are checked. Duplicate pin numbers or connection points
within one section/mode are errors; the same number/position in another section
or alternate display is permitted. Length and x/y must match the configured
grid within tolerance. Zero/negative lengths and unsupported orientations fail.

## Electrical, shield and NPTH roles

```python
pad_roles=[{
    "library_path": r"C:\Design\Parts.PcbLib",
    "footprint_name": "USB_C",
    "pad_name": "SH1",
    "role": "shield",
}]
```

Each exact library/footprint/pad selector assigns `electrical`, `shield` or
`npth`. Repeated physical pads with the same name get the same role. NPTH is
determined from a nonzero native hole and `plated=False`; an override cannot
turn it into an electrical contact. A false NPTH override is also an error.
SMD pads with `plated=False` and no hole remain electrical contacts.

Unassigned names matching SH/SHIELD/SHELL/SCREEN plus an optional number are
shield hints. Their classification is explicitly inferred and coverage remains
incomplete until confirmed with `pad_roles`. All other nondrilled/plated pads
default to electrical. Every physical pad gets its index, name, role and source.

Electrical pads must be mapped. Shields may be mapped to a schematic shield/GND
pin; unmapped shields are allowed unless `require_shield_mapping=True`. NPTH
does not require mapping, positive land dimensions or paste. Mapping an
electrical pin to NPTH, or marking an NPTH as a testpoint, is an error. Shield
and NPTH spacing still participate in the applicable copper/drill checks.

Role records may also contain `paste_required:False` for intentional paste
suppression on hand-solder contacts, fiducials or other explicitly reviewed
pads. Native testpoints default to optional paste; other electrical/shield
contacts default to required paste. The effective value is included in the
pad classification. An optional collapsed paste aperture is accepted without
changing the electrical role or excluding copper/mask clearance checks.

## Mapping checks

Mappings preserve string identifiers (`01` differs from `1`) and one-to-many
pad lists. Duplicate physical pads with one name remain a valid one-to-many
physical contact. Blank native MapAsString uses Altium's same-name mapping;
explicit maps are checked for missing/extra symbol pins, absent footprint pads,
empty/repeated pad lists, and multiple pins assigned to one pad. The latter is
reported for review of the intended short, rather than silently accepted.
Malformed raw maps remain errors with the original string.

Pin existence is evaluated across the symbol's stored parts/modes. This is
library-map validation; it does not establish net assignment of a placed
component, project compilation, ERC, actual signal identity or datasheet pinout.

## Geometry, mask and paste coverage

All reported distances are **mm**, except explicit pin fields in mils.
Geometry uses footprint-relative pad locations and rotation, native sizes,
hole type/rotation and plating, Top/Middle/Bottom lands, mask/paste mode/cache
expansion and tenting. Sufficient numerical tolerance covers native coordinate
quantization; it is not a fabrication allowance.
Edge comparisons allow 0.00006 mm for combined 0.001 mil export rounding.

- Pad dimension range, concentric annular ring and copper edge clearance are
  checked per applicable copper side. Top-only and Bottom-only pads do not
  falsely collide. Same-name physical lands are one electrical contact and
  do not require copper separation; their aperture spacing is still checked.
- Circles, rotated rectangles and round/square/slotted holes have explicit
  geometry. Slot annular rings use hole orientation. Rounded rectangles,
  octagons and noncircular round lands use containing rotated rectangles:
  sufficient separation passes; a suspected violation is unresolved rather
  than a fabricated exact error.
- NPTH drill-to-copper distance uses the actual drill envelope, including
  slots. NPTH drill land dimensions may be zero. Plated holes need a land and
  sufficient annular ring; drilled pads must use Multi Layer.
- Manual mask/paste expansions are applied to land dimensions. Collapsed
  apertures, insufficient mask web/paste separation, excessive positive paste
  growth and tented electrical SMD pads are reported. Disabled SMD apertures
  and enabled through-hole paste produce intent-review warnings.
  A manual cache not marked Manual is deferred. Separate mask/paste primitives
  can replace a suppressed pad aperture, so that case is deferred for custom
  aperture inspection rather than treated as a confirmed missing opening.
- Rule-driven expansion has no portable resolved board value in a library.
  It is deferred unless `rule_mask_expansion_mm` / `rule_paste_expansion_mm`
  are supplied. These values are explicit caller assumptions, not extracted
  dominant PCB rules. Tented solder-mask sides produce no opening.

The geometry scope is **concentric legacy standard pad lands**. The tool does
not reconstruct pad offsets, modern custom pad/mask/paste contours, custom
stencil windows, paste-area ratios or every layer of Full Stack pads. Full
stacks, unsupported shapes, additional non-pad copper/mask/paste objects and
unresolved rule values are flagged. Pair checks are deferred above 700 physical
pads per footprint. Native board DRC and manufacturer/datasheet review remain
outside this tool's result. A `pass` applies to the reported representation
and configured rules, not fabrication readiness of arbitrary custom geometry.

See Altium's [pad-stack modes and API properties](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-design-objects-interfaces-reference),
[hole/mode enumerations](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-types-reference)
and [custom mask/paste shapes](https://www.altium.com/documentation/altium-designer/pcb/custom-pad-stack).

## Results

- `success`: native reads and analysis executed successfully. Validation errors
  do not turn an executed read into a transport failure.
- `valid`: `False` if any error was found; `None` if no errors but reads,
  selection/pages or checks are incomplete; `True` for a fully checked selection
  with no errors within the declared geometry scope. Warnings can coexist with
  `True` when they request intent review rather than defer a check.
- `components`: root page results. `dependencies`: additional linked footprints.
  Each has `status:pass|fail|incomplete`, coverage, issue counts and issues with
  stable `code`, `severity`, message, affected identities and measurements.
  Symbol status covers its metadata/map; linked-footprint geometry status is in
  that footprint's own result and contributes to overall `valid`.
- `complete`: root selection read completely, no missing names/read failures,
  and no deferred checks. `has_more` / `next_offset` paginate the roots.
  `validation_complete` also becomes false for an incomplete selection/read.
- `max_issues` (1..10000) limits emitted issues across roots/dependencies,
  default 500. Analysis/counts continue after the cap; `issues_truncated` is
  explicit and cannot turn a failure into a pass.
- `libraries`, `read_errors`, native/dependency counts and `verification`
  distinguish read preservation from validation findings. No raw geometry or
  full native metadata is echoed into the validation response.

The shared reader refuses active schematic transactions and preserves active
members, section/display selection and Modified. It restores the prior document
view after each batch. New libraries may remain open. Input file hashes and
timestamps (or continued absence of unsaved files) are verified. Unknown native
completion stops dependent calls; no automatic edit/retry is performed.
Keep buffers unchanged while validation runs: separate root/dependency batches
and separate pages do not form an atomic snapshot of concurrent user edits.

## Examples

```python
validate_library_components(
    [r"C:\Design\Parts.SchLib", r"C:\Design\Parts.PcbLib"],
    rules={"pin_grid_mils": 100, "min_copper_clearance_mm": 0.15},
    limit=500,
)

validate_library_components(
    [r"C:\Design\Parts.SchLib", r"C:\Design\Parts.PcbLib"],
    component_names=["USB_C_SYMBOL"],
    pad_roles=[{
        "library_path": r"C:\Design\Parts.PcbLib", "footprint_name": "USB_C",
        "pad_name": "SH1", "role": "shield",
    }],
    rules={"rule_mask_expansion_mm": 0.05, "rule_paste_expansion_mm": 0},
)
```

## Verification

Offline tests: `server/tests/test_library_validation.py` exercise metadata,
mapping, roles, dependency scope, numeric boundaries, rotated edge distances,
slot rings, mask/paste modes, partial coverage, caps, pagination and transport.

Opt-in live harness, using the production handler without another listening
MCP server:

```powershell
python dev/test_library_validation_live.py --exercise --schlib C:\Design\SQ-CARD.SchLib --pcblib C:\Design\SQ-CARD.PcbLib
```

It validates the original libraries read-only, checks E08/M01 dependencies and
roles, then creates good/bad test fixtures only in disposable library copies.
All temporary buffers are discarded, original project states and guarded file
hashes/timestamps are compared. Diagnostics/specs stay in ignored `.altium-mcp`.
Restart the connected MCP process to refresh its catalog after installation.

Verified on 2026-11-08 in the open SQ-CARD project. The successful live run
`.altium-mcp/library_validation_20261108_233235/report.json` completed 29 steps:

- Read all 25 symbols and 25 footprints without edits. E08 passed with its
  off-filter footprint dependency. M01 classification distinguished 12
  electrical contacts, four explicitly confirmed shield contacts and two NPTH.
- Created one symbol and two good/bad footprints only in disposable copies.
  The good linked fixture passed with dirty buffers and unchanged selections.
- Bad geometry reported pad size, copper/NPTH clearance, mask web and paste
  gap errors. Unsaved fixture changes reported a missing MP, an off-grid pin
  and a collapsed required paste aperture.
- Rule-driven mask/paste gave `valid:null` until explicit rule expansions were
  supplied; the same fixture then passed. Missing names stayed explicit.
- Discarded both copied buffers, restored the original project/editor, and
  verified unchanged original document states and all 11 guarded file
  hashes/timestamps. No Altium document was saved.

The original library did **not** pass the default profile: 60 error-level
findings and 14 warnings were reported. These include configurable thresholds
and assembly intent, not automatic conclusions about manufacturing defects.
For example, M01's measured NPTH-to-copper gaps are about 0.16509 mm versus the
default 0.2 mm; some fine-pitch mask webs are below the default 0.1 mm, and
several non-testpoint pads intentionally suppress paste but have no explicit
optional-paste policy in this run. Custom aperture/slot-land checks remain
explicitly unresolved. No original finding was automatically fixed. The full
machine-readable findings are in that run's `project_libraries.json` and
`usb_roles.json`.

Final offline regression: 213 selected tests, 212 passed and one platform-
specific skip, including 16 new validator tests and the real script-bundle
dependency check. Fresh catalog inspection confirmed the documented signature
and `readOnlyHint:true` without starting another listening MCP server.
