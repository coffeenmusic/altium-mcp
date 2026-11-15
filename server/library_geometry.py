"""Concentric standard-pad envelopes; all coordinates and distances are mm."""

import math

EPS = 0.00006  # CoordToMils exports at 0.001 mil; allow combined edge quantization.


def rotate(point, degrees):
    a = math.radians(degrees)
    x, y = point
    return x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)


def rectangle(x, y, width, height, rotation=0, approximate=False):
    vertices = []
    for point in ((-width/2, -height/2), (width/2, -height/2),
                  (width/2, height/2), (-width/2, height/2)):
        px, py = rotate(point, rotation)
        vertices.append((x+px, y+py))
    return {"polygon": vertices, "approximate": approximate}


def capsule(x, y, width, height, rotation=0):
    radius = min(width, height)/2
    dx, dy = ((width-height)/2, 0) if width >= height else (0, (height-width)/2)
    dx, dy = rotate((dx, dy), rotation)
    return {"segment": ((x-dx, y-dy), (x+dx, y+dy)), "radius": radius, "approximate": False}


def pad_shape(pad, side, expansion=0):
    width, height = side["width_mm"]+2*expansion, side["height_mm"]+2*expansion
    if min(width, height) <= EPS:
        return None
    x, y, angle = pad["x_mm"], pad["y_mm"], pad["rotation"]
    kind = side["shape"]
    if kind == "round" and abs(width-height) <= EPS:
        return capsule(x, y, width, height, angle)
    # Noncircular round/rounded/octagonal pads use a containing rectangle.
    # A sufficient gap can pass; an insufficient bound must remain unresolved.
    if kind in {"round", "rectangle", "rounded_rectangle", "octagonal"}:
        return rectangle(x, y, width, height, angle, kind != "rectangle")
    return None


def hole_shape(pad):
    size, kind = pad["hole_size_mm"], pad["hole_type"]
    if size <= EPS:
        return None
    x, y = pad["x_mm"], pad["y_mm"]
    angle = pad["rotation"] + pad["hole_rotation"]
    if kind == 0:
        return capsule(x, y, size, size, angle)
    if kind == 1:
        return rectangle(x, y, size, size, angle)
    if kind == 2 and pad["hole_width_mm"] >= size-EPS:
        return capsule(x, y, pad["hole_width_mm"], size, angle)
    return None


def point_segment(point, a, b):
    vx, vy = b[0]-a[0], b[1]-a[1]
    denom = vx*vx+vy*vy
    t = max(0, min(1, ((point[0]-a[0])*vx+(point[1]-a[1])*vy)/denom)) if denom else 0
    return math.hypot(point[0]-a[0]-t*vx, point[1]-a[1]-t*vy)


def cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])


def segment_distance(a, b, c, d):
    u, v, w, z = cross(a,b,c), cross(a,b,d), cross(c,d,a), cross(c,d,b)
    if u*v < 0 and w*z < 0:
        return 0.0
    return min(point_segment(a,c,d), point_segment(b,c,d), point_segment(c,a,b), point_segment(d,a,b))


def inside(point, polygon):
    return all(cross(a, b, point) >= -1e-12 for a,b in zip(polygon, polygon[1:]+polygon[:1]))


def gap(a, b):
    """Nonnegative edge gap; overlap/touch returns zero. Rotation is respected."""
    if "polygon" not in a and "polygon" not in b:
        return max(0, segment_distance(*a["segment"], *b["segment"])-a["radius"]-b["radius"])
    if "polygon" not in a:
        a,b = b,a
    poly = a["polygon"]
    edges = list(zip(poly, poly[1:]+poly[:1]))
    if "polygon" not in b:
        c,d = b["segment"]
        if inside(c, poly) or inside(d, poly):
            return 0.0
        return max(0, min(segment_distance(p,q,c,d) for p,q in edges)-b["radius"])
    other = b["polygon"]
    if inside(poly[0], other) or inside(other[0], poly):
        return 0.0
    return min(segment_distance(p,q,r,s) for p,q in edges for r,s in zip(other,other[1:]+other[:1]))


def annular_ring(pad, side):
    """Minimum ring of concentric rectangle/circle lands around supported holes."""
    hole = hole_shape(pad)
    if hole is None:
        return None
    if side["shape"] == "rectangle":
        if "segment" in hole:
            points, radius = hole["segment"], hole["radius"]
        else:
            points, radius = hole["polygon"], 0
        local = [rotate((x-pad["x_mm"],y-pad["y_mm"]),-pad["rotation"]) for x,y in points]
        return min(side["width_mm"]/2-max(abs(p[0]) for p in local)-radius,
                   side["height_mm"]/2-max(abs(p[1]) for p in local)-radius)
    if side["shape"] == "round" and abs(side["width_mm"]-side["height_mm"]) <= EPS:
        radius = side["width_mm"]/2
        if "segment" in hole:
            return radius-max(math.hypot(x-pad["x_mm"],y-pad["y_mm"]) for x,y in hole["segment"])-hole["radius"]
        return radius-max(math.hypot(x-pad["x_mm"],y-pad["y_mm"]) for x,y in hole["polygon"])
    return None
