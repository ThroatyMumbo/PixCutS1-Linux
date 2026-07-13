"""Unit tests for make_job.py — the end-to-end job builder.

Covers the pure image-normalization (fit_to_media / fit_background), the print
raster compositing (build_print_jpeg), and the cut-unit <-> pixel inverse
transform (cut_units_to_px), including a round-trip against generate_cut.to_cut.
"""
import math

import numpy as np
import pytest
from PIL import Image

import make_job as mj
import generate_cut as g


# --- fit_to_media -----------------------------------------------------------

def test_fit_to_media_exact_size_passes_through_unchanged():
    im = Image.new("RGBA", (mj.MEDIA_W, mj.MEDIA_H), (10, 20, 30, 255))
    out, scaled = mj.fit_to_media(im, border_mm=1.25)
    assert out is im                 # identity: the validated full-bleed path
    assert scaled is False


def test_fit_to_media_scales_smaller_input_onto_the_media_canvas():
    im = Image.new("RGBA", (600, 600), (255, 0, 0, 255))
    out, scaled = mj.fit_to_media(im, border_mm=1.25)
    assert scaled is True
    assert out.size == (mj.MEDIA_W, mj.MEDIA_H)
    assert out.mode == "RGBA"
    # the corners must stay transparent (margin keeps the outward cut on-sheet)
    for corner in [(0, 0), (mj.MEDIA_W - 1, 0), (0, mj.MEDIA_H - 1), (mj.MEDIA_W - 1, mj.MEDIA_H - 1)]:
        assert out.getpixel(corner)[3] == 0
    # something opaque landed near the center
    assert out.getpixel((mj.MEDIA_W // 2, mj.MEDIA_H // 2))[3] == 255


def test_fit_to_media_preserves_aspect_ratio():
    im = Image.new("RGBA", (400, 200), (0, 255, 0, 255))   # 2:1
    out, _ = mj.fit_to_media(im, border_mm=1.25)
    alpha = np.array(out)[:, :, 3]
    ys, xs = np.nonzero(alpha)
    bbox_w, bbox_h = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
    assert bbox_w / bbox_h == pytest.approx(2.0, rel=0.02)


def test_fit_to_media_full_media_places_wider_than_usable_area():
    im = Image.new("RGBA", (400, 400), (0, 0, 255, 255))
    usable, _ = mj.fit_to_media(im, border_mm=1.25, full_media=False)
    full, _ = mj.fit_to_media(im, border_mm=1.25, full_media=True)

    def opaque_width(img):
        a = np.array(img)[:, :, 3]
        xs = np.nonzero(a.any(axis=0))[0]
        return xs.max() - xs.min() + 1

    # full-media has more room (no 5.1mm bleed inset), so the square is scaled larger
    assert opaque_width(full) > opaque_width(usable)


# --- fit_background ---------------------------------------------------------

def test_fit_background_exact_size_passes_through_unchanged():
    im = Image.new("RGBA", (mj.MEDIA_W, mj.MEDIA_H), (1, 2, 3, 255))
    assert mj.fit_background(im) is im


def test_fit_background_covers_the_whole_sheet():
    im = Image.new("RGBA", (300, 300), (128, 64, 32, 255))   # opaque, wrong aspect
    out = mj.fit_background(im)
    assert out.size == (mj.MEDIA_W, mj.MEDIA_H)
    # "cover" (center-crop) => no transparent gaps, even in the corners
    for corner in [(0, 0), (mj.MEDIA_W - 1, 0), (0, mj.MEDIA_H - 1), (mj.MEDIA_W - 1, mj.MEDIA_H - 1)]:
        assert out.getpixel(corner)[3] == 255


# --- build_print_jpeg -------------------------------------------------------

def test_build_print_jpeg_returns_jpeg_bytes_and_rgb_image():
    im = Image.new("RGBA", (16, 16), (255, 0, 0, 255))
    jpeg, rgb = mj.build_print_jpeg(im)
    assert jpeg[:3] == b"\xff\xd8\xff"          # JPEG SOI marker
    assert rgb.mode == "RGB"
    assert rgb.size == (16, 16)


def test_build_print_jpeg_composites_transparent_over_white():
    # left half opaque red, right half transparent -> right half becomes white
    im = Image.new("RGBA", (2, 1), (0, 0, 0, 0))
    im.putpixel((0, 0), (255, 0, 0, 255))       # opaque red
    im.putpixel((1, 0), (0, 0, 0, 0))           # transparent
    _, rgb = mj.build_print_jpeg(im)
    assert rgb.getpixel((0, 0)) == (255, 0, 0)  # red preserved
    assert rgb.getpixel((1, 0)) == (255, 255, 255)  # bare media = white


def test_build_print_jpeg_background_shows_through_transparent_artwork():
    art = Image.new("RGBA", (1, 1), (0, 0, 0, 0))        # fully transparent artwork
    bg = Image.new("RGBA", (1, 1), (0, 0, 255, 255))     # opaque blue background
    _, rgb = mj.build_print_jpeg(art, bg)
    assert rgb.getpixel((0, 0)) == (0, 0, 255)           # background prints through


def test_build_print_jpeg_artwork_covers_background():
    art = Image.new("RGBA", (1, 1), (255, 0, 0, 255))    # opaque red artwork
    bg = Image.new("RGBA", (1, 1), (0, 0, 255, 255))     # opaque blue background
    _, rgb = mj.build_print_jpeg(art, bg)
    assert rgb.getpixel((0, 0)) == (255, 0, 0)           # artwork wins over background


# --- cut_units_to_px: inverse of generate_cut.to_cut ------------------------

def test_cut_transform_constants_mirror_generate_cut():
    # make_job's CUT_A/CUT_B must reproduce generate_cut.to_cut exactly
    for x_mm, y_mm in [(0, 0), (25, 50), (91.4, 172.7), (50, 90)]:
        cx, cy = mj.CUT_A @ np.array([x_mm, y_mm]) + mj.CUT_B
        assert (round(cx), round(cy)) == g.to_cut(x_mm, y_mm)


def test_cut_units_to_px_round_trips_with_to_cut():
    # paper-mm -> cut units -> px -> mm should return the original within <0.2px
    for x_mm, y_mm in [(10.0, 20.0), (50.0, 90.0), (91.4, 172.7)]:
        cx, cy = g.to_cut(x_mm, y_mm)
        px, py = mj.cut_units_to_px(cx, cy, 0.0, 0.0)
        assert px == pytest.approx(x_mm * mj.PPM, abs=0.2)
        assert py == pytest.approx(y_mm * mj.PPM, abs=0.2)


def test_cut_units_to_px_adds_bias_back():
    # the preview re-adds the mechanical bias so it shows the physical landing
    cx, cy = g.to_cut(50.0, 90.0)
    px0, py0 = mj.cut_units_to_px(cx, cy, 0.0, 0.0)
    px1, py1 = mj.cut_units_to_px(cx, cy, 0.5, 0.3)
    assert px1 - px0 == pytest.approx(0.5 * mj.PPM, abs=1e-6)
    assert py1 - py0 == pytest.approx(0.3 * mj.PPM, abs=1e-6)


# --- module constants -------------------------------------------------------

def test_media_and_combojob_constants():
    assert (mj.MEDIA_W, mj.MEDIA_H) == (1200, 2100)          # 4x7" @ 300dpi
    assert mj.PPM == pytest.approx(300.0 / 25.4)
    assert (mj.MEDIA_SIZE, mj.MEDIA_TYPE, mj.JOB_TYPE) == (5013, 2030, 600)
    assert (mj.VID, mj.PID) == (0x302C, 0x3101)
    assert (mj.EP_OUT, mj.EP_IN, mj.IFACE) == (0x06, 0x86, 2)
