# Opening projects and libraries

```python
open_project(project_path="C:/Design/Controller/Controller.PrjPcb")
get_project_info()
open_document(document_path="C:/Design/Libraries/Parts.SchLib")
open_document(document_path="C:/Design/Libraries/Parts.PcbLib")
```

These tools cover resuming work after a project or library was closed manually.
Already-open targets are reused; there is no reload operation and unsaved data
is preserved. All paths are absolute Windows paths and compared by full path,
case-insensitively. The parent directory must exist.

## Tools and existing alternatives

| Tool | Target | Behavior |
| --- | --- | --- |
| `open_project(project_path)` | `.PrjPcb`, `.LibPkg` | Open a closed saved project and make it current, or select the exact already-open project. |
| `open_document(document_path)` | `.SchLib`, `.PcbLib`, `.SchDoc`, `.PcbDoc` | Open and focus a closed existing document, or focus its in-memory instance. |
| `select_project(project_path)` | Already-open `.PrjPcb`, `.LibPkg` | Select only; never open a closed project. |

Before these tools, library lookup/inspection could open a library implicitly:
`search_library_symbol`, `get_symbol_primitives` and `get_footprint_primitives`
accept a library path. Their extra search/read work is unnecessary when the goal
is simply to open the editor. There was no dedicated MCP project-opening tool.

Opening a library here opens its source editor; it does not install or compile
an integrated library or connect an Altium Workspace. Other project/library file
types are outside these tools' supported extensions.

## Guarantees and context

- A closed target must exist on disk. It is checked again in Altium before the
  native open call; a missing path cannot silently create a blank file/project.
- An already-open named unsaved target can be reused even when no file exists
  yet. It is resolved before any disk-existence check.
- No saving, explicit compilation, closing other targets, reloads or membership
  edits are requested. The tools do not clear Modified.
- `open_project` checks the current project and the open/modified states of all
  preexisting supported documents. An already-open project's source-document
  states are also compared before and after reuse.
- `open_document` checks the actual active editor path/kind and verifies that
  an already-open document keeps its Modified state. Altium may mark a newly
  loaded legacy/renamed document modified during normal loading; that flag is
  reported and retained.

Altium may restore source tabs when loading a closed project. The tools never
call a source-document open operation on a project that is already open.

The current project and active editor tab remain separate contexts.
`open_project` establishes the project used by project-level readers.
`open_document` displays the exact document and may select its owning project or
Free Documents. Use `select_project` afterwards if project-level context matters.

## Results and failures

Both tools return `success`, `opened`, `already_open`, `saved=false` and
`verification`. `opened=true` means the target was loaded in this call;
`already_open=true` means its in-memory instance was reused.

`open_project` returns the requested, previous and active project paths,
`project_saved=false`, `compiled=false`, and document-state snapshots.
`documents_before` is empty for a previously closed project; its loaded sources
appear in `documents_after`. These lists need not match for a newly opened
project. `preexisting_documents_before`/`after` track only previously open
supported documents and must match. Its `changed` flag describes project focus.

`open_document` returns the exact document path/kind, previous/active editor
paths, `focused`, disk existence and Modified flags. Its `changed` flag means
that a file was opened or the active editor changed.

Native/bridge failures can occur after a target has opened or focus has changed.
Inspect the returned state before retrying. Project failures include
`may_have_changed_focus` and, where relevant, `may_have_opened_project`.
Document failures retain the lifecycle API's `may_have_partial_changes` flag.
Uncertain bridge failures retain their unique input spec for diagnosis; no
rollback or automatic close is attempted.

## Implementation and loading

The tools reuse the existing document/project lifecycle validators and bridge.
The native implementation is in `server/AltiumScript/document_management.pas`.
It uses [`IWorkspace.DM_OpenProject(path, True)`](https://www.altium.com/documentation/altium-dxp-developer/workspace-manager-api)
and [`IClient.OpenDocument` / `ShowDocument`](https://www.altium.com/documentation/altium-dxp-developer/iclient-interface).
Already-open targets are found by full path before either native open method.

Opening bypasses the legacy document/project focus fallbacks. Successful project
opening updates the toolkit context hint only. This hint now uses an ASCII JSON
escape format (`json:` prefix) to preserve Unicode paths while still accepting
old raw-path hints. `get_project_info` also escapes its JSON before ANSI output
so a Unicode project path survives the next read.

Reconnect/restart the MCP server to expose the new tools. Reopen the updated
script project if Altium has stale script buffers; do not save old buffers over
the updated files.

## Verification

```powershell
python -m unittest server.tests.test_open_targets server.tests.test_project_selection server.tests.test_document_management -v
python dev/test_open_targets_live.py
```

The live runner copies the current project's libraries and one sheet into a
unique toolkit `.altium-mcp/open_targets_<timestamp>` directory. It writes two
minimal test project files there, opens them and the copied documents, and
verifies repeat calls, Unicode paths, rejection paths and preservation of a
deliberately unsaved parameter in the scratch SchLib. It restores the original
project/editor and compares source and fixture disk hashes/timestamps. Test
copies remain open for inspection; no user document is closed or edited.

See [live results and limitations](OPEN_TARGETS_LIVE_TEST.md). Do not run other
Altium calls concurrently with the harness.
