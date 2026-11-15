# Upstream synchronization: 2026-11-15

## Preserved history

- Fork baseline: `0c9e1dc` (`Fix PCB layer aliases and STEP readback`).
- Upstream main: `e8dcd0e3d0834c03653248bcc0545208cf0ef434`.
- Integration branch: `codex/sync-upstream-2026-11-15`.
- Local backup branch: `codex/backup-before-upstream-2026-11-15`.

The 21 local changed/new files seen at the start were committed as `0c9e1dc`
before integration. That commit is included in the backup and integration
histories. At this baseline, upstream had 126 commits absent from the fork;
the fork had 28 commits absent from upstream. Both histories are retained.

```powershell
git fetch --all --prune
git switch -c codex/backup-before-upstream-2026-11-15
git switch -c codex/sync-upstream-2026-11-15
git merge --no-commit --no-ff upstream/main
```

The fetch was repeated before finalization; the upstream head still matched
the merge input. No published history was rebased or force-pushed.

## Compatibility work

Ten files had textual conflicts. Resolving them also required checking the
interfaces that Git had combined automatically:

- Combine the fork's shared PCB aliases with upstream's visibility planning
  against real board layers. Resolve mechanical aliases through the reported
  mechanical number, retain exact/custom names during dispatch, keep canonical
  requested aliases in `accepted_layers`, and preserve strict refusal and
  visibility restoration.
- Retain layer normalization and explicit `command_started=false` refusals
  while adding upstream's command watcher and abort handling.
- Keep UID schematic editing alongside expanded upstream actions, pin
  references, sheet-symbol/entry edits and DbLib placement.
- Keep verified symbol metadata, designator templates and component kinds;
  accept upstream `PINFUNC` records and normalize the `LIBRARY` path in the
  validated private spec without modifying the caller's file.
- Preserve library screenshots and foreground/compositor guards while adding
  upstream's named screenshot targets. Both Python positional forms and named
  MCP arguments remain supported.
- Give the two independently added document-kind helpers separate native
  names. Renumber script-project sections and verify that all 24 units bundle
  without duplicate routines.
- Preserve all 91 fork tool names and all 87 upstream names: the merged server
  registers 118 unique tools. Update manifest and README coverage accordingly.
- Run all 68 offline test modules in CI. The legacy interactive
  `test_altium_script.py` remains excluded.

Upstream additions include document listing/reloading, richer screenshot
targets, OutJob editing, hierarchy checking, ECO preview, netlist comparison,
PCB queries/geometry/rendering, and expanded routing and sandbox capabilities.

## Verification

- Baseline before merging: 518 tests, 517 passed and one ANSI-code-page skip.
- Integrated suite: 811 tests, 810 passed and the same skip.
- Python source syntax checked against Python 3.10; execution used Python
  3.13 and MCP 1.30.0.
- Production Pascal bundle: 24 units, without duplicate routines.
- Real stdio MCP initialization, tool enumeration and status call passed;
  the registry exactly matched the 118 decorated production tools.
- A real Altium Designer 24 project-information command ran the newly built
  native bridge successfully.
- The opt-in PCB readback harness completed 24 live steps on disposable
  PcbLib/PcbDoc copies: constant availability, saved STEP placement matching,
  footprint batch/edit aliases, rejection cases and visibility aliases.
  Source hashes/mtimes and original document states were unchanged; copies
  were closed with discard and no Altium document was saved.

Live checks exposed the canonical `accepted_layers` compatibility case on a
renamed mechanical layer. They also exposed assumptions in the harness about
restoring Altium's internal Home page and starting with a saved design project.
These were corrected before the final successful run.

Local evidence, excluded from Git:

- `.altium-mcp/sync_20261115/baseline-tests.log`
- `.altium-mcp/sync_20261115/merged-tests.log`
- `.altium-mcp/sync_20261115/runtime-check.json`
- `.altium-mcp/sync_20261115/live-context.json`
- `.altium-mcp/pcb_readback_fixes_20261115_111552/report.json`

The live scope is the shared bridge and PCB/library regressions above; it does
not constitute an exhaustive live test of every newly added upstream tool.

## Finalization

After validation, stage the reviewed resolutions and commit the merge, then
advance main and publish both branches:

```powershell
git diff --check
git add -u
git add server/tests/test_upstream_sync.py dev/UPSTREAM_SYNC_2026-11-15.md
git commit -m "Merge upstream updates and retain fork extensions"
git switch main
git merge --ff-only codex/sync-upstream-2026-11-15
git push origin main codex/sync-upstream-2026-11-15
```

The already running MCP process must reconnect to load the new Python code and
tool registry. Production script bundles rebuild by content hash.
