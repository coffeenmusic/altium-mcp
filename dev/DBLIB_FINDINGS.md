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

## Still open

- Full database linkage so Altium's own "Update from Database" recognises the
  part. `DesignItemID` is settable, but `DatabaseLibraryName`/
  `DatabaseTableName` are read-only, so the component is currently populated
  from the DB rather than *linked* to it.
- Placing onto an existing project sheet rather than a scratch document, and
  confirming placement coordinates (the test part landed at the sheet corner).
