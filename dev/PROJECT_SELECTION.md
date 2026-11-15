# Selecting an open project

```python
select_project(project_path="C:/Design/Controller/Controller.PrjPcb")
get_project_info()
get_project_components(include_parameters=True)
```

`select_project` makes exactly one already-open `.PrjPcb` or `.LibPkg` current.
It accepts an absolute Windows path, matches the full path case-insensitively,
and verifies Altium's focused project after selection. Basename/partial matches
and script projects are not accepted. The parent directory must exist; an open,
named project does not need to have a project file on disk.

When the project may have been closed manually, use
[`open_project(project_path)`](OPEN_TARGETS.md) instead. It opens an existing
closed project and reuses one that is already open. `select_project` deliberately
keeps its already-open-only contract.

Selection does not open a closed project, open/show/close/reload a document,
compile, save anything, change membership or clear Modified. It does not require
an open sheet or any source document in the project. Repeating selection returns
`changed=false`. A closed or ambiguous target fails without selecting another
project; `available_project_paths` lists the open PCB/library project paths.

The current project and active editor document are separate Altium contexts.
This tool selects the project for `get_project_info`, `get_project_components`
and other project-level commands. It does not make a PCB or library editor tab
current; editing tools retain their own document targeting rules. A later
`get_project_components` call compiles in memory as documented for that tool;
selection itself never compiles.

## Existing analogues and implementation

There was no dedicated MCP project-selection tool. The private bridge helper
`EnsureDesignProjectFocused` recovered a previously used project by showing one
of its documents, and `dev/test_document_management_live.py` already used
`IProject.DM_SetAsCurrentProject` during teardown. Neither exposed exact-path
project selection to an MCP client.

The new tool wraps the native
[`IProject.DM_SetAsCurrentProject` API](https://www.altium.com/documentation/altium-dxp-developer/iproject-interface).
`server/project_selection.py` validates paths, sends a unique UTF-8 BOM spec
through the existing bridge and checks native readback.
`server/AltiumScript/document_management.pas` resolves the exact open project,
records its document open/modified states and invokes the native method only
when a different project is current. Unicode JSON is escaped before ANSI output.

The command bypasses both legacy focus heuristics before selection. A successful
selection updates only the toolkit's existing `last_project.txt` context hint
under the exchange directory. Project recovery also now uses the native method
instead of showing a document, allowing the selected context to survive script
editor recovery even when no sheets are open. No design file is written.

## Results and failure handling

Native results include:

- `success`, `project_path`, `previous_project_path`, `active_project_path`.
- `changed`: whether the focused project identity changed.
- `saved=false`, `project_saved=false`, `compiled=false`.
- `documents_before` and `documents_after`: each source document's path, kind
  and state (`closed`, `open` or `modified`).
- `documents_unchanged` and `verification`: selection identity and unchanged
  document states are checked before success is reported.
- `available_project_paths`; `error` on failure.

This is focus selection, so uncertain failures use `may_have_changed_focus`
rather than claiming document edits. A bridge timeout retains the unique
`spec_file`, because a delayed invocation could still change the current project.
Read current context before issuing subsequent operations. No document save
recovery or rollback is attempted.

Restart/reconnect the MCP server to expose the new tool. If Altium holds stale
script buffers, reopen `Altium_API.PrjScr` without saving those buffers over the
updated source. The live test uses a fresh copy of the script project to avoid
this cache while testing the current implementation.

## Verification

```powershell
python -m unittest server.tests.test_project_selection -v
python dev/test_project_selection_live.py --project 'C:\Design\Controller\Controller.PrjPcb'
```

The live harness requires the target to already be open. It switches to the
target, exercises repeat/case-insensitive selection and rejection paths, then
restores the original project in `finally`. It compares document states and
SHA-256 plus modification timestamps of existing design files in both project
directories. Diagnostics and copied scripts stay under the toolkit's ignored
`.altium-mcp/project_selection_<timestamp>` directory. It never calls editing,
compile or save tools. Do not run other Altium calls concurrently with it.

See the [live verification report](PROJECT_SELECTION_LIVE_TEST.md) for tested
projects, evidence and limits.
