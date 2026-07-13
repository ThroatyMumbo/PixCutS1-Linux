#!/usr/bin/env python3
"""PixCut RE calibration sheet -> 300dpi transparent PNG.

Canvas == the USABLE/reachable area so the image fills it with no bottom
whitespace (usable is more elongated than 4:7). Bleed measured as 5.1mm on
top/left/right and 0 on the bottom:
  usable = (101.6-2*5.1) x (177.8-5.1) = 91.4 x 172.7 mm
Content is inset ~2.5mm for blade safety; reference marks sit near the corners.
"""
W,H = 91.4, 172.7          # usable area (mm) = the image canvas
CX  = W/2                  # 45.7

COLORS=["#FF0000","#00FF00","#0000FF","#000000","#808080","#C0C0C0"]
CROW=[(f"K_{c[1:]}","rect",(2.5+i*15.48, 32.5, 9, 8), c, True) for i,c in enumerate(COLORS)]

# sizes are exact calibration geometry; positions fill the full usable height
ELEMENTS = [
    ("R1_8mm",  "rect", (2.5, 2.5, 8, 8),   "#E4007F", True),   # corner marks ->
    ("R2_10mm", "rect", (78.9,2.5, 10,10),  "#E4007F", True),   # transform solve
    ("R3_14mm", "rect", (2.5, 156.2,14,14), "#E4007F", True),   # (distinct sizes)
    ("R4_12mm", "rect", (76.9,158.2,12,12), "#E4007F", True),
    ("F_glyph", "F",    (38.7,5,  14, 24),  "#000000", True),   # orientation/mirror
    *CROW,                                                      # print colour probe
    ("C1_40mm", "circle",(CX, 64, 40),      "#00A4FF", True),   # curve encoding
    ("TRI_40x26","tri", (CX, 87.5,40,26),   "#FF6A00", True),   # diagonals+sharp angle
    ("C2_12mm", "circle",(CX, 123,12),      "#00A4FF", True),   # segment scaling
    ("RECT_50x16","rect",(20.7,132.5,50,16),"#FFD400", True),   # X vs Y scale/axis
    ("RR_44x18_r6","rrect",(23.7,152,44,18,6),"#7CFF00", True), # explicit radius
]

def emit(el):
    id_,k,p,fill,cut=el; st='stroke="#000" stroke-width="0.3"' if cut else ''
    if k=="rect":  x,y,w,h=p; return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}" {st}/>'
    if k=="rrect": x,y,w,h,r=p; return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" ry="{r}" fill="{fill}" {st}/>'
    if k=="circle":cx,cy,d=p; return f'<circle cx="{cx}" cy="{cy}" r="{d/2}" fill="{fill}" {st}/>'
    if k=="tri":   cx,ay,bw,h=p; return f'<polygon points="{cx},{ay} {cx-bw/2},{ay+h} {cx+bw/2},{ay+h}" fill="{fill}" {st}/>'
    if k=="F":
        x,y,w,h=p; t=w*0.28
        return f'<path d="M{x},{y} h{w} v{t} h{-(w-t)} v{h*0.34} h{w*0.62} v{t} h{-w*0.62} v{h-2*t-h*0.34} h{-t} z" fill="{fill}" {st}/>'

def svg(guide=False):
    out=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}mm" height="{H}mm" viewBox="0 0 {W} {H}">']
    if guide:
        out.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="none" stroke="#888" stroke-width="0.3"/>')
        out.append(f'<rect x="2.5" y="2.5" width="{W-5}" height="{H-5}" fill="none" stroke="red" stroke-width="0.3" stroke-dasharray="2,2"/>')
    out += [emit(e) for e in ELEMENTS]
    out.append('</svg>'); return "\n".join(out)

open("calibration_sheet.svg","w").write(svg(False))
open("_preview.svg","w").write(svg(True))
print(f"usable canvas {W} x {H} mm  (aspect {W/H:.3f})")
print(f"{'id':12} {'kind':7} {'params (mm)':26} cut")
for e in ELEMENTS: print(f"{e[0]:12} {e[1]:7} {str(e[2]):26} {'yes' if e[4] else 'NO'}")
