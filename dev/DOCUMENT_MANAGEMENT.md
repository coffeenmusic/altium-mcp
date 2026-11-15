# Document lifecycle

## Tools and existing alternatives

The toolkit previously had no dedicated lifecycle tools. `build_schematic`
creates a populated new sheet, but is not an empty-document factory. Symbol and
footprint creation require an existing library. `run_altium_script` exposes the
`SaveDoc` helper, but requires custom code and marks the target modified first.
`dev/SHEET_DUPLICATION.md` describes project inclusion through the native API.

The following tools now cover explicit file creation, saving and project inclusion:

| Tool | Behavior |
| --- | --- |
| `create_document(document_path, document_kind=None)` | Create a named document in Free Documents, without writing a file. |
| `open_document(document_path)` | Open/focus an existing document, reusing it without reloading when already open. |
| `save_document(document_path, dry_run=False)` | Save only that exact open document to its assigned filename. |
| [`close_document(document_path, require_clean=True)`](CLOSE_DOCUMENT.md) | Close one exact editor or PrjPcb/LibPkg, checking unsaved changes first; never save. |
| `add_document_to_project(project_path, document_path)` | Include one document in one exact open project, without saving either. |

Supported documents: `.SchDoc` / `SCH`, `.PcbDoc` / `PCB`, `.SchLib` / `SCHLIB`,
`.PcbLib` / `PCBLIB`. Paths are absolute Windows paths; parent directories must
already exist. Creation/saving require local storage, including rejection of
mapped network drives. Read-only targets are rejected. Document kind is inferred
from the extension; an explicit kind must match it, case-insensitively.

Closing has its own scope and validation: it also supports projects and other
loaded editor kinds, and accepts targets whose disk file/directory no longer exists.
See [close policy and explicit discard behavior](CLOSE_DOCUMENT.md).

## Examples

Create a sheet without saving it:

```python
create_document(document_path="C:/Design/Control.SchDoc")
add_document_to_project(
    project_path="C:/Design/Controller.PrjPcb",
    document_path="C:/Design/Control.SchDoc",
)
save_document(document_path="C:/Design/Control.SchDoc", dry_run=True)
```

The sheet and project membership remain in memory. `dry_run=True` checks the
open target without writing or clearing Modified. It does not establish that a
later save will succeed.

When the user has authorized saving a new library:

```python
create_document(document_path="C:/Design/Parts.SchLib", document_kind="SCHLIB")
# Populate the library with existing symbol/model tools, then save explicitly.
save_document(document_path="C:/Design/Parts.SchLib")
```

Creating a document rejects an existing disk file or an already-open document at
the same path. It never reloads or overwrites either. The returned path can be
used for saving and project inclusion before the file exists on disk. Existing
content-editing tools retain their own requirements: for example,
`edit_schematic` and `set_library_component_data` currently require saved files.

Saving requires the exact document to be open. It never falls back to the active
tab, invokes Save All, opens a closed document, changes its filename, or saves a
project. Save As, alternate formats and project-file saving are outside this
tool's scope. Only call it when saving that document is authorized.

Project targets must be existing, already-open `.PrjPcb` or `.LibPkg` files;
LibPkg accepts library documents only. Inclusion accepts an existing saved source
or an open named unsaved document. Repeating inclusion returns `changed=false`
without adding another entry. Other projects and unrelated memberships are kept.
No compile or automatic save occurs. Use Altium's project save command when the
user later requests persistence of membership.

[`open_project(project_path)`](OPEN_TARGETS.md) opens a closed existing PrjPcb or
LibPkg and selects it. This differs from adding a source document to a project.
The same guide covers opening SchLib/PcbLib libraries through `open_document`.

To make an already-open project current for project-level inspection, use
[`select_project(project_path)`](PROJECT_SELECTION.md). Inclusion itself does
not select a project. Selection does not save a project or its documents.

## Results and failures

Creation/opening/saving/inclusion return `success`, `action`, `document_path`, `document_kind`, `changed`,
`saved`, `exists_on_disk` and `may_have_partial_changes` for native responses.
Validation errors contain `success=false`, `error` and `saved=false`.
An open target also reports `was_modified` / `is_modified`.

Saving adds `dry_run`. An actual save returns `disk` with SHA-256 before/after,
file existence and size. Success requires native save acceptance, a nonempty
file and a cleared Modified flag. An unchanged hash is valid for a repeated save.
`changed=true` for saving means that a save was executed, not that content differs.

Inclusion adds `project_path`, `project_saved=false` and logical document counts
before/after. Membership is read back from the target project. Inclusion/removal
can leave Altium's project dirty indicator set; the toolkit never clears it.

API failures can occur after creation, inclusion or a disk write. Inspect the
returned state before retrying. `save_outcome_unknown=true` means saving was
attempted but completion could not be established. A bridge failure conservatively
reports possible partial changes and retains its unique `spec_file` for a delayed
invocation. Do not blindly repeat a timed-out mutation. Recover the executor and
inspect the target first.

Unicode paths are transported as UTF-8 BOM specs, then escaped after native JSON
construction so ANSI response writing cannot lose characters.

## Implementation and verification

`server/document_management.py` validates requests, writes unique escaped specs,
dispatches through the existing bridge and verifies saved files.
`server/AltiumScript/document_management.pas` resolves exact open documents and
projects, uses `Client.OpenNewDocument`, `IServerDocument.DoFileSave` and
`IProject.DM_AddSourceDocument`, and returns readback. The three commands bypass
the legacy document-focus heuristic.

Native API references:
[IClient](https://www.altium.com/documentation/altium-dxp-developer/iclient-interface),
[IServerDocument](https://www.altium.com/documentation/altium-dxp-developer/iserverdocument-interface),
[IProject](https://www.altium.com/documentation/altium-dxp-developer/iproject-interface).

Run `python -m unittest server.tests.test_document_management -v` for boundary,
transport and failure tests. See [live verification](DOCUMENT_MANAGEMENT_LIVE_TEST.md)
for Altium coverage and the deliberate restriction against saving sheets.
