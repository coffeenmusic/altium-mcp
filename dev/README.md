# Development harness

Tools for developing new Altium API functionality. Not part of the MCP server.

## Why

Altium has no headless test mode for DelphiScript, and its failure modes are
hostile to automation:

- A runtime error **kills the script and wedges the script executor** - every
  later `RunScript` silently does nothing until Altium restarts.
- Plain `try/except` does **not** catch runtime conversion errors; the error
  escapes to the enclosing block and still kills the script.
- Compile/runtime errors surface only as modal dialogs, with no log or
  traceback, and their text is often unreadable programmatically.

## Recovering a wedged executor

- `python dev/unwedge.py` sends Altium's Stop Debugging shortcut (Ctrl+F3).
  Fast (~2s) but it only lands when the **script editor tab is the active
  document** in Altium - keep `Sandbox.pas` focused for this to work.
- `--auto-restart` on the runner force-restarts Altium instead (~60-90s,
  kills X2.EXE, unsaved Altium work is lost). Guaranteed, unattended.

## Diagnosing failures

1. **Step log** (primary): the last logged line tells you which statement
   died - the one right after it.
2. **Altium window capture** (`dev/capture_window.py`): uses `PrintWindow`,
   which renders the window even when obscured or the desktop is not
   rendering. Screen-scraping (`CopyFromScreen`) returns blanks on
   remote/disconnected sessions - do not use it. When the script editor tab
   is active, its capture shows the paused line highlighted.
3. Silent debugger pauses produce **no dialog at all**; compile errors do.

## Gotcha: the sandbox is standalone

Experiments cannot use constants/helpers defined in the production units
(`REPLACEALL`, `TrimJSON`, `AddJSONProperty`, ...) - those live in
`server/AltiumScript/*.pas`, which this project does not include. Declare
what you need in `Sandbox.pas`. An undefined constant compiles fine and then
kills the script at runtime.

## sandbox_runner.py

Runs an experiment body inside `dev/sandbox/`, a **standalone script project**
that is deliberately separate from the production `Altium_API` project - a
broken experiment can never break the working MCP tooling.

```
python dev/sandbox_runner.py my_experiment.pas [timeout] [--auto-restart]
```

- Injects the body between the BEGIN/END EXPERIMENT markers of `Sandbox.pas`
- `SandboxLog()` flushes to disk after **every** call, so if the script dies
  silently the last logged step identifies the statement that killed it
- Detects and closes both dialog classes Altium uses (`#32770` task dialogs
  and Delphi `TMessageForm`), reporting their text when readable
- `--auto-restart`: on a wedge, force-restarts Altium (kills X2.EXE - unsaved
  Altium work is lost), waits for readiness, and retries the experiment once

Experiment body rules: assign findings to `ResultText`; Pascal has no inline
declarations, so reuse the scratch variables declared in `Sandbox.pas`
(`S1..S3`, `I1..I3`, `B1`, `Obj1..Obj3`, `List1`, `IntMan`, `DbDoc`) or add
more there.
