"""Validation, escaped-spec transport and readback for SchLib metadata edits."""

import json
from pathlib import Path, PureWindowsPath
import re


def text(value, label, nonempty=False):
    if not isinstance(value, str) or "\x00" in value or (nonempty and not value.strip()):
        raise ValueError(f"{label} must be a {'nonempty ' if nonempty else ''}string without NUL")


def library_path(value, extension):
    text(value, "library_path", True)
    path = Path(value)
    if not PureWindowsPath(value).is_absolute() or path.suffix.lower() != extension or not path.is_file():
        raise ValueError(f"expected an absolute path to an existing {extension}: {value}")
    return str(path.resolve())


def map_identifier(value, label):
    text(value, label, True)
    if any(c.isspace() or c in ",:()=" for c in value):
        raise ValueError(f"{label} contains whitespace or unsupported map delimiters")


def prepare_request(path, symbol_name, parameters, footprint_models, comment, description):
    path = library_path(path, ".schlib")
    text(symbol_name, "symbol_name", True)
    spec = {"library_path": path, "symbol_name": symbol_name, "parameter_count": 0, "model_count": 0}
    for name, value in (("comment", comment), ("description", description)):
        if value is not None:
            text(value, name)
            spec[name] = value
    if parameters is not None:
        if not isinstance(parameters, dict) or len(parameters) > 500:
            raise ValueError("parameters must be an object of at most 500 string values")
        names = set()
        spec["parameter_count"] = len(parameters)
        for index, (name, value) in enumerate(parameters.items()):
            text(name, "parameter name", True)
            text(value, "parameter value")
            if name.casefold() in names or name.casefold() in ("comment", "designator"):
                raise ValueError("parameter names must be distinct; use comment separately and set_library_component_properties for Designator")
            names.add(name.casefold())
            spec[f"p{index}.name"] = name
            spec[f"p{index}.value"] = value
    if footprint_models is not None:
        if not isinstance(footprint_models, list) or len(footprint_models) > 100:
            raise ValueError("footprint_models must be a list of at most 100 model definitions")
        names = set()
        current = 0
        spec["model_count"] = len(footprint_models)
        for index, model in enumerate(footprint_models):
            if not isinstance(model, dict) or set(model) - {"model_name", "library_path", "pin_map", "is_current", "description"}:
                raise ValueError(f"model {index}: unknown fields")
            text(model.get("model_name"), "model_name", True)
            name = model["model_name"]
            if name.casefold() in names:
                raise ValueError("duplicate model_name")
            names.add(name.casefold())
            prefix = f"m{index}."
            spec[prefix + "name"] = name
            spec[prefix + "library"] = library_path(model.get("library_path"), ".pcblib")
            if "description" in model:
                text(model["description"], "model description")
                spec[prefix + "description"] = model["description"]
            if "is_current" in model:
                if type(model["is_current"]) is not bool:
                    raise ValueError("is_current must be a boolean")
                spec[prefix + "current"] = int(model["is_current"])
                current += model["is_current"]
            mapping = model.get("pin_map")
            if not isinstance(mapping, dict) or not 1 <= len(mapping) <= 5000:
                raise ValueError("pin_map must map 1 to 5000 pin numbers to nonempty pad-number lists")
            used_pads = set()
            spec[prefix + "pin_count"] = len(mapping)
            rows = []
            for pin in mapping:
                map_identifier(pin, "pin number")
            for pin_index, (pin, pads) in enumerate(sorted(mapping.items())):
                map_identifier(pin, "pin number")
                if not isinstance(pads, list) or not 1 <= len(pads) <= 1000:
                    raise ValueError("each pin must map to a nonempty list of pad numbers")
                for pad in pads:
                    map_identifier(pad, "pad number")
                    if pad in used_pads:
                        raise ValueError("each pad may occur only once in a model's pin_map")
                    used_pads.add(pad)
                key = prefix + f"pin{pin_index}."
                spec[key + "name"] = pin
                spec[key + "pad_count"] = len(pads)
                for pad_index, pad in enumerate(pads):
                    spec[key + f"pad{pad_index}"] = pad
                rows.append(f"({pin}:{','.join(pads)})")
            spec[prefix + "map"] = ",".join(rows)
        if current > 1:
            raise ValueError("at most one requested footprint model may have is_current=true")
    return "\n".join(f"{key}={json.dumps(str(value), ensure_ascii=False)[1:-1]}" for key, value in spec.items()) + "\n"


def parse_pin_map(value):
    """Parse Altium's '(pin:pad, pad),(pin:pad)' representation strictly."""
    result = {}
    position = 0
    while position < len(value):
        match = re.match(r"\s*\(([^():,]+):([^()]*)\)\s*", value[position:])
        if not match:
            raise ValueError("unsupported Altium pin-map representation")
        pin = match[1].strip()
        pads = [pad.strip() for pad in match[2].split(",")]
        if pin in result or not pin or not all(pads):
            raise ValueError("duplicate/empty pin or pad in Altium pin map")
        result[pin] = pads
        position += match.end()
        if position < len(value):
            if value[position] != ",":
                raise ValueError("unsupported Altium pin-map separator")
            position += 1
            if position == len(value):
                raise ValueError("trailing Altium pin-map separator")
    return result


def enrich(snapshot):
    for model in snapshot["models"]:
        if model["model_type"].upper() == "PCBLIB":
            try:
                model["pin_map"] = parse_pin_map(model["map_as_string"])
            except ValueError as exc:
                model["pin_map"] = None
                model["mapping_warning"] = str(exc)
    return snapshot


def verify(snapshot, parameters, footprint_models, comment, description):
    errors = []
    actual_parameters = {name.casefold(): value for name, value in snapshot["parameters"].items()}
    for name, value in (parameters or {}).items():
        if actual_parameters.get(name.casefold()) != value:
            errors.append(f"parameter readback mismatch: {name}")
    for key, value in (("comment", comment), ("description", description)):
        if value is not None and snapshot[key] != value:
            errors.append(f"{key} readback mismatch")
    for requested in footprint_models or []:
        matches = [m for m in snapshot["models"] if m["model_type"].upper() == "PCBLIB" and
                   m["model_name"].casefold() == requested["model_name"].casefold()]
        if len(matches) != 1:
            errors.append(f"model readback is missing or ambiguous: {requested['model_name']}")
            continue
        model = matches[0]
        mapping = model["pin_map"]
        if mapping is None or {p: sorted(v) for p, v in mapping.items()} != {p: sorted(v) for p, v in requested["pin_map"].items()}:
            errors.append(f"pin-map readback mismatch: {requested['model_name']}")
        links = model["datafile_links"]
        if (len(links) != 1 or links[0]["entity_name"].casefold() != requested["model_name"].casefold() or
                links[0]["file_kind"].upper() != "PCBLIB" or
                Path(links[0]["location"]).resolve() != Path(requested["library_path"]).resolve()):
            errors.append(f"library-link readback mismatch: {requested['model_name']}")
        for field in ("description", "is_current"):
            if field in requested and model[field] != requested[field]:
                errors.append(f"model {field} readback mismatch: {requested['model_name']}")
    return errors
