"""Connector edits drawn on laid-out scenes: user-added connectors and changes to the
generated ones (Diagrams editor; ops ``connect`` and ``edge`` in `edits.py`).

Every diagram type lays its model out first; this overlay then runs on the scenes, so a
connector edit reaches the editor, the Word and PDF figures and the editable assets
alike, with no per-type code (`DiagramType` wires it in). Nothing here is free-placed:
an edit names ends, sides, a lane offset, arrowheads and a label, and the router below
turns them into an orthogonal route.

Router (deterministic: the 0.01 pt grid, a fixed candidate order, a total score order):

* ports at the centre of a side, spread along it when several routed connectors share it;
* candidates per side pair (``auto`` tries all four sides of that end): straight, the
  two L shapes and Z shapes through mid lines (the midpoint, gutters beside each box
  nearby, just outside both ends), each leaving and entering through a short stub;
* score, in order: points off the canvas, boxes crossed (a container counts unless it
  holds an end), runs through an end, crossings and shared runs with other connectors,
  labels hit, bends, length;
* ``bend`` shifts the middle run (a straight run slides along both sides, within their
  overlap); a shift that would hit a box is pulled back toward zero until it is clean;
* a crossing left over gets the usual hop, on the connector's own horizontal runs;
* the label sits on the longest clear run, on its edge and inside the canvas.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from . import tokens as K
from .build import edge_label
from .route import HOP_H, HOP_W
from .scene import Edge, EdgeLabel, Node, Scene, Style, q

#: A user-added connector: dashed amber, like every other user-added item.
USER_LINK = Style(stroke=K.AMBER, stroke_w=0.9, dash=(2.4, 1.8))
#: Edges that are structure, not connectors (a bus trunk, a pairing glyph).
STRUCTURAL = frozenset({"trunk", "glyph"})
#: Stub length out of a side, gutter beside a box, step when pulling a bend back.
STUB, GUTTER, BEND_STEP = 8.0, 6.0, 3.0
#: Clearance a route keeps from boxes that are not its ends.
CLEAR = 2.5
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
    out: list[Point] = []
    for p in pts:
        p = (q(p[0]), q(p[1]))
        if out and p == out[-1]:
            continue
        if len(out) >= 2:
            a, b = out[-2], out[-1]
            if (a[0] == b[0] == p[0]) or (a[1] == b[1] == p[1]):
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


def _free_frac(n: Node, side: str, others: list[Edge], want: float) -> float:
    """A port position along `side` of `n` at least 10 pt from where another connector
    already ends on that side (`want` first, then the centre and either side of it)."""
    taken = []
    for o in others:
        for p in (o.points[:1] + o.points[-1:]) if o.points else []:
            on = {"top": abs(p[1] - n.y) < 0.6, "bottom": abs(p[1] - n.y - n.h) < 0.6,
                  "left": abs(p[0] - n.x) < 0.6, "right": abs(p[0] - n.x - n.w) < 0.6}[side]
            if on:
                taken.append(p[0] if side in ("top", "bottom") else p[1])
    if not taken:
        return want
    lo, span = (n.x, n.w) if side in ("top", "bottom") else (n.y, n.h)
    for f in (want, 0.5, 0.35, 0.65, 0.2, 0.8):
        if all(abs(lo + span * f - t) >= 10 for t in taken):
            return f
    return want


def _stub(p: Point, side: str) -> Point:
    dx, dy = _NORMAL[side]
    return (q(p[0] + dx * STUB), q(p[1] + dy * STUB))


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

    @staticmethod
    def of(sc: Scene) -> Field:
        nodes = {n.id: n for n in sc.nodes}
        return Field(sc, nodes, {n.id: n.parent for n in sc.nodes})

    def ancestors(self, ident: str) -> set[str]:
        out: set[str] = set()
        cur = self.parents.get(ident)
        while cur is not None and cur not in out:
            out.add(cur)
            cur = self.parents.get(cur)
        return out

    def obstacles(self, a: Node, b: Node) -> tuple[list[Box], list[Box]]:
        """(boxes to keep clear of, the two ends) for a route between `a` and `b`."""
        holders = self.ancestors(a.id) | self.ancestors(b.id)
        skip = {a.id, b.id} | holders
        out = [Box.of(n).grown(CLEAR) for n in self.sc.nodes
               if n.kind in _SOLID and n.id not in skip and not n.id.startswith("legend:")]
        # The heading text of a zone a route runs inside is kept clear too.
        for n in self.sc.nodes:
            if n.id in holders:
                out += [Box(n.x + t.x - 1, n.y + t.baseline - t.size,
                            n.x + t.x + t.width + 1, n.y + t.baseline + t.size * 0.3)
                        for t in n.lines]
        return out, [Box.of(a), Box.of(b)]


@dataclass(frozen=True)
class Route:
    points: list[Point]
    a_side: str
    b_side: str
    score: tuple[float, ...]


def _score(pts: list[Point], f: Field, boxes: list[Box], ends: list[Box],
           others: list[Edge], labels: list[Box]) -> tuple[float, ...]:
    segs = _segments(pts)
    w, h = f.sc.width, f.sc.height
    off = sum(1 for x, y in pts if x < 1 or y < 1 or x > w - 1 or y > h - 1)
    hits = sum(1 for s in segs for r in boxes if _seg_hits(s[0], s[1], r))
    inner = segs[1:-1]
    through = sum(1 for s in inner for r in ends if _seg_hits(s[0], s[1], r, 0.25))
    cross = 0
    for o in others:
        osegs = _segments(o.points)
        for s in segs:
            for t in osegs:
                if _overlap(s, t):
                    cross += 2
                elif _h(s) and _v(t) and _cross(s, t) is not None or \
                        _v(s) and _h(t) and _cross(t, s) is not None:
                    cross += 1
    lab = sum(1 for s in segs for r in labels if _seg_hits(s[0], s[1], r, 0.0))
    length = sum(abs(s[0][0] - s[1][0]) + abs(s[0][1] - s[1][1]) for s in segs)
    return (off, hits, through, cross, lab, len(segs), q(length))


def _shapes(qa: Point, qb: Point, mids_x: list[float], mids_y: list[float]
            ) -> Iterable[list[Point]]:
    if qa[0] == qb[0] or qa[1] == qb[1]:
        yield [qa, qb]
    yield [qa, (qb[0], qa[1]), qb]
    yield [qa, (qa[0], qb[1]), qb]
    for mx in mids_x:
        yield [qa, (mx, qa[1]), (mx, qb[1]), qb]
    for my in mids_y:
        yield [qa, (qa[0], my), (qb[0], my), qb]


def _mids(lo: float, hi: float, boxes: list[Box], ends: list[Box], axis: int,
          limit: float) -> list[float]:
    """Mid lines along one axis: the midpoint, gutters beside nearby boxes, just
    outside both ends; inside the canvas, sorted and unique."""
    a, b = sorted((lo, hi))
    vals = {q((lo + hi) / 2)}
    span0, span1 = a - 40, b + 40
    for r in [*boxes, *ends]:
        r0, r1 = (r.x0, r.x1) if axis == 0 else (r.y0, r.y1)
        for v in (r0 - GUTTER, r1 + GUTTER):
            if span0 <= v <= span1:
                vals.add(q(v))
    for r in ends:
        r0, r1 = (r.x0, r.x1) if axis == 0 else (r.y0, r.y1)
        vals.update((q(r0 - 2 * GUTTER), q(r1 + 2 * GUTTER)))
    return sorted(v for v in vals if 2 <= v <= limit - 2)


def route(f: Field, a: Node, b: Node, a_side: str = "auto", b_side: str = "auto",
          others: list[Edge] | None = None, a_frac: float = 0.5, b_frac: float = 0.5
          ) -> Route:
    """The best orthogonal route from `a` to `b` (see the module notes)."""
    others = others or []
    boxes, ends = f.obstacles(a, b)
    # Only what lies near the two ends can matter (keeps large scenes fast).
    x0 = min(a.x, b.x) - 60
    x1 = max(a.x + a.w, b.x + b.w) + 60
    y0 = min(a.y, b.y) - 60
    y1 = max(a.y + a.h, b.y + b.h) + 60
    near = [r for r in boxes if r.x1 > x0 and r.x0 < x1 and r.y1 > y0 and r.y0 < y1]
    labels = [Box.of(lb) for o in others for lb in o.labels]
    near_edges = [o for o in others if any(
        x0 <= px <= x1 and y0 <= py <= y1 for px, py in o.points)]
    best: Route | None = None
    for sa in (SIDES if a_side == "auto" else (a_side,)):
        for sb in (SIDES if b_side == "auto" else (b_side,)):
            pa = port(a, sa, _free_frac(a, sa, others, a_frac))
            pb = port(b, sb, _free_frac(b, sb, others, b_frac))
            qa, qb = _stub(pa, sa), _stub(pb, sb)
            mids_x = _mids(qa[0], qb[0], near, ends, 0, f.sc.width)
            mids_y = _mids(qa[1], qb[1], near, ends, 1, f.sc.height)
            for core in _shapes(qa, qb, mids_x, mids_y):
                pts = _simplify([pa, *core, pb])
                if len(pts) < 2:
                    continue
                score = _score(pts, f, near, ends, near_edges, labels)
                if best is None or score < best.score:
                    best = Route(pts, sa, sb, score)
    assert best is not None
    return best


def _shiftable(pts: list[Point]) -> int | None:
    """The longest run that touches neither stub (index of its first point)."""
    best, best_len = None, -1.0
    for i in range(1, len(pts) - 2):
        if i == 1 or i + 1 == len(pts) - 2:
            continue
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


def with_bend(f: Field, r: Route, bend: float, a: Node, b: Node, others: list[Edge]
              ) -> tuple[list[Point], float]:
    """(points, the bend applied): `bend`, pulled back toward zero while it would score
    worse on boxes than the unbent route."""
    if not bend:
        return r.points, 0.0
    boxes, ends = f.obstacles(a, b)
    base = r.score[:3]
    step = BEND_STEP if bend > 0 else -BEND_STEP
    cur = bend
    while abs(cur) > 1e-9:
        pts = _bent(r.points, cur, a, b)
        if pts is not None and _score(pts, f, boxes, ends, [], [])[:3] <= base:
            return pts, q(cur)
        nxt = cur - step
        cur = 0.0 if (nxt > 0) != (cur > 0) else nxt
    return r.points, 0.0


def hop_own(e: Edge, others: list[Edge]) -> None:
    """A hop wherever a horizontal run of `e` crosses another connector's vertical run."""
    marks: list[Point] = []
    for t in (s for o in others for s in _segments(o.points) if _v(s)):
        for s in _segments(e.points):
            if _h(s) and (p := _cross(s, t)) is not None:
                marks.append(p)
    if not marks:
        return
    pts: list[Point] = [e.points[0]]
    for s0, s1 in _segments(e.points):
        if _h((s0, s1)):
            y = s0[1]
            sign = 1.0 if s1[0] > s0[0] else -1.0
            xs = sorted({p[0] for p in marks if p[1] == y and
                         min(s0[0], s1[0]) < p[0] < max(s0[0], s1[0])}, reverse=sign < 0)
            for x in xs:
                pts += [(q(x - sign * HOP_W), y), (q(x - sign * HOP_W * 0.5), q(y - HOP_H)),
                        (q(x + sign * HOP_W * 0.5), q(y - HOP_H)), (q(x + sign * HOP_W), y)]
        pts.append(s1)
    e.points = pts


# ─── labels ────────────────────────────────────────────────────────────


#: Where along a run a label is tried, in order.
_ALONG = (0.5, 0.3, 0.7, 0.15, 0.85)


def _spots(pts: list[Point], w: float, h: float) -> list[Point]:
    """Top-left corners to try for a w x h label: along each run (longest first),
    centred on it, then, beside a vertical run, just left and right of it (still on
    its edge for `check_scene`)."""
    segs = sorted(enumerate(_segments(pts)), key=lambda iv: (
        -(abs(iv[1][0][0] - iv[1][1][0]) + abs(iv[1][0][1] - iv[1][1][1])), iv[0]))
    out: list[Point] = []
    for _, (p, r) in segs:
        for t in _ALONG:
            cx, cy = p[0] + (r[0] - p[0]) * t, p[1] + (r[1] - p[1]) * t
            out.append((cx - w / 2, cy - h / 2))
            if p[0] == r[0]:
                out += [(cx - w - 1.5, cy - h / 2), (cx + 1.5, cy - h / 2)]
    return out


def _place(lab: EdgeLabel, e: Edge, f: Field, taken: list[Box]) -> None:
    """Put `lab` on the clearest spot of `e` (`_spots`), inside the canvas."""
    keep = {e.src, e.dst}
    boxes = [Box.of(n) for n in f.sc.nodes
             if n.kind in ("box", "strip", "pill", "tag", "label") and n.id not in keep]
    lines = [s for o in f.sc.edges if o.id != e.id for s in _segments(o.points)]
    choice = None
    for sx, sy in _spots(e.points, lab.w, lab.h):
        x = q(min(max(sx, 1.0), f.sc.width - lab.w - 1.0))
        y = q(min(max(sy, 1.0), f.sc.height - lab.h - 1.0))
        r = Box(x, y, x + lab.w, y + lab.h)
        clash = any(_overlaps(r, o) for o in [*boxes, *taken]) or \
            any(_seg_hits(s[0], s[1], r, 0.0) for s in lines)
        if choice is None:
            choice = (x, y)
        if not clash:
            choice = (x, y)
            break
    assert choice is not None
    lab.x, lab.y = choice
    taken.append(Box.of(lab))


def _overlaps(r: Box, o: Box) -> bool:
    return r.x0 < o.x1 and o.x0 < r.x1 and r.y0 < o.y1 and o.y0 < r.y1


def _label_text(e: Edge) -> str:
    return " ".join(t.text for lab in e.labels for t in lab.lines)


def _label_color(e: Edge, default: str) -> str:
    for lab in e.labels:
        for t in lab.lines:
            return t.color
    return default


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
    label: str | None  # None: keep the labels it has
    keep_labels: bool
    color: str


def _overlay(sc: Scene, extra: Extra) -> None:
    f = Field.of(sc)
    info: dict[str, dict[str, Any]] = {}
    jobs: list[_Job] = []
    kept: list[Edge] = []
    for e in sc.edges:
        ed = extra.edits.get(e.id)
        if not ed or not editable(e, f.nodes):
            kept.append(e)
            continue
        note = {"collected_label": _label_text(e), "collected_src": e.src,
                "collected_dst": e.dst}
        if ed.get("hidden"):
            info[e.id] = {**note, "hidden": True}
            continue
        src, dst = ed.get("a") or e.src, ed.get("b") or e.dst
        if src not in f.nodes or dst not in f.nodes or src == dst:
            info[e.id] = {**note, "hidden": True, "elsewhere": True}
            continue
        if "arrow" in ed:
            e.arrow_start = ed["arrow"] in ("start", "both")
            e.arrow_end = ed["arrow"] in ("end", "both")
        reroute = any(k in ed for k in ("a", "b", "a_side", "b_side"))
        e.src, e.dst = src, dst
        e.data = dict(sorted({**e.data, "edited": "true"}.items()))
        info[e.id] = note
        kept.append(e)
        jobs.append(_Job(e, f.nodes[src], f.nodes[dst], ed.get("a_side", "auto"),
                         ed.get("b_side", "auto"), float(ed.get("bend", 0) or 0), reroute,
                         ed.get("label"), "label" not in ed,
                         _label_color(e, K.IRIS)))
    for c in extra.connectors:
        a, b = f.nodes.get(str(c.get("a"))), f.nodes.get(str(c.get("b")))
        if a is None or b is None or a.id == b.id:
            continue
        arrow = str(c.get("arrow") or "end")
        e = Edge(id=str(c["id"]), src=a.id, dst=b.id, points=[], style=USER_LINK,
                 arrow_start=arrow in ("start", "both"), arrow_end=arrow in ("end", "both"),
                 data={"kind": "user", "origin": "user"})
        kept.append(e)
        jobs.append(_Job(e, a, b, str(c.get("a_side") or "auto"),
                         str(c.get("b_side") or "auto"), float(c.get("bend") or 0), True,
                         c.get("label") or "", False, K.AMBER))
    sc.edges = kept
    if not jobs:
        if info:
            sc.meta = {**sc.meta, "connectors": dict(sorted(info.items()))}
        return
    # Pass 1: sides (centre ports). Pass 2: ports spread per (node, side), final route.
    mine = {j.edge.id for j in jobs}
    moving = {j.edge.id for j in jobs if j.reroute or j.bend}
    fixed = [e for e in sc.edges if e.id not in moving]
    sides: dict[str, tuple[str, str]] = {}
    for j in jobs:
        if j.reroute:
            r = route(f, j.a, j.b, j.a_side, j.b_side, fixed)
            sides[j.edge.id] = (r.a_side, r.b_side)
    fracs = _spread(jobs, sides)
    done: list[Edge] = []
    taken = [Box.of(lb) for e in fixed for lb in e.labels if e.id not in mine]
    for j in jobs:
        e = j.edge
        others = [o for o in [*fixed, *done] if o.id != e.id]
        if j.reroute:
            sa, sb = sides[e.id]
            fa, fb = fracs.get((e.id, "a"), 0.5), fracs.get((e.id, "b"), 0.5)
            r = route(f, j.a, j.b, sa, sb, others, fa, fb)
        else:
            boxes, ends = f.obstacles(j.a, j.b)
            r = Route(list(e.points), "auto", "auto",
                      _score(list(e.points), f, boxes, ends, [], []))
        pts, applied = with_bend(f, r, j.bend, j.a, j.b, others)
        moved = j.reroute or applied != 0
        if moved:
            e.points = pts
            hop_own(e, others)
        info.setdefault(e.id, {}).update({
            "a_side": r.a_side if j.reroute else "auto",
            "b_side": r.b_side if j.reroute else "auto", "bend": applied})
        if not j.keep_labels:
            e.labels = [edge_label([j.label], 0, 0, LABEL_W, color=j.color)] \
                if j.label else []
            for lab in e.labels:
                _place(lab, e, f, taken)
        elif moved:
            for lab in e.labels:
                _place(lab, e, f, taken)
        if e.id in moving:
            done.append(e)
    sc.meta = {**sc.meta, "connectors": dict(sorted(info.items()))}


def _spread(jobs: list[_Job], sides: dict[str, tuple[str, str]]
            ) -> dict[tuple[str, str], float]:
    """Port positions (fraction along the side) where several rerouted connectors share
    one side of one box, ordered by the other end's position along that side."""
    groups: dict[tuple[str, str], list[tuple[float, str, str]]] = {}
    for j in jobs:
        if j.edge.id not in sides:
            continue
        sa, sb = sides[j.edge.id]
        for end, node, side, other in (("a", j.a, sa, j.b), ("b", j.b, sb, j.a)):
            along = other.x + other.w / 2 if side in ("top", "bottom") else \
                other.y + other.h / 2
            groups.setdefault((node.id, side), []).append((along, j.edge.id, end))
    out: dict[tuple[str, str], float] = {}
    for items in groups.values():
        if len(items) < 2:
            continue
        items.sort()
        n = len(items)
        for k, (_, ident, end) in enumerate(items):
            out[(ident, end)] = (k + 1) / (n + 1)
    return out


__all__ = ["KIND_NAMES", "USER_LINK", "Extra", "editable", "overlay", "route"]
