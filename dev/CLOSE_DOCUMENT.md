# Close a document or project

`close_document(document_path, require_clean=True)` closes an exact open target
without saving. Paths must be absolute Windows file paths. No disk file or parent
directory is required: an editor can outlive its deleted file. Path comparison is
case-insensitive and accepts either separator.

The previous toolkit had no close tool. Native `Client.CloseDocument` accepts an
exact `IServerDocument`, including a project's client document. A generic
`WorkspaceManager:CloseObject` process is unnecessary. `open_document`,
`open_project` and `select_project` do not provide closing as a side effect.

## Behavior

- The default refuses a target marked Modified and leaves its buffer open.
- For `.PrjPcb` and `.LibPkg`, preflight checks the project plus all its open
  logical source and generated documents before closing anything. Open child
  documents close first, followed by the project.
- `require_clean=False` explicitly discards unsaved changes, including project
  membership/options. Use it only when the user authorizes discarding that data.
- An already-closed or missing target succeeds with `closed=true`,
  `already_closed=true`, `changed=false`. Nothing is opened to locate it.
- Ordinary editor closing retains saved project source membership. Closing a
  project unloads it; neither operation deletes disk files.
- Script projects/documents (`PrjScr`, `pas`, `dfm`, `vbs`, `js`) are rejected to
  protect the running executor. Other loaded editor documents use their exact
  client handle. Project types other than PrjPcb/LibPkg are unsupported.

```python
# Refuse unsaved changes; close this editor, regardless of the active tab.
close_document(document_path="C:/Design/Controller/Display.SchDoc")

# Preflight the project and its open children, then close them without saving.
close_document(document_path="C:/Design/Controller/Controller.PrjPcb")

# Only after discarding this scratch buffer has been authorized:
close_document(document_path="C:/Scratch/Test.SchLib", require_clean=False)
```

## Results and failure handling

Native responses include `success`, `document_path`, `is_project`, `require_clean`,
`closed`, `already_closed`, `changed`, `was_modified`, `saved=false`,
`project_saved=false` and:

| Field | Meaning |
| --- | --- |
| `affected_documents` | Open/Modified state of the exact closing scope before execution. |
| `blocked_paths` | Modified buffers that block a clean-only request. |
| `closed_paths` | Buffers confirmed closed by readback. |
| `discarded_paths` | Closed buffers whose Modified flags were explicitly cleared. |
| `remaining_open_paths` | Affected buffers still open after refusal or failure. |
| `unrelated_documents_preserved` | Other preexisting non-script buffers retained their open/Modified state. |
| `may_have_partial_changes` | An operation started and subsequently failed. |
| `close_outcome_unknown` | A transport failure prevented confirmation. |

The Python wrapper adds `verification` to successful responses and rejects a
wrong target, incomplete closure, reported save or unrelated-state change.
It preserves native refusal details even when the bridge lifts the native failure
to its outer envelope. Validation failures occur before an Altium call.

Preflight does not make execution atomic. A native error may close some buffers
before stopping. Modified flags cleared by this request are restored on buffers
that remain open; closed/discarded data cannot be restored by this tool. On a
timeout, inspect actual state before retrying. The unique `spec_file` is retained
when completion is unknown, since a delayed script may still consume it.

Clean checking uses Altium's native Modified flags. An API edit that failed to
mark its buffer dirty is outside that guarantee. Open schematic transactions
block closing; the tool does not save, reload or repair undo groups.

## Implementation and verification

`server/document_closing.py` handles validation, UTF-8 BOM spec transport and
readback. `server/AltiumScript/document_management.pas` resolves the target,
preflights its scope, closes exact handles and escapes Unicode response JSON.
The command bypasses legacy focus recovery and is classified as mutating, so
bridge timeouts are not treated as retryable reads.

API reference: [IClient.CloseDocument](https://www.altium.com/documentation/altium-dxp-developer/iclient-interface).
The exact project's client document provides its Modified flag; the IProject
interface alone does not expose that flag in the tested Altium version.

Run `python -m unittest server.tests.test_document_closing -v` for offline checks.
See [live results and reproduction](CLOSE_DOCUMENT_LIVE_TEST.md).
