# Placement Tooling Review — Hands-On Findings

Context: An AI agent used the MCP tools to re-place 13 passives around U12 (negative
bias generator) on Base.PcbDoc. The placement succeeded, but several tool gaps made
it slower and riskier than it needs to be. Findings are ordered by impact on
placement quality/ease.

## Status (2026-07-07)

Implemented and live-tested:
- `place_components` batch absolute placement (item 12) — 12 parts in 1.8 s, one undo step
- Negative height fixed; `layer`/`footprint` added to selected coords (items 1, 5)
- `dx`/`dy` rotation-0 pad offsets + component x/y/rotation/layer in
  `get_component_pins` (items 6, 18); bottom-layer mirror model verified by a
  live flip experiment (mirror X, then rotate CCW)
- `zoom_to` designators on `get_screenshot` (item 13); screenshot now returned
  as an MCP image content block instead of inline base64 text (item 2)
- `asyncio.Lock` serializing bridge commands (item 14)
- `check_placement` verification tool (item 10): bounding-box prefilter, then
  true minimum distance per close pair via `Board.PrimPrimDistance` measured
  primitive-by-primitive with text (designator/comment) excluded — passing
  whole components to PrimPrimDistance poisons the result with floating
  designator text. Defaults to the current selection; reported real 4.95 mil
  courtyard gaps that bounding-box math called 6.4 mil.

Still open: items 3, 4, 7 (renaming pad `rotation` — documented in the tool
docstring instead), 8, 9, 11, 15, 16, 17.

## Lessons from comparing against a known-good placement (2026-07-07)

The user provided a known-good (routed) placement of the same U12 cluster.
Quantitative comparison (per-net MST airline, U12-relative frame): the agent
placement had *shorter total airline* (1729 vs 1773 mil) but is clearly worse.
Total airline is the wrong objective. What the expert did differently:

1. **Weight nets by criticality, not equally.** The expert spent +226 mil on
   R48's two non-critical nets (pushed far left into open space) to buy a
   clean routing channel, while cutting the critical input net NEG-REG-IN by
   120 mil (caps straight above pin 16) and shrinking the SW snubber loop.
2. **Face pads at their destinations.** Each passive is rotated so the
   connecting pad points at its target pin (e.g. R45 horizontal, pin 1 aimed
   straight at U12 pin 1 = 43 mil straight shot). Uniform column aesthetics
   cause doglegs and route crossings (the agent's R45/R48 crossing).
3. **Leave 40-60 mil routing channels between sub-groups** and room for GND
   stitching vias; do not pack at minimum courtyard clearance.
4. **Place in the current-flow path**: FB11 (ferrite) -> C63/C64 -> pin 16 in
   a line; output cap centered on its pin group (C68 pads aligned with pin 12
   and the GND pins); RC snubber forming a closed physical loop.
5. Result: despite looser spacing, the expert cluster is ~25% smaller in area
   because parts hug their own pins instead of forming rows.

Tooling implication: a `get_net_connections` tool with per-net airline lengths
would let the agent compute these metrics during placement (the comparison
above required manual reconstruction), ideally with per-net weights supplied
by the caller.

## Bugs found during the exercise

1. **Negative `height` in component bounds** — `GetAllComponentData` and
   `GetSelectedComponentsCoordinates` (`pcb_utils.pas:919`, `pcb_utils.pas:996`)
   compute `CoordToMils(Rect.Bottom - Rect.Top)`, which is negative because
   `Top > Bottom` in Altium coordinates. Should be `Rect.Top - Rect.Bottom`.
   The agent had to take absolute values.

2. **`get_screenshot` result is unusable over MCP** — the base64 PNG (~220 KB)
   is returned inline in the JSON result and blows past client token limits.
   The agent could only proceed because the server also writes
   `server/screenshot_pcb.png` as a debug file. Return an MCP image content
   block and/or a file path instead of inline base64 text.

3. **Double-wrapped response in `set_component_position`** — the Pascal side
   returns `{"success": true, "result": {...}}` and `main.py:1089` wraps it
   again, producing `{"success": true, "result": {"success": true, "result":
   {...}}}`.

4. **Inconsistent result shape when nothing is selected** —
   `get_selected_components_coordinates` returns a JSON *array* normally but a
   `{"message": ...}` *object* when nothing is selected. Callers must
   special-case. Prefer `{"components": [...], "count": N}` always.

## Data gaps that made placement harder

5. **No `layer` in `get_selected_components_coordinates`** — the agent had to
   assume top-side placement. Include `layer` (and `footprint`,
   like `get_component_data` already does).

6. **No component-relative pad offsets** — `get_component_pins` returns only
   absolute pad positions. To rotate a part and predict where its pads land,
   the agent had to reverse-engineer each footprint's rotation-0 pin offsets
   from current absolute position + rotation, then re-apply rotation math
   (CCW convention, also undocumented). Add per-pin `dx`/`dy` relative to the
   component origin at rotation 0, and document the rotation convention.

7. **Pad `rotation` field is the pad's own rotation** — easily confused with
   the component rotation. Rename to `pad_rotation` or document it.

8. **Bounding box semantics undocumented, no courtyard** — `width`/`height`
   come from `BoundingRectangleNoNameComment`, which includes silkscreen.
   There is no way to get the courtyard, so legal component-to-component gaps
   are guesswork. Expose courtyard rect (or per-layer bounds) if available.

9. **No board context** — no tool returns the board outline, keepout regions,
   rooms, or placement-relevant polygons (e.g. the `MotorClearance` region on
   this board). Placement decisions are blind to these. Add
   `get_board_outline` (+ keepouts/rooms).

10. **No placement verification loop** — nothing reports courtyard overlaps,
    clearance violations, or DRC results after moving parts. The agent
    verified numerically by re-reading pins, which cannot catch clearance
    errors against *unmoved* neighbors. Add a `check_placement` /
    `run_drc_summary` tool (even just pairwise courtyard-overlap detection for
    a list of designators would close the loop).

11. **No connectivity/ratsnest summary** — net topology had to be rebuilt by
    fetching pins for all 14 components and joining net names manually. A
    `get_net_connections(designators)` (net → [designator.pin @ x,y]) and/or
    total airline length per net would both guide and score placements.

## Workflow/performance issues

12. **No batch absolute placement** — `set_component_position` takes one
    component; 12 placements = 12 full round trips, each of which *launches
    Altium's script runner* (`X2.EXE -RScriptingSystem:RunScript...`) and
    polls a response file at 0.5 s intervals. Add `place_components` accepting
    `[{designator, x, y, rotation, layer?}, ...]` in one transaction
    (`move_components` already shows the batch pattern). This is the single
    highest-impact improvement.

13. **No zoom/viewport control for screenshots** — the capture is the whole
    Altium window at whatever zoom the user left it; the work area was ~5 % of
    the frame and unusable for visual verification. Add a
    `zoom_to_components(designators)` or accept a board-coordinate rectangle
    in `get_screenshot`, and crop out the Properties panel / UI chrome.

14. **Concurrent calls would clobber the exchange files** — `execute_command`
    has no lock; two in-flight tool calls share `request.json`/`response.json`.
    Add an `asyncio.Lock` in `AltiumBridge.execute_command`.

15. **Sentinel inconsistency for "keep rotation"** — `set_component_position`
    uses `-1`, `move_components` uses `0` (making "rotate to 0°" impossible via
    move). Unify on `-1` (or a nullable param) for both.

## Code robustness

16. **Hand-rolled JSON parsing in DelphiScript** (`Altium_API.pas`, e.g.
    `ExecuteMoveComponents`) is line- and indent-dependent (`Pos('"x"', line)`),
    assumes `json.dump(indent=2)` formatting, breaks on values containing
    `,`/`:`/`"`, and mutates the `for` loop variable while scanning arrays.
    At minimum, factor a shared `ExtractParam`/`ExtractStringArray` helper;
    ideally parse with a real JSON tokenizer in `json_utils.pas`.

17. **Python-side JSON "repair" hacks** (`main.py:282-301`) — blanket
    `replace('\\"', '"')` can corrupt valid responses. Fix escaping at the
    source (Pascal `JSONEscapeString` exists but isn't used everywhere, e.g.
    the error string in `SetComponentPosition`, `pcb_utils.pas:1194`).

18. **Docstrings should state coordinate conventions** — x/y are mils
    *relative to the board origin*, rotation is degrees CCW. The agent inferred
    both; make them explicit in every placement tool description so any client
    gets it right the first time.

## Suggested new tools, prioritized

| Priority | Tool | Why |
|---|---|---|
| 1 | `place_components` (batch absolute, one transaction) | 12× fewer round trips; atomic placement |
| 2 | Enriched selection/bounds data (layer, courtyard, rel. pad offsets, fixed height sign) | Removes guesswork and manual rotation math |
| 3 | `get_screenshot` region/zoom-to-components + image-file return | Makes visual verification actually possible |
| 4 | `check_placement` (courtyard overlap / clearance / DRC summary) | Closes the verify loop |
| 5 | `get_net_connections` + airline lengths | Connectivity-driven placement & scoring |
| 6 | `get_board_outline` / keepouts / rooms | Placement legality context |
