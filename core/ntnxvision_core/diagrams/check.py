"""Scene invariants: what "no text exceeds its box" means, checked the same way in tests
and available to callers (the Diagrams editor can warn on an edited scene)."""

from __future__ import annotations

from . import text as tx
from .scene import Edge, Node, Scene, TextLine

EPS = 0.05
#: How far (pt) a label may sit from its own edge (labels above a line, not on it).
ANCHOR = 4.0


def _gap_to_path(x: float, y: float, w: float, h: float,
                 points: list[tuple[float, float]]) -> float:
    """Distance from a rectangle to the nearest segment of an orthogonal polyline."""
    best = float("inf")
    for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
        sx0, sx1 = sorted((x0, x1))
        sy0, sy1 = sorted((y0, y1))
        dx = max(sx0 - (x + w), x - sx1, 0.0)
        dy = max(sy0 - (y + h), y - sy1, 0.0)
        best = min(best, (dx * dx + dy * dy) ** 0.5)
    return best


def _lines_fit(lines: list[TextLine], w: float, h: float, where: str) -> list[str]:
    problems = []
    for t in lines:
        if t.size < tx.MIN_SIZE - 1e-9:
            problems.append(f"{where}: {t.text!r} at {t.size} pt is below {tx.MIN_SIZE} pt")
        if t.x < -EPS or t.x + t.width > w + EPS:
            problems.append(f"{where}: {t.text!r} overflows its width ({t.x}+{t.width} > {w})")
        m = tx.metrics(t.bold)
        if t.baseline - m.ascent * t.size < -0.6 or t.baseline + m.descent * t.size > h + 0.6:
            problems.append(f"{where}: {t.text!r} overflows its height")
    return problems


def check_scene(sc: Scene) -> list[str]:
    """Every problem found: text wider or taller than its box, text under 6 pt, a shape
    outside the canvas, a box outside its parent, overlapping sibling boxes, and for the
    connectors the Diagrams editor drew or changed (`check_connectors`) the routing and
    label rules."""
    problems: list[str] = []
    by_id = {n.id: n for n in sc.nodes}
    if len(by_id) != len(sc.nodes):
        problems.append("duplicate node ids")
    for n in sc.nodes:
        problems += _lines_fit(n.lines, n.w, n.h, n.id)
        if n.x < -EPS or n.y < -EPS or n.x + n.w > sc.width + EPS or \
                n.y + n.h > sc.height + EPS:
            problems.append(f"{n.id}: outside the canvas")
        parent = by_id.get(n.parent or "")
        if parent is not None and n.kind != "tag" and (
                n.x < parent.x - EPS or n.y < parent.y - EPS
                or n.x + n.w > parent.x + parent.w + EPS
                or n.y + n.h > parent.y + parent.h + EPS):
            problems.append(f"{n.id}: outside its parent {parent.id}")
    boxes = [n for n in sc.nodes if n.kind in ("box", "container")]
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            if a.parent != b.parent or a.id in (b.parent, ) or b.id in (a.parent, ):
                continue
            if a.x < b.x + b.w - EPS and b.x < a.x + a.w - EPS and \
                    a.y < b.y + b.h - EPS and b.y < a.y + a.h - EPS:
                problems.append(f"{a.id} overlaps {b.id}")
    for e in sc.edges:
        for lab in e.labels:
            problems += _lines_fit(lab.lines, lab.w, lab.h, f"{e.id} label")
            if lab.x < -EPS or lab.x + lab.w > sc.width + EPS:
                problems.append(f"{e.id} label: outside the canvas")
            if _gap_to_path(lab.x, lab.y, lab.w, lab.h, e.points) > ANCHOR:
                problems.append(f"{e.id} label: not on its own edge")
        for x, y in e.points:
            if x < -EPS or y < -EPS or x > sc.width + EPS or y > sc.height + EPS:
                problems.append(f"{e.id}: point outside the canvas")
    return problems + check_connectors(sc)


# ─── connectors drawn or changed in the Diagrams editor ────────────────

#: Node kinds a connector route keeps out of (its own ends and their holders aside).
SOLID = ("box", "container", "strip", "pill", "tag")
#: Node kinds a connector label keeps off.
LABEL_SOLID = ("box", "strip", "pill", "tag", "label")
#: Closest two connector ends may sit on one side of one box (pt).
PORT_GAP = 10.0
#: A run this close (pt) to a container border, this long, "hugs" it.
HUG_DIST, HUG_LEN = 2.0, 30.0

Pt = tuple[float, float]


def _segs(points: list[Pt]) -> list[tuple[Pt, Pt]]:
    return list(zip(points, points[1:], strict=False))


def is_hop(a: Pt, b: Pt) -> bool:
    """A slanted piece of the small hop drawn where a run lifts over another."""
    from .route import HOP_H, HOP_W

    return a[0] != b[0] and a[1] != b[1] and abs(a[0] - b[0]) <= HOP_W + 0.02 and \
        abs(a[1] - b[1]) <= HOP_H + 0.02


def ancestors(nodes: dict[str, Node], ident: str) -> set[str]:
    out: set[str] = set()
    cur = nodes[ident].parent if ident in nodes else None
    while cur and cur not in out:
        out.add(cur)
        cur = nodes[cur].parent if cur in nodes else None
    return out


def seg_in_rect(a: Pt, b: Pt, x0: float, y0: float, x1: float, y1: float,
                inset: float = 0.5) -> bool:
    sx0, sx1 = sorted((a[0], b[0]))
    sy0, sy1 = sorted((a[1], b[1]))
    return sx0 < x1 - inset and sx1 > x0 + inset and sy0 < y1 - inset and sy1 > y0 + inset


def _rect_overlap(r: tuple[float, float, float, float],
                  o: tuple[float, float, float, float]) -> bool:
    return r[0] < o[2] - 0.01 and o[0] < r[2] - 0.01 and r[1] < o[3] - 0.01 and \
        o[1] < r[3] - 0.01


def side_of(n: Node, p: Pt) -> str | None:
    if abs(p[1] - n.y) < 0.6:
        return "top"
    if abs(p[1] - n.y - n.h) < 0.6:
        return "bottom"
    if abs(p[0] - n.x) < 0.6:
        return "left"
    if abs(p[0] - n.x - n.w) < 0.6:
        return "right"
    return None


def heading_boxes(n: Node) -> list[tuple[float, float, float, float]]:
    """The text lines of a container as rectangles (its heading)."""
    return [(n.x + t.x - 1, n.y + t.baseline - t.size, n.x + t.x + t.width + 1,
             n.y + t.baseline + t.size * 0.3) for t in n.lines]


def route_problems(sc: Scene, e: Edge, nodes: dict[str, Node] | None = None) -> list[str]:
    """What is wrong with the route of connector `e`: a slanted or empty run (hops
    aside), a run through a box that is neither an end nor holds one (legends
    included), an inner run through an end, a point off the canvas, a run on a
    container border, a run that doubles back, a run shared with another connector or
    over another's label, an end within 10 pt of another connector's end on the same
    side of the same box."""
    nodes = nodes if nodes is not None else {n.id: n for n in sc.nodes}
    out: list[str] = []
    pts = e.points
    if len(pts) < 2 or e.src not in nodes or e.dst not in nodes:
        return [f"{e.id}: no route"]
    holders = ancestors(nodes, e.src) | ancestors(nodes, e.dst)
    segs = _segs(pts)
    for a, b in segs:
        if a == b:
            out.append(f"{e.id}: empty run at {a}")
        elif a[0] != b[0] and a[1] != b[1] and not is_hop(a, b):
            out.append(f"{e.id}: slanted run {a}->{b}")
    for x, y in pts:
        if x < 1 - EPS or y < 1 - EPS or x > sc.width - 1 + EPS or y > sc.height - 1 + EPS:
            out.append(f"{e.id}: point {x},{y} off the canvas")
    straight = [(a, b) for a, b in segs if a[0] == b[0] or a[1] == b[1]]
    for n in sc.nodes:
        if n.kind not in SOLID or n.id in (e.src, e.dst) or n.id in holders:
            continue
        if any(seg_in_rect(a, b, n.x, n.y, n.x + n.w, n.y + n.h) for a, b in straight):
            out.append(f"{e.id}: runs through {n.kind} {n.id}")
    for end in (e.src, e.dst):
        n = nodes[end]
        if any(seg_in_rect(a, b, n.x, n.y, n.x + n.w, n.y + n.h, 0.25)
               for a, b in straight[1:-1]):
            out.append(f"{e.id}: runs through its own end {end}")
    for n in sc.nodes:
        if n.kind != "container":
            continue
        for a, b in straight:
            length = abs(a[0] - b[0]) + abs(a[1] - b[1])
            if length < HUG_LEN:
                continue
            if a[1] == b[1] and any(abs(a[1] - yy) < HUG_DIST for yy in (n.y, n.y + n.h)) \
                    and min(max(a[0], b[0]), n.x + n.w) - max(min(a[0], b[0]), n.x) > HUG_LEN:
                out.append(f"{e.id}: runs on the border of {n.id}")
            if a[0] == b[0] and any(abs(a[0] - xx) < HUG_DIST for xx in (n.x, n.x + n.w)) \
                    and min(max(a[1], b[1]), n.y + n.h) - max(min(a[1], b[1]), n.y) > HUG_LEN:
                out.append(f"{e.id}: runs on the border of {n.id}")
    for i in range(len(pts) - 2):
        a, b, c = pts[i], pts[i + 1], pts[i + 2]
        if (a[1] == b[1] == c[1] and (b[0] - a[0]) * (c[0] - b[0]) < 0) or \
                (a[0] == b[0] == c[0] and (b[1] - a[1]) * (c[1] - b[1]) < 0):
            out.append(f"{e.id}: doubles back at {b}")
    for o in sc.edges:
        if o.id == e.id or not o.points:
            continue
        for a, b in straight:
            for c, d in _segs(o.points):
                if (a[1] == b[1] == c[1] == d[1] and min(max(a[0], b[0]), max(c[0], d[0]))
                        - max(min(a[0], b[0]), min(c[0], d[0])) > 1) or \
                        (a[0] == b[0] == c[0] == d[0] and min(max(a[1], b[1]), max(c[1], d[1]))
                         - max(min(a[1], b[1]), min(c[1], d[1])) > 1):
                    out.append(f"{e.id}: shares a run with {o.id}")
                    break
            else:
                continue
            break
        for lab in o.labels:
            if any(seg_in_rect(a, b, lab.x, lab.y, lab.x + lab.w, lab.y + lab.h, 0.0)
                   for a, b in straight):
                out.append(f"{e.id}: runs over the label of {o.id}")
                break
        for end, p in ((e.src, pts[0]), (e.dst, pts[-1])):
            s = side_of(nodes[end], p)
            if s is None:
                continue
            for oend, op in ((o.src, o.points[0]), (o.dst, o.points[-1])):
                if oend == end and side_of(nodes[end], op) == s:
                    gap = abs(p[0] - op[0]) if s in ("top", "bottom") else abs(p[1] - op[1])
                    if gap < PORT_GAP:
                        out.append(f"{e.id}: ends {gap:.1f} pt from {o.id} on {end} {s}")
    return out


def crossing_problems(sc: Scene, e: Edge) -> list[str]:
    """Crossings of connector `e` with another line that carry no hop."""
    from .route import HOP_W

    out = []
    for a, b in _segs(e.points):
        for o in sc.edges:
            if o.id == e.id:
                continue
            for c, d in _segs(o.points):
                for h, v in (((a, b), (c, d)), ((c, d), (a, b))):
                    (h0, h1), (v0, v1) = h, v
                    if not (h0[1] == h1[1] and h0[0] != h1[0] and v0[0] == v1[0]
                            and v0[1] != v1[1]):
                        continue
                    vx, hy = v0[0], h0[1]
                    if min(h0[0], h1[0]) + HOP_W < vx < max(h0[0], h1[0]) - HOP_W and \
                            min(v0[1], v1[1]) + 0.5 < hy < max(v0[1], v1[1]) - 0.5:
                        out.append(f"{e.id}: crosses {o.id} at {vx},{hy} without a hop")
    return out


def label_problems(sc: Scene, e: Edge, nodes: dict[str, Node] | None = None) -> list[str]:
    """Labels of connector `e` off their line, or on a box, a zone it is not inside, a
    zone heading, another label or another line."""
    nodes = nodes if nodes is not None else {n.id: n for n in sc.nodes}
    holders = ancestors(nodes, e.src) | ancestors(nodes, e.dst)
    out = []
    for lab in e.labels:
        r = (lab.x, lab.y, lab.x + lab.w, lab.y + lab.h)
        if _gap_to_path(lab.x, lab.y, lab.w, lab.h, e.points) > ANCHOR:
            out.append(f"{e.id} label: not on its own line")
        if r[0] < -EPS or r[1] < -EPS or r[2] > sc.width + EPS or r[3] > sc.height + EPS:
            out.append(f"{e.id} label: outside the canvas")
        for n in sc.nodes:
            box = (n.x, n.y, n.x + n.w, n.y + n.h)
            if n.kind in LABEL_SOLID and n.id not in (e.src, e.dst) and _rect_overlap(r, box):
                out.append(f"{e.id} label: on {n.kind} {n.id}")
            elif n.kind == "container" and n.id not in holders | {e.src, e.dst} and \
                    _rect_overlap(r, box):
                out.append(f"{e.id} label: on zone {n.id}")
            if n.kind == "container" and any(_rect_overlap(r, t) for t in heading_boxes(n)):
                out.append(f"{e.id} label: on the heading of {n.id}")
        for o in sc.edges:
            if o.id == e.id:
                continue
            if any(_rect_overlap(r, (x.x, x.y, x.x + x.w, x.y + x.h)) for x in o.labels):
                out.append(f"{e.id} label: on the label of {o.id}")
            if any(seg_in_rect(a, b, *r, 0.0) for a, b in _segs(o.points)):
                out.append(f"{e.id} label: on the line of {o.id}")
    return out


def check_connectors(sc: Scene) -> list[str]:
    """The routing and label rules for the connectors the editor routed (user-added,
    reconnected, re-sided, lane-shifted) and the labels it placed (`scene.meta`
    "connectors": per edge id, ``routed`` / ``labeled``)."""
    info = sc.meta.get("connectors") or {}
    if not info:
        return []
    nodes = {n.id: n for n in sc.nodes}
    out: list[str] = []
    for e in sc.edges:
        note = info.get(e.id) or {}
        if note.get("routed"):
            out += route_problems(sc, e, nodes) + crossing_problems(sc, e)
        if note.get("routed") or note.get("labeled"):
            out += label_problems(sc, e, nodes)
    return out
