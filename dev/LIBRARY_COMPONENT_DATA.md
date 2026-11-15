# set_library_component_data

```python
set_library_component_data(
    library_path, symbol_name,
    parameters=None, footprint_models=None,
    comment=None, description=None
)
```

Read or update metadata of one existing symbol in an exact `.SchLib`. Changes
remain in Altium memory. The tool never saves documents, updates placed symbols,
runs an ECO or edits the referenced footprints. Repeated equivalent requests
reuse existing parameters/models and report `changed=false`.

## Existing alternatives

`edit_schematic` changes parameters on placed SchDoc instances. `build_schematic`
can assign a footprint name while placing a new component. Symbol creation tools
create geometry, and `get_symbol_primitives` deliberately excludes model metadata.
None provides the complete existing-SchLib metadata and pin-map operation.
Custom DelphiScript was the previous workaround.

## Target and read mode

`library_path` must be an absolute path to an existing `.SchLib`.
`symbol_name` matches the library reference case-insensitively and must identify
exactly one symbol. UI focus does not select the target. Open libraries are
reused without reloading unsaved changes; closed libraries are opened.
An active editor transaction blocks the operation.

Omit all edits to read the symbol's parameters, models, datafile links and maps:

```python
set_library_component_data(
    library_path=r"C:\Libraries\Parts.SchLib",
    symbol_name="Resistor"
)
```

The response contains `before` and `after` snapshots, `changed`, `saved=false`,
`was_modified`, `is_modified` and `verification`. The tool does not clear Modified.

## Parameters and text

`parameters` is a dictionary of at most 500 string values. Names are matched
case-insensitively; duplicate names, NUL characters and non-string values are
rejected. Missing parameters are added as hidden parameters shared across parts.
Existing parameter visibility and other attributes are retained. Unspecified
parameters remain. There is no parameter deletion operation.

`Comment` and `Designator` are reserved parameter names. Use the separate
`comment` argument. Use [set_library_component_properties](LIBRARY_COMPONENT_PROPERTIES.md)
for the native Designator template and Component Type, including Standard (No BOM).
`description` updates the symbol description. Empty strings are valid;
`None` means leave unchanged. Read-only parameters/Comment are rejected.

Unicode input uses a BOM-marked UTF-8 spec. The new command ASCII-escapes Unicode
in its JSON response so Altium's ANSI `SaveToFile` cannot replace characters.
The API still returns ordinary Unicode strings after JSON decoding.

## Footprint models and pin maps

```python
set_library_component_data(
    library_path=r"C:\Libraries\Parts.SchLib",
    symbol_name="Resistor",
    parameters={"Manufacturer": "Example", "MP": "R-10K"},
    comment="10k",
    footprint_models=[{
        "model_name": "R_0603",
        "library_path": r"C:\Libraries\Parts.PcbLib",
        "pin_map": {"1": ["2"], "2": ["1"]},
        "is_current": True,
        "description": "Swapped pad numbering"
    }]
)
```

Each model definition requires:

- `model_name`: an existing footprint name in the referenced PcbLib.
- `library_path`: an absolute path to an existing `.PcbLib`.
- `pin_map`: every distinct symbol pin number across **all parts**, mapped to
  a nonempty list of actual pad numbers. For example, `{"1": ["1", "EP"]}` maps
  one pin to two pads. Unmapped extra footprint pads are allowed.

Pin/pad identifiers are exact strings: `01` differs from `1`. Whitespace and
`,:()=` are unsupported in identifiers because they conflict with Altium's map
representation. Each pad may occur only once per requested model, so accidental
many-to-one shorts are rejected. One-to-many maps are supported.

Optional fields are `description` and boolean `is_current`. Omitted values
preserve existing model state; a new model without `is_current` is inactive.
At most one requested model may select `is_current=true`; selecting it clears
the current flag on other PCB models only. Non-PCB model domains are preserved.
If deselection would change a locked or managed current model, the call fails.

Models are upserted by case-insensitive `PCBLIB` model name. Ambiguous existing
names are rejected. A requested model's datafile links are replaced with one
explicit link to the validated PcbLib/footprint and `use_component_library=false`.
Other models are kept; model deletion/renaming is unsupported. Limits: 100 models,
5000 distinct pin numbers per model, 1000 pads per pin.

All requested parameters, footprint names, complete pin coverage and pad numbers
are checked before the first edit. Locked, integrated or database-managed models
are rejected rather than unlocked. The tool does not validate mechanical fit,
copper geometry, pad electrical suitability or whether an intentional pin swap
matches a datasheet. Installed-library-only, IntLib and Workspace model references
are outside this file-based operation.

## Readback and failures

Snapshots include parameter values and every model's type/name, description,
current/lock/management flags, `use_component_library`, raw `map_as_string` and
datafile links. PCB models also include parsed `pin_map`. Unsupported native map
representations retain the raw string and a `mapping_warning`.

Readback checks requested text, mappings, file links and current flags.
`verification.passed=false` changes `success` to false. Native preflight failures
return `may_have_partial_changes=false` with unchanged snapshots. API failures,
readback mismatches or bridge timeouts can leave partial in-memory edits;
inspect the response and reread before retrying. No atomic rollback is promised.
Uncertain bridge failures retain their unique input spec for diagnosis.

Model map definers are metadata, not geometry. `get_symbol_primitives` excludes
them along with other implementation records.

The implementation uses `ISch_Implementation.MapAsString`, `AddSchImplementation`
and `AddDataFileLink`; see the [official Schematic API](https://www.altium.com/documentation/altium-dxp-developer/schematic-api-design-objects-interfaces-reference).

## Verification and refresh

```powershell
python -m unittest server.tests.test_library_component_data -v
python dev/test_library_component_data_live.py --schlib 'C:\Project\Symbols.SchLib' --pcblib 'C:\Project\Footprints.PcbLib' --symbol 'Connector'
```

The live runner reads by default. `--exercise --alternate <footprint>` tests
mutations only in an unsaved copy of the source SchLib under the toolkit's
`.altium-mcp/library_component_data_<timestamp>` directory. This fixture requires
pins `1` and `2`; the alternate footprint must support identity mapping for all
symbol pins and contain extra pads for one-to-many testing. Optional
`--multipart <symbol>` checks a real multipart symbol with its existing mapping.
Original libraries and sheets are not edited or saved. The runner compares
source metadata/Modified, symbol geometry and disk hashes, including the scratch
file, and leaves the scratch copy open for inspection.

Restart the client's MCP connection to load the new Python tool. If Altium keeps
old script buffers, reopen `Altium_API.PrjScr` without saving stale buffers over
the updated source. User configuration is not rewritten by the test runner.

See the [live verification report](LIBRARY_COMPONENT_DATA_LIVE_TEST.md).
