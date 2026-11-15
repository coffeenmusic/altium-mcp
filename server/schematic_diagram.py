"""Hierarchy edits and non-electrical block diagrams on exact in-memory sheets."""

import json
import math
import uuid
from pathlib import PureWindowsPath

from altium_files import write_altium_text
from document_management import absolute_path
from schematic_document import disk_state


SIDES = {"left", "right", "top", "bottom"}
IOS = {"unspecified", "input", "output", "bidirectional"}


def text(value, name, required=True):
    if not isinstance(value, str) or "\x00" in value or (required and not value.strip()) or len(value) > 4096:
        raise ValueError(f"{name} must be a string of at most 4096 characters without NUL")
    return value


def number(value, name, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or abs(value) > 200000 or not math.isfinite(value):
        raise ValueError(f"{name} must be finite and within +/-200000 mils")
    if positive and value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def fields(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ValueError(f"expected fields {sorted(allowed)}; required {sorted(required)}")


def items(value, name, limit):
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f"{name} must be a list of at most {limit} items")
    return value


def base(sheet_path):
    return {"sheet_path": str(absolute_path(sheet_path, {".schdoc"}))}


def prepare_symbols(sheet_path, operations):
    spec = base(sheet_path)
    items(operations, "operations", 100)
    seen = set()
    for i, operation in enumerate(operations):
        fields(operation, {"op", "unique_id", "properties"}, {"op"})
        action = operation["op"]
        if action not in ("add", "update", "delete"):
            raise ValueError("op must be add, update or delete")
        if action == "add":
            if "unique_id" in operation:
                raise ValueError("add must not have unique_id")
        else:
            uid = text(operation.get("unique_id"), "unique_id")
            if uid in seen:
                raise ValueError("only one operation per symbol per batch")
            seen.add(uid)
        props = operation.get("properties", {})
        fields(props, {"designator", "filename", "x", "y", "width", "height", "entries"})
        if action == "delete" and "properties" in operation:
            raise ValueError("delete must omit properties")
        if action == "update" and not props:
            raise ValueError("update needs properties")
        if action == "add" and {"designator", "filename", "x", "y", "width", "height"} - props.keys():
            raise ValueError("add needs designator, filename, x, y, width and height")
        if ("x" in props) != ("y" in props):
            raise ValueError("supply both x and y")
        p = f"{i}."
        spec.update({p + "op": action, p + "type": "sheet_symbol"})
        if action != "add":
            spec[p + "unique_id"] = operation["unique_id"]
        for key, value in props.items():
            if key == "entries":
                entries = items(value, "entries", 100)
                names, slots = set(), set()
                spec[p + "entry_count"] = len(entries)
                for j, entry in enumerate(entries):
                    fields(entry, {"name", "side", "offset", "io_type"}, {"name", "side", "offset"})
                    name = text(entry["name"], "entry.name")
                    side = entry["side"]
                    if not isinstance(side, str) or side not in SIDES:
                        raise ValueError("entry.side must be left, right, top or bottom")
                    offset = number(entry["offset"], "entry.offset", True)
                    io = entry.get("io_type", "unspecified")
                    if not isinstance(io, str) or io not in IOS:
                        raise ValueError("entry.io_type must be unspecified, input, output or bidirectional")
                    if name.casefold() in names or (side, offset) in slots:
                        raise ValueError("entry names and side/offset pairs must be distinct")
                    limit = props.get("height" if side in {"left", "right"} else "width")
                    if limit is not None and offset >= number(limit, "entry boundary", True):
                        raise ValueError("entry.offset must be strictly inside its symbol edge")
                    names.add(name.casefold())
                    slots.add((side, offset))
                    spec.update({p + f"entry{j}." + k: v for k, v in
                                 {"name": name, "side": side, "offset": offset, "io_type": io}.items()})
            elif key in {"x", "y", "width", "height"}:
                spec[p + key] = number(value, key, key in {"width", "height"})
            else:
                spec[p + key] = text(value, key)
                if key == "filename" and (not value.lower().endswith(".schdoc") or any(c in value for c in "\r\n;")):
                    raise ValueError("filename must reference one .SchDoc")
        for axis, size in (("x", "width"), ("y", "height")):
            if axis in props and size in props:
                number(props[axis] + props[size], "symbol edge")
    spec["count"] = len(operations)
    return spec


def _inside_segment(a, b, box):
    """Whether an axis-aligned segment crosses the open interior of a box."""
    x, y, w, h = box
    if a[0] == b[0]:
        return x < a[0] < x + w and max(min(a[1], b[1]), y) < min(max(a[1], b[1]), y + h)
    return y < a[1] < y + h and max(min(a[0], b[0]), x) < min(max(a[0], b[0]), x + w)


def prepare_diagram(sheet_path, blocks, links, notes):
    spec = base(sheet_path)
    items(blocks, "blocks", 100)
    items(links, "links", 200)
    items(notes, "notes", 100)
    operations, boxes, routes = [], {}, []

    def add(kind, role, **properties):
        operations.append({"op": "add", "type": kind, "role": role, **properties})

    for block in blocks:
        fields(block, {"id", "text", "x", "y", "width", "height"}, {"id", "text", "x", "y", "width", "height"})
        ident = text(block["id"], "block.id")
        if ident in boxes:
            raise ValueError("block IDs must be distinct")
        label = text(block["text"], "block.text", False)
        x, y = (number(block[k], k) for k in ("x", "y"))
        w, h = (number(block[k], k, True) for k in ("width", "height"))
        number(x + w, "block right")
        number(y + h, "block top")
        if any(max(x, bx) < min(x + w, bx + bw) and max(y, by) < min(y + h, by + bh)
               for bx, by, bw, bh in boxes.values()):
            raise ValueError("blocks must not overlap")
        boxes[ident] = (x, y, w, h)
        add("rectangle", f"block:{ident}", x=x, y=y, width=w, height=h)
        if label:
            add("text", f"block_text:{ident}", x=x + w / 2, y=y + h / 2, text=label, centered=1)

    def anchor(ident, side):
        x, y, w, h = boxes[ident]
        return {"left": (x, y + h / 2), "right": (x + w, y + h / 2),
                "top": (x + w / 2, y + h), "bottom": (x + w / 2, y)}[side]

    for i, link in enumerate(links):
        fields(link, {"from", "to", "text", "from_side", "to_side", "points", "arrow"}, {"from", "to"})
        source, target = link["from"], link["to"]
        if not isinstance(source, str) or not isinstance(target, str) or source not in boxes or target not in boxes or source == target:
            raise ValueError("links need two distinct existing block IDs")
        sx, sy, sw, sh = boxes[source]
        tx, ty, tw, th = boxes[target]
        if abs(tx + tw / 2 - sx - sw / 2) >= abs(ty + th / 2 - sy - sh / 2):
            defaults = ("right", "left") if tx >= sx else ("left", "right")
        else:
            defaults = ("top", "bottom") if ty >= sy else ("bottom", "top")
        sides = link.get("from_side", defaults[0]), link.get("to_side", defaults[1])
        if any(not isinstance(side, str) or side not in SIDES for side in sides):
            raise ValueError("link sides must be left, right, top or bottom")
        start, end = anchor(source, sides[0]), anchor(target, sides[1])
        if "points" in link:
            points = items(link["points"], "link.points", 100)
            if len(points) < 2:
                raise ValueError("link.points needs at least two [x,y] points including both anchors")
            path = []
            for point in points:
                if not isinstance(point, (list, tuple)) or len(point) != 2:
                    raise ValueError("link.points needs [x,y] pairs")
                path.append(tuple(number(v, "link coordinate") for v in point))
            if path[0] != start or path[-1] != end:
                raise ValueError("link.points endpoints must equal the selected block anchors")
        elif start[0] == end[0] or start[1] == end[1]:
            path = [start, end]
        elif sides[0] in {"left", "right"} and sides[1] in {"left", "right"}:
            mx = (start[0] + end[0]) / 2
            path = [start, (mx, start[1]), (mx, end[1]), end]
        elif sides[0] in {"top", "bottom"} and sides[1] in {"top", "bottom"}:
            my = (start[1] + end[1]) / 2
            path = [start, (start[0], my), (end[0], my), end]
        else:
            path = [start, (end[0], start[1]) if sides[0] in {"left", "right"} else (start[0], end[1]), end]
        path = [p for j, p in enumerate(path) if j == 0 or p != path[j - 1]]
        if len(path) < 2:
            raise ValueError("link anchors must be separated")
        for a, b in zip(path, path[1:]):
            if a[0] != b[0] and a[1] != b[1]:
                raise ValueError("link segments must be orthogonal")
            if any(_inside_segment(a, b, box) for box in boxes.values()):
                raise ValueError("link crosses a block; supply an explicit route in points")
        arrow = link.get("arrow", True)
        if type(arrow) is not bool:
            raise ValueError("link.arrow must be boolean")
        add("polyline", f"link:{i}", points=path)
        routes.append({"index": i, "from": source, "to": target, "points": path})
        if arrow:
            a, b = path[-2:]
            length = min(40, math.dist(a, b) / 3)
            dx, dy = ((b[0] - a[0]) / math.dist(a, b), (b[1] - a[1]) / math.dist(a, b))
            add("polyline", f"arrow:{i}", points=[(b[0] - dx * length - dy * length / 2, b[1] - dy * length + dx * length / 2),
                                                   b, (b[0] - dx * length + dy * length / 2, b[1] - dy * length - dx * length / 2)])
        label = text(link.get("text", ""), "link.text", False)
        if label:
            j = max(range(len(path) - 1), key=lambda n: math.dist(path[n], path[n + 1]))
            a, b = path[j:j + 2]
            add("text", f"link_text:{i}", x=(a[0] + b[0]) / 2, y=(a[1] + b[1]) / 2 + 45, text=label, centered=1)
    for i, note in enumerate(notes):
        fields(note, {"x", "y", "text"}, {"x", "y", "text"})
        add("text", f"note:{i}", x=number(note["x"], "note.x"), y=number(note["y"], "note.y"), text=text(note["text"], "note.text"), centered=0)
    if len(operations) > 1000:
        raise ValueError("diagram produces more than 1000 primitives")
    for i, op in enumerate(operations):
        for key, value in op.items():
            if key == "points":
                spec[f"{i}.point_count"] = len(value)
                for j, (x, y) in enumerate(value):
                    spec.update({f"{i}.point{j}.x": number(x, "point.x"), f"{i}.point{j}.y": number(y, "point.y")})
            else:
                spec[f"{i}.{key}"] = value
    spec["count"] = len(operations)
    return spec, routes


async def execute(bridge, exchange_dir, command, spec):
    request = exchange_dir / f"{command}_{uuid.uuid4().hex}.txt"
    before = disk_state(spec["sheet_path"])
    write_altium_text(request, "\n".join(f"{k}={json.dumps(str(v), ensure_ascii=False)[1:-1]}" for k, v in spec.items()) + "\n")
    completed = False
    try:
        try:
            response = await bridge.execute_command(command, {"spec_file": str(request)})
        except Exception as exc:
            response = {"error": str(exc)}
        if response.get("command_started") is False:
            completed = True
            return {"success": False, "error": response.get("error"), "saved": False, "may_have_partial_changes": False}
        result = response.get("result")
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except ValueError:
                result = None
        completed = isinstance(result, dict) and type(result.get("success")) is bool
        if not completed:
            return {"success": False, "error": response.get("error", "invalid native result"), "saved": False,
                    "outcome_unknown": True, "may_have_partial_changes": bool(spec["count"]), "spec_file": str(request)}
        errors = []
        if result["success"]:
            if PureWindowsPath(result.get("sheet_path", "")) != PureWindowsPath(spec["sheet_path"]):
                errors.append("target identity mismatch")
            if result.get("saved") is not False or result.get("applied_count") != spec["count"]:
                errors.append("save/applied count mismatch")
            if spec["count"] and result.get("is_modified") is not True:
                errors.append("edited document is not marked Modified")
            objects = {row["unique_id"]: row for row in result.get("objects", [])}
            for i in range(spec["count"]):
                p = f"{i}."
                rows = [row for row in result.get("results", []) if row.get("index") == i]
                if len(rows) != 1:
                    errors.append(f"missing operation result {i}")
                    continue
                uid = rows[0].get("unique_id")
                obj = objects.get(uid)
                if spec[p + "op"] == "delete":
                    if obj is not None:
                        errors.append(f"deleted object still present: {i}")
                    continue
                if obj is None or obj.get("type") != spec[p + "type"]:
                    errors.append(f"object/type readback mismatch: {i}")
                    continue
                for key in ("x", "y", "width", "height", "designator", "filename", "text"):
                    if p + key in spec:
                        expected, actual = spec[p + key], obj.get(key)
                        if (isinstance(expected, (int, float)) and (not isinstance(actual, (int, float)) or abs(actual - expected) > .001)) or (isinstance(expected, str) and actual != expected):
                            errors.append(f"{i}.{key} readback mismatch")
                if p + "point_count" in spec:
                    expected = [[spec[p + f"point{j}.x"], spec[p + f"point{j}.y"]] for j in range(spec[p + "point_count"])]
                    actual = obj.get("points", [])
                    if len(actual) != len(expected) or any(abs(a - b) > .001 for aa, bb in zip(actual, expected) for a, b in zip(aa, bb)):
                        errors.append(f"{i}.points readback mismatch")
                if p + "entry_count" in spec:
                    entries = obj.get("entries", [])
                    expected = [{k: spec[p + f"entry{j}." + k] for k in ("name", "side", "offset", "io_type")} for j in range(spec[p + "entry_count"])]
                    if len(entries) != len(expected):
                        errors.append(f"{i}.entries count mismatch")
                    for entry in expected:
                        matches = [e for e in entries if e.get("name") == entry["name"]]
                        valid = False
                        if len(matches) == 1:
                            actual = matches[0]
                            offset = actual.get("offset")
                            valid = (all(actual.get(k) == v for k, v in entry.items() if k != "offset")
                                     and isinstance(offset, (int, float)) and abs(offset - entry["offset"]) <= .001
                                     and actual.get("owner_unique_id") == uid and bool(actual.get("unique_id")))
                        if not valid:
                            errors.append(f"{i}.entry readback/ownership mismatch: {entry['name']}")
                if obj.get("type") == "sheet_symbol":
                    x, y, w, h = (obj.get(k) for k in ("x", "y", "width", "height"))
                    for entry in obj.get("entries", []):
                        side, offset = entry.get("side"), entry.get("offset")
                        if side not in SIDES or not isinstance(offset, (int, float)):
                            errors.append(f"{i}.entry invalid placement")
                            continue
                        expected = {"left": (x, y + h - offset), "right": (x + w, y + h - offset),
                                    "top": (x + offset, y + h), "bottom": (x + offset, y)}[side]
                        if any(not isinstance(entry.get(k), (int, float)) or abs(entry[k] - expected[j]) > .001
                               for j, k in enumerate(("x", "y"))):
                            errors.append(f"{i}.entry coordinate mismatch: {entry.get('name')}")
        try:
            if disk_state(spec["sheet_path"]) != before:
                errors.append("target file changed unexpectedly")
        except OSError as exc:
            errors.append("cannot verify target file: " + str(exc))
        result["verification"] = {"passed": result["success"] and not errors, "errors": errors}
        if errors:
            result.update(success=False, error="diagram readback verification failed", may_have_partial_changes=bool(spec["count"]))
        return result
    finally:
        if completed:
            request.unlink(missing_ok=True)
