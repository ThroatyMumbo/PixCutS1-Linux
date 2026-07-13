"""Unit tests for generate_cut.py — the cut-vector generator.

Covers the pure geometry: the paper-mm -> cut-unit transform, the 2D vector
helpers, the outward (waste-wedge) bisector, and an end-to-end run of main()
that turns a synthetic silhouette into an HPGL program.
"""
import math
import re

import numpy as np
import pytest
from PIL import Image
from shapely.geometry import Polygon

import generate_cut as g


# --- to_cut: the full-media paper-mm -> cut-unit affine transform -----------

def test_to_cut_origin_matches_baked_constants():
    # cx = 0.0055*x - 40.5374*y + 7153.776 ; cy = 40.5079*x - 0.0142*y - 22.218
    assert g.to_cut(0.0, 0.0) == (round(7153.776), round(-22.218)) == (7154, -22)


def test_to_cut_is_affine():
    # doubling the displacement from the origin doubles the displacement in cut space
    o = np.array(g.to_cut(0.0, 0.0), float)
    a = np.array(g.to_cut(10.0, 20.0), float) - o
    b = np.array(g.to_cut(20.0, 40.0), float) - o
    assert np.allclose(b, 2 * a, atol=1.0)   # atol 1 unit absorbs the integer rounding


def test_to_cut_axis_directions_match_calibration():
    # per CLAUDE.md: feed axis (cutX) is INVERTED vs paperY; cross axis (cutY) tracks paperX
    base = g.to_cut(0.0, 0.0)
    more_y = g.to_cut(0.0, 10.0)
    more_x = g.to_cut(10.0, 0.0)
    assert more_y[0] < base[0]     # +paperY -> cutX decreases
    assert more_x[1] > base[1]     # +paperX -> cutY increases


def test_to_cut_native_resolution_is_about_40_5_units_per_mm():
    # 10 mm of cross travel should be ~405 cut units (device native ~40.52 units/mm)
    d = g.to_cut(10.0, 0.0)[1] - g.to_cut(0.0, 0.0)[1]
    assert d == pytest.approx(405.079, abs=1.0)


def test_to_cut_bias_shifts_the_source_point():
    # applying a bias is exactly equivalent to pre-subtracting it from the input
    assert g.to_cut(50.0, 90.0, 0.5, -0.3) == g.to_cut(50.0 - 0.5, 90.0 - (-0.3))


# --- 2D vector helpers ------------------------------------------------------

def test_sub():
    assert g._sub((5, 7), (2, 3)) == (3, 4)


def test_unit_is_normalized():
    ux, uy = g._unit((3.0, 4.0))
    assert math.hypot(ux, uy) == pytest.approx(1.0)
    assert (ux, uy) == pytest.approx((0.6, 0.8))


def test_unit_zero_vector_does_not_divide_by_zero():
    assert g._unit((0.0, 0.0)) == (0.0, 0.0)   # length falls back to 1.0


def test_rot_quarter_turn():
    x, y = g._rot((1.0, 0.0), math.pi / 2)
    assert (x, y) == pytest.approx((0.0, 1.0), abs=1e-9)


def test_sangle_signed():
    # +90 deg from +x to +y, -90 deg from +x to -y
    assert g._sangle((1.0, 0.0), (0.0, 1.0)) == pytest.approx(math.pi / 2)
    assert g._sangle((1.0, 0.0), (0.0, -1.0)) == pytest.approx(-math.pi / 2)


def test_ring_drops_closing_duplicate_vertex():
    sq = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    ring = g._ring(sq)
    assert len(ring) == 4                       # shapely repeats the first point; _ring strips it
    assert ring[0] != ring[-1]


# --- outward_bisector: local waste-wedge geometry ---------------------------

def test_outward_bisector_points_into_the_waste_on_a_square_corner():
    sq = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    ring = g._ring(sq)
    bis, half = g.outward_bisector(sq, ring, 0)          # vertex (0,0)
    # unit vector
    assert math.hypot(*bis) == pytest.approx(1.0)
    # points away from the interior (down-left, roughly (-.707,-.707))
    assert bis == pytest.approx((-math.sqrt(0.5), -math.sqrt(0.5)), abs=1e-6)
    # a square corner's exterior wedge is 270 deg -> half-angle 135 deg
    assert half == pytest.approx(3 * math.pi / 4, abs=1e-6)
    # stepping along the bisector must leave the polygon
    from shapely.geometry import Point
    assert not sq.contains(Point(0 + bis[0] * 0.05, 0 + bis[1] * 0.05))


# --- module constants -------------------------------------------------------

def test_join_map_accepts_us_and_uk_spelling():
    assert g._JOIN_MAP["miter"] == "mitre"
    assert g._JOIN_MAP["mitre"] == "mitre"
    assert g._JOIN_MAP["round"] == "round"
    assert g._JOIN_MAP["bevel"] == "bevel"


def test_park_position():
    assert g.PARK == (6476, 0)


# --- end-to-end: main() over a synthetic silhouette -------------------------

def _write_block_png(path, w=1200, h=2100, block=(400, 700, 800, 1400)):
    """Transparent canvas with one opaque white rectangle (a single contour)."""
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    x0, y0, x1, y1 = block
    for yy in range(y0, y1):
        for xx in range(x0, x1):
            im.putpixel((xx, yy), (255, 255, 255, 255))
    im.save(path)


def test_main_emits_valid_hpgl_program(tmp_path):
    png = tmp_path / "block.png"
    out = tmp_path / "block.hpgl"
    _write_block_png(str(png))

    g.main(["generate_cut.py", str(png), str(out)])

    prog = out.read_text()
    assert prog.startswith("IN VER0.1.0 KP42 ")           # HPGL header
    assert prog.rstrip().endswith("@")                    # park move terminator
    assert "U%d,%d" % g.PARK in prog                      # explicit park position
    # at least one pen-up move + pen-down draw for the single contour
    assert re.search(r"U-?\d+,-?\d+ D-?\d+,-?\d+", prog)


def test_main_rejects_unknown_join(tmp_path):
    png = tmp_path / "block.png"
    _write_block_png(str(png))
    with pytest.raises(SystemExit):
        g.main(["generate_cut.py", str(png), str(tmp_path / "o.hpgl"),
                "1.25", "0", "0", "chamfer"])
