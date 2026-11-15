# Close-document live verification

Verified on 2026-11-05 in Altium Designer Enterprise 24.4.1 with the open
`C:/workspace_altium/SALT-FMUV6X-JETSON/SALT-FMUV6X-JETSON.PrjPcb`.
The original `Supply.SchDoc` was already Modified before testing and remained so.

## Results

The final run completed 68 recorded calls, including cleanup and context restore.

| Check | Result |
| --- | --- |
| Default close of the actual dirty Supply.SchDoc | Refused; no buffer closed or Modified flag cleared. |
| Default close of its actual project | Refused before any child closed; dirty path returned. |
| Nonactive scratch SchDoc/PcbDoc/SchLib/PcbLib | Exact target closed; unrelated open/Modified states preserved. |
| Unicode names, uppercase paths and forward slashes | Passed for documents and Unicode-named projects. |
| Repeat close and nonexistent/deleted-parent path | Idempotent success; no files opened or created. |
| Blank named unsaved sheet | Closed; no SchDoc written. |
| Scratch sheet with an added unsaved text object | Default refused; explicit `require_clean=False` closed and reported discarded data. |
| Clean nonactive PrjPcb and LibPkg | Exact project unloaded without closing the active original project. |
| PrjPcb/LibPkg with unsaved source membership | Default refused; explicit discard closed each project and reported its path. |
| Project with clean and dirty open child sheets | Default closed neither child; explicit discard closed both children and the project. |
| Nonboolean policy and script-project target | Rejected before dispatch. |
| Cleanup | All scratch documents/projects closed. Original project/editor context restored. |
| Original disk verification | SHA-256 and modification timestamps unchanged for 21 files, including 14 SchDocs, PCB, both libraries and PrjPcb. |
| Scratch disk verification | Hashes and timestamps unchanged for all 10 copied/fixture files. |
| Original project document states | All open/closed/Modified states unchanged. |

No Altium save command was called. Fixture project files were created as local
test inputs and saved source documents were copied into the toolkit directory;
neither original nor scratch sheets were saved. Existing unsaved user data was
tested only for clean-only refusal. Discard tests were confined to unique scratch
paths. API failure after a partial close was not induced live; transport and
readback-failure handling have offline tests.

Evidence: `.altium-mcp/close_document_20261105_001401/report.json`, with individual
responses beside it. Runtime files remain ignored by Git. Offline regression:
61 tests passed across closing, document management, open targets, project
selection, script bundling, bridge locking, merge compatibility, request format
and server identity. Fresh MCP registration confirmed `require_clean=True` by
default and mutating-command classification.

## Reproduce

```powershell
python dev/test_close_document_live.py --project "C:/Design/Controller/Controller.PrjPcb" --exercise
```

Run serially with other Altium work. The harness uses the currently installed
bridge and its content-versioned script bundle. It requires an open project with
existing SchDoc/PcbDoc/SchLib/PcbLib sources and a clean open sheet. It copies saved
sources to a unique toolkit `.altium-mcp/close_document_<timestamp>` directory,
creates temporary PrjPcb/LibPkg fixtures, edits scratch sheets in memory, then
closes them. Cleanup force-closes only paths verified inside that unique directory.
Without `--exercise`, no Altium operations are performed.

## Custom API probes

The dedicated catalog had no close command. `run_altium_script` was used to read
the original project's client document and Modified flag, confirm generated-source
enumeration, and close one clean empty scratch project by its exact client
handle while the original project remained current. All probes completed.
They established the reusable `close_document(document_path, require_clean=True)`
implementation. No additional permanent tool is needed for these operations.
The remaining live checks called the new Python wrapper and existing dedicated
open/create/edit/project-selection tools directly.
