"""Layout primitives shared by every diagram type: measured text boxes, chips, edge
labels and the legend. Box heights always come from measured text, never the reverse."""

from __future__ import annotations

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


def label_block(lines: list[str], max_w: float, *, size: float = K.SIZE_SMALL,
                color: str = K.IRIS, max_lines_each: int = 2) -> tuple[list[Measured], float,
                                                                      float]:
    """Edge label text: each line fitted to `max_w`; returns (measured, width, height)."""
    rows = [Row(ln, size, False, color, max_lines_each) for ln in lines]
    measured, h = measure(rows, max_w - 4)
    w = max((m.width for m in measured), default=0.0)
    return measured, q(w * tx.SAFETY + 4), q(h + 2)


def edge_label(lines: list[str], cx: float, cy: float, max_w: float, *,
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


def legend(scene: Scene, items: list[tuple[str, Style, str]], x: float, y: float,
           max_w: float) -> float:
    """Append a legend (swatch + label per item, wrapping into rows within `max_w`) as
    ordinary nodes and edges; returns its height."""
    row_h = 11.0
    cx, cy = x, y
    for i, (kind, style, label) in enumerate(items):
        lw = tx.width(label, K.SIZE_SMALL)
        item_w = 20 + 4 + lw
        if cx > x and cx + item_w > x + max_w:
            cx, cy = x, cy + row_h
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
        cx = q(cx + item_w + 12)
    return q(cy + row_h - y)
