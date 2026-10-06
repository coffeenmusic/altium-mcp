# Generating a readable schematic

Rules for programmatic schematic generation, each one learned by getting it
wrong and having a PCB designer point at the result. An agent that follows
these should not repeat the mistakes.

Tooling: a circuit spec (part data from
[`make_part_spec.py`](make_part_spec.py)) →
[`build_circuit.pas`](build_circuit.pas) (build) →
[`check_connectivity.pas`](check_connectivity.pas) (verify), with
[`harvest_param_placement.pas`](harvest_param_placement.pas) as a one-time
setup step per library.

## 1. A pin's connection point is not `Pin.Location`

`ISch_Pin.Location` is the end attached to the component **body**. The
electrical end is `PinLength` further along `Pin.Orientation`
(`0/1/2/3` = right/up/left/down):

```pascal
x := CoordToMils(Pin.Location.X);  y := CoordToMils(Pin.Location.Y);
len := CoordToMils(Pin.PinLength);
if (Pin.Orientation = 0) then x := x + len
else if (Pin.Orientation = 1) then y := y + len
else if (Pin.Orientation = 2) then x := x - len
else if (Pin.Orientation = 3) then y := y - len;
```

`Pin.Orientation` already composes with the component's rotation — do not add
the parent's orientation on top.

Verified against a hand-wired production sheet
([`verify_pin_convention.pas`](verify_pin_convention.pas)): wire endpoints land
on `Location + PinLength` **119** times and on `Location` **0** times.

Wiring to `Location` instead draws the wire through the pin and out the far
side. It looks attached, leaves the pin unconnected, and shorts any neighbour
sharing that connection column.

## 2. Never run a riser in a pin's connection column

All of a symbol's pins on one side usually share an x. A vertical wire in that
column passes through every one of them on its way past. Offset risers by at
least 100 mil, and prefer 200–300 for readability.

## 3. Leave a wire between a pin and a node

A junction sitting directly on a pin is bad practice. Place shunt parts one
grid *below* the rail they tap, so there is a short wire between the pin and
the junction:

```
rail  ────────┬────────         tap gap = 1 grid (100 mil)
              │
           ┌──┴──┐  C1 top pin
```

Tight-packing onto the pin is acceptable only when fitting a dense sheet,
which is rare.

## 4. Ground is one grid across, one grid down

Not three. `U1.GND → 100 left → 100 down → GND port`. Shunt parts drop
100 mil from their bottom pin to their own port.

## 5. Rails belong next to the pins they serve

Floating a rail 1000 mil above the part to "keep it clear" just wastes sheet.
Put the rail one pin-row above the highest pin it connects, and route signals
straight out of their pin's own height wherever possible:

```
U1 pin rows:  FB 3000, VOUT 3300, SW 3600   ->   input rail 4100
LED+ leaves VOUT at 3300 and runs straight to J1 - no riser at all
LED- leaves FB at 3000, one riser near the connector, done
```

A signal that leaves a pin, jogs down, runs across and comes back up is a
routing failure, not a style choice.

## 6. A wire must never cross a component body

A shunt part hanging off a rail occupies vertical space *below* it. If another
net runs through that band, the wire is drawn straight through the symbol - it
looks connected to the part and is not.

Concretely: LED+ at y=3300 with C2 hanging 300 mil below it occupies
y=2900..3200, and LED- ran at y=3000. The fix was not to bend the wire but to
turn LED- down to the left of C2 and drop LED+ to its right, so neither
crosses.

Before placing a shunt, check what else routes through the band it will
occupy. `check_connectivity.pas` now tests every axis-aligned segment against
each component's DRAWN body (graphics only - wires legitimately end on pins,
which stick out past the body).

## 7. Power ports: harvest the style, and leave room for the label

Do not guess between the ground symbols. Read what the house style uses from a
real sheet (`ePowerGndPower`=4, `ePowerGndSignal`=5, `ePowerGndEarth`=6 are
easy to confuse, and the wrong one looks plausible):

| Net | Style | Orientation | ShowNetName |
|---|---|---|---|
| GND | **5** `ePowerGndSignal` — digital ground | 3 (down) | False |
| supply rails | **2** `ePowerBar` | 1 (up) | True |

**`ShowNetName := False` does not hide the label.** It reads back as False and
Altium still draws "GND" below the symbol. So a ground port always occupies
roughly 300 mil below itself, and **no wire may be routed through that band** —
the label crowds it and looks like a connection. Either move the part clear of
the run, or drop the crossing wire further away.

## 8. Net labels must touch the wire

A label near a wire names nothing. Place it **on** a wire coordinate.
`check_connectivity.pas` tests each label against every segment (endpoint or
interior) and reports any that miss.

Caveat: that proves the geometric precondition, not the compiled net name.
Confirming the net Altium actually assigns needs the project compiled, which a
free document cannot do.

## 9. Parameter text: harvest it, do not invent it

Placement is an offset from the component's `Location`, plus `Justification`
and `Orientation` — the method in the user's `CopyParamPlacement.pas`.
**`Justification` is what makes a column line up**; setting position alone
leaves it ragged, and setting `Orientation`/`Autoposition` alone does nothing
visible at all.

`harvest_param_placement.pas` reads a hand-drawn sheet and records, per
`LibReference`:

```
PLACE|CAP-NP|1|DESIGNATOR|0|300|2|0      # dx, dy, justification, orientation
PLACE|CAP-NP|1|VALUE|0|200|2|0
```

Treat the harvested numbers as relative **spacing**, then translate the block:

- **Clear the body.** Applied raw, a right-justified column anchors at the
  component origin, which for `CAP-NP` is over the plates.
- **Keep the block together.** The designator's `dy` can reach above the top
  pin, leaving it on the far side of a rail from its own parameters.
- **Allocate room for it.** A block is roughly `records × 100` mil tall and
  hangs below the body. Two parts stacked in one column will collide through
  their text long before their symbols touch.

### Measuring the body: not with `BoundingRectangle`

`Component.BoundingRectangle` **includes the parameter text**, so anchoring
text to it feeds back on itself and walks the block off the sheet. Measure the
extent from a child iterator filtered to drawable primitives:

```pascal
Iter.AddFilter_ObjectSet(MkSet(ePin, eLine, eRectangle, eArc, ePolyline, eEllipse));
```

Unfiltered, the iterator returns objects whose `BoundingRectangle` is invalid
and kills the script.

## 10. Mirroring: `Mirror(Axis)`, not `IsMirrored`

| Call | Effect |
|---|---|
| `Component.IsMirrored := True` | flag sets, **pins do not move** — display only |
| `Component.Mirror(Axis)` | real transform; pins moved `+200 → -200` |

`Axis` can be the component's own `Location`. This is the editor's **X** key.
Check which way a symbol's pins already point before rotating: `HEADER-2X1`
points left natively, so rotating it 180° puts the body between the incoming
wires and its own connection points.

## 11. Rotating a part rotates its text

Straighten designator/comment/parameters **after** any parameter-position
reset, not before — the reset re-derives placement from the body and discards
an orientation assigned earlier.

## 12. Verify with something that does not share the builder's assumptions

The original `check_connectivity.pas` computed hot ends with the **same
formula as the builder**. They agreed with each other while both could have
been wrong, and it passed a sheet with two pins wired to dead ends. A checker
that inherits the hypothesis under test catches typos, never systematic error.

What the checker distinguishes now:

| Situation | Verdict |
|---|---|
| wire endpoint on a pin's hot end | connected |
| wire crossing a pin, **junction present** | valid tap (shunt on a rail) |
| wire crossing a pin, no junction | **short** |
| junction with < 3 branches | decoration — usually an overshoot |
| net label not on a wire | **names nothing** |
| wire crossing a drawn body | **routed through a symbol** |

Branch counting: a wire *ending* at a point is one branch, a wire *passing
through* is two, a pin landing there is one more.

## DelphiScript hazards that bit repeatedly

- **Shared scratch variables.** The sandbox convention (`I1..I5`, `S1..S5`) is
  the single most repeated bug here - THREE times in one session, each time
  silent. Reusing `I1` as a loop counter after storing a coordinate in it gave
  an anchor of `24 - 50 = -26` and flung text off-sheet; reusing `I4`/`I5` for
  bounding-box edges overwrote result counters and reported `1840` pins
  shorted. Every occurrence produced plausible-looking wrong output rather
  than an error. Stash a result before any loop that might reuse its variable
  (`SavedCounts`), and an MCP port should use properly scoped locals.
- `try/except` does **not** catch runtime errors; a bad call leaves the script
  paused in the debugger and every later run silently does nothing. Recover
  with `X2.EXE -REditScript:Stop` (see [`unwedge.py`](unwedge.py)), and verify
  recovery by running a probe rather than assuming.
- Nil is not guarded for you. `GetSchDocumentByPath` returns nil for a document
  that belongs to the project but is not open; the next call kills the script.
- Do not predict a rotated symbol's pin coordinates. Place, dump `pin_map.txt`,
  then route from the measured values.
