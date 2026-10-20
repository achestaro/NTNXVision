"""Connector edits drawn on laid-out scenes: user-added connectors and changes to the
generated ones (Diagrams editor; ops ``connect`` and ``edge`` in `edits.py`).

Every diagram type lays its model out first; this overlay then runs on the scenes, so a
connector edit reaches the editor, the Word and PDF figures and the editable assets
alike, with no per-type code (`DiagramType` wires it in). Nothing here is free-placed:
an edit names ends, sides, a lane offset, arrowheads and a label, and the router below
turns them into an orthogonal route.

Router (deterministic: the 0.01 pt grid, a fixed candidate order, a total score order):

* ports on a side, at least 10 pt from where another connector already ends there (a
  side with no room is not used);
* candidates per side pair (``auto`` tries all four sides of that end): straight, the
  two L shapes and Z shapes through mid lines (the midpoint, gutters beside the boxes
  nearby, just outside both ends), each leaving and entering through a short stub;
* score, in order: points off the canvas, boxes, zone headings and other connectors'
  ends crossed, runs through an end, runs on a zone border, runs shared with another
  connector, labels crossed, crossings, bends, length;
* every route chosen is checked with the scene rules (`check.route_problems`); a pinned
  side that forces a bad route falls back to Auto (and says so), and a connector with no
  clean route is not drawn (and says so), so a figure never carries a bad route;
* ``bend`` shifts the middle run (a straight run slides along both sides, within their
  overlap), pulled back toward zero until the route is clean;
* once every route is placed, each crossing gets a hop on whichever line is horizontal;
* labels go on the first clean spot along their line (`check.label_problems`); a label
  with no clean spot is not drawn (and says so).

What happened to each changed connector is kept in ``scene.meta["connectors"]``: the
collected label and ends, whether it was routed or labelled here, the sides it uses, the
lane offset applied and any ``issue`` ("side", "route", "label").
"""

from __future__ import annotations

import bisect
import heapq
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from . import tokens as K
from .build import edge_label
from .check import (
    PORT_GAP,
    crossing_problems,
    heading_boxes,
    label_problems,
    route_problems,
    side_of,
)
from .route import HOP_H, HOP_W
from .scene import Edge, EdgeLabel, Node, Scene, Style, q

#: A user-added connector: dashed amber, like every other user-added item.
USER_LINK = Style(stroke=K.AMBER, stroke_w=0.9, dash=(2.4, 1.8))
#: Edges that are structure, not connectors (a bus trunk, a pairing glyph).
STRUCTURAL = frozenset({"trunk", "glyph"})
#: Stub length out of a side, gutter beside a box.
STUB, GUTTER = 8.0, 6.0
#: Clearance a route keeps from boxes that are not its ends; around another connector's
#: end; most mid lines tried per axis.
CLEAR, END_ZONE, MAX_MIDS = 2.5, 8.0, 18
#: Widest label a connector carries.
LABEL_W = 140.0
#: Box kinds a route keeps clear of.
_SOLID = frozenset({"box", "container", "strip", "pill", "tag"})
SIDES = ("top", "right", "bottom", "left")
_NORMAL = {"top": (0.0, -1.0), "right": (1.0, 0.0), "bottom": (0.0, 1.0),
           "left": (-1.0, 0.0)}

#: What a generated connector is called in the inspector, by its edge kind.
KIND_NAMES = {
    "pairing": "AZ pairing", "replication": "Replication", "manages": "Management link",
    "runs_on": "Runs on", "vcenter": "vCenter link", "uplink": "Uplink",
    "uses": "Traffic", "routed": "Routed link", "nat": "NAT link",
    "inbound": "Inbound rule", "outbound": "Outbound rule", "isolation": "Isolation",
    "array": "Storage link", "user": "Connector",
}

Point = tuple[float, float]


@dataclass
class Extra:
    """The connector edits of a model (payload ``connectors`` and ``edge_edits``)."""

    connectors: list[dict[str, Any]] = field(default_factory=list)
    edits: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.connectors or self.edits)


def editable(e: Edge, nodes: dict[str, Node]) -> bool:
    """Whether `e` is a connector the author may change (both ends drawn, not structure,
    not a legend sample)."""
    return bool(e.src and e.dst and e.src in nodes and e.dst in nodes
                and e.src != e.dst and e.data.get("kind") not in STRUCTURAL
                and not e.id.startswith("legend:"))


def end_box(nodes: dict[str, Node], ident: str, elements: Iterable[str] = ()) -> bool:
    """Whether scene node `ident` is a box a connector may end on besides the model's
    elements: a box straight inside a zone, named after it (a zone's Prism Central)."""
    n = nodes.get(ident)
    if n is None or n.kind != "box" or n.parent is None:
        return False
    parent = nodes.get(n.parent)
    return parent is not None and parent.kind == "container" and \
        ident.startswith(n.parent + ":")


# ─── geometry ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    @staticmethod
    def of(n: Node | EdgeLabel) -> Box:
        return Box(n.x, n.y, n.x + n.w, n.y + n.h)

    def grown(self, d: float) -> Box:
        return Box(self.x0 - d, self.y0 - d, self.x1 + d, self.y1 + d)


def _seg_hits(a: Point, b: Point, r: Box, inset: float = 0.5) -> bool:
    """Whether the orthogonal segment a-b passes through the inside of `r`."""
    x0, x1 = sorted((a[0], b[0]))
    y0, y1 = sorted((a[1], b[1]))
    return (x0 < r.x1 - inset and x1 > r.x0 + inset
            and y0 < r.y1 - inset and y1 > r.y0 + inset)


def _simplify(pts: list[Point]) -> list[Point]:
    """Drop repeated points and merge runs that carry on in the same direction (a run
    that turns back is kept as two, so a stub is never swallowed)."""
    out: list[Point] = []
    for p in pts:
        p = (q(p[0]), q(p[1]))
        if out and p == out[-1]:
            continue
        if len(out) >= 2:
            a, b = out[-2], out[-1]
            same_h = a[1] == b[1] == p[1] and (b[0] - a[0]) * (p[0] - b[0]) > 0
            same_v = a[0] == b[0] == p[0] and (b[1] - a[1]) * (p[1] - b[1]) > 0
            if same_h or same_v:
                out[-1] = p
                continue
        out.append(p)
    return out


def port(n: Node, side: str, frac: float = 0.5) -> Point:
    if side == "top":
        return (q(n.x + n.w * frac), q(n.y))
    if side == "bottom":
        return (q(n.x + n.w * frac), q(n.y + n.h))
    if side == "left":
        return (q(n.x), q(n.y + n.h * frac))
    return (q(n.x + n.w), q(n.y + n.h * frac))


#: Port positions tried along a side, in order.
_FRACS = (0.5, 0.35, 0.65, 0.2, 0.8, 0.1, 0.9)


def free_frac(n: Node, side: str, others: Iterable[Edge]) -> float | None:
    """A port position along `side` of `n` at least `PORT_GAP` from where another
    connector already ends on that side (None: no room on that side)."""
    taken = []
    for o in others:
        if not o.points:
            continue
        for end, p in ((o.src, o.points[0]), (o.dst, o.points[-1])):
            if end == n.id and side_of(n, p) == side:
                taken.append(p[0] if side in ("top", "bottom") else p[1])
    lo, span = (n.x, n.w) if side in ("top", "bottom") else (n.y, n.h)
    for f in _FRACS:
        pos = lo + span * f
        if span * min(f, 1 - f) < 3:
            continue
        if all(abs(pos - t) >= PORT_GAP for t in taken):
            return f
    return None


def _stub(p: Point, side: str, length: float = STUB) -> Point:
    dx, dy = _NORMAL[side]
    return (q(p[0] + dx * length), q(p[1] + dy * length))


def _segments(pts: list[Point]) -> list[tuple[Point, Point]]:
    return list(zip(pts, pts[1:], strict=False))


def _h(s: tuple[Point, Point]) -> bool:
    return s[0][1] == s[1][1] and s[0][0] != s[1][0]


def _v(s: tuple[Point, Point]) -> bool:
    return s[0][0] == s[1][0] and s[0][1] != s[1][1]


def _cross(h: tuple[Point, Point], v: tuple[Point, Point]) -> Point | None:
    (hx0, hy), (hx1, _) = h
    (vx, vy0), (_, vy1) = v
    lo, hi = sorted((hx0, hx1))
    vlo, vhi = sorted((vy0, vy1))
    if lo + HOP_W < vx < hi - HOP_W and vlo + 0.5 < hy < vhi - 0.5:
        return (vx, hy)
    return None


def _overlap(s: tuple[Point, Point], t: tuple[Point, Point]) -> bool:
    """Collinear runs sharing more than a point (two connectors drawn on one line)."""
    if _h(s) and _h(t) and s[0][1] == t[0][1]:
        a0, a1 = sorted((s[0][0], s[1][0]))
        b0, b1 = sorted((t[0][0], t[1][0]))
        return min(a1, b1) - max(a0, b0) > 1.0
    if _v(s) and _v(t) and s[0][0] == t[0][0]:
        a0, a1 = sorted((s[0][1], s[1][1]))
        b0, b1 = sorted((t[0][1], t[1][1]))
        return min(a1, b1) - max(a0, b0) > 1.0
    return False


# ─── the router ────────────────────────────────────────────────────────


@dataclass
class Field:
    """What one scene offers a route: its boxes, edges and labels."""

    sc: Scene
    nodes: dict[str, Node]
    parents: dict[str, str | None]
    containers: list[Node]

    @staticmethod
    def of(sc: Scene) -> Field:
        nodes = {n.id: n for n in sc.nodes}
        return Field(sc, nodes, {n.id: n.parent for n in sc.nodes},
                     [n for n in sc.nodes if n.kind == "container"])

    def ancestors(self, ident: str) -> set[str]:
        out: set[str] = set()
        cur = self.parents.get(ident)
        while cur is not None and cur not in out:
            out.add(cur)
            cur = self.parents.get(cur)
        return out

    def obstacles(self, a: Node, b: Node, others: Iterable[Edge] = (),
                  clear: float = CLEAR) -> tuple[list[Box], list[Box]]:
        """(what a route between `a` and `b` keeps clear of, the two ends): every solid
        node that is neither an end nor holds one (legends too), the heading of each
        zone holding an end, and a zone around each other connector's end."""
        holders = self.ancestors(a.id) | self.ancestors(b.id)
        skip = {a.id, b.id} | holders
        out = [Box.of(n).grown(clear) for n in self.sc.nodes
               if n.kind in _SOLID and n.id not in skip]
        for n in self.sc.nodes:
            if n.id in holders:
                out += [Box(*r) for r in heading_boxes(n)]
        for o in others:
            for p in (o.points[:1] + o.points[-1:]) if o.points else []:
                out.append(Box(p[0] - END_ZONE, p[1] - END_ZONE, p[0] + END_ZONE,
                               p[1] + END_ZONE))
        return out, [Box.of(a), Box.of(b)]


@dataclass(frozen=True)
class Route:
    points: list[Point]
    a_side: str
    b_side: str
    score: tuple[float, ...]


#: Search costs: per bend, per crossing, per point run along a zone border.
BEND_COST, CROSS_COST, HUG_COST = 24.0, 60.0, 6.0


def _lines(lo: float, hi: float, cuts: Iterable[float], fixed: Iterable[float]
           ) -> list[float]:
    """Grid lines along one axis inside [lo, hi]: the fixed ones (stub ends), and for the
    obstacle edges `cuts` a line just outside each and one midway between neighbours."""
    edges = sorted({q(c) for c in cuts if lo <= c <= hi} | {q(lo), q(hi)})
    vals = {q(v) for v in fixed if lo <= v <= hi}
    for c in edges:
        for v in (c - 1.0, c + 1.0):
            if lo <= v <= hi:
                vals.add(q(v))
    for c0, c1 in zip(edges, edges[1:], strict=False):
        vals.add(q((c0 + c1) / 2))
    out: list[float] = []
    for v in sorted(vals):
        if not out or v - out[-1] >= 0.75:
            out.append(v)
    return out


def _open(lines: list[float], spans: list[tuple[float, float]]) -> list[bool]:
    """For consecutive points of `lines`, whether the run between them misses every
    blocked interval in `spans` (open intervals)."""
    out = [True] * max(0, len(lines) - 1)
    for s0, s1 in spans:
        i = bisect.bisect_right(lines, s0)  # first line > s0
        j = bisect.bisect_left(lines, s1)   # first line >= s1
        for k in range(max(0, i - 1), min(len(out), j)):
            if lines[k] < s1 and lines[k + 1] > s0:
                out[k] = False
    return out


def _grid_search(f: Field, starts: list[tuple[str, Point, Point]],
                 goals: list[tuple[str, Point, Point]], blocks: list[Box],
                 hard: list[Box], others: list[Edge], region: Box
                 ) -> tuple[list[Point], str, str, float] | None:
    """Cheapest orthogonal path on a grid of lines between the stub ends: no point or
    run inside `blocks` (grown boxes, headings, labels, other ends) or `hard` (the two
    ends), no run along another connector; cost = length + bends + crossings + runs
    along a zone border."""
    inside = [r for r in blocks if r.x1 > region.x0 and r.x0 < region.x1 and
              r.y1 > region.y0 and r.y0 < region.y1]
    solid = inside + hard
    xs = _lines(region.x0, region.x1, [v for r in solid for v in (r.x0, r.x1)],
                [p[2][0] for p in starts] + [p[2][0] for p in goals])
    ys = _lines(region.y0, region.y1, [v for r in solid for v in (r.y0, r.y1)],
                [p[2][1] for p in starts] + [p[2][1] for p in goals])
    nx, ny = len(xs), len(ys)
    hsegs = [(min(a[0], b[0]), max(a[0], b[0]), a[1]) for o in others
             for a, b in _segments(o.points) if _h((a, b))]
    vsegs = [(min(a[1], b[1]), max(a[1], b[1]), a[0]) for o in others
             for a, b in _segments(o.points) if _v((a, b))]
    # Per horizontal line: open runs, crossings, border runs; likewise per vertical line.
    h_open, h_cross, h_hug, blocked = [], [], [], []
    for y in ys:
        spans = [(r.x0, r.x1) for r in solid if r.y0 < y < r.y1]
        row = [False] * nx
        for s0, s1 in spans:
            for i in range(bisect.bisect_right(xs, s0), bisect.bisect_left(xs, s1)):
                row[i] = True
        blocked.append(row)
        spans += [(x0, x1) for x0, x1, yy in hsegs if abs(yy - y) < 0.5]
        h_open.append(_open(xs, spans))
        h_cross.append(sorted(x for y0, y1, x in vsegs if y0 + 0.5 < y < y1 - 0.5))
        h_hug.append([(c.x, c.x + c.w) for c in f.containers
                      if min(abs(y - c.y), abs(y - c.y - c.h)) < 2.5])
    v_open, v_cross, v_hug = [], [], []
    for x in xs:
        spans = [(r.y0, r.y1) for r in solid if r.x0 < x < r.x1]
        spans += [(y0, y1) for y0, y1, xx in vsegs if abs(xx - x) < 0.5]
        v_open.append(_open(ys, spans))
        v_cross.append(sorted(y for x0, x1, y in hsegs if x0 + 0.5 < x < x1 - 0.5))
        v_hug.append([(c.y, c.y + c.h) for c in f.containers
                      if min(abs(x - c.x), abs(x - c.x - c.w)) < 2.5])

    def free(i: int, j: int) -> bool:
        return not blocked[j][i]

    def hug(spans: list[tuple[float, float]], lo: float, hi: float) -> float:
        return sum(max(0.0, min(hi, s1) - max(lo, s0)) for s0, s1 in spans)

    xi = {x: i for i, x in enumerate(xs)}
    yi = {y: j for j, y in enumerate(ys)}
    dirs = ((1, 0), (-1, 0), (0, 1), (0, -1))
    normal = {"right": 0, "left": 1, "bottom": 2, "top": 3}
    heap: list[tuple[float, int, int, int, int]] = []
    best: dict[tuple[int, int, int], float] = {}
    prev: dict[tuple[int, int, int], tuple[int, int, int] | None] = {}
    origin: dict[tuple[int, int, int], str] = {}
    for side, _p, qa in starts:
        si, sj = xi.get(qa[0]), yi.get(qa[1])
        if si is None or sj is None or not free(si, sj):
            continue
        st0 = (si, sj, normal[side])
        best[st0] = 0.0
        prev[st0] = None
        origin[st0] = side
        heapq.heappush(heap, (0.0, si, sj, normal[side], len(heap)))
    goal_at = {}
    for side, _p, qb in goals:
        gi, gj = xi.get(qb[0]), yi.get(qb[1])
        if gi is not None and gj is not None and free(gi, gj):
            goal_at[(gi, gj)] = (side, (normal[side] ^ 1))  # arriving runs toward the box
    found: tuple[float, tuple[int, int, int], str] | None = None
    while heap:
        cost, i, j, d, _ = heapq.heappop(heap)
        st = (i, j, d)
        if cost > best.get(st, float("inf")) + 1e-9:
            continue
        if found is not None and cost >= found[0]:
            break
        if (i, j) in goal_at and d != (goal_at[(i, j)][1] ^ 1):  # never turn back in
            side, want = goal_at[(i, j)]
            total = cost + (0.0 if d == want else BEND_COST)
            if found is None or total < found[0]:
                found = (total, st, side)
        for nd, (dx, dy) in enumerate(dirs):
            if nd == (d ^ 1):
                continue  # never straight back
            ni, nj = i + dx, j + dy
            if not (0 <= ni < nx and 0 <= nj < ny):
                continue
            if dx:
                k = min(i, ni)
                if not h_open[j][k]:
                    continue
                lo, hi = xs[k], xs[k + 1]
                step = hi - lo
                n_cross = bisect.bisect_left(h_cross[j], hi) - bisect.bisect_right(h_cross[j], lo)
                step += HUG_COST * hug(h_hug[j], lo, hi)
            else:
                k = min(j, nj)
                if not v_open[i][k]:
                    continue
                lo, hi = ys[k], ys[k + 1]
                step = hi - lo
                n_cross = bisect.bisect_left(v_cross[i], hi) - bisect.bisect_right(v_cross[i], lo)
                step += HUG_COST * hug(v_hug[i], lo, hi)
            if not free(ni, nj):
                continue
            nc = cost + step + CROSS_COST * n_cross + (BEND_COST if nd != d else 0.0)
            ns = (ni, nj, nd)
            if nc < best.get(ns, float("inf")) - 1e-9:
                best[ns] = nc
                prev[ns] = st
                origin[ns] = origin[st]
                heapq.heappush(heap, (nc, ni, nj, nd, len(heap)))
    if found is None:
        return None
    total, st, b_side = found
    cells: list[Point] = []
    cur: tuple[int, int, int] | None = st
    while cur is not None:
        cells.append((xs[cur[0]], ys[cur[1]]))
        cur = prev[cur]
    cells.reverse()
    return cells, origin[st], b_side, total


def route(f: Field, a: Node, b: Node, a_side: str = "auto", b_side: str = "auto",
          others: list[Edge] | None = None, straight: bool = True) -> Route | None:
    """The cheapest clean orthogonal route from `a` to `b` (see the module notes); None
    when there is none (no room for a port, or every way is blocked). A crowded figure
    is tried again without the zones kept clear around other connectors' ends, then with
    a closer clearance to boxes (never through one)."""
    others = [o for o in (others or []) if o.points]
    labels = [Box.of(lb) for o in others for lb in o.labels]
    strict = f.obstacles(a, b, others)[0] + labels
    loose = f.obstacles(a, b, ())[0] + labels
    tight = f.obstacles(a, b, (), clear=0.75)[0] + labels
    for boxes in (strict, loose, tight):
        got = _route(f, a, b, a_side, b_side, others, boxes, straight)
        if got is not None:
            return got
    return None


def _route(f: Field, a: Node, b: Node, a_side: str, b_side: str, others: list[Edge],
           boxes: list[Box], straight: bool = True) -> Route | None:
    hard = [Box.of(a), Box.of(b)]
    w, h = f.sc.width, f.sc.height

    def gap(n: Node, other: Node, side: str) -> float:
        """How far `other` lies beyond `side` of `n` (inf: not that way)."""
        if side == "right":
            d = other.x - (n.x + n.w)
        elif side == "left":
            d = n.x - (other.x + other.w)
        elif side == "bottom":
            d = other.y - (n.y + n.h)
        else:
            d = n.y - (other.y + other.h)
        return d if d >= 0 else float("inf")

    def stubs(n: Node, wanted: str) -> list[tuple[str, Point, Point]]:
        out = []
        other = b if n is a else a
        for side in (SIDES if wanted == "auto" else (wanted,)):
            room = gap(n, other, side)
            if not straight and room < 30 and wanted == "auto":
                continue  # the long way round: not across the narrow gap between them
            frac = free_frac(n, side, others)
            if frac is None:
                continue
            p = port(n, side, frac)
            # The longest stub that ends in free space: cards packed close leave a
            # narrow corridor, and a side at the canvas edge leaves little room.
            for length in (STUB, 6.0, 4.5, 3.0):
                if length > room / 2 - 0.5:
                    continue  # stubs facing across a narrow gap would cross
                s = _stub(p, side, length)
                if not (2 <= s[0] <= w - 2 and 2 <= s[1] <= h - 2):
                    continue
                if any(_seg_hits(p, s, r, 0.0) or (r.x0 < s[0] < r.x1 and r.y0 < s[1] < r.y1)
                       for r in boxes):
                    continue  # a chip or a neighbour right outside this side
                if any(r.x0 - 1 <= s[0] <= r.x1 + 1 and r.y0 - 1 <= s[1] <= r.y1 + 1
                       for r in hard):
                    continue  # at the other end already: the straight case covers it
                if any((side in ("left", "right") and min(abs(s[0] - c.x), abs(
                        s[0] - c.x - c.w)) < 2.5 and c.y <= s[1] <= c.y + c.h) or
                       (side in ("top", "bottom") and min(abs(s[1] - c.y), abs(
                           s[1] - c.y - c.h)) < 2.5 and c.x <= s[0] <= c.x + c.w)
                       for c in f.containers):
                    continue  # it would run on a zone border from here
                out.append((side, p, s))
                break
        return out

    facing = _facing(f, a, b, a_side, b_side, others, boxes) if straight else None
    if facing is not None:
        return facing
    starts, goals = stubs(a, a_side), stubs(b, b_side)
    if not starts or not goals:
        return None
    x0 = max(2.0, min(a.x, b.x) - 90)
    x1 = min(w - 2.0, max(a.x + a.w, b.x + b.w) + 90)
    y0 = max(2.0, min(a.y, b.y) - 90)
    y1 = min(h - 2.0, max(a.y + a.h, b.y + b.h) + 90)
    regions = [Box(x0, y0, x1, y1), Box(2.0, 2.0, w - 2.0, h - 2.0)]
    if regions[0] == regions[1]:
        regions.pop()
    for region in regions:
        got = _grid_search(f, starts, goals, boxes, hard, others, region)
        if got is None:
            continue
        cells, sa, sb, total = got
        pa = next(p for s, p, _ in starts if s == sa)
        pb = next(p for s, p, _ in goals if s == sb)
        pts = _simplify([pa, *cells, pb])
        return Route(pts, sa, sb, (q(total),))
    return None


def _facing(f: Field, a: Node, b: Node, a_side: str, b_side: str, others: list[Edge],
            boxes: list[Box]) -> Route | None:
    """A straight run between two sides that face each other (cards side by side or
    one above the other) when one fits: no stubs, no bends."""
    pairs = (("right", "left"), ("left", "right"), ("bottom", "top"), ("top", "bottom"))
    for sa, sb in pairs:
        if a_side not in ("auto", sa) or b_side not in ("auto", sb):
            continue
        if sa in ("right", "left"):
            gap_ok = (b.x - (a.x + a.w) >= 4) if sa == "right" else (a.x - (b.x + b.w) >= 4)
            lo, hi = max(a.y, b.y) + 3, min(a.y + a.h, b.y + b.h) - 3
        else:
            gap_ok = (b.y - (a.y + a.h) >= 4) if sa == "bottom" else (a.y - (b.y + b.h) >= 4)
            lo, hi = max(a.x, b.x) + 3, min(a.x + a.w, b.x + b.w) - 3
        if not gap_ok or hi < lo:
            continue
        mid = (lo + hi) / 2
        for k in (0.0, -0.3, 0.3, -0.15, 0.15, -0.45, 0.45):
            v = q(mid + (hi - lo) * k)
            if sa in ("right", "left"):
                pa, pb = (q(a.x + (a.w if sa == "right" else 0)), v), \
                    (q(b.x + (b.w if sb == "right" else 0)), v)
            else:
                pa, pb = (v, q(a.y + (a.h if sa == "bottom" else 0))), \
                    (v, q(b.y + (b.h if sb == "bottom" else 0)))
            if any(_seg_hits(pa, pb, r) for r in boxes):
                continue
            if not _port_free(a, sa, pa, others) or not _port_free(b, sb, pb, others):
                continue
            if any(_overlap((pa, pb), t) for o in others for t in _segments(o.points)):
                continue
            return Route([pa, pb], sa, sb, (q(abs(pa[0] - pb[0]) + abs(pa[1] - pb[1])),))
    return None


def _port_free(n: Node, side: str, p: Point, others: Iterable[Edge]) -> bool:
    for o in others:
        if not o.points:
            continue
        for end, op in ((o.src, o.points[0]), (o.dst, o.points[-1])):
            if end == n.id and side_of(n, op) == side:
                d = abs(p[0] - op[0]) if side in ("top", "bottom") else abs(p[1] - op[1])
                if d < PORT_GAP:
                    return False
    return True


def _shiftable(pts: list[Point]) -> int | None:
    """The longest run that touches neither stub (index of its first point)."""
    best, best_len = None, -1.0
    for i in range(2, len(pts) - 3):
        s = (pts[i], pts[i + 1])
        ln = abs(s[0][0] - s[1][0]) + abs(s[0][1] - s[1][1])
        if ln > best_len:
            best, best_len = i, ln
    return best


def _bent(pts: list[Point], bend: float, a: Node, b: Node) -> list[Point] | None:
    """`pts` with its middle run offset by `bend` (None: not possible)."""
    pts = list(pts)
    if len(pts) == 2:  # a straight run slides along both sides
        (x0, y0), (x1, y1) = pts
        if y0 == y1:
            lo, hi = max(a.y, b.y) + 2, min(a.y + a.h, b.y + b.h) - 2
            y = y0 + bend
            if not lo <= y <= hi:
                return None
            return [(x0, q(y)), (x1, q(y))]
        lo, hi = max(a.x, b.x) + 2, min(a.x + a.w, b.x + b.w) - 2
        x = x0 + bend
        if not lo <= x <= hi:
            return None
        return [(q(x), y0), (q(x), y1)]
    i = _shiftable(pts)
    if i is not None:
        p, r = pts[i], pts[i + 1]
        if p[1] == r[1]:
            pts[i], pts[i + 1] = (p[0], q(p[1] + bend)), (r[0], q(r[1] + bend))
        else:
            pts[i], pts[i + 1] = (q(p[0] + bend), p[1]), (q(r[0] + bend), r[1])
        return _simplify(pts)
    if len(pts) == 5:  # an L: give it a jog at the corner
        pa, qa, c, qb, pb = pts
        if qa[1] == c[1]:
            mx = q(c[0] + bend)
            return _simplify([pa, qa, (mx, qa[1]), (mx, qb[1]), qb, pb])
        my = q(c[1] + bend)
        return _simplify([pa, qa, (qa[0], my), (qb[0], my), qb, pb])
    return None


def _bends(bend: float) -> list[float]:
    """Lane offsets to try, from `bend` back toward zero (at most 16)."""
    if not bend:
        return []
    step = max(3.0, abs(bend) / 15)
    out, cur = [], abs(bend)
    while cur > 1e-9:
        out.append(q(cur if bend > 0 else -cur))
        cur = round(cur - step, 2)
    return out


def _clean(sc: Scene, e: Edge, nodes: dict[str, Node]) -> bool:
    return not route_problems(sc, e, nodes)


def place_bend(sc: Scene, e: Edge, pts: list[Point], bend: float, a: Node, b: Node,
               nodes: dict[str, Node]) -> tuple[list[Point], float]:
    """(points, the bend applied): the first lane offset from `bend` back toward zero
    whose route is clean; `pts` and 0 when none is."""
    for cur in _bends(bend):
        bent = _bent(pts, cur, a, b)
        if bent is None:
            continue
        e.points = bent
        if _clean(sc, e, nodes):
            return bent, cur
    e.points = pts
    return pts, 0.0


def _hop(e: Edge, marks: list[Point]) -> None:
    """A hop at each of `marks` on the horizontal runs of `e`."""
    pts: list[Point] = [e.points[0]]
    for s0, s1 in _segments(e.points):
        if _h((s0, s1)):
            y = s0[1]
            sign = 1.0 if s1[0] > s0[0] else -1.0
            xs = sorted({p[0] for p in marks if p[1] == y and
                         min(s0[0], s1[0]) + HOP_W < p[0] < max(s0[0], s1[0]) - HOP_W},
                        reverse=sign < 0)
            for x in xs:
                pts += [(q(x - sign * HOP_W), y), (q(x - sign * HOP_W * 0.5), q(y - HOP_H)),
                        (q(x + sign * HOP_W * 0.5), q(y - HOP_H)), (q(x + sign * HOP_W), y)]
        pts.append(s1)
    e.points = pts


def hop_crossings(sc: Scene, routed: set[str]) -> None:
    """Each crossing of a routed connector with another line gets a hop on whichever of
    the two runs is horizontal (after every route is placed)."""
    marks: dict[str, list[Point]] = {}
    edges = sc.edges
    for e in edges:
        if e.id not in routed:
            continue
        for o in edges:
            if o.id == e.id or (o.id in routed and o.id < e.id):
                continue
            for s in _segments(e.points):
                for t in _segments(o.points):
                    if _h(s) and _v(t) and (p := _cross(s, t)) is not None:
                        marks.setdefault(e.id, []).append(p)
                    elif _v(s) and _h(t) and (p := _cross(t, s)) is not None:
                        marks.setdefault(o.id, []).append(p)
    for e in edges:
        if e.id in marks:
            _hop(e, marks[e.id])


# ─── labels ────────────────────────────────────────────────────────────

#: Where along a run a label is tried, in order.
_ALONG = (0.5, 0.35, 0.65, 0.2, 0.8, 0.1, 0.9)


def _spots(pts: list[Point], w: float, h: float) -> list[Point]:
    """Top-left corners to try for a w x h label: along each run (longest first),
    centred on it, then beside it (left and right of a vertical run, above and below a
    horizontal one), always within reach of the line for `check_scene`."""
    segs = sorted(enumerate(_segments(pts)), key=lambda iv: (
        -(abs(iv[1][0][0] - iv[1][1][0]) + abs(iv[1][0][1] - iv[1][1][1])), iv[0]))
    out: list[Point] = []
    for _, (p, r) in segs:
        if p[0] != r[0] and p[1] != r[1]:
            continue  # a hop
        for t in _ALONG:
            cx, cy = p[0] + (r[0] - p[0]) * t, p[1] + (r[1] - p[1]) * t
            out.append((cx - w / 2, cy - h / 2))
            for gap in (1.5, 3.5):
                if p[0] == r[0]:
                    out += [(cx - w - gap, cy - h / 2), (cx + gap, cy - h / 2)]
                else:
                    out += [(cx - w / 2, cy - h - gap), (cx - w / 2, cy + gap)]
    return out


def place_labels(sc: Scene, e: Edge, nodes: dict[str, Node]) -> bool:
    """Put every label of `e` on the first clean spot (`check.label_problems`); False
    (labels removed) when one has none."""
    labels, e.labels = list(e.labels), []
    for lab in labels:
        e.labels.append(lab)
        for sx, sy in _spots(e.points, lab.w, lab.h):
            lab.x = q(min(max(sx, 1.0), sc.width - lab.w - 1.0))
            lab.y = q(min(max(sy, 1.0), sc.height - lab.h - 1.0))
            if not label_problems(sc, e, nodes) and not _on_own_ends(lab, e, nodes):
                break
        else:
            e.labels = []
            return False
    return True


def _on_own_ends(lab: EdgeLabel, e: Edge, nodes: dict[str, Node]) -> bool:
    """Whether the label covers text of its own ends or of anything inside them (it may
    overlap an end's empty margin, where its line starts)."""
    r = Box.of(lab)
    ends = {e.src, e.dst}
    for n in nodes.values():
        if n.id in ends or n.parent in ends:
            if n.id not in ends and _overlaps(r, Box.of(n)):
                return True  # a chip, strip or pill on the end
            for t in n.lines:
                tb = Box(n.x + t.x - 1, n.y + t.baseline - t.size, n.x + t.x + t.width + 1,
                         n.y + t.baseline + t.size * 0.3)
                if _overlaps(r, tb):
                    return True
    return False


def _overlaps(r: Box, o: Box) -> bool:
    return r.x0 < o.x1 and o.x0 < r.x1 and r.y0 < o.y1 and o.y0 < r.y1


def _label_text(e: Edge) -> str:
    return " ".join(t.text for lab in e.labels for t in lab.lines)


def _label_color(e: Edge, default: str) -> str:
    for lab in e.labels:
        for t in lab.lines:
            return t.color
    return default


def _longer_with_label(sc: Scene, f: Field, j: _Job, e: Edge, labels: list[EdgeLabel],
                       nodes: dict[str, Node]) -> bool:
    """A short straight connector whose label has no room: route it the long way round
    instead (with room for the label), when that route is clean."""
    keep = list(e.points)
    e.points = []
    r = route(f, j.a, j.b, j.a_side, j.b_side, [o for o in sc.edges if o.id != e.id],
              straight=False)
    if r is not None:
        e.points = r.points
        e.labels = list(labels)
        if _clean(sc, e, nodes) and not crossing_problems(sc, e) and \
                place_labels(sc, e, nodes):
            return True
    e.points, e.labels = keep, []
    return False


# ─── the overlay ───────────────────────────────────────────────────────


def overlay(scenes: list[Scene], extra: Extra) -> None:
    """Apply the connector edits of a model to its laid-out `scenes` (in place)."""
    if not extra:
        return
    for sc in scenes:
        _overlay(sc, extra)


@dataclass
class _Job:
    edge: Edge
    a: Node
    b: Node
    a_side: str
    b_side: str
    bend: float
    reroute: bool
    #: Reconnected or user-added: its old geometry is no fallback.
    new_ends: bool
    label: str | None  # None: keep the labels it has
    keep_labels: bool
    color: str
    orig_points: list[Point] = field(default_factory=list)
    orig_labels: list[EdgeLabel] = field(default_factory=list)


def _overlay(sc: Scene, extra: Extra) -> None:
    f = Field.of(sc)
    nodes = f.nodes
    info: dict[str, dict[str, Any]] = {}
    jobs: list[_Job] = []
    kept: list[Edge] = []
    for e in sc.edges:
        ed = extra.edits.get(e.id)
        if not ed or not editable(e, nodes):
            kept.append(e)
            continue
        note: dict[str, Any] = {"collected_label": _label_text(e), "collected_src": e.src,
                                "collected_dst": e.dst}
        if ed.get("hidden"):
            info[e.id] = {**note, "hidden": True}
            continue
        src, dst = ed.get("a") or e.src, ed.get("b") or e.dst
        if src not in nodes or dst not in nodes or src == dst:
            info[e.id] = {**note, "elsewhere": True}
            continue
        if "arrow" in ed:
            e.arrow_start = ed["arrow"] in ("start", "both")
            e.arrow_end = ed["arrow"] in ("end", "both")
        reroute = any(k in ed for k in ("a", "b", "a_side", "b_side"))
        e.src, e.dst = src, dst
        e.data = dict(sorted({**e.data, "edited": "true"}.items()))
        info[e.id] = note
        kept.append(e)
        jobs.append(_Job(e, nodes[src], nodes[dst], ed.get("a_side", "auto"),
                         ed.get("b_side", "auto"), float(ed.get("bend", 0) or 0), reroute,
                         "a" in ed or "b" in ed, ed.get("label"), "label" not in ed,
                         _label_color(e, K.IRIS), list(e.points), list(e.labels)))
    for c in extra.connectors:
        a, b = nodes.get(str(c.get("a"))), nodes.get(str(c.get("b")))
        if a is None or b is None or a.id == b.id:
            continue
        arrow = str(c.get("arrow") or "end")
        e = Edge(id=str(c["id"]), src=a.id, dst=b.id, points=[], style=USER_LINK,
                 arrow_start=arrow in ("start", "both"), arrow_end=arrow in ("end", "both"),
                 data={"kind": "user", "origin": "user"})
        kept.append(e)
        info[e.id] = {}
        jobs.append(_Job(e, a, b, str(c.get("a_side") or "auto"),
                         str(c.get("b_side") or "auto"), float(c.get("bend") or 0), True,
                         True, c.get("label") or "", False, K.AMBER))
    sc.edges = kept
    moving = {j.edge.id for j in jobs if j.reroute or j.bend}
    for j in jobs:
        if j.edge.id in moving:
            j.edge.points = []  # placed in turn below; never routed around its old self
    routed: set[str] = set()
    dropped: set[str] = set()
    for j in jobs:
        e = j.edge
        note = info.setdefault(e.id, {})
        if e.id not in moving:
            continue
        ok = False
        if j.reroute:
            tries = [(j.a_side, j.b_side)]
            if (j.a_side, j.b_side) != ("auto", "auto"):
                tries.append(("auto", "auto"))
            for k, (sa, sb) in enumerate(tries):
                r = route(f, j.a, j.b, sa, sb, [o for o in sc.edges if o.id != e.id])
                if r is None:
                    continue
                e.points = r.points
                if not _clean(sc, e, nodes):
                    e.points = []
                    continue
                pts, applied = place_bend(sc, e, r.points, j.bend, j.a, j.b, nodes)
                e.points = pts
                note.update(bend=applied)
                if k:
                    note["issue"] = "side"
                ok = True
                break
        else:
            e.points = list(j.orig_points)
            pts, applied = place_bend(sc, e, e.points, j.bend, j.a, j.b, nodes)
            e.points = pts
            note.update(bend=applied)
            ok = True
        if ok:
            if j.reroute or note.get("bend"):
                routed.add(e.id)
            continue
        if j.new_ends:
            note["issue"] = "route"
            dropped.add(e.id)
        else:  # a pinned side with no clean route: the generated route stays
            e.points, e.labels = list(j.orig_points), list(j.orig_labels)
            note.update(issue="side", bend=0)
    sc.edges = [e for e in sc.edges if e.id not in dropped]
    hop_crossings(sc, routed)
    for j in jobs:
        e = j.edge
        if e.id in dropped:
            continue
        note = info[e.id]
        if not j.keep_labels:
            e.labels = [edge_label([j.label], 0, 0, LABEL_W, color=j.color)] \
                if j.label else []
        if e.labels and (not j.keep_labels or e.id in routed):
            labels = list(e.labels)
            placed = place_labels(sc, e, nodes)
            if not placed and j.reroute:
                placed = _longer_with_label(sc, f, j, e, labels, nodes)
            if placed:
                note["labeled"] = True
            else:
                note["issue"] = note.get("issue") or "label"
        if e.id in routed:
            note["routed"] = True
        if e.points:
            note["a_side_used"] = side_of(nodes[e.src], e.points[0]) or "auto"
            note["b_side_used"] = side_of(nodes[e.dst], e.points[-1]) or "auto"
    # A last safety net: whatever still breaks a rule is not drawn (and says so).
    for e in list(sc.edges):
        note = info.get(e.id) or {}
        if note.get("routed") and (route_problems(sc, e, nodes)
                                   or crossing_problems(sc, e)):
            sc.edges.remove(e)
            note.pop("routed", None)
            note.pop("labeled", None)
            note["issue"] = "route"
    if info:
        sc.meta = {**sc.meta, "connectors": dict(sorted(info.items()))}


__all__ = ["KIND_NAMES", "USER_LINK", "Extra", "editable", "end_box", "free_frac",
           "overlay", "route"]
