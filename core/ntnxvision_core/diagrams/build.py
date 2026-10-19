"""Layout primitives shared by every diagram type: measured text boxes, chips, edge
labels and the legend. Box heights always come from measured text, never the reverse."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from . import text as tx
from . import tokens as K
from .scene import Edge, EdgeLabel, Node, Scene, Style, TextLine, q


@dataclass(frozen=True)
class Row:
    """One paragraph of a box: fitted to the box width (shrink to `min_size`, wrap to
    `max_lines`, then ellipsise)."""

    text: str
    size: float
    bold: bool = False
    color: str = K.INK
    max_lines: int = 1
    min_size: float | None = None
    gap_before: float = 0.0


@dataclass(frozen=True)
class Measured:
    text: str
    size: float
    bold: bool
    color: str
    top: float  # line box top, relative to the text block
    width: float


def measure(rows: list[Row], inner_w: float) -> tuple[list[Measured], float]:
    out: list[Measured] = []
    y = 0.0
    for r in rows:
        if not r.text:
            continue
        y += r.gap_before if out else 0.0
        s, lines = tx.fit(r.text, r.size, r.bold, inner_w, r.max_lines, r.min_size or r.size)
        for ln in lines:
            out.append(Measured(ln, s, r.bold, r.color, y, tx.natural(ln, s, r.bold)))
            y += tx.line_height(s)
    return out, q(y)


def text_h(rows: list[Row], inner_w: float) -> float:
    return measure(rows, inner_w)[1]


def place_lines(measured: list[Measured], box_w: float, top: float, align: str,
                pad_x: float) -> list[TextLine]:
    out = []
    for m in measured:
        x = (box_w - m.width) / 2 if align == "center" else pad_x
        out.append(TextLine(text=m.text, size=m.size, bold=m.bold, color=m.color, x=q(x),
                            baseline=q(top + m.top + tx.baseline(m.size)), width=m.width))
    return out


def box(id: str, kind: str, x: float, y: float, w: float, rows: list[Row], style: Style, *,
        pad_x: float = 5.0, pad_y: float = 4.0, align: str = "center", h: float | None = None,
        valign: str = "top", parent: str | None = None,
        data: dict[str, str] | None = None) -> Node:
    """A node whose text is fitted to `w - 2 * pad_x`; height from the text unless `h`."""
    measured, th = measure(rows, w - 2 * pad_x)
    hh = h if h is not None else th + 2 * pad_y
    top = pad_y if valign == "top" else (hh - th) / 2
    return Node(id=id, kind=kind, x=q(x), y=q(y), w=q(w), h=q(hh), style=style,
                lines=place_lines(measured, w, top, align, pad_x), parent=parent,
                data=dict(sorted((data or {}).items())))


CHIP_H = 9.0


def chip_w(label: str) -> float:
    return q(tx.width(label, K.SIZE_CHIP, True) + 7)


def chip(id: str, label: str, x: float, edge_y: float, style: Style,
         parent: str | None = None) -> Node:
    """A small filled tag straddling a container's top edge (`edge_y`), left side at `x`."""
    return box(id, "tag", x, edge_y - CHIP_H / 2, chip_w(label),
               [Row(label, K.SIZE_CHIP, True, K.WHITE)], style, pad_x=3.5, pad_y=0,
               h=CHIP_H, valign="middle", parent=parent)


LabelText = str | tuple[str, str]


def label_block(lines: list[LabelText], max_w: float, *, size: float = K.SIZE_SMALL,
                color: str = K.IRIS, max_lines_each: int = 3) -> tuple[list[Measured], float,
                                                                       float]:
    """Edge label text: each line (text, or (text, colour)) fitted to `max_w`; returns
    (measured, width, height)."""
    rows = [Row(ln, size, False, color, max_lines_each) if isinstance(ln, str)
            else Row(ln[0], size, False, ln[1], max_lines_each) for ln in lines]
    measured, h = measure(rows, max_w - 4)
    w = max((m.width for m in measured), default=0.0)
    return measured, q(w * tx.SAFETY + 4), q(h + 2)


def edge_label(lines: list[LabelText], cx: float, cy: float, max_w: float, *,
               color: str = K.IRIS, background: bool = True,
               size: float = K.SIZE_SMALL) -> EdgeLabel:
    """A label centred on (cx, cy)."""
    measured, w, h = label_block(lines, max_w, color=color, size=size)
    return EdgeLabel(x=q(cx - w / 2), y=q(cy - h / 2), w=w, h=h,
                     lines=place_lines(measured, w, 1.0, "center", 0.0),
                     background=background)


def edge(id: str, src: str, dst: str, pts: list[tuple[float, float]], style: Style, *,
         arrow_end: bool = True, arrow_start: bool = False,
         data: dict[str, str] | None = None) -> Edge:
    return Edge(id=id, src=src, dst=dst, points=[(q(a), q(b)) for a, b in pts], style=style,
                arrow_start=arrow_start, arrow_end=arrow_end,
                data=dict(sorted((data or {}).items())))


def _legend_rows(widths: list[float], gap: float, max_w: float) -> list[int]:
    """Items per row: as few rows as a greedy fill needs, balanced so no row is left
    with a lone item when the others are full."""
    def fill(limit: float) -> list[int]:
        rows: list[int] = []
        cur = 0.0
        for w in widths:
            if rows and cur + gap + w <= limit:
                rows[-1] += 1
                cur += gap + w
            else:
                rows.append(1)
                cur = w
        return rows

    greedy = fill(max_w)
    total = sum(widths) + gap * (len(widths) - 1)
    target = max(max(widths, default=0.0), total / len(greedy) if greedy else 0.0)
    limit = target
    while limit < max_w:
        rows = fill(limit)
        if len(rows) <= len(greedy):
            return rows
        limit += 4.0
    return greedy


def legend(scene: Scene, items: list[tuple[str, Style, str]], x: float, y: float,
           max_w: float) -> float:
    """Append a legend (swatch + label per item, in balanced rows within `max_w`) as
    ordinary nodes and edges; returns its height."""
    row_h = 11.0
    gap = 12.0
    widths = [20 + 4 + tx.width(label, K.SIZE_SMALL) for _, _, label in items]
    rows = _legend_rows(widths, gap, max_w)
    i = 0
    cy = y
    for count in rows:
        cx = x
        for _ in range(count):
            kind, style, label = items[i]
            lw = tx.width(label, K.SIZE_SMALL)
            sid = f"legend:{i}"
            if kind == "box":
                scene.nodes.append(Node(id=sid, kind="swatch", x=q(cx), y=q(cy + 1.5), w=17,
                                        h=7.5, style=style))
            else:
                scene.edges.append(edge(sid, "", "", [(cx, cy + 5.25), (cx + 17, cy + 5.25)],
                                        style, arrow_end=False))
            scene.nodes.append(box(f"{sid}:label", "label", cx + 21, cy, lw + 1,
                                   [Row(label, K.SIZE_SMALL, False, K.INK_2)], K.LABEL,
                                   pad_x=0, pad_y=0, align="left", h=row_h - 0.5,
                                   valign="middle"))
            cx = q(cx + widths[i] + gap)
            i += 1
        cy += row_h
    return q(cy - y)



#: A row hung on a trunk: (height, draw(x, y, w) -> nodes, the row node first).
TrunkRow = tuple[float, Callable[[float, float, float], list[Node]]]


def trunk_list(sc: Scene, bar: str, top: float, x: float, w: float, rows: list[TrunkRow],
               *, gap: float = 3.5, indent: float = 11.0, style: Style = K.NET_LINK) -> float:
    """Hang `rows` under a bar on one vertical trunk with a stub to each row, the way a
    switch carries its segments (N1) and a VPC its overlay subnets (N2), so the two
    read alike. The trunk runs at `x + 4.5` down from `top` (the bar's bottom edge);
    rows start at `x + indent`, `w - indent` wide, 5 pt under the bar. Returns the y
    under the last row (gap included)."""
    trunk = q(x + 4.5)
    sx, sw = q(x + indent), q(w - indent)
    cy = q(top + 5)
    last = top
    for h, draw in rows:
        nodes = draw(sx, cy, sw)
        sc.nodes.extend(nodes)
        rid = nodes[0].id
        mid = q(cy + h / 2)
        sc.edges.append(edge(f"{bar}->{rid}", bar, rid, [(trunk, last), (trunk, mid),
                                                         (sx, mid)], style,
                             arrow_end=False, data={"kind": "trunk"}))
        last = mid
        cy = q(cy + h + gap)
    return cy
