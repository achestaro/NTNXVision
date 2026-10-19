"""Scenes -> one draw.io (diagrams.net) file, one tab per scene, uncompressed mxGraph XML.

Containers (an availability zone, a POD) are draw.io containers: their members use
parent-relative geometry and move with them. Every node keeps its text as HTML runs
(size, weight and colour per line) and its semantic data as cell attributes. Connectors
keep their source and target (when the edge has ends), their orthogonal waypoints and
their first label; further labels are text cells. Fonts: Calibri, then Figtree, Carlito,
Segoe UI and Arial (`FONT`). Numbers print through `num`, so equal scenes give equal bytes.
"""

from __future__ import annotations

import math
import re
from xml.sax.saxutils import escape, quoteattr

from .. import text as tx
from ..scene import LAYER, Edge, EdgeLabel, Node, Scene, TextLine, num
from .vsdx import page_names

#: Calibri first, as in the .vsdx: its widths are checked against the layout's reserve
#: (`text.SAFETY`); Figtree, the measuring font, where Calibri is missing.
FONT = "Calibri,Figtree,Carlito,Segoe UI,Arial"
_ATTR = re.compile(r"[^A-Za-z0-9_]")


def _html(lines: list[TextLine]) -> str:
    out = []
    for t in lines:
        s = escape(t.text)
        if t.bold:
            s = f"<b>{s}</b>"
        out.append(f'<font style="font-size:{num(t.size)}px" color="{t.color}">{s}</font>')
    return "<br>".join(out)


def _align(lines: list[TextLine], w: float) -> tuple[bool, float, float]:
    """(centred, left spacing, top spacing) that place the text block like the scene."""
    centred = all(abs(t.x - (w - t.width) / 2) < 0.6 for t in lines) and any(
        t.x > 1.0 for t in lines)
    left = 0.0 if centred else min(t.x for t in lines)
    top = max(0.0, lines[0].baseline - tx.baseline(lines[0].size))
    return centred, left, top


def _text_style(lines: list[TextLine], w: float) -> list[str]:
    if not lines:
        return []
    centred, left, top = _align(lines, w)
    return [f"align={'center' if centred else 'left'}", "verticalAlign=top",
            f"spacingLeft={num(left)}", f"spacingRight={num(left if centred else 0)}",
            f"spacingTop={num(top)}", "spacingBottom=0", "spacing=0"]


def _node_style(n: Node, container: bool = False) -> str:
    st = n.style
    parts = ["html=1", "whiteSpace=nowrap", f"fontFamily={FONT}", *_text_style(n.lines, n.w)]
    if n.kind == "label":
        parts += ["text=1", "fillColor=none", "strokeColor=none"]
    else:
        parts += [f"fillColor={st.fill or 'none'}", f"strokeColor={st.stroke or 'none'}",
                  f"strokeWidth={num(st.stroke_w)}"]
        if st.dash:
            parts += ["dashed=1", f"dashPattern={num(st.dash[0])} {num(st.dash[1])}"]
        if st.radius:
            parts += ["rounded=1", "absoluteArcSize=1", f"arcSize={num(st.radius * 2)}"]
    if container or n.kind == "container":
        parts += ["container=1", "collapsible=0", "recursiveResize=0"]
    return ";".join(parts) + ";"


def _port(n: Node | None, p: tuple[float, float]) -> tuple[float, float] | None:
    """`p` relative to node `n` (0..1 each way), when it lies on the node's edge."""
    if n is None or n.w <= 0 or n.h <= 0:
        return None
    rx, ry = (p[0] - n.x) / n.w, (p[1] - n.y) / n.h
    if not (-0.01 <= rx <= 1.01 and -0.01 <= ry <= 1.01):
        return None
    return (min(1.0, max(0.0, rx)), min(1.0, max(0.0, ry)))


def _edge_style(e: Edge, by_id: dict[str, Node] | None = None) -> str:
    st = e.style
    color = st.stroke or "#000000"
    routed = bool(e.src or e.dst)
    # Arrowheads grow with the line, as in the report.
    size = num(4 + 2 * st.stroke_w)
    parts = ["html=1", "rounded=0", f"fontFamily={FONT}", f"strokeColor={color}",
             f"strokeWidth={num(st.stroke_w)}", "labelBackgroundColor=#FFFFFF",
             f"endArrow={'block' if e.arrow_end else 'none'}", "endFill=1", f"endSize={size}",
             f"startArrow={'block' if e.arrow_start else 'none'}", "startFill=1",
             f"startSize={size}"]
    if routed:
        parts.insert(0, "edgeStyle=orthogonalEdgeStyle")
        # Pinned ports: the edge leaves and enters where the layout drew it, and
        # keeps those points when a shape is moved.
        by_id = by_id or {}
        if e.points and (src := _port(by_id.get(e.src), e.points[0])) is not None:
            parts += [f"exitX={num(src[0])}", f"exitY={num(src[1])}", "exitDx=0", "exitDy=0"]
        if e.points and (dst := _port(by_id.get(e.dst), e.points[-1])) is not None:
            parts += [f"entryX={num(dst[0])}", f"entryY={num(dst[1])}", "entryDx=0",
                      "entryDy=0"]
    if e.labels and not e.labels[0].background:
        parts[parts.index("labelBackgroundColor=#FFFFFF")] = "labelBackgroundColor=none"
    if st.dash:
        parts += ["dashed=1", f"dashPattern={num(st.dash[0])} {num(st.dash[1])}"]
    return ";".join(parts) + ";"


def _label_position(e: Edge, lab: EdgeLabel) -> tuple[float, tuple[float, float]]:
    """draw.io places an edge label at a relative position in [-1, 1] along the path,
    plus an offset: the point of the path nearest the label centre, and the rest."""
    pts = e.points
    lx, ly = lab.x + lab.w / 2, lab.y + lab.h / 2
    seg = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:], strict=False)]
    total = sum(seg) or 1.0
    best = (float("inf"), 0.0, pts[0])
    run = 0.0
    for (a, b), length in zip(zip(pts, pts[1:], strict=False), seg, strict=True):
        if length:
            t = ((lx - a[0]) * (b[0] - a[0]) + (ly - a[1]) * (b[1] - a[1])) / length**2
            t = max(0.0, min(1.0, t))
            px, py = a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])
            d = math.hypot(lx - px, ly - py)
            if d < best[0]:
                best = (d, run + t * length, (px, py))
        run += length
    _, along, (px, py) = best
    return 2 * along / total - 1, (lx - px, ly - py)


def _data_attrs(data: dict[str, str]) -> str:
    out = []
    seen = {"id", "label", "placeholders"}
    for k, v in data.items():
        name = _ATTR.sub("_", k)
        if name in seen or not name or name[0].isdigit():
            continue
        seen.add(name)
        out.append(f" {name}={quoteattr(v)}")
    return "".join(out)


def _scene(sc: Scene, idx: int, name: str) -> str:
    by_id = {n.id: n for n in sc.nodes}
    index = {n.id: i for i, n in enumerate(sc.nodes)}

    # Every node with members is a draw.io container (an AZ, a POD, a card holding its
    # chips), so moving it moves what it holds.
    parents = {n.parent for n in sc.nodes if n.parent in by_id}

    def container_of(n: Node) -> str | None:
        return n.parent if n.parent in by_id and n.parent != n.id else None

    def depth(n: Node) -> int:
        d, c = 0, container_of(n)
        while c is not None:
            d += 1
            c = container_of(by_id[c])
        return d

    def cid(x: str) -> str:
        return f"{idx}:{x}"

    cells = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']
    # Parents before members; then the scene's paint order.
    nodes = sorted(sc.nodes, key=lambda n: (LAYER.get(n.kind, 1) >= 2,
                                            depth(n) if n.id in parents else 99,
                                            LAYER.get(n.kind, 1), index[n.id]))
    lower = [n for n in nodes if LAYER.get(n.kind, 1) < 2]
    upper = [n for n in nodes if LAYER.get(n.kind, 1) >= 2]

    def vertex(n: Node) -> str:
        parent = container_of(n)
        ox, oy = (by_id[parent].x, by_id[parent].y) if parent else (0.0, 0.0)
        value = _html(n.lines)
        attrs = _data_attrs({"kind": n.kind, **n.data})
        return (f'<object id={quoteattr(cid(n.id))} label={quoteattr(value)}{attrs}>'
                f'<mxCell style={quoteattr(_node_style(n, n.id in parents))} vertex="1" '
                f'parent={quoteattr(cid(parent) if parent else "1")}>'
                f'<mxGeometry x="{num(n.x - ox)}" y="{num(n.y - oy)}" width="{num(n.w)}" '
                f'height="{num(n.h)}" as="geometry"/></mxCell></object>')

    cells += [vertex(n) for n in lower]
    for e in sc.edges:
        src = f" source={quoteattr(cid(e.src))}" if e.src in by_id else ""
        dst = f" target={quoteattr(cid(e.dst))}" if e.dst in by_id else ""
        first = e.labels[0] if e.labels else None
        label = _html(first.lines) if first else ""
        rel_x, (dx, dy) = _label_position(e, first) if first else (0.0, (0.0, 0.0))
        way = "".join(f'<mxPoint x="{num(x)}" y="{num(y)}"/>' for x, y in e.points[1:-1])
        (sx, sy), (tx_, ty) = e.points[0], e.points[-1]
        cells.append(
            f'<mxCell id={quoteattr(cid(e.id))} value={quoteattr(label)} '
            f'style={quoteattr(_edge_style(e, by_id))} edge="1" parent="1"{src}{dst}>'
            f'<mxGeometry x="{num(rel_x)}" relative="1" as="geometry">'
            f'<mxPoint x="{num(sx)}" y="{num(sy)}" as="sourcePoint"/>'
            f'<mxPoint x="{num(tx_)}" y="{num(ty)}" as="targetPoint"/>'
            f'<Array as="points">{way}</Array>'
            f'<mxPoint x="{num(dx)}" y="{num(dy)}" as="offset"/></mxGeometry></mxCell>')
        for k, lab in enumerate(e.labels[1:], start=1):
            style = ";".join(["text=1", "html=1", "whiteSpace=nowrap", f"fontFamily={FONT}",
                              f"fillColor={'#FFFFFF' if lab.background else 'none'}",
                              "strokeColor=none", *_text_style(lab.lines, lab.w)]) + ";"
            cells.append(
                f'<mxCell id={quoteattr(cid(f"{e.id}:label:{k}"))} '
                f'value={quoteattr(_html(lab.lines))} style={quoteattr(style)} vertex="1" '
                f'parent="1"><mxGeometry x="{num(lab.x)}" y="{num(lab.y)}" '
                f'width="{num(lab.w)}" height="{num(lab.h)}" as="geometry"/></mxCell>')
    cells += [vertex(n) for n in upper]
    return (f'<diagram id={quoteattr(f"{idx}:{sc.id}")} name={quoteattr(name)}>'
            f'<mxGraphModel dx="0" dy="0" grid="0" gridSize="10" guides="1" tooltips="1" '
            f'connect="1" arrows="1" fold="1" page="1" pageScale="1" '
            f'pageWidth="{num(sc.width)}" pageHeight="{num(sc.height)}" '
            f'background="#FFFFFF" math="0" shadow="0">'
            f'<root>{"".join(cells)}</root></mxGraphModel></diagram>')


def render_drawio(scenes: list[Scene], names: list[str] | None = None) -> bytes:
    """One .drawio file holding every scene as its own tab, named `names`."""
    tabs = page_names(scenes, names)
    body = "".join(_scene(sc, i, tabs[i]) for i, sc in enumerate(scenes))
    return (f'<mxfile host="NTNXVision" version="24.7.17" type="device" '
            f'pages="{len(scenes)}">{body}</mxfile>\n').encode()
