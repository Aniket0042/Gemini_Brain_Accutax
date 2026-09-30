"""Visual regression: page 1 of each golden PDF against a reviewed baseline image.

Opt-in, because pixels depend on the machine's fonts and matplotlib build:

    REPORT_VISUAL=1 python -m pytest tests/unit/test_report_visual.py
    REPORT_VISUAL=1 UPDATE_REPORT_VISUAL=1 python -m pytest tests/unit/test_report_visual.py   # new baselines

Baselines live in tests/report_golden/visual/. A page fails when more than
1% of its pixels changed noticeably; the diff image is written next to the
baseline as <name>.diff.png for review.
"""
import os
from pathlib import Path

import pytest

from gemini_brain.artifacts.generate import render_pdf
from tests.report_golden.harness import build

pytestmark = pytest.mark.skipif(os.environ.get("REPORT_VISUAL") != "1", reason="set REPORT_VISUAL=1 to run")

BASELINES = Path(__file__).resolve().parents[1] / "report_golden" / "visual"
PAGES = ["pnl_full", "pnl_waterfall", "customers_pie", "customers_ranking", "invoices_long", "vat_stacked",
         "negative_pie_refused", "arabic_names"]
DPI = 60
#: A pixel "changed" when any channel moved by more than this (anti-aliasing noise stays below).
PIXEL_TOLERANCE = 40
#: Share of changed pixels a page may have before it fails.
MAX_CHANGED = 0.01


def _page_image(name: str):
    pymupdf = pytest.importorskip("pymupdf")
    np = pytest.importorskip("numpy")
    spec = build(name)
    doc = pymupdf.open(stream=render_pdf(spec))
    pix = doc[0].get_pixmap(dpi=DPI)
    return pymupdf, np, pix, np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)


@pytest.mark.parametrize("name", PAGES)
def test_first_page_matches_baseline(name):
    pymupdf, np, pix, actual = _page_image(name)
    baseline_path = BASELINES / f"{name}.png"
    if os.environ.get("UPDATE_REPORT_VISUAL") == "1" or not baseline_path.exists():
        BASELINES.mkdir(parents=True, exist_ok=True)
        pix.save(str(baseline_path))
        if os.environ.get("UPDATE_REPORT_VISUAL") != "1":
            pytest.skip(f"baseline created for {name}; review {baseline_path} and commit it")
        return
    base = pymupdf.Pixmap(str(baseline_path))
    expected = np.frombuffer(base.samples, dtype=np.uint8).reshape(base.height, base.width, base.n)
    assert expected.shape == actual.shape, f"{name}: page size changed {expected.shape} -> {actual.shape}"
    changed = (np.abs(expected.astype(int) - actual.astype(int)).max(axis=2) > PIXEL_TOLERANCE)
    ratio = changed.mean()
    if ratio > MAX_CHANGED:
        diff = actual.copy()
        diff[changed] = [227, 73, 72][: actual.shape[2]]  # changed pixels in red
        pymupdf.Pixmap(pymupdf.csRGB, pix.width, pix.height, diff.tobytes(), False).save(
            str(BASELINES / f"{name}.diff.png"))
    assert ratio <= MAX_CHANGED, f"{name}: {ratio:.2%} of page 1 changed; see {name}.diff.png"
