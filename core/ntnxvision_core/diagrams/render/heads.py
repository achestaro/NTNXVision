"""Arrowhead shapes, shared by every renderer (SVG, PNG, PDF) and mirrored by the editor
(`app/src/renderer/views/diagrams/arrowheads.ts`).

An edge end draws a head when its ``arrow_start`` / ``arrow_end`` is on; the shape is
``head_start`` / ``head_end`` (an author's ``edge`` edit), else the filled arrow every
generated connector uses. Sizes grow with the stroke width, as the arrow always did.

* ``arrow``: filled triangle (the default);
* ``open``: an open chevron, stroked;
* ``diamond``: filled diamond;
* ``circle``: filled dot.

The stroke is shortened under each head (`trimmed`) so a tip stays sharp and a dashed
line never shows through a filled head.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..scene import Edge

#: Arrowhead shapes an edit may choose ("none": a plain line end).
HEADS = ("none", "arrow", "open", "diamond", "circle")

Pt = tuple[float, float]


@dataclass(frozen=True)
class Mark:
    """One drawn head: a filled ``polygon``, a stroked ``polyline`` or a filled ``circle``
    (centre ``points[0]``, radius ``r``)."""

    shape: str
    points: list[Pt]
    r: float = 0.0


def kinds(e: Edge) -> tuple[str, str]:
    """(start, end) head shapes of `e`, "none" where it has no head."""
    def one(on: bool, kind: str) -> str:
        if not on:
            return "none"
        return kind if kind in HEADS and kind != "none" else "arrow"

    return one(e.arrow_start, e.head_start), one(e.arrow_end, e.head_end)


def arrow_points(p0: Pt, p1: Pt, width: float) -> list[Pt]:
    """Arrowhead triangle at p1 pointing away from p0 (orthogonal segments)."""
    (x0, y0), (x1, y1) = p0, p1
    length, half = 4.2 + width, 2.0 + width * 0.6
    dx, dy = (x1 > x0) - (x1 < x0), (y1 > y0) - (y1 < y0)
    bx, by = x1 - dx * length, y1 - dy * length
    return [(x1, y1), (bx - dy * half, by + dx * half), (bx + dy * half, by - dx * half)]


def _unit(p0: Pt, p1: Pt) -> Pt:
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n else (1.0, 0.0)


def cut(kind: str, width: float) -> float:
    """How far the stroke stops short of the tip under a head of `kind`."""
    if kind == "arrow":
        return 3.0 + width
    if kind == "open":
        return width * 0.6
    if kind == "diamond":
        return 2 * (3.0 + width) - 0.4
    if kind == "circle":
        return 2 * (1.6 + 0.6 * width) - 0.4
    return 0.0


def mark(kind: str, p0: Pt, p1: Pt, width: float) -> Mark | None:
    """The head of `kind` at `p1`, pointing away from `p0`."""
    if kind == "arrow":
        return Mark("polygon", arrow_points(p0, p1, width))
    ux, uy = _unit(p0, p1)
    nx, ny = -uy, ux
    tx, ty = p1
    if kind == "open":
        length, half = 4.2 + width, 2.0 + width * 0.6
        bx, by = tx - ux * length, ty - uy * length
        return Mark("polyline", [(bx + nx * half, by + ny * half), (tx, ty),
                                 (bx - nx * half, by - ny * half)])
    if kind == "diamond":
        length, half = 2 * (3.0 + width), 2.0 + width * 0.6
        mx, my = tx - ux * length / 2, ty - uy * length / 2
        return Mark("polygon", [(tx, ty), (mx + nx * half, my + ny * half),
                                (tx - ux * length, ty - uy * length),
                                (mx - nx * half, my - ny * half)])
    if kind == "circle":
        r = 1.6 + 0.6 * width
        return Mark("circle", [(tx - ux * r, ty - uy * r)], r)
    return None


def marks(e: Edge) -> list[Mark]:
    """The heads `e` draws (end first, then start, the order renderers always used)."""
    if len(e.points) < 2:
        return []
    start, end = kinds(e)
    out = []
    for kind, a, b in ((end, -2, -1), (start, 1, 0)):
        m = mark(kind, e.points[a], e.points[b], e.style.stroke_w)
        if m is not None:
            out.append(m)
    return out


def trimmed(e: Edge) -> list[Pt]:
    """The stroke path, shortened under each head so tips stay sharp."""
    pts = list(e.points)
    if len(pts) < 2:
        return pts
    w = e.style.stroke_w
    start, end = kinds(e)

    def pull(a: Pt, b: Pt, by: float) -> Pt:
        if by <= 0:
            return b
        dx, dy = (b[0] > a[0]) - (b[0] < a[0]), (b[1] > a[1]) - (b[1] < a[1])
        if dx and dy:  # a diagonal run (never routed, but drawn the same way)
            ux, uy = _unit(a, b)
            return (b[0] - ux * by, b[1] - uy * by)
        return (b[0] - dx * by, b[1] - dy * by)

    if end != "none":
        pts[-1] = pull(pts[-2], pts[-1], cut(end, w))
    if start != "none":
        pts[0] = pull(pts[1], pts[0], cut(start, w))
    return pts
