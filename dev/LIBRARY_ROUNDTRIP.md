# verify_library_roundtrip

```python
verify_library_roundtrip(
    library_paths, save=False, max_differences=100,
    coordinate_tolerance_mils=0.001,
)
```

Capture whole-library native contents, close and reopen the exact files in
Altium, and compare the reopened contents. Existing getters/save/close tools
cover individual steps; this tool adds preflight, snapshots, comparison,
backups, STEP evidence and restoration in one controlled operation.

## Save policy

- `library_paths`: one absolute SchLib/PcbLib path or 1..8 distinct paths. Every
  member is compared; there is no selected-component-only save. Named open
  unsaved libraries are accepted only with explicit saving.
- **`save=False` is the default.** All targets must already be saved and clean.
  A dirty/unsaved target refuses the batch before any target is saved or closed.
  No Modified flag is cleared to force a close, and no changes are discarded.
- **`save=True` explicitly authorizes saving all contents of these libraries.**
  Use it only when the user authorized saving those exact targets. Existing
  files get verified byte-for-byte backups before native execution; new files
  have no preimage. Local writable paths are required. Each exact document's
  `DoFileSave` is checked before a clean close. No Save All, sheet/project save,
  compilation, project membership edit or automatic rollback occurs.

All targets are preflighted/snapshotted before the first save or close. A later
API failure can still leave earlier libraries saved: inspect `saved_paths`,
`save_attempted_paths`, `reopened_paths`, per-library editor states and backups.
The backup is a file preimage, not a copy of earlier unsaved buffer contents.
Never restore it automatically over a user's subsequent changes.

Originally open libraries remain open; initially closed ones are closed again
when clean. Native member, part and alternate display selections are restored.
The previously active document and focused project are restored; unrelated
open documents retain their states. This restores document focus, not exact
zoom, camera pose, window layout or undo history across a close/reopen.
An active schematic transaction blocks the operation without forced recovery.

## Compared contents

| Area | Comparison |
|---|---|
| SchLib geometry | Every member's supported primitive geometry, pins, ownership and available graphic/font fields |
| Symbol data | Native Description/Comment/Designator/ComponentKind, part/display counts, parameters including displayed properties |
| Bindings | Model names/types/flags, datafile links and pin-to-pad maps |
| PcbLib geometry | Every footprint's native Height/Description, pads/holes/stacks, graphics, mask/paste settings and testpoint flags exposed by the established getter |
| Native 3D bodies | Model file/embed flag, available origin, body heights and layer |
| Saved STEP | Body rotations/Z offset and SHA-256/size of used embedded or linked model payloads |

Iteration order is ignored while duplicate objects/parameters remain significant.
Polyline/polygon vertex order is preserved. PCB track endpoints and recognized
pin-map ordering are normalized. Embedded model filenames use case-insensitive
basenames; binding locations remain explicit. Altium-generated SchLib primitive
UIDs and request-local handles are ignored: a no-save reopen was observed to
regenerate them. Pin numbers/names, real bindings and custom parameters named
`_id` or `library_path` are not ignored.

Coordinates use the established getter's library mils. Numeric coordinate
tolerance is 0..1 mil, default 0.001; mm fields use its mm equivalent. Angle
tolerance is 0.001 degree. Enum/count/string/bool comparisons remain exact.
Normalized exact fingerprints are included; tolerated coordinate rounding can
produce different fingerprints without a significant comparison difference.

The native readers expose a bounded representation, not arbitrary document
serialization. Unknown/unreadable primitives, Full Stack pads and unreported
region hole contours produce coverage gaps. No renderer/kernel comparison,
screen-image comparison or proof of every unexposed custom property is claimed.

## STEP evidence and dirty buffers

The saved PcbLib reader associates body MODELID references with used embedded
model streams. Zlib payloads are decompressed before hashing so recompression
alone does not masquerade as changed STEP. Unrecognized non-zlib payloads are
hashed as opaque bytes; unreadable/oversized evidence remains explicit. Linked
files are hashed when their stored absolute/library-relative location resolves.
The reader does not guess Altium search paths or download missing assets.
Inspection is limited to 128 MiB per model.

**A dirty PcbLib with 3D bodies cannot receive full pre-save STEP equivalence.**
The script API exposes body bindings and some geometry but cannot read all
current model rotations/Z offset/payload bytes from that dirty buffer. Its old
saved file is not substituted for the missing current data. Native before/after
fields are compared, post-save evidence is retained, and this gap gives
`equivalent:null` unless an observed difference already makes it `False`.

A subsequent clean no-save roundtrip can fully compare the now-saved model
evidence. That checks the saved state, not a retroactive proof of inaccessible
earlier dirty STEP data. Missing linked files, extruded/custom bodies without
STEP evidence and unsupported file records likewise remain explicit gaps.

## Results and artifacts

- `success` means the native lifecycle and preservation protocol succeeded;
  it does not mean the data matched.
- `equivalent` is `False` for observed differences, `None` for incomplete
  comparison/coverage, or `True` for matching fully covered exposed contents.
  `coverage_complete` and each library's `coverage_gaps` explain the scope.
- `difference_count` counts all observed differences. `max_differences`
  (1..10000) caps emitted details only; `differences_truncated` remains explicit.
- `verification` checks exact targets, explicit save policy, confirmed reopen,
  editor/selection/context restoration and file-write boundaries. No-save mode
  verifies unchanged input hashes and modification timestamps. Explicit saves
  may change only files in `save_attempted_paths` among the requested inputs.
- `report_path` identifies `.altium-mcp/roundtrip_runs/<id>/report.json`.
  The same run holds `native.json` with full before/after snapshots,
  `step_before.json`, `step_after.json`, and verified `backups` when saving.
  The public result contains differences/digests rather than all primitives.

Incomplete native completion sets `outcome_unknown` and, for explicit saves,
`save_outcome_unknown`. Preserve the request/evidence and inspect the executor
before another command. `saved:false` in an unknown response is not proof that
no file was written. Known preflight refusal is distinguished from timeout.
Keep buffers/files unchanged while a check runs; snapshots cannot prove the
absence of concurrent user or external file edits.

## Examples

```python
# Reopen clean saved buffers without writing either file.
verify_library_roundtrip([
    r"C:\Design\Parts.SchLib", r"C:\Design\Parts.PcbLib",
])

# Only after saving these exact libraries is authorized.
verify_library_roundtrip([
    r"C:\Scratch\Parts.SchLib", r"C:\Scratch\Parts.PcbLib",
], save=True)
```

## Verification

Offline: `server/tests/test_library_roundtrip.py` covers policy/backup failures,
actual content differences, ordering/UID normalization, reserved parameter
names, numeric boundaries, caps, unsupported geometry, STEP placement/payloads,
dirty-buffer gaps and unknown completion. OLE fixtures exercise embedded and
external model evidence plus Unicode field decoding.

```powershell
python dev/test_library_roundtrip_live.py --exercise --schlib C:\Design\SQ-CARD.SchLib --pcblib C:\Design\SQ-CARD.PcbLib
```

The harness saves **only disposable copies and a newly created scratch SchLib**
under the toolkit's ignored directory. Original project files and sheets are
never saved. It invokes production handlers without another listening MCP server.
Restart the connected MCP process to refresh its new tool catalog.

Passed in Altium on 2026-11-10. The 21-step run is recorded in
`.altium-mcp/library_roundtrip_20261110_001324/report.json`: 25 symbols and 25
footprints, including E08 parts/maps and M01's embedded 3,384,961-byte STEP;
default clean reopen, dirty refusal, explicit saves/backups, persisted Unicode
parameters, Height, dirty STEP coverage and a new unsaved library all behaved
as specified. All scratch editors were closed. Original project states and
all nine guarded original file hashes/timestamps remained unchanged.

A final no-save run on all three now-saved scratch libraries confirmed the
latest restoration path, `project_restored:true`, zero differences and complete
coverage: `.altium-mcp/roundtrip_runs/cd71d25c4df847a98868d59b690bde3f/report.json`.
Offline regression: 234 selected tests, 233 passed and one platform-specific skip.
