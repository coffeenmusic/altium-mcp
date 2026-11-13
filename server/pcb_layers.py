"""Layer names for set_pcb_layer_visibility, resolved against the board's own.

Altium spells one layer several ways: its layer tab says "Multi-Layer", the
Layer Stack Manager "Top Solder", the scripting API "Multi Layer" and "Top
Solder Mask", and a copper layer may be renamed ("Inner 1" for Mid Layer 1).
The Altium side matches names exactly, so a name it did not know used to be
skipped while the rest were applied: an exclusive show then hid that layer
too. Here every name is resolved against get_pcb_layers first.
"""
import difflib
import re
from typing import Dict, List, Tuple

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
    exact = {l["name"] for l in board}
    found, renamed, unknown = [], {}, []
    for name in names:
        k = key(name)
        real = name if name in exact else by_key.get(k) or (by_id.get(str(_IDS[k])) if k in _IDS else None)
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
