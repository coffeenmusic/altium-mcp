"""Pictures of a board area or a schematic sheet, drawn here rather than
captured from Altium's window, so nothing in Altium moves or comes to the
front.

render_pcb draws what objects_in_area returns (with region outlines) and the
board outline. render_sheet reads the .SchDoc file itself (schdoc_reader),
so the sheet need not be open, and shows what is saved on disk.

Pure Python with PIL; the drawing is unit-tested in tests/test_render.py.
"""

import math
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from pcb_geometry import MULTI_LAYER, is_copper_layer, layer_key, via_layers

MAX_PIXELS = 4000          # longest side; dpi is lowered to stay under it

# Colours close to Altium's defaults, on its black background. Layer names
# go through layer_key, so "Multi Layer" and "multi-layer" are one layer.
PCB_BACKGROUND = (0, 0, 0)
PCB_COLOURS = {layer_key(name): colour for name, colour in {
    "Top Layer": (255, 0, 0), "Bottom Layer": (0, 0, 255), "Multi Layer": (192, 192, 192),
    "Top Overlay": (255, 255, 0), "Bottom Overlay": (128, 128, 0), "Keep-Out Layer": (255, 0, 255),
    "Top Paste": (128, 128, 128), "Bottom Paste": (128, 0, 0), "Top Solder": (128, 0, 128),
    "Bottom Solder": (255, 0, 255), "Drill Drawing": (255, 0, 42),
}.items()}
MID_COLOURS = [(188, 142, 0), (112, 219, 250), (0, 204, 102), (153, 51, 255), (0, 255, 255), (128, 128, 255)]
MECH_COLOUR = (196, 112, 230)
VIA_COLOUR = (160, 160, 160)
HOLE_COLOUR = (60, 60, 60)
OUTLINE_COLOUR = (255, 255, 255)
DEFAULT_PCB_LAYERS = ["Top Layer", "Bottom Layer", "Top Overlay", "Multi Layer"]


def _layer_number(key: str, prefix: str) -> Optional[int]:
    rest = key[len(prefix):]
    return int(rest) if key.startswith(prefix) and rest.isdigit() else None


def layer_colour(layer: str) -> Tuple[int, int, int]:
    key = layer_key(layer)
    if key in PCB_COLOURS:
        return PCB_COLOURS[key]
    if key.startswith("midlayer"):
        n = _layer_number(key, "midlayer")
        return MID_COLOURS[(n - 1) % len(MID_COLOURS)] if n else MID_COLOURS[0]
    if key.startswith("mechanical"):
        return MECH_COLOUR
    return (200, 200, 200)


def layer_order(layer: str) -> int:
    """Drawing order: bottom side first, top side last."""
    key = layer_key(layer)
    order = [layer_key(n) for n in ("Bottom Overlay", "Bottom Solder", "Bottom Paste", "Bottom Layer")]
    if key in order:
        return order.index(key)
    if key.startswith("midlayer"):
        n = _layer_number(key, "midlayer")
        return 100 - n if n is not None else 50
    after = [layer_key(n) for n in ("Top Layer", "Multi Layer", "Top Paste", "Top Solder", "Top Overlay",
                                    "Keep-Out Layer")]
    if key in after:
        return 200 + after.index(key)
    return 300


class Canvas:
    """A box in board or sheet units (mils) mapped to pixels, y up."""

    def __init__(self, bbox, dpi: float, background, margin: float = 0.0):
        x1, y1, x2, y2 = bbox
        self.x0, self.y0 = min(x1, x2) - margin, min(y1, y2) - margin
        self.x1, self.y1 = max(x1, x2) + margin, max(y1, y2) + margin
        span = max(self.x1 - self.x0, self.y1 - self.y0, 1e-6)
        self.dpi = float(dpi)
        if span * self.dpi / 1000.0 > MAX_PIXELS:
            self.dpi = MAX_PIXELS * 1000.0 / span
        self.k = self.dpi / 1000.0                     # pixels per mil
        self.size = (max(1, int(round((self.x1 - self.x0) * self.k))),
                     max(1, int(round((self.y1 - self.y0) * self.k))))
        self.image = Image.new("RGB", self.size, background)

    def p(self, x, y) -> Tuple[float, float]:
        return ((x - self.x0) * self.k, (self.y1 - y) * self.k)

    def d(self, v) -> float:
        return v * self.k


def _font(px: float, name: str = "arial.ttf"):
    size = max(6, int(round(px)))
    for candidate in (name, "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _rect_corners(cx, cy, w, h, angle_deg):
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    out = []
    for dx, dy in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
        out.append((cx + dx * c - dy * s, cy + dx * s + dy * c))
    return out


def _segment(draw, cv: Canvas, x1, y1, x2, y2, width, colour):
    w = max(1, int(round(cv.d(width))))
    a, b = cv.p(x1, y1), cv.p(x2, y2)
    draw.line([a, b], fill=colour, width=w)
    r = w / 2.0
    if w > 2:
        for px, py in (a, b):
            draw.ellipse([px - r, py - r, px + r, py + r], fill=colour)


def _pad(draw, cv: Canvas, o: dict, colour):
    w, h, rot = o["x_size"], o["y_size"], o.get("rotation", 0) % 180
    if o.get("shape") == "round":
        # An oval pad at another angle is drawn upright: close enough to see
        if round(rot) == 90:
            w, h = h, w
        a, b = cv.p(o["x"] - w / 2, o["y"] + h / 2), cv.p(o["x"] + w / 2, o["y"] - h / 2)
        draw.ellipse([a, b], fill=colour)
    elif o.get("shape") == "octagonal":
        cut = min(w, h) * 0.29
        pts = [(-w / 2 + cut, -h / 2), (w / 2 - cut, -h / 2), (w / 2, -h / 2 + cut), (w / 2, h / 2 - cut),
               (w / 2 - cut, h / 2), (-w / 2 + cut, h / 2), (-w / 2, h / 2 - cut), (-w / 2, -h / 2 + cut)]
        a = math.radians(rot)
        draw.polygon([cv.p(o["x"] + x * math.cos(a) - y * math.sin(a), o["y"] + x * math.sin(a) + y * math.cos(a))
                      for x, y in pts], fill=colour)
    else:
        draw.polygon([cv.p(x, y) for x, y in _rect_corners(o["x"], o["y"], w, h, rot)], fill=colour)
    if o.get("hole"):
        r = o["hole"] / 2
        a, b = cv.p(o["x"] - r, o["y"] + r), cv.p(o["x"] + r, o["y"] - r)
        draw.ellipse([a, b], fill=HOLE_COLOUR)


def _arc(draw, cv: Canvas, o: dict, colour):
    w = max(1, int(round(cv.d(o["width"]))))
    # PIL draws the width inward from the box: a box of radius r + w/2 puts
    # the copper from r - w/2 to r + w/2, as Altium has it
    r = o["radius"] + w / cv.k / 2
    a, b = cv.p(o["cx"] - r, o["cy"] + r), cv.p(o["cx"] + r, o["cy"] - r)
    start, end = o["start_angle"], o["end_angle"]
    # PIL measures clockwise on a y-down image; Altium counter-clockwise with y up
    draw.arc([a, b], start=-end, end=-start, fill=colour, width=w)


def _region(sheet: Image.Image, cv: Canvas, contours: List[dict], colour):
    """One region onto sheet: its solid contours less its holes, through a
    mask of the region's own box, so its holes cut nothing else."""
    polys = [(c["hole"], [cv.p(x, y) for x, y in c["points"]]) for c in contours if len(c["points"]) >= 3]
    if not polys:
        return
    xs = [x for _, pts in polys for x, _ in pts]
    ys = [y for _, pts in polys for _, y in pts]
    x0, y0 = max(0, int(math.floor(min(xs)))), max(0, int(math.floor(min(ys))))
    x1, y1 = min(sheet.size[0], int(math.ceil(max(xs))) + 1), min(sheet.size[1], int(math.ceil(max(ys))) + 1)
    if x1 <= x0 or y1 <= y0:
        return
    mask = Image.new("L", (x1 - x0, y1 - y0), 0)
    md = ImageDraw.Draw(mask)
    for hole in (False, True):
        for h, pts in polys:
            if h == hole:
                md.polygon([(x - x0, y - y0) for x, y in pts], fill=0 if hole else 255)
    sheet.paste(tuple(colour) + (255,), (x0, y0, x1, y1), mask)


def _angle_off(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def _arc_sweep(s: dict, nxt: dict) -> Tuple[float, float]:
    """Where an outline arc starts (degrees) and how far it turns (+ is
    counter-clockwise) from its vertex s to the next vertex nxt."""
    a0 = math.degrees(math.atan2(s["y"] - s["cy"], s["x"] - s["cx"]))
    if "start_angle" not in s:
        # Angles not known: the short way round
        a1 = math.degrees(math.atan2(nxt["y"] - s["cy"], nxt["x"] - s["cx"]))
        return a0, (a1 - a0 + 540) % 360 - 180
    # Altium's arc runs counter-clockwise from its start angle to its end
    # angle; the outline walks it from whichever end is this vertex
    ccw = (s["end_angle"] - s["start_angle"]) % 360 or 360
    forward = _angle_off(a0, s["start_angle"]) <= _angle_off(a0, s["end_angle"])
    return a0, ccw if forward else -ccw


def _outline_points(segments: List[dict]) -> List[Tuple[float, float]]:
    """The outline as points, its arcs as short lines."""
    pts = []
    n = len(segments)
    for i, s in enumerate(segments):
        pts.append((s["x"], s["y"]))
        if s["kind"] == 1 and s["radius"] > 0:
            a0, sweep = _arc_sweep(s, segments[(i + 1) % n])
            steps = max(16, int(abs(sweep) / 5))
            for k in range(1, steps):
                a = math.radians(a0 + sweep * k / steps)
                pts.append((s["cx"] + s["radius"] * math.cos(a), s["cy"] + s["radius"] * math.sin(a)))
    return pts


def outline_box(segments: List[dict]) -> Optional[List[float]]:
    """[x1, y1, x2, y2] around the outline, its arcs included."""
    pts = _outline_points(segments)
    if not pts:
        return None
    xs, ys = [x for x, _ in pts], [y for _, y in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


def _via_wanted(v: dict, wanted: set, copper_wanted: bool, stack: Optional[List[str]]) -> bool:
    """Whether a via reaches one of the wanted layers: a through via any
    copper layer; a blind via the layers it spans, when the board's layer
    stack says which those are, else (span not known) any copper layer."""
    ends = {layer_key(v.get("low_layer")), layer_key(v.get("high_layer"))}
    through = ends == {layer_key("Top Layer"), layer_key("Bottom Layer")}
    if not copper_wanted:
        return through and MULTI_LAYER in wanted
    keys = {layer_key(n) for n in (stack or [])}
    if through or not ends <= keys:
        return True
    return bool({layer_key(n) for n in via_layers(v["low_layer"], v["high_layer"], stack)} & wanted)


def render_pcb(objects: List[dict], bbox, layers: Optional[List[str]] = None, dpi: float = 300,
               outline: Optional[List[dict]] = None, labels: bool = True,
               stack: Optional[List[str]] = None) -> Tuple[Image.Image, dict]:
    """Draw board objects (objects_in_area dicts in mils, with region
    contours) inside bbox [x1, y1, x2, y2] (mils), layer by layer. stack is
    the board's copper layers top to bottom, for the layers a blind via spans."""
    cv = Canvas(bbox, dpi, PCB_BACKGROUND)
    wanted = {layer_key(l) for l in (layers or DEFAULT_PCB_LAYERS)}
    copper_wanted = any(is_copper_layer(l, stack) for l in wanted)
    if copper_wanted:
        wanted.add(MULTI_LAYER)
    by_layer: Dict[str, List[dict]] = {}
    names: Dict[str, str] = {}          # layer key -> Altium's name, for drawn
    vias = []
    texts = []
    for o in objects:
        if o["kind"] == "via":
            if _via_wanted(o, wanted, copper_wanted, stack):
                vias.append(o)
            continue
        if o["kind"] in ("polygon", "component") or o.get("hidden"):
            continue
        layer = layer_key(o.get("layer"))
        if layer not in wanted:
            continue
        names.setdefault(layer, (o.get("layer") or "").lower())
        (texts if o["kind"] == "text" else by_layer.setdefault(layer, [])).append(o)

    drawn = {}
    for key in sorted(by_layer, key=layer_order):
        layer = names[key]
        colour = layer_colour(layer)
        # Pours on an image of their own, each region with its holes cut, and
        # the other copper on top: copper in a pour's cut-out stays visible
        sheet = Image.new("RGBA", cv.size, (0, 0, 0, 0))
        copper = Image.new("RGBA", cv.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(copper)
        for o in by_layer[key]:
            k = o["kind"]
            if k == "track":
                _segment(draw, cv, o["x1"], o["y1"], o["x2"], o["y2"], o["width"], colour)
            elif k == "arc":
                _arc(draw, cv, o, colour)
            elif k == "pad":
                _pad(draw, cv, o, colour)
            elif k == "fill":
                cx, cy = (o["x1"] + o["x2"]) / 2, (o["y1"] + o["y2"]) / 2
                w, h = abs(o["x2"] - o["x1"]), abs(o["y2"] - o["y1"])
                draw.polygon([cv.p(x, y) for x, y in _rect_corners(cx, cy, w, h, o.get("rotation", 0))], fill=colour)
            elif k == "region":
                l, b, r, t = o["bounds"]
                _region(sheet, cv, o.get("contours") or [{"hole": False, "points": [(l, b), (r, b), (r, t), (l, t)]}],
                        colour)
            drawn[layer] = drawn.get(layer, 0) + 1
        sheet.alpha_composite(copper)
        # Copper slightly see-through, so the layers below still show
        alpha = 255 if key in (MULTI_LAYER, layer_key("Top Overlay"), layer_key("Bottom Overlay")) else 190
        sheet.putalpha(sheet.getchannel("A").point(lambda v: alpha if v else 0))
        cv.image.paste(sheet, (0, 0), sheet)

    draw = ImageDraw.Draw(cv.image)
    if vias or copper_wanted:
        for v in vias:
            r = v["size"] / 2
            draw.ellipse([cv.p(v["x"] - r, v["y"] + r), cv.p(v["x"] + r, v["y"] - r)], fill=VIA_COLOUR)
            r = v["hole"] / 2
            draw.ellipse([cv.p(v["x"] - r, v["y"] + r), cv.p(v["x"] + r, v["y"] - r)], fill=HOLE_COLOUR)
        drawn["vias"] = len(vias)
    for t in texts:
        colour = layer_colour(t.get("layer", ""))
        font = _font(cv.d(t["height"]) * 1.15)
        x, y = cv.p(t["x"], t["y"])
        rot = round(t.get("rotation", 0)) % 360
        if rot in (0, 360):
            draw.text((x, y), t["text"], fill=colour, font=font, anchor="ls")
        else:
            box = draw.textbbox((0, 0), t["text"], font=font, anchor="ls")
            tile = Image.new("RGBA", (box[2] - box[0] + 2, box[3] - box[1] + 2), (0, 0, 0, 0))
            ImageDraw.Draw(tile).text((-box[0] + 1, -box[1] + 1), t["text"], fill=colour, font=font)
            tile = tile.rotate(rot, expand=True)
            cv.image.paste(tile, (int(x), int(y - tile.size[1])), tile)
    drawn["texts"] = len(texts)
    if outline:
        pts = [cv.p(x, y) for x, y in _outline_points(outline)]
        if len(pts) > 1:
            draw.line(pts + [pts[0]], fill=OUTLINE_COLOUR, width=max(1, int(cv.d(5))))
    return cv.image, {"dpi": round(cv.dpi, 1), "size_px": list(cv.size), "drawn": drawn}


# ---------------------------------------------------------------- schematic

SCH_BACKGROUND = (255, 255, 252)
WIRE = (0, 0, 160)
BODY_LINE = (128, 0, 0)
BODY_FILL = (255, 255, 176)
TEXT = (0, 0, 0)
LABEL = (128, 0, 0)
PORT_FILL = (255, 255, 176)
JUNCTION = (0, 0, 160)
POWER = (0, 0, 0)


def _colour(value, default):
    """Altium stores colours as BGR integers."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return (v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF)


def render_sheet(records: List[dict], bbox=None, dpi: float = 150) -> Tuple[Image.Image, dict]:
    """Draw a sheet's records (schdoc_reader.read_schdoc) inside bbox
    [x1, y1, x2, y2] in mils, or the whole drawing."""
    from schdoc_reader import coord
    from schematic_check import EM_PER_POINT, PIN_DIR, text_box

    hdr = next((r for r in records if r and r.get("RECORD") == "31"), {})
    fonts = {i: (hdr.get(f"FONTNAME{i}", "Times New Roman"), float(hdr.get(f"SIZE{i}", 10) or 10))
             for i in range(1, int(hdr.get("FONTIDCOUNT", 0) or 0) + 1)}
    skip = {i for i, r in enumerate(records) if r and r.get("RECORD") == "39"}
    for i, r in enumerate(records):
        if r and "OWNERINDEX" in r and int(r["OWNERINDEX"]) + 1 in skip:
            skip.add(i)
    comps = {i: (int(r.get("CURRENTPARTID", 1) or 1), int(r.get("DISPLAYMODE", 0) or 0))
             for i, r in enumerate(records) if r and r.get("RECORD") == "1"}

    def owner(r):
        return int(r["OWNERINDEX"]) + 1 if "OWNERINDEX" in r else None

    def visible(r):
        o = owner(r)
        if o not in comps:
            return True
        part, mode = comps[o]
        return int(r.get("OWNERPARTID", -1) or -1) in (-1, 0, part) and \
            int(r.get("OWNERPARTDISPLAYMODE", 0) or 0) == mode

    def pts(r):
        n = int(r.get("LOCATIONCOUNT", 0) or 0)
        return [(coord(r, f"X{k}"), coord(r, f"Y{k}")) for k in range(1, n + 1)]

    items = [(i, r) for i, r in enumerate(records) if r and i not in skip and visible(r)
             and r.get("RECORD") not in ("31", "39", "1") and r.get("ISHIDDEN") != "T"]
    auto = bbox is None
    if auto:
        xs, ys = [], []
        for _, r in items:
            found = list(pts(r))
            if "LOCATION.X" in r or "LOCATION.Y" in r:
                x, y = coord(r, "LOCATION.X"), coord(r, "LOCATION.Y")
                found.append((x, y))
                if "CORNER.X" in r or "CORNER.Y" in r:
                    found.append((coord(r, "CORNER.X"), coord(r, "CORNER.Y")))
                if r.get("RECORD") == "15":
                    found.append((x + coord(r, "XSIZE"), y - coord(r, "YSIZE")))
                if r.get("RECORD") == "18":
                    found.append((x + (coord(r, "WIDTH") or 300), y))
                if r.get("TEXT") and r.get("RECORD") in ("4", "25", "17", "34", "41"):
                    # Room for the text itself, either side of its anchor
                    # (justification decides which): about 60 mil a character
                    reach = 60 * len(r["TEXT"])
                    found += [(x - reach, y - 100), (x + reach, y + 100)]
            for p in found:
                xs.append(p[0])
                ys.append(p[1])
        bbox = (min(xs), min(ys), max(xs), max(ys)) if xs else (0, 0, 1000, 1000)
    cv = Canvas(bbox, dpi, SCH_BACKGROUND, margin=100 if auto else 0)
    draw = ImageDraw.Draw(cv.image)
    lw = max(1, int(round(cv.d(10))))
    counts: Dict[str, int] = {}

    def text(x, y, s, font_id=1, orientation=0, justification=0, colour=TEXT):
        name, size = fonts.get(int(font_id or 1), ("Times New Roman", 10))
        h = size * EM_PER_POINT
        font = _font(cv.d(h), "times.ttf" if "times" in name.lower() else "arial.ttf")
        w = draw.textlength(s, font=font) / cv.k if s else 0
        box = text_box(x, y, w, h, orientation % 4, justification)
        if orientation % 2 == 0:
            a = cv.p(box.l, box.t)
            draw.text(a, s, fill=colour, font=font)
        else:
            tile = Image.new("RGBA", (max(1, int(cv.d(w)) + 2), max(1, int(cv.d(h)) + 4)), (0, 0, 0, 0))
            ImageDraw.Draw(tile).text((1, 0), s, fill=colour, font=font)
            tile = tile.rotate(90 * (orientation % 4), expand=True)
            cv.image.paste(tile, tuple(int(v) for v in cv.p(box.l, box.t)), tile)

    for i, r in items:
        k = r.get("RECORD")
        x, y = coord(r, "LOCATION.X"), coord(r, "LOCATION.Y")
        line = _colour(r.get("COLOR"), BODY_LINE)
        fill = _colour(r.get("AREACOLOR"), BODY_FILL)
        counts[k] = counts.get(k, 0) + 1
        if k == "27":                                      # wire
            draw.line([cv.p(*p) for p in pts(r)], fill=WIRE, width=lw, joint="curve")
        elif k == "26":                                    # bus
            draw.line([cv.p(*p) for p in pts(r)], fill=WIRE, width=lw * 3, joint="curve")
        elif k in ("6", "5"):                              # polyline, bezier (as its control polygon)
            draw.line([cv.p(*p) for p in pts(r)], fill=line, width=lw)
        elif k == "7":                                     # polygon
            p = [cv.p(*q) for q in pts(r)]
            if len(p) >= 3:
                draw.polygon(p, fill=fill if r.get("ISSOLID") == "T" else None, outline=line, width=lw)
        elif k in ("14", "10"):                            # rectangle, rounded rectangle
            a, b = cv.p(x, coord(r, "CORNER.Y")), cv.p(coord(r, "CORNER.X"), y)
            box = [min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])]
            draw.rectangle(box, fill=fill if r.get("ISSOLID") == "T" else None, outline=line, width=lw)
        elif k == "13":                                    # line
            draw.line([cv.p(x, y), cv.p(coord(r, "CORNER.X"), coord(r, "CORNER.Y"))], fill=line, width=lw)
        elif k in ("8", "12", "11"):                       # ellipse, arc, elliptical arc
            rx = coord(r, "RADIUS")
            ry = coord(r, "SECONDARYRADIUS") or rx
            box = [*cv.p(x - rx, y + ry), *cv.p(x + rx, y - ry)]
            if k == "8":
                draw.ellipse(box, fill=fill if r.get("ISSOLID") == "T" else None, outline=line, width=lw)
            else:
                a0 = float(r.get("STARTANGLE", 0) or 0)
                a1 = float(r.get("ENDANGLE", 360) or 360)
                draw.arc(box, start=-a1, end=-a0, fill=line, width=lw)
        elif k == "2":                                     # pin
            cong = int(r.get("PINCONGLOMERATE", 0) or 0)
            d = PIN_DIR[cong & 3]
            length = coord(r, "PINLENGTH")
            draw.line([cv.p(x, y), cv.p(x + d[0] * length, y + d[1] * length)], fill=TEXT, width=lw)
            name, number = r.get("NAME", ""), r.get("DESIGNATOR", "")
            vertical = (cong & 3) in (1, 3)
            # PINCONGLOMERATE: 0x08 shows the name, 0x10 the number
            if name and cong & 0x08 and name.strip():
                # The name sits inside the body, next to the pin's base
                text(x - d[0] * 20, y - d[1] * 20, name, 1, 1 if vertical else 0,
                     (5 if d[0] > 0 else 3) if not vertical else (3 if d[1] > 0 else 5))
            if number and cong & 0x10:
                text(x + d[0] * length / 2, y + d[1] * length / 2 + (0 if vertical else 5), number, 1,
                     1 if vertical else 0, 1)
        elif k == "29":                                    # junction
            rr = 15
            draw.ellipse([*cv.p(x - rr, y + rr), *cv.p(x + rr, y - rr)], fill=JUNCTION)
        elif k == "25":                                    # net label
            text(x, y, r.get("TEXT", ""), r.get("FONTID", 1), int(r.get("ORIENTATION", 0) or 0),
                 int(r.get("JUSTIFICATION", 0) or 0), LABEL)
        elif k in ("4", "34", "41") and r.get("TEXT") and not r.get("TEXT", "").startswith("="):
            text(x, y, r["TEXT"], r.get("FONTID", 1), int(r.get("ORIENTATION", 0) or 0),
                 int(r.get("JUSTIFICATION", 0) or 0), _colour(r.get("COLOR"), TEXT))
        elif k == "17":                                    # power port
            o = int(r.get("ORIENTATION", 0) or 0)
            d = PIN_DIR[o]
            draw.line([cv.p(x, y), cv.p(x + d[0] * 50, y + d[1] * 50)], fill=POWER, width=lw)
            bx, by = x + d[0] * 50, y + d[1] * 50
            draw.line([cv.p(bx - d[1] * 50, by - d[0] * 50), cv.p(bx + d[1] * 50, by + d[0] * 50)], fill=POWER,
                      width=lw * 2)
            text(bx + d[0] * 20, by + d[1] * 20, r.get("TEXT", ""), r.get("FONTID", 1), 0,
                 {0: 3, 1: 1, 2: 5, 3: 7}[o])
        elif k == "18":                                    # port
            w = coord(r, "WIDTH") or 300
            h = coord(r, "HEIGHT") or 100
            style = int(r.get("STYLE", 0) or 0)
            if style in (4, 5, 6, 7):                      # vertical ports run up from the location
                box = [*cv.p(x - h / 2, y + w), *cv.p(x + h / 2, y)]
            else:
                box = [*cv.p(x, y + h / 2), *cv.p(x + w, y - h / 2)]
            draw.rectangle([min(box[0], box[2]), min(box[1], box[3]), max(box[0], box[2]), max(box[1], box[3])],
                           fill=_colour(r.get("AREACOLOR"), PORT_FILL), outline=TEXT, width=lw)
            text(x + 20, y, r.get("NAME", ""), r.get("FONTID", 1), 0, 3)
        elif k == "15":                                    # sheet symbol
            w, h = coord(r, "XSIZE"), coord(r, "YSIZE")
            draw.rectangle([*cv.p(x, y), *cv.p(x + w, y - h)], fill=_colour(r.get("AREACOLOR"), BODY_FILL),
                           outline=_colour(r.get("COLOR"), BODY_LINE), width=lw)
        elif k in ("32", "33") and r.get("TEXT"):          # sheet name, file name
            text(x, y, r["TEXT"], r.get("FONTID", 1), int(r.get("ORIENTATION", 0) or 0), 0)
        elif k == "22":                                    # no-ERC
            rr = 25
            draw.line([*cv.p(x - rr, y - rr), *cv.p(x + rr, y + rr)], fill=(255, 0, 0), width=lw)
            draw.line([*cv.p(x - rr, y + rr), *cv.p(x + rr, y - rr)], fill=(255, 0, 0), width=lw)
    return cv.image, {"dpi": round(cv.dpi, 1), "size_px": list(cv.size),
                      "bbox": [round(cv.x0, 1), round(cv.y0, 1), round(cv.x1, 1), round(cv.y1, 1)]}
