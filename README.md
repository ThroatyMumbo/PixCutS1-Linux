# PixCut S1 — open-source Linux driver & notes

Reverse-engineered tooling for the **Liene PixCut S1** sticker printer,
which does not currently support Linux. The protocol was recovered by
capturing USB traffic with a [Cynthion](https://greatscottgadgets.com/cynthion/)
analyzer + Packetry and decoding it with `tshark`.

Currently USB only - does **not** support the Bluetooth communication layer.
And honestly, does anybody actually like Bluetooth?

> ⚠️ **Unofficial.** Not affiliated with or endorsed by Liene. Use at your own risk.

## Requirements

- Python 3 and the packages in `requirements.txt` (or just run `./pixcut` which installs everything in a venv automatically)
- USB access to the device. Add a udev rule so you don't need root:
  ```
  # /etc/udev/rules.d/70-pixcut.rules
  SUBSYSTEM=="usb", ATTRS{idVendor}=="302c", ATTRS{idProduct}=="3101", MODE="0666", TAG+="uaccess"
  ```
  Then `sudo udevadm control --reload && sudo udevadm trigger`.

The driver only uses vendor interface 2 (endpoints `0x06`/`0x86`), which no
kernel driver claims, so it does not conflict with `usblp` on the print interface.

## Usage

Connect the printer over USB, power it on, wait for it to stop making noise, then give it a go:

```
./pixcut artwork.png            # dry run: writes _print.jpg, _cut.hpgl, _preview.png
./pixcut artwork.png --send     # pull the trigger
```

Recommended input is a transparent PNG at print resolution (1200×2100 = 4×7" @ 300 dpi).
Transparent pixels are converted to white or the set background image and used to calculate the cut outline.

If you wanna be fancy you can also pass a modified `template.xcf` and it'll automatically extract the "Sticker" and "Background" layers (requires GIMP):
```
./pixcut stickers.xcf            # check that the previews look gud
./pixcut stickers.xcf --send     # boom!
```

### Arguments

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--background` | path | White fill | PNG composited full-bleed *under* the artwork in the print only; prints but is ignored by the cut. |
| `--border` | mm | `1.25` | Cut border: how far the cut outline is offset outward from the artwork edge. |
| `--join` | `round`\|`miter`\|`bevel` | `round` | Corner style where the offset outline turns. `round` matches the vendor. |
| `--full-media` | flag | off | When scaling, fit the whole sheet instead of the usable area (5.1 mm bleed top/left/right). Ignored for exact-size input. |
| `--bias-x` | mm | `0.0` | Mechanical cross-axis (X) print→cut registration nudge. |
| `--bias-y` | mm | `0.0` | Mechanical feed-axis (Y) registration nudge; +Y shifts the cut up. |
| `--send` | flag | off | Stream the job to the hardware over USB. **Moves the machine and consumes a sheet.** Without it, `pixcut` only does a dry run. |

The `pixcut` wrapper builds/reuses a `.venv` and passes every argument straight through to `make_job.py`. Set `PIXCUT_PYTHON` to choose the base Python used to create the venv.

## Motivation

The official PixCut vendor software only supports Android, iOS, macOS, and Windows 🤮.
It also requires a completely unnecessary and clunky account creation process, because
we're living in the future! Every gadget and gizmo you buy requires an app that you must
**register** and **login** with to use. When I was trying to use the Windows software
for this thing to print a nice sticker label for my customized [DOOM on Loopy cartridge shell](https://github.com/ThroatyMumbo/LoopyDOOM),
I was about ready to smash it with a sledgehammer after it prompted me to check my email
for a verification code the **fifth time in a row**. All just to print a single sticker.

I hope these scripts help you avoid this same frustration, assuming you're in the
0.00000001% of people who own one of these and prefer Linux.
