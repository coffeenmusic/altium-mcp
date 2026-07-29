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

Result: component count went 0 -> 1, reported as `RES-DISCRETE/R900`, and a
window capture confirmed the symbol is fully drawn with its parameter text
(not an empty placeholder).

Symbol location: the DbLib's `LibrarySearchPath` lists the symbol folders;
the `.SchLib` containing a given `Symbol_Name` can be found by scanning those
files (component names appear as plain and UTF-16 text inside the binaries).

## Still open

- Attaching full database linkage (so "Update from Database" recognises the
  part) - `DesignItemID` alone is set; the read-only `DatabaseLibraryName`/
  `DatabaseTableName` must come from somewhere else (likely component
  parameters, or a different creation route).
- Adding the footprint model to the placed component.
- Placing onto an existing project sheet rather than a scratch document.
