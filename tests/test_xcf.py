"""Unit tests for the XCF (GIMP) input path in make_job.py.

Two groups:
  * GIMP-gated integration tests that actually render template.xcf's layers
    (skipped when GIMP is not on PATH);
  * GIMP-free unit tests for the two error paths (no GIMP installed, and an XCF
    with no visible 'Sticker' layer), driven by monkeypatching.
"""
import os
import shutil
import subprocess
import types

import pytest
from PIL import Image

import make_job as mj

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_XCF = os.path.join(REPO_ROOT, "template.xcf")
HAVE_GIMP = shutil.which("gimp-console") or shutil.which("gimp")

gimp_required = pytest.mark.skipif(not HAVE_GIMP, reason="GIMP (gimp-console) not on PATH")
template_required = pytest.mark.skipif(not os.path.exists(TEMPLATE_XCF), reason="template.xcf missing")


# --- GIMP-gated integration: real layer extraction --------------------------

@gimp_required
@template_required
def test_extract_xcf_layers_renders_sticker_and_background():
    sticker, bg, tmpdir = mj.extract_xcf_layers(TEMPLATE_XCF)
    try:
        # Sticker is required; rendered flattened onto the full media canvas
        assert os.path.exists(sticker)
        st = Image.open(sticker)
        assert st.size == (mj.MEDIA_W, mj.MEDIA_H)
        assert st.mode == "RGBA"                 # alpha preserved (transparent = no ink / no cut)
        # template.xcf ships a Background layer too -> optional PNG present, same canvas
        assert bg is not None and os.path.exists(bg)
        assert Image.open(bg).size == (mj.MEDIA_W, mj.MEDIA_H)
        assert os.path.isdir(tmpdir)             # caller owns the tmpdir
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@gimp_required
@template_required
def test_make_job_end_to_end_on_xcf(tmp_path, monkeypatch):
    prefix = str(tmp_path / "tmpl")
    monkeypatch.setattr(mj.sys, "argv", ["make_job.py", TEMPLATE_XCF, prefix])
    mj.main()
    # the XCF split -> PNG -> normal pipeline still emits the three dry-run artifacts
    assert os.path.exists(prefix + "_print.jpg")
    assert os.path.exists(prefix + "_cut.hpgl")
    assert os.path.exists(prefix + "_preview.png")
    hpgl = open(prefix + "_cut.hpgl").read()
    assert hpgl.startswith("IN VER0.1.0 KP42 ")   # valid cut program (blank sticker -> header+park)
    with open(prefix + "_print.jpg", "rb") as f:
        assert f.read(3) == b"\xff\xd8\xff"        # valid JPEG


# --- GIMP-free error paths (monkeypatched) ----------------------------------

def test_extract_xcf_layers_errors_clearly_without_gimp(monkeypatch):
    monkeypatch.setattr(mj.shutil, "which", lambda _name: None)   # pretend GIMP absent
    with pytest.raises(SystemExit) as ei:
        mj.extract_xcf_layers("whatever.xcf")
    assert "GIMP" in str(ei.value)


def test_extract_xcf_layers_errors_when_no_sticker_layer(monkeypatch, tmp_path):
    # pretend GIMP exists but produced no sticker.png (no matching layer),
    # while reporting the layers it did see on stderr.
    monkeypatch.setattr(mj.shutil, "which", lambda _name: "/usr/bin/gimp-console")

    def fake_run(cmd, **kwargs):
        # GIMP runs, writes nothing, logs the layer names it scanned
        return types.SimpleNamespace(
            stdout="", returncode=0,
            stderr="LAYER: Doodle\nLAYER: Backdrop\n")
    monkeypatch.setattr(mj.subprocess, "run", fake_run)

    with pytest.raises(SystemExit) as ei:
        mj.extract_xcf_layers("nostick.xcf")
    msg = str(ei.value)
    assert mj.STICKER_NEEDLE in msg               # names the needle it looked for
    assert "Doodle" in msg and "Backdrop" in msg  # lists the layers actually seen


def test_extract_xcf_layers_cleans_tmpdir_on_missing_sticker(monkeypatch):
    # the tmpdir it created must be removed before it bails out (no leak)
    monkeypatch.setattr(mj.shutil, "which", lambda _name: "/usr/bin/gimp-console")
    created = {}
    real_mkdtemp = mj.tempfile.mkdtemp

    def spy_mkdtemp(*a, **k):
        d = real_mkdtemp(*a, **k)
        created["dir"] = d
        return d
    monkeypatch.setattr(mj.tempfile, "mkdtemp", spy_mkdtemp)
    monkeypatch.setattr(mj.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(stdout="", returncode=0, stderr=""))

    with pytest.raises(SystemExit):
        mj.extract_xcf_layers("nostick.xcf")
    assert not os.path.exists(created["dir"])     # cleaned up, no leftover tmpdir
