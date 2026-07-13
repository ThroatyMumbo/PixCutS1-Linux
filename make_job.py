#!/usr/bin/env python3
"""End-to-end PixCut S1 job builder: artwork PNG -> (print JPEG + cut HPGL) ->
combo-job stream.

Simple case (this script): the input PNG is ALREADY at print resolution
(1200x2100 = 4x7" @ 300dpi). Transparent background (zero alpha) marks "no ink"
for the printer and "outside the sticker" for the cutter, exactly as the vendor
expects.

  print raster = PNG composited onto WHITE, saved baseline JPEG, 4:4:4, q90
                 (matches the vendor's encoder: SOF0, samp 1x1, ~q90, no DRI).
  cut vector   = generate_cut.py's validated U/D path (alpha silhouette offset
                 outward by the cut border, rounded joins, overcut tabs).

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
import sys, os, re, json, time, struct, argparse, subprocess
import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
PPM  = 300.0 / 25.4                       # px per mm (media raster is 300 dpi)
MEDIA_W, MEDIA_H = 1200, 2100             # 4x7" @ 300dpi -- the print raster size
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


def build_print_jpeg(png_path):
    """PNG -> exact print-raster JPEG bytes (composited on white, vendor format)."""
    im = Image.open(png_path).convert("RGBA")
    if im.size != (MEDIA_W, MEDIA_H):
        sys.exit(f"expected a {MEDIA_W}x{MEDIA_H} image (300dpi 4x7\"), got {im.size}. "
                 "This simple path does no scaling/positioning yet.")
    bg = Image.new("RGB", im.size, (255, 255, 255))     # transparent -> white (no ink)
    bg.paste(im, mask=im.split()[3])
    import io
    buf = io.BytesIO()
    # baseline (no progression), 4:4:4 (subsampling=0), no restart markers -> matches vendor
    bg.save(buf, format="JPEG", quality=JPEG_QUALITY, subsampling=0, optimize=False)
    return buf.getvalue(), bg


def build_cut_hpgl(png_path, border_mm, bias_x, bias_y, out_hpgl):
    """Delegate to the validated generator; return the HPGL text."""
    r = subprocess.run([sys.executable, os.path.join(HERE, "generate_cut.py"),
                        png_path, out_hpgl, str(border_mm), str(bias_x), str(bias_y)],
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
    ap.add_argument("png", help="artwork, transparent bg, 1200x2100 @ 300dpi")
    ap.add_argument("out_prefix", nargs="?", help="output prefix (default: PNG name)")
    ap.add_argument("--border", type=float, default=1.25, help="cut border mm (default 1.25)")
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
    jpeg, base_rgb = build_print_jpeg(args.png)
    print(f"      {len(jpeg)} B  baseline JPEG  {MEDIA_W}x{MEDIA_H}  4:4:4  q{JPEG_QUALITY}")

    print(f"[2/2] cut vector    {args.png} -> {hpgl_path}  (border {args.border} mm, "
          f"bias x={args.bias_x} y={args.bias_y} mm)")
    hpgl = build_cut_hpgl(args.png, args.border, args.bias_x, args.bias_y, hpgl_path)

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
