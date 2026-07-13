#!/usr/bin/env python3
"""End-to-end PixCut S1 job builder: artwork PNG -> (print JPEG + cut HPGL) ->
combo-job stream.

The input PNG is normalized to print resolution (1200x2100 = 4x7" @ 300dpi): if it
is already that size it passes through unchanged (assumed already laid out in the
usable area); otherwise it is scaled to fit (aspect preserved, centered) within the
reachable usable area (5.1mm bleed top/left/right, 0 bottom) -- or the whole sheet
with --full-media -- leaving a transparent margin so the outward cut stays on the
sheet. Transparent background (zero alpha) marks "no ink" for the printer and
"outside the sticker" for the cutter, exactly as the vendor expects.

  print raster = PNG composited onto WHITE, saved baseline JPEG, 4:4:4, q90
                 (matches the vendor's encoder: SOF0, samp 1x1, ~q90, no DRI).
                 An optional --background PNG is composited UNDER the artwork
                 (full-bleed) so it prints but is invisible to the cutter.
  cut vector   = generate_cut.py's validated U/D path (alpha silhouette offset
                 outward by the cut border, round/miter/bevel joins, overcut tabs).
                 Derived from the ARTWORK ONLY -- the background never affects the cut.

DRY RUN (default) touches no hardware and writes three files:
  <out>_print.jpg      the EXACT bytes we'd stream to the printer
  <out>_cut.hpgl       the EXACT cut program we'd stream
  <out>_preview.png    the print JPEG with the cut path drawn in RED, obtained
                       by inverse-mapping the generated cut units back to print
                       pixels -- so it previews the LITERAL file, tabs and all.

  ./make_job.py godot_logo.png                 # -> godot_logo_{print.jpg,cut.hpgl,preview.png}
  ./make_job.py godot_logo.png out --border 1.5

--send builds the combo-job and streams it over USB (interface 2, EP 0x06/0x86),
reusing the proven replay path. THIS MOVES THE MACHINE AND CONSUMES A SHEET.
"""
import sys, os, re, json, time, struct, argparse, subprocess, tempfile
import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
PPM  = 300.0 / 25.4                       # px per mm (media raster is 300 dpi)
MEDIA_W, MEDIA_H = 1200, 2100             # 4x7" @ 300dpi -- the print raster size
BLEED_MM = 5.1                            # reachable/usable-area inset: top/left/right
                                          # have 5.1mm bleed, bottom is 0 (the media edge)
JPEG_QUALITY = 90                         # backed out of the vendor's quant table

# combo-job constants (decoded from real captures)
MEDIA_SIZE, MEDIA_TYPE, JOB_TYPE, CHANNEL, COPIES = 5013, 2030, 600, 0, 1

# full-media paper-mm (pixel 0,0 = paper top-left) -> cut units, solved from the
# vendor calibration (print<->cut, 16 shapes, 0.05mm residual). Mirror of
# generate_cut.to_cut; kept here so we can INVERT it for the preview.
CUT_A = np.array([[0.0055, -40.5374], [40.5079, -0.0142]])
CUT_B = np.array([7153.776, -22.218])
CUT_AINV = np.linalg.inv(CUT_A)
# OPTIONAL mechanical print->cut registration bias (mm, NOT in the data — pass-to-pass
# feed slip). Default 0; measure per machine and pass via --bias-x/--bias-y. The preview
# ADDS these back so it shows the physical (concentric) result, not the compensated data.
BIAS_X_MM, BIAS_Y_MM = 0.0, 0.0

VID, PID, IFACE, EP_OUT, EP_IN = 0x302C, 0x3101, 2, 0x06, 0x86


def fit_to_media(im_rgba, border_mm, full_media=False):
    """Normalize artwork to the 1200x2100 @300dpi RGBA media canvas.

    Exact-size input passes through unchanged: it's assumed to already be laid out in
    the usable area (the validated full-bleed path). Otherwise scale-to-fit preserving
    aspect ratio, centered into a placement box that leaves a transparent margin >= the
    cut border (+1mm safety) so the OUTWARD-offset cut still lands on the sheet. By
    default the box is the reachable USABLE area (5.1mm bleed top/left/right, 0 bottom);
    full_media=True places on the whole sheet instead. Both the print raster and the
    cut vector derive from this canvas, so they stay 1:1 concentric."""
    if im_rgba.size == (MEDIA_W, MEDIA_H):
        return im_rgba, False
    m = int(round((border_mm + 1.0) * PPM))               # keep the outward cut on-sheet
    if full_media:
        x0, y0, x1, y1 = m, m, MEDIA_W - m, MEDIA_H - m
    else:
        b = int(round(BLEED_MM * PPM))                    # usable inset; bottom edge = media edge (0)
        x0, y0, x1, y1 = b + m, b + m, MEDIA_W - b - m, MEDIA_H - m
    box_w, box_h = x1 - x0, y1 - y0
    scale = min(box_w / im_rgba.width, box_h / im_rgba.height)
    new_w, new_h = max(1, round(im_rgba.width*scale)), max(1, round(im_rgba.height*scale))
    art = im_rgba.resize((new_w, new_h), Image.LANCZOS)
    canvas = Image.new("RGBA", (MEDIA_W, MEDIA_H), (0, 0, 0, 0))  # transparent = no ink / outside sticker
    canvas.paste(art, (x0 + (box_w - new_w)//2, y0 + (box_h - new_h)//2))
    return canvas, True


def fit_background(im_rgba):
    """Scale a background image to COVER the full 1200x2100 media (fill, center-crop),
    preserving aspect. Backgrounds are decorative full-bleed under-layers: unlike the
    artwork they carry no cut, so they always fill the whole sheet rather than being
    inset into the usable area. Exact-size input passes through unchanged."""
    if im_rgba.size == (MEDIA_W, MEDIA_H):
        return im_rgba
    scale = max(MEDIA_W / im_rgba.width, MEDIA_H / im_rgba.height)   # cover, not fit
    new_w, new_h = max(1, round(im_rgba.width*scale)), max(1, round(im_rgba.height*scale))
    scaled = im_rgba.resize((new_w, new_h), Image.LANCZOS)
    canvas = Image.new("RGBA", (MEDIA_W, MEDIA_H), (0, 0, 0, 0))
    canvas.paste(scaled, ((MEDIA_W - new_w)//2, (MEDIA_H - new_h)//2))  # center-crop overflow
    return canvas


def build_print_jpeg(im_rgba, bg_rgba=None):
    """Normalized 1200x2100 RGBA -> exact print-raster JPEG bytes (vendor format).
    Composites [white] <- [optional background] <- [artwork]; transparent pixels of
    the top layer let the layer beneath show through, and bare media stays white."""
    base = Image.new("RGBA", im_rgba.size, (255, 255, 255, 255))  # bare media = white (no ink)
    if bg_rgba is not None:
        base = Image.alpha_composite(base, bg_rgba)               # background under the art
    base = Image.alpha_composite(base, im_rgba)                   # artwork on top
    rgb = base.convert("RGB")
    import io
    buf = io.BytesIO()
    # baseline (no progression), 4:4:4 (subsampling=0), no restart markers -> matches vendor
    rgb.save(buf, format="JPEG", quality=JPEG_QUALITY, subsampling=0, optimize=False)
    return buf.getvalue(), rgb


def build_cut_hpgl(png_path, border_mm, bias_x, bias_y, join, out_hpgl):
    """Delegate to the validated generator; return the HPGL text."""
    r = subprocess.run([sys.executable, os.path.join(HERE, "generate_cut.py"),
                        png_path, out_hpgl, str(border_mm), str(bias_x), str(bias_y), join],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"generate_cut.py failed:\n{r.stderr}")
    print("  generate_cut:", r.stdout.strip())
    return open(out_hpgl).read()


def cut_units_to_px(cx, cy, bias_x, bias_y):
    """Inverse of to_cut (-> full-media paper-mm), add the mechanical bias back so the
    preview shows the PHYSICAL landing (concentric), then -> px. Round-trips to <0.2px."""
    xmm, ymm = CUT_AINV @ (np.array([cx, cy], float) - CUT_B)
    return (xmm + bias_x) * PPM, (ymm + bias_y) * PPM


def render_preview(base_rgb, hpgl, out_png, bias_x, bias_y):
    """Draw the generated cut path over the print image in red (pen-down = line)."""
    img = base_rgb.copy()
    dr = ImageDraw.Draw(img)
    pen = None  # last point in px
    for m in re.finditer(r"([UD])(-?\d+),(-?\d+)", hpgl):
        cmd, cx, cy = m.group(1), int(m.group(2)), int(m.group(3))
        pt = cut_units_to_px(cx, cy, bias_x, bias_y)
        if cmd == "D" and pen is not None:
            dr.line([pen, pt], fill=(255, 0, 0), width=3)
        pen = pt
    img.save(out_png)


def send_job(jpeg, hpgl_bytes):
    """Build combo-job, stream [cut][print], poll to done. MOVES THE MACHINE."""
    import usb.core, usb.util
    # NOTE: the device's JSON parser needs COMPACT separators (no spaces). With
    # Python's default ", "/": " it still returns a job_id but mis-parses the
    # nested numeric file-size -> accepts chunk 1 then "unsupported cmd" on the
    # rest. Match the vendor wire format exactly.
    combo = {"method": "combo-job", "params": [
        {"method": "print-job", "params": {"channel": CHANNEL, "copies": COPIES,
            "file-size": len(jpeg), "media-size": MEDIA_SIZE,
            "media-type": MEDIA_TYPE, "job-type": JOB_TYPE}},
        {"method": "cut-job", "params": {"file-size": len(hpgl_bytes), "job-type": JOB_TYPE}}],
        "id": 1}
    payload = hpgl_bytes + jpeg                       # order = [cut vector][print JPEG]

    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None: sys.exit("PixCut not found")
    if dev.is_kernel_driver_active(IFACE): dev.detach_kernel_driver(IFACE)
    usb.util.claim_interface(dev, IFACE)

    def rpc_read(tmo=15000): return bytes(dev.read(EP_IN, 512, timeout=tmo))
    def send_json(method, params, i):
        dev.write(EP_OUT, b"cmd json\n" + json.dumps(
            {"method": method, "params": params, "id": i},
            separators=(",", ":")).encode(), timeout=5000)
        return json.loads(rpc_read().decode("utf-8", "replace"))

    print("waiting for printer ready (state 20) ...")
    ready = False
    for _ in range(30):
        st = send_json("get-prop", ["printer-state", "printer-sub-state", "printer-state-alerts"], 1)
        print("  printer-state:", st.get("result"))
        if st.get("result", [None])[0] == "20": ready = True; break
        time.sleep(2)
    if not ready:
        usb.util.release_interface(dev, IFACE)
        sys.exit("Printer never reached ready(20) -- media loaded? cover closed? Job NOT sent.")

    print("\n>>> sending combo-job (MACHINE WILL START) ...")
    dev.write(EP_OUT, b"cmd json\n" + json.dumps(combo, separators=(",", ":")).encode(), timeout=5000)
    job_id = json.loads(rpc_read().decode("utf-8", "replace"))["result"]["job_id"]
    print("job_id =", job_id)

    # stream in chunks; each carries OUR job_id (LE u32) or it's discarded.
    # CHUNK PAYLOAD MUST BE 10215: the device has a ~10240-byte bulk receive window
    # and both the vendor software and replay frame every chunk as
    # header(22) + jobid(4) + payload(10215) = 10241 B. Larger payloads (we used
    # 10240 -> 10266 B frames) overrun the window and desync the parser after the
    # first chunk -> "unsupported cmd" on every subsequent chunk, transfer-status 3.
    CH = 10215
    n = (len(payload) + CH - 1) // CH
    print(f">>> streaming {len(payload)}B in {n} chunks (job {job_id}) ...")
    for k in range(n):
        part = payload[k*CH:(k+1)*CH]
        frame = b"cmd data EXTLEN=%d\n" % len(part) + struct.pack("<I", job_id) + part
        dev.write(EP_OUT, frame, timeout=20000)
        ok = rpc_read().decode("utf-8", "replace").strip()
        print(f"  chunk {k+1:2}/{n} ({len(part)}B) -> {ok}")

    print("\n>>> polling progress ...")
    saw_busy = False; i = 100
    for _ in range(80):
        ps = send_json("get-prop", ["printer-state", "printer-sub-state", "printer-state-alerts"], i); i += 1
        ji = send_json("get-job-info", {"job-id": job_id}, i); i += 1
        pr = ps.get("result", ["?", "", ""])
        res = ji.get("result"); r = res[0] if isinstance(res, list) and res and isinstance(res[0], dict) else {}
        print(f"  printer-state={pr}  job-state={r.get('job-state')} sub={r.get('job-sub-state')} "
              f"reason={r.get('job-state-reason')} cut-progress={r.get('cutting-progress')}")
        if pr[0] == "40": saw_busy = True
        if saw_busy and pr[0] == "20":
            print("\n*** JOB COMPLETE (printer returned to ready) ***"); break
        time.sleep(3)
    usb.util.release_interface(dev, IFACE)


def main():
    ap = argparse.ArgumentParser(description="PixCut E2E job builder (dry-run by default).")
    ap.add_argument("png", help="artwork, transparent bg (1200x2100 @300dpi used as-is; "
                                 "any other size is scaled to fit)")
    ap.add_argument("out_prefix", nargs="?", help="output prefix (default: PNG name)")
    ap.add_argument("--background", help="optional PNG composited UNDER the artwork (full-bleed, "
                                         "scaled to cover the sheet); prints but is ignored by the cut")
    ap.add_argument("--border", type=float, default=1.25, help="cut border mm (default 1.25)")
    ap.add_argument("--join", choices=["round", "miter", "bevel"], default="round",
                    help="offset corner style: round (vendor default) | miter | bevel")
    ap.add_argument("--full-media", action="store_true",
                    help="when scaling, fit the whole sheet instead of the usable area "
                         "(5.1mm bleed top/left/right); ignored for exact-size input")
    ap.add_argument("--bias-x", type=float, default=BIAS_X_MM,
                    help=f"mechanical cross(X) registration bias mm (default {BIAS_X_MM})")
    ap.add_argument("--bias-y", type=float, default=BIAS_Y_MM,
                    help=f"mechanical feed(Y) registration bias mm, +Y=up (default {BIAS_Y_MM})")
    ap.add_argument("--send", action="store_true",
                    help="STREAM TO HARDWARE (moves the machine, uses a sheet)")
    args = ap.parse_args()

    prefix = args.out_prefix or os.path.splitext(os.path.basename(args.png))[0]
    jpg_path = f"{prefix}_print.jpg"
    hpgl_path = f"{prefix}_cut.hpgl"
    prev_path = f"{prefix}_preview.png"

    print(f"[1/2] print raster  {args.png} -> {jpg_path}")
    art = Image.open(args.png).convert("RGBA")
    norm, scaled = fit_to_media(art, args.border, args.full_media)
    if scaled:
        where = "full media" if args.full_media else "usable area"
        print(f"      input {art.width}x{art.height} != {MEDIA_W}x{MEDIA_H} -> scaled to fit "
              f"{where} (aspect preserved, centered, >={args.border}+1 mm margin)")
    bg_norm = None
    if args.background:
        bg_art = Image.open(args.background).convert("RGBA")
        bg_norm = fit_background(bg_art)
        print(f"      background {args.background} {bg_art.width}x{bg_art.height} -> "
              f"cover {MEDIA_W}x{MEDIA_H} under the artwork (ignored by the cut)")
    jpeg, base_rgb = build_print_jpeg(norm, bg_norm)
    print(f"      {len(jpeg)} B  baseline JPEG  {MEDIA_W}x{MEDIA_H}  4:4:4  q{JPEG_QUALITY}")

    # the cut generator reads a PNG and treats it AS the full 1200x2100 @300dpi media.
    # Pass the exact original when it's already media-sized (byte-for-byte the validated
    # path); otherwise hand it the normalized canvas via a temp PNG so print & cut match.
    if scaled:
        tmp = tempfile.NamedTemporaryFile(prefix="pixcut_norm_", suffix=".png", delete=False)
        tmp.close(); norm.save(tmp.name); cut_png = tmp.name
    else:
        cut_png = args.png

    print(f"[2/2] cut vector    {args.png} -> {hpgl_path}  (border {args.border} mm, "
          f"{args.join} joins, bias x={args.bias_x} y={args.bias_y} mm)")
    try:
        hpgl = build_cut_hpgl(cut_png, args.border, args.bias_x, args.bias_y, args.join, hpgl_path)
    finally:
        if scaled: os.unlink(cut_png)

    if args.send:
        print(f"\n=== SENDING: print {len(jpeg)}B + cut {len(hpgl.encode())}B ===")
        send_job(jpeg, hpgl.encode())
        return

    # dry run: write the exact print bytes + the red-overlay proof
    open(jpg_path, "wb").write(jpeg)
    render_preview(base_rgb, hpgl, prev_path, args.bias_x, args.bias_y)
    print(f"\nDRY RUN complete:\n  {jpg_path}     (exact bytes to printer)\n"
          f"  {hpgl_path}    (exact cut program)\n"
          f"  {prev_path}   (print + cut path in red)")
    print(f"\ncombo-job would declare: print file-size={len(jpeg)}, "
          f"cut file-size={len(hpgl.encode())}, stream order [cut][print].\n"
          "Re-run with --send to drive the hardware.")


if __name__ == "__main__":
    main()
