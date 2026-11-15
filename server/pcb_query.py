"""Running Altium query expressions (the PCB Filter panel's language).

Two traps of the engine, both met on a real board:
  * Bare numbers are read in the engine's own unit, which is not the board's
    display unit (a board shown in mil was queried in mm), and unit suffixes
    ("15mil") are a syntax error. So values are given here with a unit, and
    the expression is sent written both ways, in mm and in mil; the bridge
    finds the engine's unit and runs the matching one.
  * Decimals must use a dot, whatever the Windows locale.

A malformed expression makes Altium stop with a modal "... expected at Line:2
... Continue execution?" dialog. watch_errors answers it (continue, so the
query returns empty) and keeps the message to report instead.
"""
import asyncio
import ctypes
import re
from ctypes import wintypes
from typing import List, Tuple

import pcb_shapes
from sandbox import _process_name

_VALUE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?|\.\d+)\s*(mils?|mm|um|µm|in|inch)\b", re.I)
_TO_MM = {"mil": 0.0254, "mils": 0.0254, "mm": 1.0, "um": 0.001, "µm": 0.001, "in": 25.4, "inch": 25.4}


def _number(v: float) -> str:
    return f"{v:.6f}".rstrip("0").rstrip(".") or "0"


# Quoted text ('U1', 'CAP 4.7uF 2.2mm'); an unclosed quote runs to the end
_QUOTED = re.compile(r"'[^']*(?:'|$)")


def _outside_quotes(expression: str, change) -> str:
    """The expression with change applied to the parts outside quoted text."""
    parts, last = [], 0
    for m in _QUOTED.finditer(expression):
        parts += [change(expression[last:m.start()]), m.group(0)]
        last = m.end()
    return "".join(parts + [change(expression[last:])])


def _blank_quotes(expression: str) -> str:
    """The expression with each quoted text emptied, for checks."""
    return _QUOTED.sub("''", expression)


def with_units(expression: str) -> Tuple[str, str, List[str]]:
    """The expression with every value that has a unit written in mm and in
    mil, and the values found (for the reply). Quoted text is left as it is:
    a name or comment may hold a size ('CAP 0603 2.2mm')."""
    found = []

    def convert(scale):
        def sub(m):
            mm = float(m.group(1)) * _TO_MM[m.group(2).lower()]
            found.append(m.group(0))
            return _number(mm / scale)
        return lambda text: _VALUE.sub(sub, text)

    in_mm = _outside_quotes(expression, convert(1.0))
    in_mil = _outside_quotes(expression, convert(0.0254))
    return in_mm, in_mil, found[: len(found) // 2]


# Properties that hold a length, so a bare number compared with them is read
# in the engine's unit. Angles, counts and the like are left alone.
_SIZE_PROPERTY = re.compile(
    r"^(?:[XY]\d?|.*(?:width|size|hole|radius|diameter|height|length|gap|clearance|expansion|"
    r"thickness|distance|offset|margin|standoff|pullback))$", re.I)


def bare_numbers(expression: str) -> List[str]:
    """Numbers without a unit compared with a length property (Width > 6),
    so the reply can warn that the engine reads them in its own unit."""
    stripped = _VALUE.sub("", _blank_quotes(expression))
    return [number for prop, number in
            re.findall(r"(\w+)\s*(?:<=|>=|<>|<|>|=)\s*(\d+(?:\.\d+)?)(?![\w.])", stripped)
            if _SIZE_PROPERTY.match(prop)]


# which_rule's rule kinds; the one-object ones take no object_b
RULE_KINDS = ("clearance", "short_circuit", "hole_to_hole", "silk_to_silk", "silk_to_mask",
              "component_clearance", "polygon_connect", "width", "hole_size", "routing_via",
              "solder_mask_expansion", "paste_mask_expansion", "plane_connect")
UNARY_RULE_KINDS = ("width", "hole_size", "routing_via", "solder_mask_expansion",
                    "paste_mask_expansion", "plane_connect")


def check_rule_kind(kind: str, object_b: str) -> str:
    """'' when which_rule can run kind with this object_b, else what is wrong."""
    if kind not in RULE_KINDS:
        return f"unknown rule_kind {kind!r}; one of {', '.join(RULE_KINDS)}"
    if kind in UNARY_RULE_KINDS and object_b.strip():
        return f"{kind} rules apply to one object: leave object_b empty"
    if kind not in UNARY_RULE_KINDS and not object_b.strip():
        return f"{kind} rules apply between two objects: give object_b"
    return ""


def rule_note(kind: str, result: dict) -> str:
    """A note for which_rule's reply: why no rule applies, or that the rule
    found is disabled."""
    if result.get("rule"):
        return "" if result.get("enabled", True) else "The rule is disabled, so the DRC does not test it."
    a, b = result.get("object_a") or {}, result.get("object_b") or {}
    if kind not in UNARY_RULE_KINDS and a.get("net") and a.get("net") == b.get("net"):
        return (f"No {kind} rule applies to this pair. Both are on net {a['net']}: a rule whose net "
                f"scope is Different Nets Only (the default for clearance) does not apply between "
                f"objects of the same net.")
    return f"No {kind} rule applies to " + ("this object." if kind in UNARY_RULE_KINDS else "this pair.")


# A function call's argument list (not And/Or/Not before a bracket)
_CALL_ARGUMENTS = re.compile(r"(?<=\w)(?<!\band)(?<!\bnot)(?<!\bor)\s*\([^()']*\)", re.I)

# Where the engine expects a layer name, so pcb_utils' QueryLayerNames can
# give a generic one the board's name: OnLayer('..') (ExistsOnLayer too) and
# Layer = / <> '..', written in one form
_LAYER_CALL = re.compile(r"(?<![\w.])(OnLayer|ExistsOnLayer)\s*\(\s*'([^']*)'\s*\)", re.I)
_LAYER_COMPARE = re.compile(r"(?<![\w.])Layer\s*(=|<>)\s*'", re.I)


def layer_arguments(expression: str) -> str:
    """The expression with its layer arguments written as QueryLayerNames
    looks for them: OnLayer('Mid Layer 1'), Layer = 'Mid Layer 1'."""
    expression = _LAYER_CALL.sub(lambda m: f"{m.group(1)}('{m.group(2)}')", expression)
    return _LAYER_COMPARE.sub(lambda m: f"Layer {m.group(1)} '", expression)


def check(expression: str) -> str:
    """'' when the expression can be sent, else what is wrong."""
    if not expression.strip():
        return "the expression is empty"
    if any(c in expression for c in '"\r\n'):
        return 'the expression cannot contain " or a line break; quote names with \' as Altium does'
    # A comma between digits is a decimal comma, except between the
    # arguments of a function such as InRegionAbsolute(10,20,30,40) and
    # inside quoted text, where it is part of a name (InNet('D0,1'))
    if re.search(r"\d,\d", _CALL_ARGUMENTS.sub("()", _VALUE.sub("", _blank_quotes(expression)))):
        return "write decimals with a dot (0.25mm), not a comma"
    # The engine has no position properties ("Undeclared identifier: X")
    unquoted = re.sub(r"'[^']*'", "''", expression)
    if re.search(r"(?<![\w.])[XY]\s*(?:[<>=]|<>)", unquoted, re.I):
        return ("Altium's query language has no X or Y property; give the box as area=[x1, y1, x2, y2] "
                "instead (which_rule: area_a, area_b)")
    return ""


QUERY_LIMIT_MAX = 1_000_000


def check_limit(limit) -> str:
    """'' when limit is a whole number from 0 (counts only) to
    QUERY_LIMIT_MAX, else what is wrong. The bridge reads it with StrToInt,
    which overflows past 2^31."""
    if isinstance(limit, bool) or not isinstance(limit, (int, float)) or limit != int(limit):
        return "limit must be a whole number"
    if not 0 <= limit <= QUERY_LIMIT_MAX:
        return f"limit must be from 0 to {QUERY_LIMIT_MAX:,}"
    return ""


_MILS_PER = {"mil": 1.0, "mm": 1 / 0.0254}


def area_text(area, unit: str = "mil") -> Tuple[str, str]:
    """An area [x1, y1, x2, y2] from the board origin as the bridge takes it
    ('x1|y1|x2|y2' in internal units), or ('', error). None or [] is the
    whole board: ('', ''). Clamped to Altium's design space by
    pcb_shapes.area_box: past about 214,748 mil the bridge's StrToInt
    overflows."""
    if not area:
        return "", ""
    if unit not in _MILS_PER:
        return "", "unit must be 'mil' or 'mm'"
    if not isinstance(area, (list, tuple)) or len(area) != 4:
        return "", "area must be [x1, y1, x2, y2]"
    try:
        mils = [float(v) * _MILS_PER[unit] for v in area]
    except (TypeError, ValueError):
        return "", "area must be [x1, y1, x2, y2]"
    box, error = pcb_shapes.area_box(mils)
    if error:
        return "", error.replace(" in mils", "")
    return box, ""


_proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _text(h) -> str:
    """A window's text, '' when its thread does not answer within 0.5 s
    (SMTO_ABORTIFHUNG): a hung window must not block the server."""
    b = ctypes.create_unicode_buffer(512)
    answered = ctypes.c_size_t()
    if not ctypes.windll.user32.SendMessageTimeoutW(h, 0x000D, 512, b, 0x0002, 500, ctypes.byref(answered)):
        return ""
    return b.value


def _cls(h) -> str:
    b = ctypes.create_unicode_buffer(64)
    ctypes.windll.user32.GetClassNameW(h, b, 64)
    return b.value


def _error_dialogs():
    user32 = ctypes.windll.user32
    found = []

    def cb(h, _):
        if user32.IsWindowVisible(h) and _cls(h) == "#32770" and _process_name(h).lower() == "x2.exe":
            kids = []
            user32.EnumChildWindows(h, _proto(lambda c, _: kids.append(c) or True), 0)
            message = next((_text(c) for c in kids
                            if _cls(c) == "Static" and "Continue execution" in _text(c)), "")
            ok = next((c for c in kids if _text(c) == "OK"), None)
            if message and ok:
                found.append((h, message, ok))
        return True
    user32.EnumWindows(_proto(cb), 0)
    return found


def _message_boxes(title: str):
    user32 = ctypes.windll.user32
    found = []

    def cb(h, _):
        # Altium's own windows only: the text is asked for last
        if (user32.IsWindowVisible(h) and _cls(h) == "TMessageForm"
                and _process_name(h).lower() == "x2.exe" and _text(h) == title):
            found.append(h)
        return True
    user32.EnumWindows(_proto(cb), 0)
    return found


def _press_ok(box) -> None:
    """Altium's TXPBitBtn ignores BM_CLICK; posted mouse messages work."""
    user32 = ctypes.windll.user32
    kids = []
    user32.EnumChildWindows(box, _proto(lambda c, _: kids.append(c) or True), 0)
    ok = next((c for c in kids if _text(c) == "OK"), None)
    if ok is None:
        user32.PostMessageW(box, 0x0010, 0, 0)
        return
    r = wintypes.RECT()
    user32.GetClientRect(ok, ctypes.byref(r))
    lp = ((r.bottom // 2) << 16) | (r.right // 2)
    user32.PostMessageW(ok, 0x0201, 1, lp)
    user32.PostMessageW(ok, 0x0202, 0, lp)


async def watch_errors(errors: List[str], abort: asyncio.Event) -> None:
    """Answer Altium's script error dialogs while a query runs. Runs until
    cancelled by the caller.

    A syntax error stops the query once: continuing lets it return empty. An
    unknown name ("Undeclared identifier: InPad") fails again for every
    object tested, one dialog each, so a second dialog cancels the script and
    sets abort, and the caller stops waiting for it."""
    while True:
        # Altium's own "Error" box (comparing a text property with a number,
        # for one): its text is not readable, and closing it lets the query end
        for h in _message_boxes("Error"):
            errors.append("Altium reported an error while evaluating it (a property compared with a "
                          "value of the wrong type, e.g. Name = 1 instead of Name = '1'? Put each "
                          "comparison in brackets: IsComponent And Footprint = 'X' is read as "
                          "(IsComponent And Footprint) = 'X'; write IsComponent And (Footprint = 'X'))")
            _press_ok(h)
        for h, message, ok in _error_dialogs():
            errors.append(" ".join(message.split()).replace(" Continue execution?", ""))
            if len(errors) == 1:
                ctypes.windll.user32.SendMessageW(ok, 0x00F5, 0, 0)        # BM_CLICK: continue
            else:
                ctypes.windll.user32.PostMessageW(h, 0x0111, 2, 0)        # WM_COMMAND IDCANCEL
                abort.set()
        await asyncio.sleep(0.2)
