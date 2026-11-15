"""Required metadata and preflight for single and batch symbol creation."""

import json
from pathlib import Path, PureWindowsPath
from urllib.parse import urlsplit

from altium_files import read_altium_text, spec_with_long_library
from library_component_properties import designator, kind as component_kind


REQUIRED_PARAMETERS = ("Value", "MF", "MP", "HelpURL", "Package", "Type", "Instructions", "System")
CANONICAL_NAMES = {name.casefold(): name for name in REQUIRED_PARAMETERS}
PLACEHOLDERS = {"", "none", "null", "n/a", "na", "unknown", "tbd", "todo", "?", "-", "*", "value",
                "new component", "component", "description"}


def text(value, label, nonempty=False):
    if not isinstance(value, str) or "\x00" in value or (nonempty and not value.strip()):
        raise ValueError(f"{label} must be a {'nonempty ' if nonempty else ''}string without NUL")


def validate(symbol_name, description, parameters):
    """Return canonical parameter names; never invent missing component data."""
    text(symbol_name, "symbol_name", True)
    text(description, "description", True)
    if not isinstance(parameters, dict) or len(parameters) > 500:
        raise ValueError("parameters must be an object of at most 500 string values")
    normalized = {}
    seen = set()
    for name, value in parameters.items():
        text(name, "parameter name", True)
        text(value, f"parameter {name}")
        folded = name.casefold()
        if name != name.strip() or any(c in name for c in "=|\r\n"):
            raise ValueError("parameter names cannot have surrounding whitespace or =, |, CR, LF")
        if folded in seen:
            raise ValueError(f"duplicate parameter name: {name}")
        if folded in {"comment", "designator", "description"}:
            raise ValueError(f"{name} is a built-in field, not a custom creation parameter")
        if folded in {"pacage", "pacage metric"}:
            raise ValueError(f"{name} is obsolete; use Package")
        seen.add(folded)
        normalized[CANONICAL_NAMES.get(folded, name)] = value
    missing = [name for name in REQUIRED_PARAMETERS if name not in normalized]
    if missing:
        raise ValueError("missing required parameters: " + ", ".join(missing))
    for name in REQUIRED_PARAMETERS:
        if normalized[name].strip().casefold() in PLACEHOLDERS:
            raise ValueError(f"{name} must contain an actual value, not an empty string or placeholder")
    url = urlsplit(normalized["HelpURL"])
    if url.scheme.lower() not in {"http", "https"} or not url.netloc or any(c.isspace() for c in normalized["HelpURL"]):
        raise ValueError("HelpURL must be an absolute HTTP(S) documentation URL")
    if (description.strip().casefold() in PLACEHOLDERS
            or description.strip().casefold() in {symbol_name.strip().casefold(), normalized["MP"].strip().casefold()}
            or len(description.split()) < 3):
        raise ValueError("description must describe the component category, function/specifications and package "
                         "in at least three words; a placeholder or part number alone is insufficient")
    return normalized


def prepare(symbol_name, description, pins, parameters, part_count=1, graphics=None):
    parameters = validate(symbol_name, description, parameters)
    if type(part_count) is not int or not 1 <= part_count <= 1000:
        raise ValueError("part_count must be an integer from 1 to 1000")
    for name, entries in (("pins", pins), ("graphics", graphics if graphics is not None else [])):
        if not isinstance(entries, list):
            raise ValueError(f"{name} must be a list of strings")
        for entry in entries:
            text(entry, name + " entry", True)
            if entry.startswith("Description="):
                raise ValueError("pass the description in its separate argument")
    fields = {"description": description, "parameter_count": len(parameters)}
    for index, (name, value) in enumerate(parameters.items()):
        fields[f"p{index}.name"] = name
        fields[f"p{index}.value"] = value
    spec = "\n".join(f"{key}={json.dumps(str(value), ensure_ascii=False)[1:-1]}" for key, value in fields.items()) + "\n"
    return spec, parameters


def prepare_batch(spec_file):
    """Preflight creation metadata while preserving upstream's library edits."""
    text(spec_file, "spec_file", True)
    lines = read_altium_text(spec_file).splitlines()
    current = None
    symbols = []
    names = set()
    library_seen = False
    prepared_lines = []
    block_count = 0

    def finish():
        if current is None:
            return
        prepared_lines.append(current["header"])
        if current["mode"] == "SYMBOL":
            _, parameters = prepare(current["symbol_name"], current["description"], current["pins"],
                                    current["parameters"], current["part_count"], current["graphics"])
            current["parameters"] = parameters
            prepared_lines.extend(f"PARAM|{name}|{value}" for name, value in parameters.items())
            if "final_description" in current:
                validate(current["symbol_name"], current["final_description"], parameters)
                current["description"] = current["final_description"]
            symbols.append(current)
        prepared_lines.extend(current["records"])

    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        kind, _, rest = line.partition("|")
        kind = kind.strip().upper()
        try:
            if kind == "LIBRARY":
                if library_seen or current is not None:
                    raise ValueError("LIBRARY may appear only once before the first SYMBOL")
                path = rest.strip()
                if not PureWindowsPath(path).is_absolute() or Path(path).suffix.lower() != ".schlib" or not Path(path).is_file():
                    raise ValueError("LIBRARY must be an absolute path to an existing .SchLib")
                library_seen = True
                prepared_lines.append(kind + "|" + rest)
            elif kind in {"SYMBOL", "EDITSYMBOL"}:
                finish()
                fields = rest.split("|")
                if kind == "SYMBOL" and not 2 <= len(fields) <= 5:
                    raise ValueError("expected SYMBOL|name|description[|part_count[|designator_template[|component_kind]]]; description cannot contain |")
                if kind == "EDITSYMBOL" and len(fields) != 1:
                    raise ValueError("expected EDITSYMBOL|name")
                name = fields[0].strip()
                text(name, "symbol name", True)
                if kind == "SYMBOL" and name.casefold() in names:
                    raise ValueError(f"duplicate symbol name: {name}")
                if kind == "SYMBOL":
                    names.add(name.casefold())
                block_count += 1
                current = {"mode": kind, "header": kind + "|" + rest, "records": [],
                           "symbol_name": name, "description": fields[1] if kind == "SYMBOL" else "",
                           "part_count": int(fields[2]) if len(fields) >= 3 and fields[2].strip() else 1,
                           "parameters": {}, "param_names": set(), "pins": [], "graphics": []}
                if kind == "SYMBOL":
                    current["designator_template"] = designator(fields[3].strip() if len(fields) >= 4 and fields[3].strip() else "U?")
                    current["component_kind"] = component_kind(fields[4].strip() if len(fields) >= 5 and fields[4].strip() else "standard")
                    current["header"] = "|".join((kind, fields[0], fields[1], str(current["part_count"]),
                                                  current["designator_template"], current["component_kind"]))
            elif current is None:
                raise ValueError("expected SYMBOL before component records")
            elif kind == "PARAM":
                if current["mode"] != "SYMBOL":
                    raise ValueError("PARAM belongs to SYMBOL; use SYMPARAM for EDITSYMBOL")
                name, separator, value = rest.partition("|")
                if not separator:
                    raise ValueError("expected PARAM|name|value")
                if name.casefold() in current["param_names"]:
                    raise ValueError(f"duplicate parameter name: {name}")
                current["param_names"].add(name.casefold())
                current["parameters"][CANONICAL_NAMES.get(name.casefold(), name)] = value
            elif kind in {"PIN", "GRAPHIC"}:
                if current["mode"] != "SYMBOL":
                    raise ValueError(f"{kind} belongs only to SYMBOL")
                current["pins" if kind == "PIN" else "graphics"].append(rest)
                current["records"].append(kind + "|" + rest)
            elif kind in {"COMMENT", "DESCRIPTION", "SYMPARAM", "PINPARAM", "PINDESC", "PINSYMBOL", "PINFUNC"}:
                text(rest, kind)
                current["records"].append(kind + "|" + rest)
                if current["mode"] == "SYMBOL":
                    if kind == "COMMENT":
                        current["comment"] = rest.split("|", 1)[0]
                    elif kind == "DESCRIPTION":
                        current["final_description"] = rest
                    elif kind == "SYMPARAM":
                        fields = rest.split("|")
                        if len(fields) < 3:
                            raise ValueError("expected SYMPARAM|name|value|visible[|x|y|justification|font]")
                        name, value = fields[:2]
                        name = CANONICAL_NAMES.get(name.casefold(), name)
                        # SYMPARAM replaces existing values, as upstream does.
                        existing = next((key for key in current["parameters"] if key.casefold() == name.casefold()), name)
                        current["parameters"][existing] = value
            else:
                raise ValueError(f"unknown record: {kind}")
        except (ValueError, TypeError) as exc:
            raise ValueError(f"line {number}: {exc}") from exc
    finish()
    if not block_count:
        raise ValueError("batch must contain at least one SYMBOL or EDITSYMBOL")
    prepared = "\n".join(prepared_lines) + "\n"
    return spec_with_long_library(prepared) or prepared, symbols


def verify(result, description, parameters, comment=None, designator_template=None, component_kind=None):
    """Compare Altium readback to the request, including the native Description."""
    metadata = result.get("metadata", {})
    actual = {name.casefold(): value for name, value in metadata.get("parameters", {}).items()}
    errors = [f"parameter mismatch: {name}" for name, value in parameters.items() if actual.get(name.casefold()) != value]
    if metadata.get("description") != description:
        errors.append("native Description mismatch")
    if metadata.get("comment") != (parameters["MP"] if comment is None else comment):
        errors.append("Comment mismatch")
    for key, expected in (("designator_template", designator_template), ("component_kind", component_kind)):
        if expected is not None and metadata.get(key) != expected:
            errors.append("native " + key + " mismatch")
    result["verification"] = {"passed": not errors, "errors": errors}
    if errors:
        result.update(success=False, error="created symbol metadata readback mismatch", may_have_partial_changes=True)
    return result
