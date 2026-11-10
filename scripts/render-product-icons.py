#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Copy the Nutanix product icons NTNXVision uses into the repo, cropped and rasterised.

Usage (from the repo root, with the core's venv Python)::

    core/.venv/bin/python scripts/render-product-icons.py "<Nutanix Product Icons folder>"

The source folder is the Nutanix product icon set (``Nutanix Product Icons
2026-09-24 05_12_15/``, kept outside this repo). Only the colour, icon-only variants
(``Product-Icons-Digital-<Product>-Color.svg``) are used. For each product this writes:

* ``core/ntnxvision_core/exporters/icons/<id>.svg``: the original paths, unchanged, with
  the viewBox cropped to a square around the drawing (the source has a wide 310 x 194
  artboard), so every consumer can place it in a square box;
* ``core/ntnxvision_core/exporters/icons/<id>.png``: the same square at ``PX`` pixels with
  a transparent background, for the consumers that cannot take SVG (python-docx,
  openpyxl, the Pillow diagram renderer);
* ``app/src/renderer/assets/product-icons/<id>.svg``: the cropped SVG for the Diagrams
  editor.

Rasterising needs PyMuPDF (``pip install pymupdf``), only here: it is a one-off
generation step, not a runtime or dev dependency. The outputs are checked in.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE_OUT = ROOT / "core" / "ntnxvision_core" / "exporters" / "icons"
APP_OUT = ROOT / "app" / "src" / "renderer" / "assets" / "product-icons"
PX = 256
#: Room around the drawing, as a share of the square's side.
PAD = 0.04

#: Product id -> the source file's product name (the set spells Cloud Manager
#: "CloudManger").
SOURCES = {
    "prism": "Prism",
    "nci": "CloudInfrastructure",
    "ncm": "CloudManger",
    "ndb": "Database",
    "nus": "NUS",
    "nc2": "NC2",
    "nkp": "NKP",
    "nai": "NAI",
}

_PATH = re.compile(r'<path fill="(#[0-9a-fA-F]{6})" d="([^"]+)"\s*/>')
_VIEWBOX = re.compile(r'viewBox="([^"]+)"')


def _crop(svg: str) -> tuple[float, float, float, float]:
    """A square viewBox around the drawing in `svg`, found from a high-resolution
    raster (the paths use arcs, so the bounds are measured, not computed)."""
    import pymupdf  # type: ignore[import-not-found]

    vb = [float(v) for v in _VIEWBOX.search(svg).group(1).split()]  # type: ignore[union-attr]
    doc = pymupdf.open(stream=svg.encode(), filetype="svg")
    scale = 8.0
    pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=True)
    xs, ys = [], []
    n = pix.n
    samples = pix.samples
    for y in range(pix.height):
        row = y * pix.stride
        for x in range(pix.width):
            if samples[row + x * n + n - 1] > 8:
                xs.append(x)
                ys.append(y)
    x0, x1 = min(xs) / scale, (max(xs) + 1) / scale
    y0, y1 = min(ys) / scale, (max(ys) + 1) / scale
    side = max(x1 - x0, y1 - y0)
    side *= 1 + 2 * PAD
    cx, cy = (x0 + x1) / 2 + vb[0], (y0 + y1) / 2 + vb[1]
    return round(cx - side / 2, 2), round(cy - side / 2, 2), round(side, 2), round(side, 2)


def _cropped(svg: str, box: tuple[float, float, float, float]) -> str:
    paths = _PATH.findall(svg)
    if not paths or len(paths) != svg.count("<path"):
        raise SystemExit("unexpected SVG content: only <path fill d/> elements are supported")
    vb = " ".join(f"{v:g}" for v in box)
    body = "".join(f'<path fill="{f.upper()}" d="{d}"/>' for f, d in paths)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" width="{PX}" '
            f'height="{PX}">{body}</svg>\n')


def main(src: Path) -> None:
    import pymupdf  # type: ignore[import-not-found]

    CORE_OUT.mkdir(parents=True, exist_ok=True)
    APP_OUT.mkdir(parents=True, exist_ok=True)
    for pid, name in SOURCES.items():
        path = src / f"Product-Icons-Digital-{name}-Color.svg"
        svg = path.read_text(encoding="utf-8")
        out = _cropped(svg, _crop(svg))
        (CORE_OUT / f"{pid}.svg").write_text(out, encoding="utf-8")
        (APP_OUT / f"{pid}.svg").write_text(out, encoding="utf-8")
        doc = pymupdf.open(stream=out.encode(), filetype="svg")
        page = doc[0]
        k = PX / page.rect.width
        pix = page.get_pixmap(matrix=pymupdf.Matrix(k, k), alpha=True)
        pix.save(str(CORE_OUT / f"{pid}.png"))
        print(f"{pid}: {path.name} -> {pid}.svg, {pid}.png ({pix.width}x{pix.height})")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(Path(sys.argv[1]))
