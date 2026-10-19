"""Scene -> PNG (Pillow): the raster used in the Word report and previews.

Drawn at 2x the target DPI, then downsampled (LANCZOS) for anti-aliased shapes. Text is
the bundled Figtree with Pillow's BASIC layout (no kerning), matching the measurement.
No timestamp chunk is written, so equal pixels give equal bytes on one Pillow build.
"""

from __future__ import annotations

import io
import math
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from ntnxvision_core.exporters import theme as T

from ..scene import Edge, EdgeLabel, Node, Scene, Style, TextLine, draw_order
from .svg import arrow_points, trimmed

SS = 2  # supersampling


def _rgb(h: str | None) -> tuple[int, int, int] | None:
    if not h:
        return None
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


@lru_cache(maxsize=64)
def _font(px: int, bold: bool) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(T.FIGTREE_SEMIBOLD if bold else T.FIGTREE), max(1, px),
                              layout_engine=ImageFont.Layout.BASIC)


def _dashed(d: ImageDraw.ImageDraw, pts: list[tuple[float, float]], fill: object, width: int,
            on: float, off: float) -> None:
    period = on + off
    start = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:], strict=False):
        length = math.hypot(x1 - x0, y1 - y0)
        if length == 0:
            continue
        ux, uy = (x1 - x0) / length, (y1 - y0) / length
        k = math.floor(start / period)
        while True:
            a = k * period - start
            if a >= length:
                break
            a2, b2 = max(a, 0.0), min(a + on, length)
            if b2 > a2:
                d.line([(x0 + ux * a2, y0 + uy * a2), (x0 + ux * b2, y0 + uy * b2)],
                       fill=fill, width=width)  # type: ignore[arg-type]
            k += 1
        start += length


def _rr_outline(x0: float, y0: float, x1: float, y1: float, r: float
                ) -> list[tuple[float, float]]:
    pts = []
    for cx, cy, a0 in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90),
                       (x0 + r, y0 + r, 180)):
        for i in range(7):
            a = math.radians(a0 + i * 15)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    pts.append(pts[0])
    return pts


def _shape(d: ImageDraw.ImageDraw, x: float, y: float, w: float, h: float, st: Style,
           k: float) -> None:
    if not (st.fill or st.stroke):
        return
    bx = [x * k, y * k, (x + w) * k, (y + h) * k]
    r = min(st.radius, h / 2) * k
    lw = max(1, round(st.stroke_w * k)) if st.stroke else 0
    if st.dash and st.stroke:
        d.rounded_rectangle(bx, r, fill=_rgb(st.fill))
        outline = _rr_outline(bx[0], bx[1], bx[2], bx[3], r)
        _dashed(d, outline, _rgb(st.stroke), lw, st.dash[0] * k, st.dash[1] * k)
    else:
        d.rounded_rectangle(bx, r, fill=_rgb(st.fill), outline=_rgb(st.stroke), width=lw)


def _text(d: ImageDraw.ImageDraw, lines: list[TextLine], ox: float, oy: float,
          k: float) -> None:
    for t in lines:
        d.text(((ox + t.x) * k, (oy + t.baseline) * k), t.text,
               font=_font(round(t.size * k), t.bold), fill=_rgb(t.color), anchor="ls")


def _node(d: ImageDraw.ImageDraw, n: Node, k: float) -> None:
    if n.kind != "label":
        _shape(d, n.x, n.y, n.w, n.h, n.style, k)
    _text(d, n.lines, n.x, n.y, k)


def _edge(d: ImageDraw.ImageDraw, e: Edge, k: float) -> None:
    st = e.style
    color = _rgb(st.stroke or "#000000")
    pts = [(x * k, y * k) for x, y in trimmed(e)]
    lw = max(1, round(st.stroke_w * k))
    if st.dash:
        _dashed(d, pts, color, lw, st.dash[0] * k, st.dash[1] * k)
    else:
        d.line(pts, fill=color, width=lw, joint="curve")
    for on, a, b in ((e.arrow_end, -2, -1), (e.arrow_start, 1, 0)):
        if on and len(e.points) >= 2:
            tri = arrow_points(e.points[a], e.points[b], st.stroke_w)
            d.polygon([(x * k, y * k) for x, y in tri], fill=color)


def _label(d: ImageDraw.ImageDraw, lab: EdgeLabel, k: float) -> None:
    if lab.background:
        d.rounded_rectangle([lab.x * k, lab.y * k, (lab.x + lab.w) * k, (lab.y + lab.h) * k],
                            1.5 * k, fill=(255, 255, 255))
    _text(d, lab.lines, lab.x, lab.y, k)


def render_png(sc: Scene, dpi: int = 220, palette: bool = False) -> bytes:
    """The scene as PNG. `palette` quantises to an adaptive palette (median cut, no
    dither): flat diagram colours survive and the file is several times smaller, which
    is what the Word fallback picture uses."""
    k = dpi / 72 * SS
    width, height = math.ceil(sc.width * k), math.ceil(sc.height * k)
    img = Image.new("RGB", (width, height), (255, 255, 255))
    d = ImageDraw.Draw(img)
    for kind, item in draw_order(sc):
        if kind == "node":
            _node(d, item, k)
        elif kind == "edge":
            _edge(d, item, k)
        else:
            _label(d, item[1], k)
    img = img.resize((width // SS, height // SS), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    if palette:
        img = img.quantize(colors=256, method=Image.Quantize.MEDIANCUT,
                           dither=Image.Dither.NONE)
    img.save(buf, "PNG", optimize=palette, dpi=(dpi, dpi))
    return buf.getvalue()
