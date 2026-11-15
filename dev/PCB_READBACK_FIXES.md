# STEP references, PCB constants and layer aliases

These fixes update existing tools. They never save Altium documents.

## Saved STEP placement

`get_footprint_primitives(library_path, footprint_name)` compares Windows model
references case-insensitively, normalizing slashes and relative path segments.
It reserves exact references before trying a filename fallback. The STEP source
file does not need to exist for reading an embedded model's saved placement.

| Live reference | Saved reference | Result |
|---|---|---|
| `C:\models\USB.step` | `c:/models/USB.STEP` | Exact normalized reference |
| `C:\models\USB.step` | `USB.step` | Filename fallback |
| `USB.step` | `C:\models\USB.step` | Filename fallback |
| `C:\one\USB.step` | `C:\two\USB.step` | Unmatched; distinct qualified paths |
| `USB.step` | Two different directories or poses | Ambiguous; no pose guessed |

Matching consumes each saved record at most once. Duplicate identical references
and poses are interchangeable; duplicate references with different poses are
ambiguous. Competing live references are also checked before filename fallback.
Iterator order cannot choose between different models.

Each `bodies_3d` entry includes:

- `placement`: `saved library file`, `not in the saved library file`, or
  `ambiguous saved model reference`.
- `placement_match`: `full_path` (exact normalized reference, including an exact
  basename), `file_name` (fallback), `unmatched`, or `ambiguous`.
- `rotation_x/y/z` in degrees and `model_z_offset` in mils, or null without a match.

These remain **saved-file snapshots**, not live rotation readback. A dirty buffer
can have a different pose. This change does not make `IPCB_Model.GetState`
out-parameters readable and does not trigger save/reopen.

## PCB API constants

```python
get_pcb_api_constants(names=["eRound", "eRounded", "eNoLayer", "eMultiLayer"])
```

The whitelist contains `eRound`, `eRounded`, `eRectangular`, `eOctagonal`,
`eRoundedRectangular`, `eCacheManual`, `eTopLayer`, `eBottomLayer`, `eTopOverlay`,
`eBottomOverlay`, `eMechanical13`, `eMechanical15`, `eMultiLayer`, and `eNoLayer`.
Requests outside it fail before dispatch; arbitrary expressions are not evaluated.

Each identifier is evaluated separately in the running Altium version. One
unavailable constant does not prevent checking the remaining names. The result
includes `constants`, `availability`, `checked_in_running_altium`, `saved=false`
and verification. Availability entries have these distinct meanings:

| Field | Meaning |
|---|---|
| `available` | The requested identifier exists and was evaluated directly |
| `resolved` | A numeric result was obtained directly or through the explicit alias |
| `native_name` | Identifier that supplies the numeric value |
| `alias` | The tool used its compatibility alias |
| `native_error` | Original native evaluation error, when present |

Altium AD24 reports `Undeclared identifier: eRound`; the tool resolves that
previously advertised spelling through `eRounded=1`, explicitly returning
`available=false`, `resolved=true`, `alias=true`, `native_name="eRounded"`.
`eNoLayer=0` is a valid result. No numeric fallback is hardcoded. An unresolved
name is omitted from `constants`, remains described in `availability`, and makes
`success=false`. Python validates both complete and partial native replies.

Altium's [PCB API types reference](https://www.altium.com/documentation/altium-dxp-developer/pcb-api-types-reference)
lists `eRounded` in `TShape`; availability in the installed version is established
by the runtime evaluation, not inferred from that documentation.

## Shared PCB layer names

Python command payloads, footprint batch specs, selectors, library validation
and native PCB layer conversions now use shared normalization. Responses use
canonical names in layer fields. Display names of custom stack layers remain
available; native board lookup still resolves them against the board.

| Accepted spellings, case-insensitive | Canonical name |
|---|---|
| `Multi Layer`, `Multi-Layer`, `MultiLayer`, `eMultiLayer` | `Multi-Layer` |
| `Mechanical13`, `Mechanical 13`, `Mechanical Layer 13`, `eMechanical13`, `mech-13` | `Mechanical 13` |
| `eTopLayer`, `top-layer` | `Top Layer` |
| `eTopSolder`, `Top Solder Mask` | `Top Solder Mask` |
| `eMidLayer3`, `Mid Layer 3` | `Mid Layer 3` |
| `eNoLayer`, `No-Layer` | `No Layer` |

Spaces, hyphens and underscores are normalized for recognized aliases. Text,
model paths and unrelated string fields are preserved. Unknown names still need
native resolution and cannot silently become a destination layer. `No Layer`
is allowed as a selector where supported, never as a footprint destination.

Successful layer commands include canonical `accepted_layers`; visibility
commands exclude names reported in `not_found_layers`. Footprint batch creation
and primitive editing also return `accepted_layers` despite using spec files.
Legacy primitive Layer writes remain limited to Mechanical 1..16. The visibility
tool retains V7 lookup of enabled Mechanical 1..1024 layers, including aliases for
layers that have custom display names.

## Verification

The opt-in harness uses fresh production Python imports without another MCP
listener. It copies the project's saved PcbLib/PcbDoc into `.altium-mcp`, reads
the saved M01 body, checks both reference forms, creates a temporary alias fixture,
edits its tracks, verifies geometry preservation and refusal cases, and exercises
board visibility. It closes both copies with explicit discard and checks original
document states and file hashes/mtimes. It never writes into the PCB project.

```powershell
python -m unittest server.tests.test_pcblib_file server.tests.test_footprint_editor server.tests.test_pcb_layers server.tests.test_script_bundle
python dev/test_pcb_readback_fixes_live.py --exercise
```

Reconnect the existing MCP server to load updated Python normalization and the
expanded constant whitelist. Native script bundles rebuild automatically.

### 2026-11-14 results

- 518 automated tests: 517 passed, one skipped for the host ANSI code page.
  The legacy interactive `test_altium_script` class requires keyboard input and
  was excluded. Live checks for these changes run separately in the opt-in harness.
  Evidence: `.altium-mcp/pcb_readback_fixes_regression.log`.
- 24 live Altium steps passed in the open SQ-CARD session, including all 14
  constant names, the saved `GSWITCH_GT_USB_7010ASV` M01 STEP body, both model
  reference forms, batch/edit layer aliases, refusal cases and board visibility.
  M01 returned rotation X/Y/Z and Z offset as zero, matching its saved records.
- Both scratch documents were closed with discard. Original document states
  and all guarded file hashes/mtimes were unchanged; no Altium document was saved.
  Evidence: `.altium-mcp/pcb_readback_fixes_20261114_071330/report.json`.

These checks used dedicated production tools; no ad hoc DelphiScript was needed.
