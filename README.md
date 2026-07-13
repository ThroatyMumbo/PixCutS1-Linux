# PixCut S1 — open-source Linux driver & notes

Reverse-engineered tooling for the **Liene PixCut S1** print-and-cut machine
(USB `302c:3101`), which ships with no Linux support and enumerates as a generic
USB printer. The protocol was recovered by capturing USB traffic with a
[Cynthion](https://greatscottgadgets.com/cynthion/) analyzer + Packetry and
decoding it with `tshark`.

These scripts can drive the machine directly from Linux: composite artwork into
the vendor's print raster, generate a matching cut contour, and stream a
combined print+cut job over USB.

> ⚠️ **Unofficial.** Not affiliated with or endorsed by Liene. Sending a job
> moves the machine and consumes a sheet. Use at your own risk.

## Requirements

- Python 3 and the packages in `requirements.txt`:
  ```
  pip install -r requirements.txt
  ```
- `tshark` (Wireshark CLI) — only for the capture-decoding scripts.
- USB access to the device. Add a udev rule so you don't need root:
  ```
  # /etc/udev/rules.d/70-pixcut.rules
  SUBSYSTEM=="usb", ATTRS{idVendor}=="302c", ATTRS{idProduct}=="3101", MODE="0666", TAG+="uaccess"
  ```
  Then `sudo udevadm control --reload && sudo udevadm trigger`.

The driver only uses vendor interface 2 (endpoints `0x06`/`0x86`), which no
kernel driver claims, so it does not conflict with `usblp` on the print interface.

## Usage

Read-only probe (safe — just queries device state):
```
python3 test_scripts/pixcut_probe.py
```

Build a print+cut job from artwork. Input must be a transparent PNG at print
resolution (1200×2100 = 4×7" @ 300 dpi); transparent pixels are "no ink" for the
printer and "outside the sticker" for the cutter:
```
python3 make_job.py artwork.png            # dry run: writes _print.jpg, _cut.hpgl, _preview.png
python3 make_job.py artwork.png --send     # streams to hardware (moves the machine)
```
`--border` sets the cut offset in mm; `--bias-x/--bias-y` apply an optional
per-machine mechanical registration fudge (default 0). Inspect `_preview.png`
before sending — it overlays the exact cut path (tabs and all) on the print.

Generate just a cut vector:
```
python3 generate_cut.py artwork.png out.hpgl [border_mm]
```

## Layout

- `make_job.py` — end-to-end job builder (PNG → print JPEG + cut HPGL → USB stream).
- `generate_cut.py` — alpha silhouette → offset cut contour with overcut tabs.
- `test_scripts/` — RE and calibration helpers (`pixcut_probe.py`,
  `extract_job.py`, `prep_replay.py`, `replay_job.py`, `calibration_sheet.py`,
  `cut_calibration.py`).
- `test_samples/` — sample inputs/outputs.

## License

[MIT](LICENSE).
