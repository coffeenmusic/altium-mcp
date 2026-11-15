# get_schematic_connectivity

```python
get_schematic_connectivity(sheet_path, compiled=True, expected_connections=None)
```

Returns active pin numbers, electrical connection coordinates and actual
compiled net names for one schematic sheet. Optionally checks expected
connections. Never edits or saves sheets or clears their Modified flag.

All returned coordinates are mils, including editor fallback for missing compiler
pins. The previous internal-unit leak in fallback/UID/clone pin snapshots was
corrected and verified against all 79 pins in the power sequencer draft; see
[script replacements and live evidence](POWER_SEQUENCER_SCRIPT_REPLACEMENTS.md).

## Existing alternatives

| Tool | Provides | Missing capability |
| --- | --- | --- |
| `get_net_connections` | PCB pad connectivity | Current schematic data |
| `get_schematic_data` | Schematic component data | Full sheet-specific pin-to-net map |
| `edit_schematic(path, [])` | Editor objects and pin coordinates | Compiled nets |
| `get_project_components` | Compiled project components | Pin-to-net map |
| `get_erc_violations` | Compiler/ERC diagnostics | Expected-connection checks |
| `dev/netlist.py`, `dev/check_connectivity.pas` | Geometric checks | Compiler authority; limited hierarchy, port and mirrored-symbol handling |

The implementation uses Workspace Manager's `DM_Compile`,
`DM_DocumentFlattened`, `DM_Pins`, `DM_PinNumber`, `DM_FlattenedNetName`,
`DM_PartUniqueId` and coordinates. It never infers nets from drawing proximity.
See the [official Workspace Manager API](https://www.altium.com/documentation/altium-dxp-developer/workspace-manager-api).

## Sheet selection and compilation

`sheet_path` must be an absolute path to an existing `.SchDoc` belonging to
exactly one open `.PrjPcb`. Ownership is resolved by exact path, independently
of UI focus. A file shared by multiple open projects is rejected because its
networks may depend on the owning project.

A closed sheet is opened for reading. An open sheet is reused without reloading
it from disk, preserving unsaved changes. An active editor transaction blocks
the operation.

- `compiled=True`: explicitly compile the owning project in memory, then read
  its model. Returns `compile_performed=true`, `may_be_stale=false`.
- `compiled=False`: read the existing compiler cache without explicitly
  compiling. Returns `compile_performed=false`, `may_be_stale=true` and a warning.
  Nets and coordinates may predate unsaved edits. If no model exists, the call
  fails and requests `compiled=True`.

Compilation may update derived Altium data such as `.PrjPcbStructure`; it does
not save `.SchDoc` files or repair project errors.

## Response

`success` means retrieval succeeded, not that the expected circuit matched.
The response includes `sheet_path`, `project_path`, `saved=false`,
`was_modified`, `is_modified`, compilation/cache flags,
`coordinate_units="mils"`, `net_source="altium_workspace_manager"`,
`pins`, `nets` and `warnings`.

Each `pins` record contains:

| Field | Meaning |
| --- | --- |
| `index` | Record index in this response, not a persistent ID |
| `designator`, `logical_designator` | Physical instance and logical designators |
| `component_uid` | Source component UID on the sheet |
| `pin_number`, `pin_name` | Pin number and name; numbers are strings, so `01` differs from `1` |
| `part_id`, `instance_id` | Component part and full compiled pin instance ID |
| `x`, `y` | Electrical connection point in mils |
| `net_name`, `net_id` | Actual flattened net name and key within the current project model |
| `logical_net_name` | Local net name from the model, when available |
| `resolution` | Mapping/net resolution status |
| `coordinate_source` | `compiled_model` or `schematic_editor` |
| `editor_x`, `editor_y`, `geometry_matches` | Coordinate comparison with the editor for matched model records |

Compiler coordinates take precedence. Editor geometry supplies comparison data
and coordinates for pins missing from the model; it never determines their net.
For mirrored or complex symbols, `geometry_matches=false` needs investigation,
not replacement of compiler data with a geometric guess.

Only the placed symbol's active part and display mode are inventoried, including
shared pins with `OwnerPartId=0`. Unused alternative parts are excluded.

Each `nets` record includes `net_id`, `net_name`, local `pin_indexes`,
`sheet_pin_count` and `project_pin_count`. The latter counts pins enumerated by
the entire flattened project model, exposing cross-sheet connectivity through
ports, power and hierarchy without returning all remote endpoints.

`net_id` equals the flattened name in the current model. Automatically generated
names may change after edits and recompilation; do not treat them as permanent IDs.

## Unknown nets and coverage

Live editor pins are reconciled with compiler records by component UID, pin
number and part. Pins are retained even when absent from the compiler model.

| `resolution` | Meaning |
| --- | --- |
| `resolved` | Matching compiler record and net found |
| `not_in_compiled_model` | Placed pin has no compiler record; `net_name=null` |
| `not_assigned` | Compiler returned an empty name or `?` |
| `ambiguous_model` | Contradictory records share one pin identity; no net is chosen |

`placed_pin_count` and `model_pin_count` describe the two inventories.
`missing_model_pin_count` counts placed pins absent from the model;
`unresolved_pin_count` counts output records without an unambiguous net.
`model_only_pins` contains compiler pins not matched to editor pins.
`complete=false` indicates missing/unresolved records or fatal compiler errors.

`compile_errors`, `compile_fatals` and `compile_warnings` report compiler counts.
Use `get_erc_violations` for details. `complete=true` does not certify electrical
correctness or replace ERC. `checks.passed` covers only the supplied expectations;
unrelated coverage gaps remain in the response.

## Expected connections

```python
get_schematic_connectivity(
    sheet_path=r"C:\Projects\Board\Display.SchDoc",
    compiled=True,
    expected_connections=[
        # Check one pin's actual net name.
        {"pins": [{"designator": "C2", "pin_number": "1"}],
         "net_name": "TMDS_D2_N"},
        # Both pins must share a net.
        {"pins": [{"designator": "R10", "pin_number": "2"},
                  {"designator": "U1", "pin_number": "3"}]},
        # Reject additional pins on this sheet only.
        {"pins": [{"designator": "R11", "pin_number": "1"},
                  {"designator": "C10", "pin_number": "2"}],
         "net_name": "FILTER_OUT", "exact": True},
        # Both pins must belong to different known nets.
        {"pins": [{"designator": "U1", "pin_number": "1"},
                  {"designator": "U1", "pin_number": "2"}],
         "connected": False}
    ]
)
```

The R/U designators illustrate the schema; use actual project endpoints.
Defaults: `connected=true`, `exact=false`. Optional `net_name` checks every
listed pin. Designators and expected net names are case-insensitive; pin
numbers are exact strings.

To disambiguate a selector, add `component_uid`, `part_id` or `instance_id` from
a fresh response. Include only populated fields: omit `instance_id=null` for
unresolved pins. Zero or multiple matches fail the check. Repeated channels are
not merged solely by logical designator.

`connected=false` requires exactly two pins and forbids `net_name` and
`exact=true`. Unknown nets cannot prove disconnection and fail the check.
A single pin requires `net_name` or `exact=true`. Selecting the same pin twice
is rejected. Limits: 1000 checks, 2000 selectors per check. Unknown fields and
invalid types are rejected before calling Altium.

`checks` contains `passed`, `checked_count`, `failed_count`,
`compile_performed`, `scope="sheet"` and per-check results with `issues`:

| Issue | Meaning |
| --- | --- |
| `missing_pin`, `ambiguous_pin` | Selector did not identify exactly one pin |
| `duplicate_resolved_pin` | Different selectors identified the same pin |
| `unresolved_net` | Actual net could not be established |
| `different_nets` | Expected connection is absent |
| `unexpected_net` | Actual net name differs |
| `unexpected_connection` | Forbidden connection exists |
| `unexpected_members` | Additional pins on the target sheet share the net |

`exact=true` checks only the target sheet. Remote GND endpoints do not count as
unexpected members. This is not a project-wide membership check or a complete
short-circuit audit.

## Tests and client refresh

```powershell
python -m unittest server.tests.test_schematic_connectivity -v
python dev/test_schematic_connectivity_live.py --sheet 'C:\Projects\Board\Display.SchDoc' --second-sheet 'C:\Projects\Board\Interfaces.SchDoc'
```

The live test requires the MCP Python environment and a running Altium project.
It only reads: compares compiler/editor coordinates, valid/invalid expectations,
cache behavior, sheet switching, object snapshots, Modified flags and SHA-256
hashes of all project `.SchDoc` files. Add `--expected <file.json>` for an
independently prepared `expected_connections` array. Artifacts and fresh PrjScr
copies used to bypass Altium's script cache stay under the toolkit's
`.altium-mcp/schematic_connectivity_<timestamp>` directory.

Restart the MCP connection to refresh its tool catalog. If Altium retains old
script buffers, reopen `Altium_API.PrjScr` without saving stale buffers over the
updated files. See the [live test report](SCHEMATIC_CONNECTIVITY_LIVE_TEST.md).
