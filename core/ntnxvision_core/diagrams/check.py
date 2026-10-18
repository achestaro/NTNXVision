"""Scene invariants: what "no text exceeds its box" means, checked the same way in tests
and available to callers (the Diagrams editor can warn on an edited scene)."""

from __future__ import annotations

from . import text as tx
from .scene import Scene, TextLine

EPS = 0.05


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
    outside the canvas, a box outside its parent, overlapping sibling boxes."""
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
        for x, y in e.points:
            if x < -EPS or y < -EPS or x > sc.width + EPS or y > sc.height + EPS:
                problems.append(f"{e.id}: point outside the canvas")
    return problems
