"""Scene -> native fpdf2 drawing: vector shapes and real, selectable Figtree text.

Not an SVG import: drawing the scene directly reuses the report's embedded fonts, keeps
the text searchable, and places glyphs by advance width (no kerning), exactly as the
layout measured them.
"""

from __future__ import annotations

from fpdf import FPDF

from ntnxvision_core.exporters import theme as T

from ..scene import Edge, EdgeLabel, Node, Scene, Style, TextLine, draw_order
from .svg import arrow_points, trimmed

#: Font family the drawing uses; the report registers "Figtree" with "" and "B".
FAMILY = "Figtree"
PT_MM = 25.4 / 72


def ensure_fonts(pdf: FPDF, family: str = FAMILY) -> None:
    if f"{family.lower()}" not in pdf.fonts:
        pdf.add_font(family, "", str(T.FIGTREE))
    if f"{family.lower()}B" not in pdf.fonts:
        pdf.add_font(family, "B", str(T.FIGTREE_SEMIBOLD))


def _rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


class _Pen:
    def __init__(self, pdf: FPDF, ox: float, oy: float, k: float, family: str) -> None:
        self.pdf, self.ox, self.oy, self.k, self.family = pdf, ox, oy, k, family

    def X(self, x: float) -> float:  # noqa: N802
        return self.ox + x * self.k

    def Y(self, y: float) -> float:  # noqa: N802
        return self.oy + y * self.k

    def dash(self, st: Style) -> None:
        if st.dash:
            self.pdf.set_dash_pattern(dash=st.dash[0] * self.k, gap=st.dash[1] * self.k)
        else:
            self.pdf.set_dash_pattern()

    def shape(self, x: float, y: float, w: float, h: float, st: Style) -> None:
        pdf, k = self.pdf, self.k
        if not (st.fill or st.stroke):
            return
        style = ("F" if st.fill else "") + ("D" if st.stroke else "")
        if st.fill:
            pdf.set_fill_color(*_rgb(st.fill))
        if st.stroke:
            pdf.set_draw_color(*_rgb(st.stroke))
            pdf.set_line_width(st.stroke_w * k)
        self.dash(st)
        r = min(st.radius, h / 2) * k
        pdf.rect(self.X(x), self.Y(y), w * k, h * k, style=style, round_corners=bool(r),
                 corner_radius=r)
        pdf.set_dash_pattern()

    def text(self, lines: list[TextLine], ox: float, oy: float) -> None:
        pdf = self.pdf
        for t in lines:
            pdf.set_font(self.family, "B" if t.bold else "", t.size * self.k / PT_MM)
            pdf.set_text_color(*_rgb(t.color))
            pdf.text(self.X(ox + t.x), self.Y(oy + t.baseline), t.text)

    def node(self, n: Node) -> None:
        if n.kind != "label":
            self.shape(n.x, n.y, n.w, n.h, n.style)
        self.text(n.lines, n.x, n.y)

    def edge(self, e: Edge) -> None:
        pdf, k, st = self.pdf, self.k, e.style
        color = _rgb(st.stroke or "#000000")
        pdf.set_draw_color(*color)
        pdf.set_line_width(st.stroke_w * k)
        self.dash(st)
        pdf.polyline([(self.X(x), self.Y(y)) for x, y in trimmed(e)], style="D")
        pdf.set_dash_pattern()
        for on, a, b in ((e.arrow_end, -2, -1), (e.arrow_start, 1, 0)):
            if on and len(e.points) >= 2:
                pdf.set_fill_color(*color)
                tri = arrow_points(e.points[a], e.points[b], st.stroke_w)
                pdf.polygon([(self.X(x), self.Y(y)) for x, y in tri], style="F")

    def label(self, lab: EdgeLabel) -> None:
        if lab.background:
            self.pdf.set_fill_color(255, 255, 255)
            self.pdf.rect(self.X(lab.x), self.Y(lab.y), lab.w * self.k, lab.h * self.k,
                          style="F")
        self.text(lab.lines, lab.x, lab.y)


def draw_scene(pdf: FPDF, sc: Scene, x: float, y: float, scale: float | None = None,
               family: str = FAMILY) -> float:
    """Draw `sc` with its top-left at (x, y) in document units. `scale` is document
    units per point (default: 1:1 in the document's unit). Returns the drawn height."""
    ensure_fonts(pdf, family)
    k = scale if scale is not None else 1 / pdf.k
    pen = _Pen(pdf, x, y, k, family)
    for kind, item in draw_order(sc):
        if kind == "node":
            pen.node(item)
        elif kind == "edge":
            pen.edge(item)
        else:
            pen.label(item[1])
    pdf.set_dash_pattern()
    return sc.height * k
