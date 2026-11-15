# get_library_components_data

```python
get_library_components_data(
    library_paths, component_names=None, fields=None, offset=0, limit=50
)
```

Read multiple members of one or several SchLib/PcbLib buffers through one
native script invocation. Existing metadata, symbol, footprint-height and pad
tools cover individual pieces; this tool combines their read paths without
opening an editor for every component or using ad-hoc scripts.

## Inputs

- `library_paths`: an absolute `.SchLib`/`.PcbLib` path string or a list of
  1..16 distinct paths. A saved closed library is opened. An unsaved path must
  already identify an open named library. Dirty buffers are reused without
  reload. Ambiguous aliases or a wrong document kind are refused.
- `component_names`: omitted for all members, or 1..500 distinct exact names,
  matched case-insensitively. These are symbol LibReferences and footprint
  names, not schematic designators or a `Component ID` parameter such as M01.
  A name present in multiple input libraries returns every match. Missing names
  are evaluated across the batch; each row identifies its library explicitly.
- `fields`: a nonempty distinct subset of `properties`, `parameters`, `models`,
  `pins`, `pads`. Default: `properties`, `parameters`, `models`. Pin/pad arrays
  are opt-in to keep normal metadata responses compact. Unsupported sections
  are omitted and listed in each library's `unsupported_fields`.
- `offset`: matching-member offset, 0..1000000. `limit`: page size, 1..500,
  default 50. Pagination follows input library order and native case-insensitive
  name order within each library. Duplicate/ambiguous names within one library
  produce a component error rather than choosing an arbitrary member.

## Available data

| Section | SchLib | PcbLib |
|---|---|---|
| Always | Library path/kind and exact component name | Library path/kind and exact footprint name |
| `properties` | Description, Comment, native Designator template/read-only state, ComponentKind name/code, part/display counts and current part/mode | Native Height in mm and Description |
| `parameters` | Native name/value dictionary, matching `set_library_component_data` read semantics | Unsupported |
| `models` | Every native implementation: type/name, description, current/lock/database flags, datafile links, raw MapAsString; PCB models also get parsed `pin_map` | Unsupported |
| `pins` | All stored parts/modes: string pin number/name, UID, part/display ownership, x/y/length, orientation/electrical type, visibility, color, description, IEEE symbols and pin parameters | Unsupported |
| `pads` | Unsupported | Every physical pad's string name/layer and native `is_testpoint_top` / `is_testpoint_bottom` booleans |

Pin positions/lengths are native library mils, Y up; they are not transformed
connection points of placed schematic components. Pad details deliberately
report metadata/flags, with no mixed active/inactive-library coordinate origin.
Use `get_footprint_primitives` when pad geometry is needed.

Duplicate physical pad names and pin numbers shared by multiple sections remain
separate array entries. No conversion of `"01"` to `"1"` is performed. Parsed
pin maps retain one-to-many pad lists. An unrecognized map keeps its raw string
and gets `pin_map:null` plus a `mapping_warning`.

Height is the native footprint property, not the height of a STEP body. Testpoint
booleans are library-pad flags, not compiled-board net assignment. Model links
retain their stored locations; the read does not follow unrequested libraries
or validate their files/pads. PcbLib rows do not fabricate schematic Designator,
ComponentKind, custom symbol parameters or schematic pin implementations.

## Examples

Inventory properties of both libraries in one call:

```python
get_library_components_data(
    library_paths=[r"C:\Design\Parts.SchLib", r"C:\Design\Parts.PcbLib"],
    fields=["properties"],
    limit=500,
)
```

Read a symbol and its footprint, including its pin map, Height and pad flags:

```python
get_library_components_data(
    library_paths=[r"C:\Design\SQ-CARD.SchLib", r"C:\Design\SQ-CARD.PcbLib"],
    component_names=["LTST-C155KGJRKT", "LITEON_LTST_C155KGJRKT_4"],
    fields=["properties", "parameters", "models", "pins", "pads"],
)
```

To find names from project IDs, first request `parameters` and inspect their
`Component ID` values. Then request detailed pins/pads only for those native
names. Follow `next_offset` with the same paths, names and fields for later
pages; keep source buffers unchanged while paging for a consistent traversal.

## Results, partial data and preservation

`components` contains only the requested applicable sections. `libraries`
reports paths/kinds, counts, open status, Modified before/after and current
member/part/display before/after. `matched_count` is the complete matching
selection size; `returned_count` counts successful rows in this page.
`page_count` also includes failed row slots, so an unreadable component cannot
cause a repeated-page loop. `has_more` and `next_offset` identify later pages.

Unknown names give `missing_names` and `complete:false`, while the read itself
can succeed. Library or row read errors set `success:false` and preserve other
rows in `library_errors` / `component_errors`. A later library's failure does
not discard earlier data. `complete` requires a successful read, no missing
names and no later page; it does not include earlier pages in the same response.

The tool reads current native buffers without selecting individual members or
changing their parts/display modes. It restores the previously active document
view, while newly opened libraries remain open. An active schematic transaction
refuses the batch before opening libraries. No PreProcess, object edits,
compilation, document saves or project updates are performed.

Python checks exact library/row identities, count/pagination consistency,
native flags, unchanged Modified/selection, restored view and all requested
library file hashes/timestamps (or continued file absence). It strips internal
handles/raw snapshots and unused sections from the public response. This is a
read-consistency check, not an electrical or cross-library link audit.

An incomplete executor response reports `outcome_unknown:true` and retains its
request spec until the executor is inspected. No second listening MCP server
or custom snippet is needed for normal operation. Restart the connected MCP
process to refresh the new tool catalog; Altium does not need restarting.

## Verification

Unit tests: `server/tests/test_library_components_data.py`. The opt-in harness
compares E08/M01/M03/M04/M06 with the established getters, checks pagination and
partial errors, then changes only disposable copied buffers to confirm reads
see unsaved Designator/kind/parameter, Height and both-side testpoint values:

```powershell
python dev/test_library_components_data_live.py --exercise --schlib C:\Design\SQ-CARD.SchLib --pcblib C:\Design\SQ-CARD.PcbLib
```

It also reads fresh named unsaved libraries, verifies original project states
and file hashes/timestamps, and discards all scratch buffers. Libraries, specs
and reports live under ignored `.altium-mcp`; no design files are saved.

Offline regression on 2026-11-08: 197 selected tests, 196 passed and one
platform-specific test skipped. This includes seven new batch-reader tests,
the real DelphiScript bundle dependency check and existing library/document/
schematic tests. Fresh MCP catalog inspection confirmed the signature and
`readOnlyHint:true` without starting another listening server.

Live verification on 2026-11-08 used the open SQ-CARD project and disposable
copies of its SchLib/PcbLib. The successful 40-step run is recorded locally in
`.altium-mcp/library_components_20261108_222107/report.json`:

- Inventoried 25 symbols and 25 footprints in one batch. Detailed data for
  E08/M01/M03/M04/M06 and their five footprints matched the established getters,
  including E08's two parts, model maps, pin coordinates, Height and pad flags.
- Traversed the same 50 members in pages of 19/19/12; checked an unknown name
  and a missing second library while retaining the first library's 25 rows.
- Read unsaved fixture changes: Designator `Z?`, `standard_no_bom`, a Unicode
  parameter, Height 4.56 mm and both testpoint flags on a Multi Layer pad.
  Both dirty buffers retained Modified and their current members/parts.
- Read both default members from two newly created named unsaved libraries,
  while their assigned files remained absent.
- Discarded all four scratch buffers, restored the original project/editor,
  and verified unchanged original document states plus hashes/timestamps of
  all 11 guarded files. No Altium document was saved.

An earlier harness run stopped at an incorrect `Multi-Layer` string assumption;
Altium reports `Multi Layer`. It closed its scratch buffers with all file guards
unchanged. The successful run above uses the corrected layer comparison.
