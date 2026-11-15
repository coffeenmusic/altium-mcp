# Symbol creation metadata

`create_schematic_symbol` and `create_symbols_batch` require populated metadata
before creating anything in Altium. Existing project symbols and libraries are
not migrated. `set_library_component_data` remains a general metadata editor;
it does not require the creation schema for existing symbols.

## Required fields

| Parameter | Content |
|---|---|
| `Value` | Nominal value or a useful functional value for non-passives |
| `MF` | Actual manufacturer |
| `MP` | Manufacturer part number |
| `HelpURL` | Absolute HTTP(S) documentation URL |
| `Package` | Package/case designation, such as 0603 or LQFP-100 |
| `Type` | Component or mounting type, such as SMD |
| `Instructions` | Assembly instruction, such as Fit or DNP |
| `System` | Functional block, such as Power or MCU |

All eight fields must contain nonempty string values. Empty strings and common
placeholders (`None`, `N/A`, `TBD`, `*`, etc.) are rejected. Missing information
must be obtained from the user or an authoritative component source; the server
does not invent values. Names match case-insensitively and use the spelling above
when stored. Additional string parameters are allowed, up to 500 total.
JLCPCB/LCSC fields are optional. Duplicate names are rejected.

Use [`get_lcsc_component_info`](LCSC_COMPONENT_DATA.md) to obtain catalog data.
After creation, `set_library_component_lcsc_data(library_path, symbol_name,
lcsc_id, qty=1)` fills only supplier fields after checking the symbol's MP.
Alternatively, merge the chosen card's `lcsc_parameters` into the creation
parameter dictionary; the existing required core fields still apply.

`Pacage` and `Pacage Metric` are rejected; use `Package`. Built-in `Comment`,
`Designator` and `Description` cannot be supplied as custom parameters. Comment
is set to MP and hidden; Value is visible below the symbol, while other custom
parameters are hidden and shared across symbol parts. No document is saved.
Explicit upstream batch records such as `COMMENT` and `SYMPARAM` can override
these presentation defaults; verification uses the requested final values.

## Searchable Description

The separate `description` argument populates Altium's native component
Description (`ComponentDescription`), which is used in library searches.
Include the category/function, important electrical ratings and package, using
verified datasheet information. For example:

```text
Resistor, 10k, 1%, 100mW, 0603 SMD
```

The server rejects blank descriptions, common placeholders, a symbol name or
MPN alone, and descriptions with fewer than three whitespace-separated words.
This is a basic quality gate, not datasheet verification: the caller still must
provide accurate specifications. Description is transported separately from
pin records, preserving Unicode, punctuation, quotes and line breaks.

## Single symbol

`parameters` is now a required argument, before optional `part_count` and
`graphics`. Existing callers must supply it. The desired SchLib must be focused.

```python
create_schematic_symbol(
    symbol_name="R-10K",
    description="Resistor, 10k, 1%, 100mW, 0603 SMD",
    pins=[
        "1|A|eElectricPassive|eRotate0|0|0",
        "2|B|eElectricPassive|eRotate180|600|0",
    ],
    parameters={
        "Value": "10k", "MF": "Example", "MP": "R-10K",
        "HelpURL": "https://example.com/datasheet.pdf",
        "Package": "0603", "Type": "SMD",
        "Instructions": "Fit", "System": "Power",
    },
)
```

The example data is illustrative. Replace it with actual component data.

## Batch creation

Creation headers accept optional native properties:

```text
SYMBOL|name|description[|part_count[|designator_template[|component_kind]]]
```

Omitted/blank optional fields default to `1`, `U?`, `standard`. Old two/three-
field headers after `SYMBOL` remain valid. For example, `|1|J?|standard` selects
a connector prefix; `|1|D?|Standard (No BOM)` sets a diode prefix and native
No BOM kind. To specify only the kind, leave the preceding slots blank:
`SYMBOL|name|description|||standard_no_bom`.

Designators follow the existing [native properties rules](LIBRARY_COMPONENT_PROPERTIES.md):
a letter, then letters/digits/underscore, followed by one `?`, at most 64
characters. Kinds are `standard`, `mechanical`, `graphical`, `net_tie_bom`,
`net_tie_no_bom`, `standard_no_bom`; case-insensitive UI labels also work,
including `Standard (No BOM)`, `Net Tie (In BOM)` and `Net Tie`. Python normalizes
labels and validates every header before any Altium call. Invalid properties
in a later symbol reject the entire request without creating earlier symbols.

Both fields are set on the newly allocated native component inside its creation
transaction, before it is registered in the library. No follow-up property tool,
custom Designator/BOM parameter or extra property-edit transaction is used.
Defaults reset for each symbol. `EDITSYMBOL|name` keeps its existing syntax and
does not accept these creation-only header fields. The single-symbol MCP tool's
signature and default `U?`/`standard` behavior are unchanged.

Upstream's `EDITSYMBOL`, `COMMENT`, `DESCRIPTION`, `SYMPARAM`, `PINPARAM`,
`PINDESC` and `PINSYMBOL` records are supported alongside `PARAM`. Existing-symbol
edit blocks do not require the creation metadata schema and never create a
missing symbol. In creation blocks, `SYMPARAM` values can supply required
metadata, with the same validation as `PARAM`. Metadata readback occurs after
all property edits. Results retain upstream's `edited`, `failed` and `problems`
fields, plus the fork's creation verification. A batch with reported problems
does not claim success.

Add a complete set of `PARAM|name|value` records for **each** symbol:

```text
LIBRARY|C:\Libraries\Parts.SchLib
SYMBOL|R-10K|Resistor, 10k, 1%, 100mW, 0603 SMD|1|R?|standard
PARAM|Value|10k
PARAM|MF|Example
PARAM|MP|R-10K
PARAM|HelpURL|https://example.com/datasheet.pdf
PARAM|Package|0603
PARAM|Type|SMD
PARAM|Instructions|Fit
PARAM|System|Power
PIN|1|A|eElectricPassive|eRotate0|0|0
PIN|2|B|eElectricPassive|eRotate180|600|0
```

All blocks are validated before opening a library or creating the first symbol.
Unknown records, duplicate symbol names and incomplete later blocks reject the
whole request. `LIBRARY` is optional and may appear only before the first symbol;
its path must be absolute and identify an existing SchLib. Records are single
lines; a batch Description cannot contain `|`. Parameter values retain everything
after the second `|`, including additional pipes, commas, quotes and equals signs.
The tool sends a unique BOM-marked UTF-8 copy, leaving the input file unchanged.

## Readback, failures and loading the update

Creation returns actual `metadata`, `verification`, and `saved=false`. Readback
compares all requested parameters, native Description and Comment. Batch creation
also verifies native Designator and ComponentKind, including the legacy defaults,
after all property edits. Batch output
also contains a per-symbol `components` list. A mismatch makes `success=false`.
API failures or timeouts can leave partial in-memory changes; inspect the result
before retrying. No atomic rollback is promised. Uncertain bridge failures retain
the input file under the shared exchange directory for diagnosis.

Restart/reconnect the MCP server to load the new required argument and validator.
If Altium retains old script buffers, reopen `Altium_API.PrjScr` without saving
stale buffers over the updated source. Python and DelphiScript must be updated
together; the old direct bridge request without `metadata_file` is unsupported.

```powershell
python -m unittest server.tests.test_symbol_metadata -v
python dev/test_symbol_creation_live.py --schlib 'C:\Project\Symbols.SchLib'
```

The live runner creates test symbols only in an unsaved scratch copy under the
toolkit's `.altium-mcp` directory and verifies source metadata and disk hashes.

Native batch-property regression:

```powershell
python dev/test_batch_symbol_properties_live.py --exercise --schlib C:\Design\Symbols.SchLib
```

It creates all six kinds in one batch, checks optional/default fields, native
readback, Unicode parameters and multipart handling, rejects malformed later
headers, tests the unchanged single-symbol entry point, and discards the scratch
library without saving. Existing symbols, original project states and guarded
file hashes/timestamps are checked.

Verified in Altium on 2026-11-10. The 15-step run
`.altium-mcp/batch_symbol_properties_20261110_102713/report.json` created nine
symbols in one batch: all six native kinds, UI-label aliases, custom/default
Designator templates, a multipart symbol and legacy/partially omitted headers.
Native batch readback confirmed properties, pins and Unicode parameters without
follow-up property writes. A separate old single-symbol call retained
`U?`/`standard`. Invalid Designator/kind in a later block created nothing.
The scratch library was discarded; an existing symbol, original project states
and all 12 guarded file hashes/timestamps remained unchanged. No document saved.

Regression: 238 selected tests, 237 passed and one platform-specific skip.
The four new batch-property tests cover defaults/aliases, all kinds, preflight
without native calls and native property readback mismatches. Fresh MCP catalog
inspection confirmed the extended format and unchanged `spec_file` signature.
