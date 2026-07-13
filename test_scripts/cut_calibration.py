#!/usr/bin/env python3
"""Cut-registration calibration target: a full-media 1200x2100 transparent PNG of
solid squares at PRECISELY known positions, for measuring the physical cut against
the preview / design (scale, offset, skew).

Two layers, distinguished by alpha (generate_cut only cuts alpha > 128):
  * FIDUCIALS  (alpha 255, black squares)  -> these GET CUT. Measure the cut piece.
  * REFERENCE  (alpha 110, gray)           -> PRINTED, NOT cut: a crosshair at each
    fiducial's exact center (arms poke out past the cut line) + 10 mm edge rulers.
    Drawn UNDER the squares so they never notch the cut silhouette.

Workflow:
  ./cut_calibration.py                 # -> cut_calib.png + ground-truth table
  ./make_job.py cut_calib.png --border 2.5          # dry run: print.jpg/cut.hpgl/preview
  ./make_job.py cut_calib.png --border 2.5 --send   # print + cut on hardware
Then measure each cut square's edges/center and compare to the printed table.
"""
import argparse
from PIL import Image, ImageDraw

DPI = 300
PPM = DPI / 25.4                       # px per mm
W, H = 1200, 2100                      # full media (4x7") @ 300 dpi
REF_ALPHA = 110                        # reference layer: printed (gray) but NOT cut (<128)

# fiducial square centres in FULL-MEDIA paper-mm (pixel 0,0 = paper top-left).
# Chosen near the reachable usable-area corners/edges (x in ~5.1..96.5, y ~5.1..177.8)
# with clearance for the border+overcut, to give long measurement baselines.
COLS = [15.0, 50.8, 86.0]              # left / centre / right   (X baseline 71.0 mm)
ROWS = [16.0, 90.0, 166.0]             # top  / centre / bottom  (Y baseline 150.0 mm)


def px(v_mm):
    return v_mm * PPM


def build(size_mm, out_png):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    gray = (0, 0, 0, REF_ALPHA)
    black = (0, 0, 0, 255)

    # --- reference layer FIRST (so squares overwrite the crosshair centres) ---
    # edge rulers: ticks every 10 mm along the top and left, longer every 50 mm
    for x in range(10, 96, 10):
        long = (x % 50 == 0)
        dr.line([(px(x), px(2)), (px(x), px(2 + (4 if long else 2.5)))], fill=gray, width=2)
    for y in range(10, 176, 10):
        long = (y % 50 == 0)
        dr.line([(px(2), px(y)), (px(2 + (4 if long else 2.5)), px(y))], fill=gray, width=2)
    # crosshair at each fiducial centre; arms reach past the cut line into the waste
    arm = size_mm / 2 + 4.0
    for cy in ROWS:
        for cx in COLS:
            dr.line([(px(cx - arm), px(cy)), (px(cx + arm), px(cy))], fill=gray, width=2)
            dr.line([(px(cx), px(cy - arm)), (px(cx), px(cy + arm))], fill=gray, width=2)

    # --- fiducial squares LAST (crisp alpha-255 silhouettes -> the cut) ---
    h = size_mm / 2
    for cy in ROWS:
        for cx in COLS:
            dr.rectangle([px(cx - h), px(cy - h), px(cx + h), px(cy + h)], fill=black)

    im.save(out_png)
    return im


def table(size_mm, border_mm):
    h = size_mm / 2
    edge = h + border_mm               # centre -> cut edge (straight sides; corners rounded r=border)
    print(f"\nGround truth  (square {size_mm} mm, border {border_mm} mm):")
    print(f"  cut edge sits {edge:.2f} mm from each centre; outer size {size_mm + 2*border_mm:.2f} mm")
    print(f"  {'name':4} {'centre (x,y) mm':18} {'cut X [min,max]':20} {'cut Y [min,max]'}")
    names = [["TL", "TC", "TR"], ["ML", "MC", "MR"], ["BL", "BC", "BR"]]
    for r, cy in enumerate(ROWS):
        for c, cx in enumerate(COLS):
            print(f"  {names[r][c]:4} ({cx:5.1f},{cy:6.1f})     "
                  f"[{cx-edge:5.1f},{cx+edge:5.1f}]      [{cy-edge:6.1f},{cy+edge:6.1f}]")
    print(f"  centre-to-centre: X {COLS[-1]-COLS[0]:.1f} mm ({COLS[0]}->{COLS[-1]}), "
          f"Y {ROWS[-1]-ROWS[0]:.1f} mm ({ROWS[0]}->{ROWS[-1]})")


def main():
    ap = argparse.ArgumentParser(description="Generate a cut-registration calibration PNG.")
    ap.add_argument("--size", type=float, default=10.0, help="fiducial square size mm (default 10)")
    ap.add_argument("--border", type=float, default=2.5, help="border for the ground-truth table (default 2.5)")
    ap.add_argument("--out", default="cut_calib.png")
    args = ap.parse_args()
    build(args.size, args.out)
    print(f"wrote {args.out}  ({W}x{H}, {len(COLS)*len(ROWS)} fiducials)")
    table(args.size, args.border)
    print(f"\nnext: ./make_job.py {args.out} --border {args.border} [--send]")


if __name__ == "__main__":
    main()
