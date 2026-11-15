# Cross-sheet cloning live verification

Tested on 2026-11-05 in Altium Designer Enterprise 24.4.1 with the open
`C:/workspace_altium/SALT-FMUV6X-JETSON/SALT-FMUV6X-JETSON.PrjPcb`.

## Confirmed checks

| Check | Result |
| --- | --- |
| Two instances copied between scratch copies of Display.SchDoc | Passed; two new component roots and fresh owned-object UIDs. |
| Unsaved comment/parameter overrides containing Unicode and delimiters | Preserved exactly. |
| Rotation/mirror, parameter display, pins, primitive counts and model links | Preservation readback passed. |
| Source snapshot and Modified flag | Unchanged by successful clone calls. |
| Existing target objects and prior unsaved edits | Preserved. |
| A valid selector followed by a missing designator | Rejected with zero copies; target snapshot unchanged. |
| Repeat into occupied target designators | Rejected with zero copies; target snapshot unchanged. |
| New named unsaved destination | Two copies placed; no SchDoc written. |
| Actual unsaved Supply.SchDoc used as read-only source | One instance copied; original snapshot and Modified flag preserved. |
| Same-sheet alias and missing closed target | Rejected. |

The initial project already had Modified `Supply.SchDoc` and
`Power-Sequencer-Draft.SchDoc`. Originals were never clone targets. No save or
compile command was issued. All source/target fixture edits were in memory.

## Initial interrupted multipart run

The `M1` test on SoM.SchDoc selected placed parts 1, 3, 4, 6 and 7. The native log
recorded registration of all five copies into a unique unsaved scratch target,
then the bridge timed out before a complete response. Altium remained busy.
The mutation was not retried, and Altium was not terminated because it holds
unsaved user data. Final source/target readback and scratch cleanup were not
obtained for that interrupted run.

An unnecessary whole-response `DocumentJSON` pass was removed: the common
`JSONEscapeString` already escapes Unicode per value, and rebuilding a large
string character by character can be quadratic. Progress heartbeats and stage
logs were added. This correction passed offline tests and the subsequent live
run below.

Evidence and exact recovery state are stored centrally in
`.altium-mcp/schematic_cloning_20261105_220451/`, including per-call responses,
`recovery_state.json`, original project/sheet snapshots and the retained spec
path. The incomplete run must not be reported as a fully passing live test.

The user's machine restart ended that invocation. The next run used new unique
scratch documents; it did not retry into the previous unknown-outcome target.

## Completed after restart

The fresh 2026-11-05 run passed, including the complete multipart case:

- All five placed `M1` sections (1, 3, 4, 6 and 7) copied successfully, with fresh
  root/owned-object UIDs and verified metadata, pin and primitive preservation.
- Source readback and Modified state remained unchanged. Ordinary two-component
  copies preserved unsaved Unicode/parameter/pose edits; missing selectors and
  target collisions rejected with zero copies.
- A named unsaved destination received copies without a SchDoc being written.
- All four scratch buffers were closed with explicit discard. Read-only sources
  originally closed were closed cleanly again. Original project states, original
  file hashes/mtimes and scratch-copy disk content were unchanged.

Evidence: `.altium-mcp/schematic_cloning_20261105_233600/report.json`, with
`checks_passed`, `original_states_unchanged`, `original_disk_unchanged`,
`scratch_disk_unchanged` and `unsaved_targets_not_written` all true.
The original project had no Modified documents after reboot, so the earlier
dirty-original Supply test was not repeated; a dirty scratch source was tested.
No save or compile command was issued.

## Reproduce

```powershell
python dev/test_schematic_cloning_live.py --project "C:/Design/Controller/Controller.PrjPcb" --source "C:/Design/Controller/Display.SchDoc" --multipart-sheet "C:/Design/Controller/SoM.SchDoc" --multipart-designator M1 --exercise
```

The harness copies saved sources into a unique toolkit `.altium-mcp` directory,
creates unsaved destinations, records disk baselines and compares original
document states. After an unknown executor outcome it records the incomplete run
and skips further Altium calls. Without `--exercise`, no Altium action is taken.

Offline regression: 66 tests passed for cloning, script bundling, schematic
editing, metadata, opening/closing and bridge compatibility.

## Custom scripts

Read-only API probes inspected multipart root UIDs and owned parameter/model UID
trees because existing tools could not clone from an explicit different source
sheet. The probes succeeded and informed
`clone_schematic_components(source_sheet, target_sheet, designators)`.
Implementation tests called the new Python wrapper and existing dedicated tools;
the initial large multipart invocation was interrupted; the new run passed.
