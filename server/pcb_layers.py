"""Shared PCB aliases and visibility planning against the board's actual layers."""

import json
import difflib
import re
from typing import Dict, List, Tuple


_NAMES = ("Top Layer", "Bottom Layer", "Top Overlay", "Bottom Overlay",
          "Top Paste", "Bottom Paste", "Top Solder Mask", "Bottom Solder Mask",
          "Drill Guide", "Drill Drawing", "Keep Out Layer", "Multi-Layer", "No Layer")
_ALIASES = {re.sub(r"[\s_-]", "", name).casefold(): name for name in _NAMES}
_ALIASES.update({"topsolder": "Top Solder Mask", "bottomsolder": "Bottom Solder Mask",
                 "keepout": "Keep Out Layer"})
_FIELDS = {"layer", "layer_name", "from_layer", "to_layer", "low_layer", "high_layer",
           "start_layer", "end_layer", "layers", "layer_names"}


def canonical_name(value, allow_no_layer=True, legacy_only=False):
    if not isinstance(value, str) or not value.strip() or any(c in value for c in "|\r\n\x00"):
        raise ValueError("layer must be a nonempty name without record delimiters")
    name = value.strip()
    compact = re.sub(r"[\s_-]", "", name).casefold()
    if compact.startswith("e"):
        compact = compact[1:]
    mechanical = re.fullmatch(r"(?:mechanical|mech)(?:layer)?(\d+)", compact)
    if mechanical:
        index = int(mechanical[1])
        if not 1 <= index <= 1024:
            raise ValueError("mechanical layer number must be within 1..1024")
        if legacy_only and index > 16:
            raise ValueError("mechanical layers above 16 require the V7 layer API")
        return f"Mechanical {index}"
    mid = re.fullmatch(r"mid(?:layer)?(\d+)", compact)
    if mid and 1 <= int(mid[1]) <= 30:
        return f"Mid Layer {int(mid[1])}"
    canonical = _ALIASES.get(compact, name)
    if canonical == "No Layer" and not allow_no_layer:
        raise ValueError("No Layer is not a valid destination")
    return canonical


def normalize_fields(value, key=""):
    """Copy JSON payloads, normalizing only layer fields, never free text or paths."""
    if isinstance(value, dict):
        return {k: normalize_fields(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_fields(v, key) for v in value]
    if isinstance(value, str) and value.strip() and key in _FIELDS:
        return canonical_name(value)
    if isinstance(value, str) and key == "result":
        try:
            parsed = json.loads(value)
        except ValueError:
            return value
        if isinstance(parsed, (dict, list)):
            return json.dumps(normalize_fields(parsed), ensure_ascii=False)
    return value


def requested_names(value):
    names = []
    def visit(item, key=""):
        if isinstance(item, dict):
            for k, v in item.items():
                visit(v, k)
        elif isinstance(item, list):
            for v in item:
                visit(v, key)
        elif isinstance(item, str) and key in _FIELDS and item not in names:
            names.append(item)
    visit(value)
    return names


# Altium's V6 layer ids by the names Altium uses for them (as key() makes
# them). Mechanical layers are left out: the board reports them by their
# V7 id, and by name they always match.
_IDS = {"toplayer": 1, "bottomlayer": 32, "topoverlay": 33, "bottomoverlay": 34,
        "toppaste": 35, "bottompaste": 36, "topsolder": 37, "topsoldermask": 37,
        "bottomsolder": 38, "bottomsoldermask": 38, "drillguide": 55, "keepout": 56,
        "keepoutlayer": 56, "drilldrawing": 73, "multilayer": 74}
_IDS.update({f"midlayer{n}": n + 1 for n in range(1, 31)})
_IDS.update({f"internalplane{n}": n + 38 for n in range(1, 17)})


def key(name: str) -> str:
    """A layer name in any case, without spaces, hyphens or underscores."""
    return re.sub(r"[\s_\-]", "", (name or "").lower())


def board_layers(layers) -> List[dict]:
    """The layers in a get_pcb_layers result: copper, mechanical, special."""
    if not isinstance(layers, dict):
        return []
    return [l for group in ("copper_layers", "mechanical_layers", "special_layers")
            for l in layers.get(group) or [] if isinstance(l, dict) and l.get("name")]


def displayed(layers) -> Dict[str, bool]:
    """{layer name: shown} for every layer of the board."""
    return {l["name"]: bool(l.get("is_displayed")) for l in board_layers(layers)}


def accepted_names(requested, applied, resolved_names=None):
    """Canonical requested aliases, while retaining real names for custom layers."""
    if requested is None or requested == ["*"]:
        return list(applied)
    if isinstance(requested, str):
        requested = [requested]
    accepted = []
    for name in requested:
        native = (resolved_names or {}).get(name, name)
        if native not in applied and canonical_name(native) not in applied:
            continue
        canonical = canonical_name(name)
        known_alias = key(canonical) in _IDS or re.fullmatch(r"(?:Mechanical|Mid Layer) \d+", canonical)
        value = canonical if known_alias else native
        if value not in accepted:
            accepted.append(value)
    return accepted


def resolve(names: List[str], layers) -> Tuple[List[str], Dict[str, str], List[dict]]:
    """(the board's names for `names`, {asked: board's name} where they
    differ, [{"name", "suggestions"}] for names no layer has).

    A name matches a layer's own name in any case and with or without
    spaces, hyphens and underscores, or Altium's name for that layer
    ("Mid Layer 1" for a copper layer renamed "Inner 1", "Top Solder" for
    "Top Solder Mask", "Multi-Layer" for "Multi Layer")."""
    board = board_layers(layers)
    by_key = {}
    for l in board:
        by_key.setdefault(key(l["name"]), l["name"])
    by_id = {str(l.get("layer_id")): l["name"] for l in board}
    by_mechanical = {str(int(str(l["mechanical_number"]))): l["name"] for l in board
                     if str(l.get("mechanical_number", "")).isdigit()}
    exact = {l["name"] for l in board}
    found, renamed, unknown = [], {}, []
    for name in names:
        k = key(name)
        real = name if name in exact else by_key.get(k) or (by_id.get(str(_IDS[k])) if k in _IDS else None)
        if real is None:
            try:
                canonical = canonical_name(name)
            except ValueError:
                canonical = name
            alias = key(canonical)
            real = by_key.get(alias) or (by_id.get(str(_IDS[alias])) if alias in _IDS else None)
            mechanical = re.fullmatch(r"Mechanical (\d+)", canonical)
            if real is None and mechanical:
                real = by_mechanical.get(str(int(mechanical[1])))
        if real is None:
            close = difflib.get_close_matches(k, list(by_key), n=3, cutoff=0.6)
            unknown.append({"name": name, "suggestions": [by_key[c] for c in close]})
            continue
        if real != name:
            renamed[name] = real
        if real not in found:
            found.append(real)
    return found, renamed, unknown


def plan(layers, layer_names=None, visible=True, exclusive=True, strict=True,
         restore_state=None) -> Tuple[List[dict], dict]:
    """(the set_pcb_layer_visibility requests to send to Altium, the reply so
    far). With a name no layer has, strict sends nothing and the reply says
    which names and what the board has. Without strict the names are sent
    as they are, as before, and Altium skips the unknown ones.

    layer_names ["*"] means every layer. restore_state is a previous_state
    reply: each layer it names is shown or hidden as it says, the others
    are left alone; a layer it names that the board no longer has is
    skipped and listed in unresolved, the others are still restored."""
    state = displayed(layers)
    out = {"previous_state": state}
    if not state:
        return [], {"success": False, "error": "The board reported no layers: is a PCB document open?"}
    if restore_state is not None:
        if not isinstance(restore_state, dict):
            return [], {**out, "success": False,
                        "error": "restore_state is the previous_state object of an earlier reply"}
        groups = [(True, [n for n, v in restore_state.items() if v], False),
                  (False, [n for n, v in restore_state.items() if not v], False)]
    else:
        if isinstance(layer_names, str):
            layer_names = [layer_names]
        if not layer_names or not all(isinstance(n, str) for n in layer_names):
            return [], {**out, "success": False,
                        "error": 'layer_names is a list of layer names, or ["*"] for every layer'}
        names = list(state) if [n.strip() for n in layer_names] == ["*"] else layer_names
        groups = [(bool(visible), names, bool(exclusive))]
    calls, renamed, unknown = [], {}, []
    for shown, names, excl in groups:
        found, ren, unk = resolve(names, layers)
        renamed.update(ren)
        unknown += unk
        if not strict and restore_state is None:
            found += [u["name"] for u in unk]
        if found:
            calls.append({"layer_names": found, "visible": shown, "exclusive": excl})
    if renamed:
        out["resolved_names"] = renamed
    if unknown:
        out["unresolved"] = unknown
        if strict and restore_state is None:
            out.update(success=False, valid_names=list(state),
                       error=f"No layer is called {', '.join(repr(u['name']) for u in unknown)}; "
                             f"nothing was changed. valid_names lists the board's layers.")
            return [], out
    return calls, out
