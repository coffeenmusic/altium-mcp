# DbLib schematic placement - API findings

Established with the sandbox harness (`dev/sandbox_runner.py`) against a
production DbLib. "Verified" means it actually ran and produced the stated
result; failures are recorded so they are not retried blindly.

## Reading the library

| Call | Result |
|---|---|
| `IntegratedLibraryManager` | non-nil (verified) |
| `.AvailableLibraryCount` / `.AvailableLibraryPath(i)` | enumerates installed libraries (verified) |
| `.AvailableLibraryType(i)` | **3 = DbLib**, 0 = IntLib - the discriminator |
| `.GetAvailableDBLibDocAtPath(path)` | returns `IDatabaseLibDocument`, works over a UNC/network share |
| `IDatabaseLibDocument.GetTableCount` / `.GetTableNameAt(i)` | enumerated all 30 tables |

## Reading part data (no Altium needed)

A `.DbLib` file is an INI: `ConnectionString` points at the backing database
(here Access/ACE OLEDB). Enabled `*_Query` tables carry
`UserWhereText=[Corp_Part_Number] = '{Corp_Part_Number}'`, i.e. they are
parameterised lookups keyed by the corporate part number. Useful columns:
`Corp_Part_Number`, `Symbol_Name`, `Footprint_Name`, `Description`.

Read-only queries via OLEDB (PowerShell `System.Data.OleDb` or Python) return
part rows directly - the fastest way to resolve a part number to its symbol
and footprint names without driving Altium.

## Placement: what does NOT work

- `IntegratedLibraryManager.PlaceLibraryComponent(libRef, libPath, params)`
  returns without error but **places nothing**. Verified for both the DbLib
  and a plain `.SchLib`, with the target document confirmed current, and with
  a follow-up run confirming it is not merely deferred. Not DbLib-specific.
- Process `Sch:PlaceIntegratedComponentFromDB` with the documented parameters
  (`LibReference`, `Library`, `SourceLibraryName`, `DatabaseTableName`,
  `CurFootprint`, `PartId`, `Location.X/Y`) runs without error and places
  nothing.
- `ISch_Component.DatabaseLibraryName` / `.DatabaseTableName` are **not
  writable** - assigning to them kills the script (and wedges the executor).
  `.DesignItemID` *is* writable.

## Placement: what DOES work (verified)

Replicate the symbol out of its source `.SchLib` and register it:

1. `Client.OpenDocument('SchLib', <symbol library path>)` + `Client.ShowDocument`
2. `SchServer.GetCurrentSchDocument` -> iterate `SchLibIterator_Create` with
   `MkSet(eSchComponent)` to find the component whose `LibReference` matches
   the DB's `Symbol_Name`
3. `Component.Replicate` (returns a detached copy)
4. `GetWorkSpace.DM_CreateNewDocument('SCH')` for a target sheet (also focuses
   it); `SchServer.GetCurrentSchDocument` then returns it
5. On the replica set `Designator.Text`, `Location` (`Point(MilsToCoord..)`),
   and `DesignItemID`
6. `TargetDoc.RegisterSchObjectInContainer(replica)` then
   `SchServer.RobotManager.SendMessage(TargetDoc.I_ObjectAddress, c_BroadCast,
   SCHM_PrimitiveRegistration, replica.I_ObjectAddress)`
7. `TargetDoc.GraphicallyInvalidate`

Result: component count went 0 -> 1, reported as `RES-DISCRETE/R900`, drawn
with its graphics.

**Important correction:** this alone produces an *unlinked shell*. The symbol
carries its library placeholder parameter values (`TOLERANCE=TOL`,
`PKG_STYLE=PKG_STL`, `VALUE=VALUE`) - none of the database values are pulled
in, because this path bypasses Altium's DbLib machinery entirely. Populating
them is a separate step (below).

Symbol location: the DbLib's `LibrarySearchPath` lists the symbol folders;
the `.SchLib` containing a given `Symbol_Name` can be found by scanning those
files (component names appear as plain and UTF-16 text inside the binaries).

## How the DbLib mapping is actually defined

The `.DbLib` has no `Symbol`/`Footprint`/`Param` keys; the mapping lives in 81
`Options=` lines, one per mapped field, e.g.

```
Options=FieldName=RESISTORS_Query.Altium_Footprint|TableNameOnly=RESISTORS_Query|
        FieldNameOnly=Altium_Footprint|FieldType=1|ParameterName=[Footprint Ref]|...
```

Only *system* fields are mapped explicitly (bracketed parameter names):
`Description -> [Description]`, `Altium_Symbol -> [Library Ref]`,
`Altium_Footprint -> [Footprint Ref]`, `Altium_3DModel -> [PCB3D Ref]`, plus
`Corp_Part_Number` as a normal parameter. Everything else is matched
**implicitly by name**: a symbol parameter is filled from the database column
of the same name, case-insensitively.

Worked example (`RES-DISCRETE`, part `1102-0001`, table `RESISTORS_Query`):

| symbol parameter | placeholder | DB column | DB value |
|---|---|---|---|
| `VALUE` | `VALUE` | `Value` | `200` |
| `TOLERANCE` | `TOL` | `Tolerance` | `1%` |
| `PKG_STYLE` | `PKG_STL` | `Pkg_Style` | `0201` |
| `PWR_RATING` | `PWR` | `Pwr_Rating` | `1/20W` |
| `Comment` | (empty) | `Description` | `RES, 0201, 1%, 1/20W, 200` |

Note the enabled table is the `*_Query` view, not the base table, and the two
disagree: `RESISTORS.Footprint_Name = R0201A` but
`RESISTORS_Query.Altium_Footprint = RESC0603X03N`. The `*_Query` view is what
Altium uses, so it is authoritative.

## Parameter population (verified)

Iterate the replicated component's `eParameter` children and assign
`.Text` from the matching database column before registering it. Values read
back from the objects afterwards confirmed real data
(`PWR_RATING=1/20W`, `VALUE=200`, `TOLERANCE=1%`, `PKG_STYLE=0201`), and the
component `Comment` was set from the DB `Description`.

## Full component construction (verified)

Matching what Altium's own DbLib placement produces requires three things
beyond replicating the symbol. Read back off the placed component:

1. **Fill existing placeholder parameters** - iterate `eParameter` children and
   assign `.Text` from the same-named database column.
2. **Add the database columns the symbol does not carry** - for each remaining
   non-null column, `SchServer.SchObjectFactory(eParameter, eCreate_Default)`,
   set `Name`/`Text`/`ParamType`/`ReadOnlyState`/`IsHidden`, then
   `Component.AddSchObject(param)` plus a `SCHM_PrimitiveRegistration` robot
   message. Reference placements show these as hidden, database-sourced
   parameters.
3. **Attach the footprint model** - `Component.AddSchImplementation`, then
   `ClearAllDatafileLinks`, `ModelName` (from `Altium_Footprint`),
   `ModelType := 'PCBLIB'`, `IsCurrent := True`,
   `UseComponentLibrary := True`.

Verified result on the placed part: 17 parameters (5 symbol + 11 database +
Comment) and 1 `PCBLIB` model `RESC0603X03N` marked current, with
`LibReference=RES-DISCRETE`, `DesignItemID=1102-0001`, and Comment from the
database Description.

Ordering note: register the component on the sheet *before* adding parameters
and the model.

## What a genuine GUI/database-placed component stores

Read off a production part (C17) placed through Altium's own DbLib flow:

| field | value |
|---|---|
| `LibReference` | `CAP-NP` |
| `DesignItemID` | `2104-0017` (the corporate part number) |
| `DatabaseTableName` | `CAPACITORS_Query` (the *_Query view) |
| `DatabaseLibraryName` | `Neoventus_Components.DbLib` (**file name only**, no path) |
| `SourceLibraryName` | `Neoventus_Components.DbLib` |
| `LibraryPath` | `*` |
| `Comment` | `CAP-NP` (the **LibReference**, not the description) |
| parameters | 18, all `hidden=True`, `readonly=0`, `paramtype=0` |
| model | one `PCBLIB`, `IsCurrent=True`, `UseComponentLibrary=True`, **1 datafile link** |

Two corrections to earlier work this implies: `Comment` should be the
LibReference (not the DB description), and the model needs a datafile link.
The per-parameter flags carry no special "database" marker - the linkage lives
on the component (`DatabaseTableName`/`DatabaseLibraryName`).

## Update from Database works on constructed parts (user-verified)

Running Altium's **Update from Database** on a scripted component populated it
correctly. So Altium matches the part by `DesignItemID` (which *is* writable)
and does not require the script to stamp `DatabaseLibraryName`/
`DatabaseTableName` itself.

This changes the architecture substantially - and removes the need for
per-table column knowledge:

1. place the symbol (replicate from the source `.SchLib`)
2. set `Designator`, `DesignItemID` (the corporate part number), `Comment`
3. let Altium's Update from Database fill every parameter and model natively

Nothing about the placement needs to be table-specific, because Altium does the
mapping. Remaining task: identify the process name so the tool can trigger the
update programmatically instead of relying on the user running the menu item.

## Known bug: parameter text left behind when positioning

Symptom (observed after Update from Database): the component body moves to the
requested location but its parameter/designator text stays where the part
originally sat.

Cause: assigning `Component.Location` moves the component origin and its
graphical primitives, but child text objects (`Designator`, each
`ISch_Parameter`) carry their own absolute coordinates and are left behind at
the library symbol's coordinates.

Fix: do not assign `Location` on a replica. Register the component, then use
`Component.MoveByXY(dx, dy)`, which translates the component together with its
children. (Alternatively, offset every child's `Location` by the same delta.)
This also explains why the earlier test parts appeared at the sheet corner.

## Historical note: the read-only linkage fields

`DatabaseLibraryName` and `DatabaseTableName` are **read-only** - assigning
them kills the script both before *and* after registration (verified
separately). This was originally thought to be a blocker, but it is not: write
access is unnecessary because Update from Database resolves the part from
`DesignItemID` (see above). Do not attempt to set these fields.

Native placement APIs that would do this correctly are no-ops in the scripting
context, retried with the exact identity values above (file-name-only library,
`*_Query` table): both `Sch:PlaceIntegratedComponentFromDB` and
`IntegratedLibraryManager.PlaceLibraryComponent` return without error and place
nothing.

## Path to GUI-identical placement

Since Update from Database works on constructed parts, the plan is: replicate
the symbol, set the identity fields, position with `MoveByXY`, then trigger the
update. If a replica of an **already database-linked donor** component also
carries the linkage fields (they are copied, not assigned), that would make the
part fully indistinguishable from a GUI placement - worth testing next.

Fallback if the update cannot be triggered programmatically: drive the GUI via
UI automation, which is authentic by construction but fragile.

## Still open

- Making placement GUI-identical (see paths above) - the current construction
  approach cannot set the linkage fields.
- Deriving parameters dynamically per table instead of hardcoding columns:
  query the part's `*_Query` view and use its non-null columns, so every
  category works without table-specific code. (Only relevant if construction
  remains the approach; path 1 above makes Altium do it.)
- Placing onto an existing project sheet rather than a scratch document, and
  confirming placement coordinates (the test part landed at the sheet corner).
