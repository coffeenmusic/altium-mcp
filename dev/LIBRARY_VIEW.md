# set_library_view

```python
set_library_view(
    library_path, component_name=None, part_id=None, display_mode=None,
    fit=True, view_mode=None, camera=None,
)
```

Navigate a precise SchLib symbol/section or PcbLib footprint and prepare its
view for inspection. `search_library_symbol` provides partial-name navigation;
`open_document` selects a document; `zoom_schematic` fits SchDoc sheets. They
did not jointly cover exact library members, multipart sections and PCB camera
presets. This tool combines these presentation actions without saving.

## Inputs

| Input | Behavior |
|---|---|
| `library_path` | Absolute `.SchLib` or `.PcbLib`; saved or already open with a named unsaved path. Dirty buffers are reused without reload. Missing files that are not open are refused. |
| `component_name` | Exact case-insensitive symbol LibReference or footprint name. Must match once; no substring fallback. Omission uses the current member of that library. |
| `part_id` | SchLib only: 1-based section, checked against PartCount. Omission preserves the selected symbol's current part. Shared primitive ownership ID 0 is not a selectable section. |
| `display_mode` | SchLib only: 0-based alternate representation, checked against DisplayModeCount. Omission preserves the current representation. This selects an existing mode; it does not create one. |
| `fit` | Boolean, default `True`: fit objects; explicit 3D/camera requests frame padded physical bounds, excluding drafting text. `False` omits fitting. Native view changes can still affect framing. |
| `view_mode` | PcbLib: `"2d"` or `"3d"`. Omission sends no mode command. SchLib accepts `"2d"` and rejects `"3d"`. Fitting without an explicit camera can reset orientation. |
| `camera` | PcbLib: a preset from the table below. Implies `view_mode="3d"` if omitted; rejects an explicit `"2d"`. Omission sends no camera command. |

PcbLib rejects part/display selectors; SchLib rejects camera presets. Input
domain checks happen before opening a library. Missing/ambiguous members and
out-of-range sections/modes are refused before fitting or camera commands.
Opening the requested library can still change the active document on refusal.
An active schematic transaction refuses navigation before changing document
focus; finish that user/editor operation before retrying.

Use `fit=False` with no other selectors to read the current native member and,
for SchLib, its section and display mode. It does not read the camera pose.

## Native actions and presets

SchLib sections use `SetState_CurrentSchComponentPartId` and
`SetState_CurrentSchComponentDisplayMode` on the library editor, with their
corresponding getters for readback. See Altium's
[ISch_Lib reference](https://www.altium.com/documentation/altium-dxp-developer/isch-lib-interface).
The tool does not change primitive OwnerPartId or OwnerPartDisplayMode.

PcbLib selects native CurrentComponent, dispatches `PCB:SwitchTo2D` or
`PCB:SwitchTo3D`, fits if requested, and applies the camera last. SchLib/2D fit
uses `SCH:Zoom` / `PCB:Zoom` with `Action=All`. In PcbLib, Zoom All resets the
camera to a top view, so it must never run after a requested camera preset.
Explicit 3D/camera fitting instead uses
`IPCB_Board.GraphicalView_ZoomOnRect` over the non-text primitives with a square
frame and margin of one third of the larger span, at least 100 mils. It runs
before posing the camera. Empty/text-only footprints need `fit=False` in 3D.
See the [PCB system interface reference](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-system-interfaces-reference).
The command/parameter pairs below
come from the installed Altium `System/AdvPcb.rcs` menu definitions.

| `camera` | `PCB:ResetCamera` parameter |
|---|---|
| `top` | `To=Top` |
| `bottom` | `To=Bottom` |
| `front` | `To=Front` |
| `back` | `To=Back` |
| `left` | `To=Left` |
| `right` | `To=Right` |
| `isometric` | `To=RotateOrtho` |
| `flipped_isometric` | `To=ISOMETRIC` |

Only this finite command set is accepted. The tool does not send arbitrary
process strings, rotate component geometry, change model transforms, perform
mouse/keyboard automation or provide numeric orbit/pan controls.

## Examples

Inspect the second section of the E08 dual LED:

```python
set_library_view(
    r"C:\Design\SQ-CARD.SchLib", "LTST-C155KGJRKT",
    part_id=2, display_mode=0,
)
```

Inspect M01's USB-C footprint and embedded model:

```python
set_library_view(
    r"C:\Design\SQ-CARD.PcbLib", "GSWITCH_GT_USB_7010ASV",
    camera="isometric",
)
get_screenshot(view_type="pcblib")

set_library_view(
    r"C:\Design\SQ-CARD.PcbLib", "GSWITCH_GT_USB_7010ASV",
    camera="bottom",
)
```

Pass `view_mode="2d"` to return to 2D. The tool never saves a library or updates
placed components. It intentionally leaves the requested view active.

## Verification and limits

`selected` reports the actual native member and SchLib section/display mode,
including their counts. Results also include the previous member, active view
name, exact dispatched `actions`, Modified states, `saved:false` and
`verification`.

Native snapshots and Python checks cover exact selection, unchanged exposed
target geometry/metadata, library membership/metadata, Modified and disk
hash/timestamp. Iterator order, temporary interface handles and the equivalent
endpoint order of undirected tracks are normalized. Altium can reorder a
model's serialized pin-map entries when switching sections; recognized maps
are parsed and compared as actual pin-to-pad relations. Snapshots are removed from
the public result to keep responses short.

Fit, 2D/3D mode and camera commands have no supported pose/mode readback in the
available scripting interfaces. `verification.fit_mode_camera="dispatch_only"`
states this explicitly: `verification.passed` covers the listed `scope`, not
proof of the rendered camera or loaded STEP model. Use the existing screenshot
tool when visual confirmation matters. Live screenshots validate the tested
Altium version's behavior; future versions can implement processes differently.
The shared screenshot capture also refuses images if Altium loses foreground
before/during capture, preventing a different app from being reported as Altium.

No PreProcess/object-edit/save calls are used for navigation. Errors can leave
the view partially changed; inspect `may_have_view_changes`. A failed content
preservation check reports `may_have_partial_changes:true`. An incomplete
executor reply reports `outcome_unknown:true` and retains its request spec.
Inspect Altium before retrying an uncertain execution.

## Tests

Unit tests: `server/tests/test_library_view.py`. The opt-in live harness copies
both libraries under ignored `.altium-mcp`, switches E08 sections and M01 views,
captures screenshots, checks refusals and verifies unchanged files/buffer states:

```powershell
python dev/test_library_view_live.py --exercise --schlib C:\Design\SQ-CARD.SchLib --pcblib C:\Design\SQ-CARD.PcbLib
```

The fixture must contain the default E08/M01 names or specify `--symbol` and
`--footprint`. A custom sandbox script adds one temporary alternate display
mode on a model-free fixture symbol in the disposable SchLib to test selecting
modes 0/1; existing creation tools do not expose display-mode-count creation.
This is fixture preparation,
not a navigation fallback. The dirty fixture also tests preserving Modified.
Scratch buffers are discarded without saving, and the original project/editor
is restored. Reports, raw native responses and screenshots remain in the run
directory. Restart the connected MCP process to refresh the new tool catalog;
Altium does not need restarting.

## Verification results

Passed on 2026-11-08 in Altium Designer Enterprise 24.4.1 with SQ-CARD open.
Run `library_view_20261108_214457` completed 33 recorded steps and 12 captures:
E08 sections 1/2, alternate display modes 0/1/0 on a disposable fixture, missing
members and out-of-range refusals, M01 2D/3D, first-entry isometric from 2D,
all eight camera presets, and current-member readback. Dirty state was preserved
on the alternate-mode fixture. Rendered model regions changed by 12.8%..26.6%
relative to the Top preset, rejecting the earlier silent camera no-op. Visual
inspection confirmed the fitted isometric, underside and front/opening views.
All 11 guarded files matched their hashes/timestamps; original project document
states matched and both scratch libraries were discarded without saving.

An earlier run correctly refused navigation/cleanup during an active schematic
transaction. Its two scratch buffers were subsequently closed after that
operation finished. Camera API probes also used disposable copies, all closed
without saving. Earlier occluded or incorrect-pose captures are not evidence
for the final implementation; the successful run above supersedes them.

Offline regression: 190 tests, 189 passed and one platform-specific skip.
The fresh-process MCP catalog exposes the complete signature.

Custom-script scope was limited to API/camera dispatch exploration and
disposable alternate-mode fixture preparation. Production navigation uses
`set_library_view(library_path, component_name, ...)`. A future creation-tool
extension `create_schematic_symbol(..., display_mode_count=...)` could replace
the fixture-preparation snippet; this task does not add that creation feature.
