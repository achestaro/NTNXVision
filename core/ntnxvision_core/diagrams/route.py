"""Orthogonal routing helpers shared by the layouts (DESIGN.md §10 `route.py`).

The layouts place boxes; edges then run through gutters (vertical lanes between
columns) and channels (horizontal runs under the columns). This module decides the
order of those lanes and channels, where edges attach to a box side, keeps edge labels
apart, and, last, draws a small hop wherever two edges still cross.

* `spread`: attachment points along one box side, ordered by the coordinate of each
  edge's other end, so edges leaving one side do not cross each other there.
* `lane_order`: the left-to-right order of bent edges through a gutter that crosses
  the fewest other runs (exhaustive for small sets, by the left end otherwise).
* `nested_channels`: channel order under the columns: a bus whose span lies inside
  another's runs closer to the columns, so the outer bus's drops never cut it.
* `deoverlap`: labels on a horizontal run slid along it until none overlap.
* `crossings` / `hop`: count the crossings left; replace each crossing on a horizontal
  run by a small hop (the run lifts over the vertical one).
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass

from .scene import Edge, q

Point = tuple[float, float]
#: Hop size (pt): half width along the run, height above it.
HOP_W, HOP_H = 2.6, 2.4


def spread(lo: float, hi: float, others: Sequence[tuple[float, str]]) -> dict[str, float]:
    """Attachment coordinates between `lo` and `hi` for (other end, key) pairs, evenly
    spaced and ordered by the other end (ties by key)."""
    order = sorted(others)
    k = len(order)
    return {key: q(lo + (hi - lo) * (j + 1) / (k + 1)) for j, (_, key) in enumerate(order)}


def lane_order(spans: list[tuple[float, float]]) -> list[int]:
    """Left-to-right lane order for bent edges crossing a gutter, each (y on the left,
    y on the right): a left run crosses every lane left of its own that spans its y, a
    right run every lane right of its own. Exhaustive up to 6 edges, else by left y."""
    n = len(spans)
    base = sorted(range(n), key=lambda i: (spans[i][0], spans[i][1], i))
    if n > 6:
        return base

    def inside(y: float, k: int) -> bool:
        a, b = sorted(spans[k])
        return a < y < b

    best, best_n = base, None
    for perm in itertools.permutations(base):
        at = {k: j for j, k in enumerate(perm)}
        n_cross = sum(1 for i in range(n) for k in range(n) if i != k and (
            (at[k] < at[i] and inside(spans[i][0], k))
            or (at[k] > at[i] and inside(spans[i][1], k))))
        if best_n is None or n_cross < best_n:
            best, best_n = list(perm), n_cross
    return best


def nested_channels(spans: dict[str, tuple[float, float]]) -> list[str]:
    """Channel order (nearest the columns first) for buses spanning x ranges: a span
    inside another goes first; disjoint spans keep left-to-right order."""
    def key(k: str) -> tuple[float, float, str]:
        a, b = spans[k]
        inside_of = sum(1 for o, (c, d) in spans.items() if o != k and c <= a and b <= d
                        and (c, d) != (a, b))
        return (-inside_of, b - a, k)

    return sorted(spans, key=lambda k: (key(k), spans[k][0]))


@dataclass(frozen=True)
class Trunk:
    """A bus from one column to one target below: stubs from its cards (x where the
    stub starts, y) to its lane, the lane down to its channel, the channel to its entry
    point, and the drop into the target at `bottom`."""

    stubs: tuple[tuple[float, float], ...]
    lane: float
    lane_top: float
    entry: float
    bottom: float


def _trunk_segments(t: Trunk, y: float) -> tuple[list[tuple[Point, Point]],
                                                 list[tuple[Point, Point]]]:
    hs = [((x0, sy), (t.lane, sy)) for x0, sy in t.stubs]
    hs.append(((t.lane, y), (t.entry, y)))
    vs = [((t.lane, t.lane_top), (t.lane, y)), ((t.entry, y), (t.entry, t.bottom))]
    return hs, vs


def trunk_crossings(trunks: Sequence[Trunk], ys: Sequence[float]) -> int:
    """Crossings between different trunks drawn on channels `ys`."""
    segs = [_trunk_segments(t, y) for t, y in zip(trunks, ys, strict=True)]
    n = 0
    for i, j in itertools.permutations(range(len(trunks)), 2):
        for h in segs[i][0]:
            if not _horizontal(h):
                continue
            for v in segs[j][1]:
                if _vertical(v) and _cross(h, v) is not None:
                    n += 1
    return n


def channel_order(trunks: Sequence[Trunk], top: float, pitch: float) -> list[int]:
    """Channel index (0 = nearest the columns) per trunk with the fewest crossings:
    exhaustive up to 6 trunks, else nested spans (`nested_channels`)."""
    n = len(trunks)
    if n == 0:
        return []
    spans = {str(i): (min(t.lane, t.entry), max(t.lane, t.entry))
             for i, t in enumerate(trunks)}
    base = [int(k) for k in nested_channels(spans)]
    if n > 6:
        return [base.index(i) for i in range(n)]
    best, best_n = base, None
    for perm in itertools.permutations(base):
        ys = [0.0] * n
        for k, i in enumerate(perm):
            ys[i] = top + k * pitch
        c = trunk_crossings(trunks, ys)
        if best_n is None or c < best_n:
            best, best_n = list(perm), c
    return [best.index(i) for i in range(n)]


def deoverlap(edges: Sequence[Edge], width: float, gap: float = 2.0) -> None:
    """Slide overlapping edge labels apart along x, each staying on its own edge (the
    anchoring `check.check_scene` enforces) and inside `width`; a pair that cannot
    separate that way is left as it is."""
    from .check import ANCHOR, _gap_to_path

    owned = [(lb, e) for e in edges for lb in e.labels]
    owned.sort(key=lambda le: (le[0].x, le[0].y))
    for (a, ea), (b, eb) in itertools.combinations(owned, 2):
        if not (a.y < b.y + b.h and b.y < a.y + a.h and a.x < b.x + b.w + gap
                and b.x < a.x + a.w + gap):
            continue
        for lab, owner, x in ((b, eb, q(a.x + a.w + gap)), (a, ea, q(b.x - gap - a.w))):
            old = lab.x
            lab.x = x
            if lab.x >= 0 and lab.x + lab.w <= width and \
                    _gap_to_path(lab.x, lab.y, lab.w, lab.h, owner.points) <= ANCHOR:
                break
            lab.x = old


def _segments(points: list[Point]) -> list[tuple[Point, Point]]:
    return list(zip(points, points[1:], strict=False))


def _cross(h: tuple[Point, Point], v: tuple[Point, Point]) -> Point | None:
    """Where horizontal run `h` crosses vertical run `v` strictly inside both."""
    (hx0, hy), (hx1, _) = h
    (vx, vy0), (_, vy1) = v
    lo, hi = sorted((hx0, hx1))
    vlo, vhi = sorted((vy0, vy1))
    if lo + HOP_W < vx < hi - HOP_W and vlo + 0.5 < hy < vhi - 0.5:
        return (vx, hy)
    return None


def _horizontal(s: tuple[Point, Point]) -> bool:
    return abs(s[0][1] - s[1][1]) < 0.01 and abs(s[0][0] - s[1][0]) >= 0.01


def _vertical(s: tuple[Point, Point]) -> bool:
    return abs(s[0][0] - s[1][0]) < 0.01 and abs(s[0][1] - s[1][1]) >= 0.01


def crossings(edges: Sequence[Edge]) -> list[tuple[str, str, Point]]:
    """Every point where a horizontal run of one edge crosses a vertical run of another
    (edges of one bus that share an endpoint do not count)."""
    out = []
    for e1, e2 in itertools.permutations(edges, 2):
        if {e1.src, e1.dst} & {e2.src, e2.dst} and e1.dst == e2.dst:
            continue
        for h in filter(_horizontal, _segments(e1.points)):
            for v in filter(_vertical, _segments(e2.points)):
                if (p := _cross(h, v)) is not None:
                    out.append((e1.id, e2.id, p))
    return out


def hop(edges: Sequence[Edge]) -> int:
    """Replace each crossing on a horizontal run with a small hop over the vertical
    run; returns how many hops were drawn."""
    marks: dict[str, list[Point]] = {}
    for a, _b, p in crossings(edges):
        marks.setdefault(a, []).append(p)
    n = 0
    for e in edges:
        if e.id not in marks:
            continue
        pts: list[Point] = [e.points[0]]
        for s0, s1 in _segments(e.points):
            if _horizontal((s0, s1)):
                y = s0[1]
                sign = 1.0 if s1[0] > s0[0] else -1.0
                xs = sorted({p[0] for p in marks[e.id] if p[1] == y and
                             min(s0[0], s1[0]) < p[0] < max(s0[0], s1[0])},
                            reverse=sign < 0)
                for x in xs:
                    pts += [(q(x - sign * HOP_W), y), (q(x - sign * HOP_W * 0.5), q(y - HOP_H)),
                            (q(x + sign * HOP_W * 0.5), q(y - HOP_H)),
                            (q(x + sign * HOP_W), y)]
                    n += 1
            pts.append(s1)
        e.points = pts
    return n
