#!/usr/bin/env python3
"""Generate a PixCut cut vector from a transparent PNG, the same way the vendor
software appears to: cut = alpha silhouette OFFSET OUTWARD by the cut-border
(Minkowski sum with a disk) -> flattened to polylines. The corner style of that
offset is configurable (round like the vendor, or miter/bevel).

General purpose: works on any transparent PNG (each opaque blob -> one contour).
Usage: generate_cut.py <in.png> [out.hpgl] [border_mm] [bias_x] [bias_y] [join]
       join = round (default, vendor) | miter | bevel
"""
import sys, math, numpy as np, cv2
from shapely.geometry import Polygon, MultiPolygon, Point

# corner style for the outward offset. "round" (disk Minkowski) is what the vendor
# software uses; "miter" keeps sharp corners as extended points; "bevel" clips them
# to a flat chamfer. shapely spells it "mitre"; accept the US "miter" too.
_JOIN_MAP = {"round": "round", "miter": "mitre", "mitre": "mitre", "bevel": "bevel"}

IN   = sys.argv[1]
OUT  = sys.argv[2] if len(sys.argv) > 2 else "generated_cut.hpgl"
BORDER_MM = float(sys.argv[3]) if len(sys.argv) > 3 else 1.25  # cut offset (backed out of vendor data)
BIAS_X_MM = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0   # OPTIONAL mechanical print->cut
BIAS_Y_MM = float(sys.argv[5]) if len(sys.argv) > 5 else 0.0   # registration fudge (NOT in the data;
                                                              # paper feeds between passes -> a feed(Y)-only
                                                              # slip is inherent). Default 0; measure per
                                                              # machine and pass in. +Y shifts the cut UP.
JOIN = (sys.argv[6].lower() if len(sys.argv) > 6 else "round") # corner style: round|miter|bevel
if JOIN not in _JOIN_MAP:
    sys.exit(f"unknown join style {JOIN!r}; use one of round|miter|bevel")
JOIN = _JOIN_MAP[JOIN]
MITRE_LIMIT = 5.0    # cap on how far a miter spike may extend (x border) before it's beveled;
                     # shapely default. Ignored for round/bevel. Keeps acute corners from
                     # growing long spurs that could poke into an adjacent shape.
PRESIMP_MM = 0.12    # kill raster staircase on the input silhouette
FLAT_MM    = 0.05    # polyline flattening tolerance (~vendor's ~0.04mm)
SPUR_MM    = 1.3     # length of each arm of the overcut tab at the start vertex
SPAN_DEG   = 45.0    # angle BETWEEN the two tab arms (each SPAN_DEG/2 off the outward bisector)
FIT_MARGIN_DEG = 8.0 # each arm must clear the adjacent cut edges by this; tighter vertices skipped
PARK = (6476, 0)     # device park position

# FULL-MEDIA paper-mm (pixel 0,0 = paper top-left) -> cut units. Solved by matching
# the vendor's PRINTED shape positions (calib_print.jpg, 1:1 @300dpi) to its cut
# (calib_cut.hpgl) over all 16 calibration shapes -> residual 0.05mm mean / 0.21 max.
# Device native res ~40.52 units/mm; origin baked into the constants (cutY=0 at the
# paper left edge, cutX=0 at the paper bottom edge), so NO separate bleed term. This
# targets a cut that is 1:1 concentric with our full-media 300dpi print (the older
# 39.32 affine carried the vendor's fit-scale and made the cut ~2.5% too small).
def to_cut(x_mm, y_mm):
    x_mm -= BIAS_X_MM; y_mm -= BIAS_Y_MM             # aim high/left so the mechanical slip lands it right
    cx =  0.0055*x_mm - 40.5374*y_mm + 7153.776
    cy = 40.5079*x_mm -  0.0142*y_mm -   22.218
    return round(cx), round(cy)

# --- small 2D vector helpers (design-mm space) for overcut-tab placement ---
def _ring(poly):    return list(poly.exterior.coords)[:-1]
def _sub(a, b):     return (a[0]-b[0], a[1]-b[1])
def _unit(a):       l = math.hypot(*a) or 1.0; return (a[0]/l, a[1]/l)
def _rot(a, t):     c, s = math.cos(t), math.sin(t); return (a[0]*c - a[1]*s, a[0]*s + a[1]*c)
def _sangle(a, b):  return math.atan2(a[0]*b[1] - a[1]*b[0], a[0]*b[0] + a[1]*b[1])  # signed a->b

def outward_bisector(poly, ring, i):
    """Unit vector bisecting the WASTE (exterior) wedge at vertex i, and that
    wedge's half-angle. Purely local (from the two adjacent cut edges), so it's
    correct on concave shapes where a centroid-based 'outward' would not be."""
    n = len(ring); V = ring[i]
    ea = _unit(_sub(ring[(i-1) % n], V))     # V -> previous vertex (one bounding edge)
    eb = _unit(_sub(ring[(i+1) % n], V))     # V -> next vertex     (other bounding edge)
    s = (ea[0]+eb[0], ea[1]+eb[1])
    bis = (-ea[1], ea[0]) if math.hypot(*s) < 1e-6 else _unit(s)   # straight edge -> use normal
    if poly.contains(Point(V[0]+bis[0]*0.05, V[1]+bis[1]*0.05)):   # flip to point into the waste
        bis = (-bis[0], -bis[1])
    return bis, abs(_sangle(bis, ea))        # half-angle of the exterior (waste) wedge


def main():
    im = cv2.imread(IN, cv2.IMREAD_UNCHANGED)
    if im.shape[2] == 4: alpha = im[:, :, 3]
    else: alpha = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)  # fallback: non-white = shape
    mask = (alpha > 128).astype(np.uint8)
    ppm = 300.0 / 25.4   # px per mm (PNG is 300 dpi)

    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    polys = []
    for c in cnts:
        if cv2.contourArea(c) < (0.5*ppm)**2:  # ignore <0.5mm specks
            continue
        pts_mm = c[:, 0, :].astype(float) / ppm          # px -> full-media paper-mm (x=col,y=row)
        p = Polygon(pts_mm)
        if not p.is_valid: p = p.buffer(0)
        p = p.simplify(PRESIMP_MM)                        # de-staircase silhouette
        off = p.buffer(BORDER_MM, join_style=JOIN, quad_segs=16, mitre_limit=MITRE_LIMIT)  # <-- the core op
        off = off.simplify(FLAT_MM)                       # flatten to polylines
        parts = off.geoms if isinstance(off, MultiPolygon) else [off]
        polys.extend(parts)

    def tab_fits(poly, ring, i):
        # a vertex can host the tab only if the waste wedge is wide enough for BOTH
        # arms (plus margin) AND both arms actually stay outside the cut loop.
        bis, half_wedge = outward_bisector(poly, ring, i)
        half = math.radians(SPAN_DEG) / 2
        if half_wedge < half + math.radians(FIT_MARGIN_DEG):
            return False, bis
        V = ring[i]
        for d in (_rot(bis, half), _rot(bis, -half)):
            for t in (0.5, 1.0):
                if poly.contains(Point(V[0]+SPUR_MM*t*d[0], V[1]+SPUR_MM*t*d[1])):
                    return False, bis
        return True, bis

    def best_start(poly, others):
        # consider only vertices where the tab actually fits; among those, put it in
        # the most open waste (farthest from other shapes). Bulletproof: if NOTHING
        # fits (all corners tight), fall back to the widest waste wedge available.
        ring = _ring(poly)
        fits = []
        for i in range(len(ring)):
            ok, bis = tab_fits(poly, ring, i)
            if ok: fits.append((i, bis))
        if not fits:
            return max(range(len(ring)), key=lambda k: outward_bisector(poly, ring, k)[1])
        def clearance(item):
            i, bis = item; V = ring[i]
            probe = Point(V[0]+bis[0]*SPUR_MM*1.5, V[1]+bis[1]*SPUR_MM*1.5)
            return min((probe.distance(o) for o in others), default=1e9)
        return max(fits, key=clearance)[0]

    def xtab(poly, si):
        # overcut tab: two arms SPAN_DEG apart, symmetric about the OUTWARD (waste)
        # bisector, so both stay in the waste (never into the sticker). Blade enters
        # one arm -> cuts the loop -> exits the other, severing the start junction twice.
        ring = _ring(poly); V = ring[si]
        bis, _ = outward_bisector(poly, ring, si)
        half = math.radians(SPAN_DEG) / 2
        d1, d2 = _rot(bis, half), _rot(bis, -half)
        tip1 = (V[0]+SPUR_MM*d1[0], V[1]+SPUR_MM*d1[1])
        tip2 = (V[0]+SPUR_MM*d2[0], V[1]+SPUR_MM*d2[1])
        loop = ring[si:] + ring[:si] + [ring[si]]                  # loop start/end at V
        return [tip1] + loop + [tip2]                             # tip1 -> V -> loop -> V -> tip2

    # emit HPGL-like program
    out = ["IN VER0.1.0 KP42 "]
    for j, poly in enumerate(polys):
        others = [polys[k] for k in range(len(polys)) if k != j]
        cut = [to_cut(x, y) for x, y in xtab(poly, best_start(poly, others))]
        out.append("U%d,%d " % cut[0] + " ".join("D%d,%d" % p for p in cut))
    out.append("U%d,%d  @ " % PARK)
    prog = " ".join(out)
    open(OUT, "w").write(prog)
    print(f"contours: {len(polys)}   join: {JOIN}   bytes: {len(prog)}   -> {OUT}")

if __name__ == "__main__":
    main()
