"""Scene -> SVG with live text (the editable asset).

Real ``<text>`` elements in ``font-family: Figtree`` with Office-safe fallbacks; every
position, size and colour comes from the scene, so the file is byte-stable. Each shape is
a ``<g>`` carrying the scene id, and semantic data is kept in ``data-*`` attributes and a
``<title>`` (full names that a box had to ellipsise stay readable there).
"""

from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr

from ..scene import Edge, EdgeLabel, Node, Scene, Style, TextLine, draw_order, num

FONT_STACK = "Figtree, Calibri, 'Segoe UI', Arial, sans-serif"


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


def _text(lines: list[TextLine], ox: float, oy: float) -> str:
    out = []
    for t in lines:
        weight = ' font-weight="600"' if t.bold else ""
        out.append(f'<text x="{num(ox + t.x)}" y="{num(oy + t.baseline)}" '
                   f'font-size="{num(t.size)}"{weight} fill="{t.color}">'
                   f"{escape(t.text)}</text>")
    return "".join(out)


def _node(n: Node) -> str:
    attrs = "".join(f" data-{k.replace('_', '-')}={quoteattr(v)}" for k, v in n.data.items())
    title = f"<title>{escape(n.data.get('name') or n.text)}</title>" if n.lines else ""
    shape = "" if n.kind == "label" else _rect(n.x, n.y, n.w, n.h, n.style)
    return (f'<g id={quoteattr(n.id)} data-kind="{n.kind}"{attrs}>{title}{shape}'
            f"{_text(n.lines, n.x, n.y)}</g>")


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


def _label(e: Edge, lab: EdgeLabel) -> str:
    bg = (f'<rect x="{num(lab.x)}" y="{num(lab.y)}" width="{num(lab.w)}" '
          f'height="{num(lab.h)}" rx="1.5" fill="#FFFFFF"/>' if lab.background else "")
    return f"<g>{bg}{_text(lab.lines, lab.x, lab.y)}</g>"


def render_svg(sc: Scene) -> bytes:
    body: list[str] = []
    for kind, item in draw_order(sc):
        if kind == "node":
            body.append(_node(item))
        elif kind == "edge":
            body.append(_edge(item))
        else:
            body.append(_label(*item))
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
           f'width="{num(sc.width)}pt" height="{num(sc.height)}pt" '
           f'viewBox="0 0 {num(sc.width)} {num(sc.height)}" font-family="{FONT_STACK}">'
           f"<title>{escape(sc.caption or sc.title)}</title>"
           f'<rect width="{num(sc.width)}" height="{num(sc.height)}" fill="#FFFFFF"/>'
           + "".join(body) + "</svg>\n")
    return svg.encode("utf-8")
