"""Scene -> SVG, in two text modes.

* ``live`` (the editable asset): real ``<text>`` elements in ``font-family: Figtree``
  with Office-safe fallbacks. Each shape is a ``<g>`` carrying the scene id; semantic
  data is kept in ``data-*`` attributes and a ``<title>`` (full names that a box had to
  ellipsise stay readable there).
* ``outline`` (the copy embedded in the Word report): every glyph is an inline
  ``<path>`` drawn from the bundled Figtree, placed by the same advance widths the
  layout measured, so the figure looks identical with no font installed. Paths are
  inline, never ``<use>``: Office's SVG support is the weakest consumer.

Every position, size and colour comes from the scene and every number is printed by
`num`, so both modes are byte-stable.
"""

from __future__ import annotations

from functools import lru_cache
from xml.sax.saxutils import escape, quoteattr

from fontTools.pens.svgPathPen import SVGPathPen  # type: ignore[import-untyped]
from fontTools.pens.transformPen import TransformPen  # type: ignore[import-untyped]
from fontTools.ttLib import TTFont  # type: ignore[import-untyped]

from ntnxvision_core.exporters import theme as T

from .. import text as tx
from ..scene import Edge, EdgeLabel, Node, Scene, Style, TextLine, draw_order, num

FONT_STACK = "Figtree, Calibri, 'Segoe UI', Arial, sans-serif"
#: Glyph outlines are kept in a 1,000-unit em and scaled to the text size.
_EM = 1000.0


def _dash(st: Style) -> str:
    return f' stroke-dasharray="{num(st.dash[0])} {num(st.dash[1])}"' if st.dash else ""


def _rect(x: float, y: float, w: float, h: float, st: Style) -> str:
    out = (f'<rect x="{num(x)}" y="{num(y)}" width="{num(w)}" height="{num(h)}" '
           f'fill="{st.fill or "none"}"')
    if st.radius:
        out += f' rx="{num(min(st.radius, h / 2))}"'
    if st.stroke:
        out += f' stroke="{st.stroke}" stroke-width="{num(st.stroke_w)}"{_dash(st)}'
    return out + "/>"


@lru_cache(maxsize=2)
def _font(bold: bool) -> TTFont:
    return TTFont(str(T.FIGTREE_SEMIBOLD if bold else T.FIGTREE))


@lru_cache(maxsize=4096)
def glyph_path(bold: bool, ch: str) -> str:
    """The outline of `ch` as SVG path data in a 1,000-unit em, baseline at y 0 (y down);
    "" for spaces and characters the font has no outline for."""
    if ch.isspace():
        return ""
    f = _font(bold)
    name = f.getBestCmap().get(ord(ch))
    if name is None:
        return ""
    gs = f.getGlyphSet()
    pen = SVGPathPen(gs, ntos=num)
    s = _EM / f["head"].unitsPerEm
    gs[name].draw(TransformPen(pen, (s, 0, 0, -s, 0, 0)))
    return str(pen.getCommands())


def _outline(t: TextLine, x: float, y: float) -> str:
    m = tx.metrics(t.bold)
    k = t.size / _EM
    parts = []
    cx = x
    for ch in t.text:
        d = glyph_path(t.bold, ch)
        if d:
            parts.append(f'<path transform="translate({num(cx)} {num(y)}) scale({k:.6f})" '
                         f'd="{d}"/>')
        cx += m.advances.get(ord(ch), m.default_adv) * t.size / m.upm
    return f'<g fill="{t.color}">{"".join(parts)}</g>' if parts else ""


def _anchor(t: TextLine, w: float | None) -> tuple[float, str]:
    """(x, text-anchor) for live text: a line centred in its box anchors at the box
    centre, so a viewer that substitutes a wider or narrower font keeps it centred."""
    if w is not None and abs((w - t.width) / 2 - t.x) < 0.06:
        return w / 2, ' text-anchor="middle"'
    return t.x, ""


def _text(lines: list[TextLine], ox: float, oy: float, mode: str = "live",
          w: float | None = None) -> str:
    out = []
    for t in lines:
        if mode == "outline":
            out.append(_outline(t, ox + t.x, oy + t.baseline))
            continue
        weight = ' font-weight="600"' if t.bold else ""
        x, anchor = _anchor(t, w)
        # textLength pins each line to the width the layout measured, so a viewer
        # without Figtree (Arial runs up to 11% wider on digits) never overflows a box.
        out.append(f'<text x="{num(ox + x)}" y="{num(oy + t.baseline)}"{anchor} '
                   f'font-size="{num(t.size)}"{weight} fill="{t.color}" '
                   f'textLength="{num(t.width)}" lengthAdjust="spacingAndGlyphs">'
                   f"{escape(t.text)}</text>")
    return "".join(out)


def _node(n: Node, mode: str) -> str:
    names = {"data-kind"}
    attrs = ""
    for k, v in n.data.items():
        # The node kind owns data-kind; a data key spelled like an existing one is dropped.
        name = f"data-{k.replace('_', '-')}"
        if name not in names:
            names.add(name)
            attrs += f" {name}={quoteattr(v)}"
    title = f"<title>{escape(n.data.get('name') or n.text)}</title>" if n.lines else ""
    shape = "" if n.kind == "label" else _rect(n.x, n.y, n.w, n.h, n.style)
    return (f'<g id={quoteattr(n.id)} data-kind="{n.kind}"{attrs}>{title}{shape}'
            f"{_text(n.lines, n.x, n.y, mode, n.w)}</g>")


def arrow_points(p0: tuple[float, float], p1: tuple[float, float], width: float
                 ) -> list[tuple[float, float]]:
    """Arrowhead triangle at p1 pointing away from p0 (orthogonal segments)."""
    (x0, y0), (x1, y1) = p0, p1
    length, half = 4.2 + width, 2.0 + width * 0.6
    dx, dy = (x1 > x0) - (x1 < x0), (y1 > y0) - (y1 < y0)
    bx, by = x1 - dx * length, y1 - dy * length
    return [(x1, y1), (bx - dy * half, by + dx * half), (bx + dy * half, by - dx * half)]


def trimmed(e: Edge) -> list[tuple[float, float]]:
    """The stroke path, shortened under each arrowhead so tips stay sharp."""
    pts = list(e.points)
    if len(pts) < 2:
        return pts
    w = e.style.stroke_w
    cut = 3.0 + w

    def pull(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
        dx, dy = (b[0] > a[0]) - (b[0] < a[0]), (b[1] > a[1]) - (b[1] < a[1])
        return (b[0] - dx * cut, b[1] - dy * cut)

    if e.arrow_end:
        pts[-1] = pull(pts[-2], pts[-1])
    if e.arrow_start:
        pts[0] = pull(pts[1], pts[0])
    return pts


def _edge(e: Edge) -> str:
    st = e.style
    color = st.stroke or "#000000"
    pts = " ".join(f"{num(x)},{num(y)}" for x, y in trimmed(e))
    out = [f'<polyline points="{pts}" fill="none" stroke="{color}" '
           f'stroke-width="{num(st.stroke_w)}" stroke-linejoin="round"{_dash(st)}/>']
    for on, a, b in ((e.arrow_end, -2, -1), (e.arrow_start, 1, 0)):
        if on and len(e.points) >= 2:
            tri = arrow_points(e.points[a], e.points[b], st.stroke_w)
            out.append(f'<polygon points="{" ".join(f"{num(x)},{num(y)}" for x, y in tri)}" '
                       f'fill="{color}"/>')
    kind = e.data.get("kind", "")
    return f'<g id={quoteattr(e.id)} data-kind="edge" data-link={quoteattr(kind)}>' \
           + "".join(out) + "</g>"


def _label(e: Edge, lab: EdgeLabel, mode: str) -> str:
    bg = (f'<rect x="{num(lab.x)}" y="{num(lab.y)}" width="{num(lab.w)}" '
          f'height="{num(lab.h)}" rx="1.5" fill="#FFFFFF"/>' if lab.background else "")
    return f"<g>{bg}{_text(lab.lines, lab.x, lab.y, mode, lab.w)}</g>"


def render_svg(sc: Scene, mode: str = "live") -> bytes:
    """The scene as SVG: ``mode`` "live" (real text, the editable asset) or "outline"
    (glyph paths, for embedding where the font may be missing)."""
    if mode not in ("live", "outline"):
        raise ValueError(f"Unknown SVG text mode: {mode!r}")
    body: list[str] = []
    for kind, item in draw_order(sc):
        if kind == "node":
            body.append(_node(item, mode))
        elif kind == "edge":
            body.append(_edge(item))
        else:
            edge, lab = item
            body.append(_label(edge, lab, mode))
    font = f' font-family="{FONT_STACK}"' if mode == "live" else ""
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
           f'width="{num(sc.width)}pt" height="{num(sc.height)}pt" '
           f'viewBox="0 0 {num(sc.width)} {num(sc.height)}"{font}>'
           f"<title>{escape(sc.caption or sc.title)}</title>"
           f'<rect width="{num(sc.width)}" height="{num(sc.height)}" fill="#FFFFFF"/>'
           + "".join(body) + "</svg>\n")
    return svg.encode("utf-8")
