# Library editing live verification

Tested on 2026-11-05 after the machine restart in Altium Designer Enterprise
24.4.1, using the open `SALT-FMUV6X-JETSON.PrjPcb` project.

## Results

| Operation | Result |
| --- | --- |
| Native enum lookup | Passed: round rectangle 9, Mechanical 13/15 layers 69/71, manual cache mode 2. |
| Existing graphic layer updates | Passed for two tracks, one arc and one fill; all four native readbacks confirmed. Geometry preserved. |
| Existing pad overrides | Passed for six pads: manual mask +0.05 mm, paste 0; locks and geometry preserved. |
| Omitted paste setting | Preserved while changing mask expansion. |
| Reset overrides to rules | Passed for both mask and paste on all six pads. |
| Missing primitive/pad in a batch | Rejected with zero updates; complete primitive data unchanged. |
| Unknown batch layer | Rejected before footprint creation. Mechanical aliases resolved correctly. |
| Clean-only close of dirty fixture | Refused; explicit fixture discard then succeeded without saving. |
| Symbol field formatting through existing batch tool | VALUE / PKG_STYLE positions and visibility changed; PLACE and Comment shown, then hidden. All parameter values, pins and graphics preserved. |
| SchLib / PcbLib screenshots | Both showed actual editor geometry after the capture fix; PNGs inspected. |
| Unicode transport | Paths with Greek, Cyrillic and an emoji opened, read, edited and closed correctly. |

All edits were confined to unique scratch copies in the toolkit's `.altium-mcp`
directory. The original library's full readback was unchanged. The initial and
final project snapshots were identical: no Modified documents and only
`Power-Sequencer-Draft.SchDoc` open. All 20 original project/document files kept
their SHA-256, size and modification time. Scratch copies were discarded without
saving; their disk hashes and modification times were unchanged too.

Evidence:

- `.altium-mcp/footprint_editor_20261105_232835/report.json`: complete passing run.
- `.altium-mcp/live_recheck_20261105_231643/`: initial/final project snapshots,
  disk baselines, symbol styling specs/readbacks, `screenshots.json`, `schlib.png`
  and `pcblib.png`.
- Cross-sheet/multipart results: [SCHEMATIC_CLONING_LIVE_TEST.md](SCHEMATIC_CLONING_LIVE_TEST.md).

## Corrections found during live testing

- `JSONUnescape` used byte-sized `Chr` for UTF-16 escapes, turning U+03A9 into
  U+00A9. The server now generates a 130562-byte UTF-16 lookup resource in the
  exchange directory at startup. The decoder loads it only for escaped code
  units above 255, preserving surrogate pairs as well. No binary lookup table or
  non-English fixture text is stored in repository sources.
- Fill selectors now use `x1Location` / `y1Location` / `x2Location` / `y2Location`.
- Symbol body bounds now iterate only graphics and pins, excluding model objects
  without `OwnerPartId`. This repairs the existing `EDITSYMBOL` replacement.
- Screenshots now copy the composed desktop after confirmed foreground
  activation. Altium's own window DC produced a black/corrupted GPU canvas.
- A pre-dispatch busy guard reports `command_started=false`; the footprint
  wrapper no longer misreports that refusal as an unknown mutation.
- Harness comparisons ignore group-iterator order, allow 0.001 mil expansion
  conversion tolerance and wait for the library-open repaint to settle.

Intermediate failed runs were retained as diagnostic evidence; they are not
reported as passing. Each known failed fixture was closed without saving.

## Interpreter probes

Read-only `run_altium_script` probes were needed to repair an existing-tool
Unicode error; dedicated document tools cannot inspect interpreter conversion
behavior. No separate public tool is needed: the reusable fix is in the common
JSON decoder used by `get_footprint_primitives` and other existing commands.

`WideChar` and `CreateOleObject` were unavailable; `UTF8Decode` on constructed
byte characters did not produce the required string. Loading a generated UTF-16
code-unit table succeeded and preserved Greek, Cyrillic and surrogate units.
All four probes touched only strings or the exchange resource, no Altium design
objects. The executor remained usable.

## Loading the Python corrections

Live handlers were exercised from fresh Python processes using the production
wrappers; they did not start a second listening MCP server. Native bridge units
use content-versioned bundles. The already-running client connection predates
the Python screenshot/guard fixes: restart that MCP connection to load them.
Restarting or saving Altium is not part of that refresh.
