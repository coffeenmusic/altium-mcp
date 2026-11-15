# Document lifecycle live verification

Verified on 2026-11-03 in Altium Designer Enterprise 24.4.1 with the already-open
`C:/workspace_altium/SALT-FMUV6X-JETSON/SALT-FMUV6X-JETSON.PrjPcb`.

## Results

The final run completed all 34 recorded calls and cleanup:

| Check | Result |
| --- | --- |
| Create named SchDoc, PcbDoc, SchLib and PcbLib | All opened in memory; no files written by creation. |
| Repeat creation at an open unsaved path | Rejected without replacing the document. |
| Save preview for all four document kinds | Passed; no disk writes or Modified changes. |
| Actual save of scratch SchLib and PcbLib | Passed; nonempty native files and cleared Modified flags. |
| Repeat save of each existing scratch library | Passed, including the existing-file attribute check. |
| Unicode SchLib filename | Create, save, repeat and include passed through UTF-8 specs and escaped response JSON. |
| Include all four kinds in the exact open project | Passed despite the temporary documents taking focus. |
| Repeat inclusion | `changed=false`; logical document count stayed constant. |
| Missing source / closed save target / kind mismatch / unopened project | All rejected. |
| Cleanup temporary memberships | Project returned from 22 to its original 18 documents. |
| Original document open/modified states | Unchanged across the final run. |
| Display.SchDoc object snapshot | All 382 supported objects unchanged. |
| Original disk contents | SHA-256 unchanged for all 40 original files directly in the project directory, including 14 SchDocs, PCB, both libraries and PrjPcb. |
| Scratch schematic and PCB | Both still unsaved; no SchDoc or PcbDoc was written. |

Actual sheet saving was deliberately not attempted. Library-package inclusion
(`.LibPkg`) has boundary tests but was not exercised live. The project was never
saved. Its membership was restored without clearing Altium's dirty indicator.
Scratch documents remain open under Free Documents for inspection.

Final machine-readable evidence is in the ignored toolkit directory:
`.altium-mcp/document_management_20261103_184300/report.json`. Individual native
responses, original snapshots and cleanup logs are alongside the report.

## Reproduce

Use the toolkit's configured Python environment with Altium running:

```powershell
python dev/test_document_management_live.py --project "C:/Design/Controller.PrjPcb" --exercise
```

The harness reads the first saved schematic from the requested project's file
and focuses it with `edit_schematic(..., operations=[])` to establish context.
It creates a unique toolkit `.altium-mcp/document_management_<timestamp>` directory,
copies the current script project there to avoid Altium's script cache, creates
four scratch documents and saves only the two scratch libraries. Source documents
are never edited or saved. Temporary membership is removed in `finally` using
live document paths under that unique directory; Unicode literals are not used
in the cleanup script. The original sheet is focused again at the end.

Without `--exercise`, the harness only prepares local diagnostics and reads disk
hashes. Do not run other Altium MCP calls concurrently with the live harness.

## Custom scripts used

| Operation | Reason | Reusable tool | Outcome |
| --- | --- | --- | --- |
| Probe named unsaved document creation | No dedicated lifecycle command existed; establish `Client.OpenNewDocument` behavior. | `create_document(document_path, document_kind=None)` | Verified; implemented. |
| Probe saving one scratch library | Existing `SaveDoc` required custom code and changed Modified before saving; establish direct `DoFileSave` behavior. | `save_document(document_path, dry_run=False)` | Verified; implemented. |
| Remove only temporary test memberships and recover exact original context | Test teardown; no dedicated removal command exists. | Candidate: `remove_document_from_project(project_path, document_path)` | Verified removal and restored membership; no general removal tool added. |
| Check executor after a rejected prototype | Validate recovery before continuing API exploration. | Existing `run_altium_script(script, timeout_seconds)` | Executor ready; no replacement tool needed. |

Automated regression: 91 tests run, 90 passed, one CP1252-specific test skipped
on the host's CP1251 configuration. Python syntax, documentation examples,
`git diff --check` and the repository scan for literal Cyrillic also passed.
