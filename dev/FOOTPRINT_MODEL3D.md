# Existing footprint 3D models

```python
edit_footprint_model3d(
    library_path, footprint_name, body_index=None, changes=None,
    delete=False, expected_model_file=None, body_id=None,
)
```

Read/edit/replace/delete an existing 3D body in an exact `.PcbLib` buffer. Never
saves, updates placed PCB components or changes project membership. Adding a body
remains `create_footprints_batch` with `EDITFOOTPRINT` / `MODEL3D`. Graphic editors
cannot edit models; `set_footprint_properties` changes native footprint Height
independently of 3D geometry.

## Selection

Omit `changes` and `delete` to inventory live bodies without editing. Lists report
filename, embedding, model type, X/Y in mm, native height extents, projected
bounds, layer and `body_address`. Prefer `body_id=<body_address>` when editing:
Altium can reorder its group iterator after edits. IDs refer to this live buffer;
stale IDs fail. Read again after switching members or reopening the document.

Alternatively, `body_index` selects a zero-based position in the current
inventory. An edit without either selector requires exactly one body. If both are
given they must agree. `expected_model_file` optionally guards the exact native
filename, including case/path. It cannot distinguish two bodies with the same
source. Do not reuse an old index after another edit.

The library path must be absolute and identify a saved or already-open named
PcbLib. Reuses dirty buffers without reload. The footprint name must match exactly
once, case-insensitively. The tool focuses the library/member and leaves that
view selected.

## Changes

| Field | Meaning |
|---|---|
| `x_mm`, `y_mm` | Absolute model origin relative to the footprint origin, not a delta |
| `rotation_x`, `rotation_y`, `rotation_z` | Native model rotations in degrees |
| `z_mm` | Native model Z offset, not the body's minimum height |
| `embed` | Boolean: embedded or linked model |
| `model_path` | Nonempty existing absolute `.step`/`.stp` source for replacement |

X/Y and embedding can be changed independently; omitted values stay unchanged.
Linear values must be finite and within +/-1000 mm; rotations within +/-360000
degrees. STEP units are not rescaled. Update/replace supports generic STEP bodies;
delete also accepts other body types.

**Rotation/Z edits require all four pose fields together. Replacement also
requires that complete pose.** `IPCB_Model.GetState` returns these values through
out-parameters that DelphiScript does not receive. The tool cannot safely
preserve omitted pose values from a dirty buffer, and never substitutes saved
file placement for live state. Position/embedding-only edits do not call
`SetState` or reconstruct the body.

Replacement loads a new model before editing, retains the existing body/layer,
preserves omitted X/Y and defaults to the old embedding value. Linking requires
an accessible absolute source. For an embedded model with only a basename,
supply `model_path` and a full pose to avoid a broken link. The STEP source is
read, never overwritten. Altium model-factory failures stop before mutation.

`delete=True` is exclusive with nonempty `changes`. It detaches just the target
from both the footprint group and the displayed library board, preserving other
bodies and primitives. Missing, stale and ambiguous targets fail without editing.

## Examples

```python
edit_footprint_model3d(r"C:\project\Parts.PcbLib", "USB-C")

# Use body_address from the latest inventory. Move only X.
edit_footprint_model3d(r"C:\project\Parts.PcbLib", "USB-C", body_id="123456789",
    expected_model_file="Connector.step", changes={"x_mm": 1.25})

# Replace and embed; preserve X/Y and explicitly set the complete pose.
edit_footprint_model3d(r"C:\project\Parts.PcbLib", "USB-C", body_id="123456789",
    changes={"model_path": r"C:\models\Connector_v2.step", "embed": True,
             "rotation_x": 0, "rotation_y": 0, "rotation_z": 90, "z_mm": .25})

edit_footprint_model3d(r"C:\project\Parts.PcbLib", "USB-C", body_id="123456789",
    delete=True)
```

Use the filename actually returned by inventory: Altium may return a basename or
absolute path. Object addresses in examples are placeholders.

## Verification and failures

One native undo group surrounds an actual edit. Position/embedding no-ops and
read mode preserve Modified. Full pose/replacement requests always run and mark
the buffer modified because pose equality cannot be independently read.

Native snapshots verify X/Y, filename, embedding, body count/identity, unchanged
other bodies, footprint Height/description and non-body primitives. Library file
hash/mtime must remain unchanged. `pose_applied` confirms values passed to Altium,
**not independent numeric pose readback**; `verification.pose_readback_available`
is false. Native bounds/heights expose geometric effects but a passed verification
does not promise independent STEP payload, rendering or save/reopen comparison.

Completed failures report `may_have_partial_changes`; there is no automatic
rollback. For `outcome_unknown=True`, do not retry or send cleanup blindly:
inspect Altium and the retained request first. There is no save option.

## Tests

Offline: `python -m unittest server.tests.test_footprint_model3d`.

```powershell
python dev/test_footprint_model3d_live.py --exercise `
  --pcblib C:\project\Parts.PcbLib --footprint USB-C
```

The opt-in harness edits only a disposable copy and discards it afterwards. The
default fixture is `GSWITCH_GT_USB_7010ASV` in the open SQ-CARD library, with an
embedded STEP in `Library/Models/0` and an initial zero pose. It extracts that STEP
through file I/O without a CAD save. Checks cover clean/dirty no-ops, refusals,
X/Y preservation, geometric effects of X/Y/Z rotations and Z, embedding both
ways, replacement, multiple bodies, stale IDs, deletion and member switching.
Original document states and project/scratch file hashes/mtimes are compared.
Unknown completion stops further dispatch, including cleanup.

2026-11-10 validation: 254 regression tests, 253 passed and one skipped for the
host ANSI code page. The final live run passed all 40 steps in the open SQ-CARD
session: `.altium-mcp/footprint_model3d_20261110_180532/report.json`. Native
bounds/heights independently confirmed the test rotations and Z effects. All 13
guarded files retained their hashes/mtimes, original document states were equal,
and the dirty scratch buffer was closed with explicit discard. No CAD save ran.

Deletion initially returned after member switching even when immediate group
readback looked correct. The final implementation detaches the body from its
component and board, clears its component reference and calls
`PCBServer.DestroyPCBObject`; both single/last-body deletion now survive member
switching. The separate focused check also passed in
`.altium-mcp/model3d_delete_20261110_180356/report.json`.

Tests used fresh imports of the production handlers, without a second MCP
listener. Restart the existing MCP connection to load the new Python tool
catalog. Native script bundles are rebuilt automatically from their content.
