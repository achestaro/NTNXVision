"""The positioned, output-independent scene every renderer draws.

Units are points (1/72 in), origin top-left, y down. Every coordinate enters the scene
through `q()` (a 0.01 pt grid) and every serialiser prints numbers with `num()`, so equal
scenes give equal bytes in every format.

Text is laid out completely here: each `TextLine` carries its size, weight, colour, its
x offset and baseline inside the node, and its measured width. Renderers never measure,
wrap or align text themselves, which is what keeps SVG, PNG and PDF identical.

Z order is part of the contract (`draw_order`): containers, then boxes, then edges, then
edge labels, then the chips, pills and strips that sit on box edges, so a connector never
crosses a label.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

SCENE_SCHEMA = "ntnxvision.diagram.scene/1"


def q(v: float) -> float:
    """Snap to the 0.01 pt grid: the one rounding rule of the whole engine."""
    r = round(v * 100) / 100
    return 0.0 if r == 0 else r


def num(v: float) -> str:
    """Compact, stable number text for every serialiser (no exponent, never -0)."""
    v = q(v)
    if v == 0:
        return "0"
    return f"{v:.2f}".rstrip("0").rstrip(".")


@dataclass(frozen=True)
class Style:
    """A visual style token. Renderers only ever see these, never raw literals."""

    fill: str | None = None  # "#RRGGBB" or None
    stroke: str | None = None
    stroke_w: float = 0.75
    #: Dash pattern (on, off) in points, or None for a solid stroke.
    dash: tuple[float, float] | None = None
    radius: float = 0.0


@dataclass(frozen=True)
class TextLine:
    text: str
    size: float  # pt
    bold: bool
    color: str
    #: Left edge of the line, relative to the node's (or label's) left edge.
    x: float
    #: Baseline, relative to the node's (or label's) top edge.
    baseline: float
    #: Measured advance width (no safety margin), for checks and editors.
    width: float


@dataclass
class Node:
    id: str  # stable, derived from object identity (never a counter or position)
    #: container | box | strip | pill | tag | label | swatch
    kind: str
    x: float
    y: float
    w: float
    h: float
    style: Style
    lines: list[TextLine] = field(default_factory=list)
    parent: str | None = None
    #: Semantic identity for Visio Shape Data, SVG titles and the editor.
    data: dict[str, str] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n".join(t.text for t in self.lines)


@dataclass
class EdgeLabel:
    x: float
    y: float
    w: float
    h: float
    lines: list[TextLine] = field(default_factory=list)
    #: White background behind the text (labels that sit on the line).
    background: bool = True


@dataclass
class Edge:
    id: str
    src: str
    dst: str
    points: list[tuple[float, float]]
    style: Style
    arrow_start: bool = False
    arrow_end: bool = False
    labels: list[EdgeLabel] = field(default_factory=list)
    data: dict[str, str] = field(default_factory=dict)


@dataclass
class Scene:
    id: str
    type: str
    title: str
    caption: str
    width: float
    height: float
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    #: (part, of) when a diagram is split into parts, else (1, 1).
    part: tuple[int, int] = (1, 1)
    meta: dict[str, Any] = field(default_factory=dict)

    def node(self, node_id: str) -> Node:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(node_id)


#: Paint layer per node kind (lower first). Edges sit at layer 2, their labels at 2.5.
_LAYER = {"container": 0, "box": 1, "swatch": 1, "label": 1, "strip": 3, "pill": 3,
          "tag": 3}


def draw_order(sc: Scene) -> list[tuple[str, Any]]:
    """The paint order every renderer follows: [("node", n) | ("edge", e) |
    ("label", (e, label))]. Stable: ties keep the scene's list order."""
    out: list[tuple[str, Any]] = []
    nodes = sorted(enumerate(sc.nodes), key=lambda iv: (_LAYER.get(iv[1].kind, 1), iv[0]))
    out.extend(("node", n) for _, n in nodes if _LAYER.get(n.kind, 1) < 2)
    out.extend(("edge", e) for e in sc.edges)
    out.extend(("label", (e, lab)) for e in sc.edges for lab in e.labels)
    out.extend(("node", n) for _, n in nodes if _LAYER.get(n.kind, 1) >= 2)
    return out


# ─── serialisation ─────────────────────────────────────────────────────


def dumps(obj: dict[str, Any]) -> bytes:
    """Canonical JSON: sorted keys, fixed separators, UTF-8, trailing newline."""
    return (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            + "\n").encode("utf-8")


def to_doc(sc: Scene) -> dict[str, Any]:
    doc = asdict(sc)
    doc["schema"] = SCENE_SCHEMA
    return doc


def _style(d: dict[str, Any]) -> Style:
    dash = d.get("dash")
    return Style(fill=d.get("fill"), stroke=d.get("stroke"), stroke_w=d.get("stroke_w", 0.75),
                 dash=(float(dash[0]), float(dash[1])) if dash else None,
                 radius=d.get("radius", 0.0))


def _lines(items: list[dict[str, Any]]) -> list[TextLine]:
    return [TextLine(**t) for t in items]


def from_doc(d: dict[str, Any]) -> Scene:
    if d.get("schema") != SCENE_SCHEMA:
        raise ValueError(f"Unsupported scene schema: {d.get('schema')!r}")
    nodes = [Node(id=n["id"], kind=n["kind"], x=n["x"], y=n["y"], w=n["w"], h=n["h"],
                  style=_style(n["style"]), lines=_lines(n["lines"]), parent=n.get("parent"),
                  data=dict(n.get("data") or {})) for n in d["nodes"]]
    edges = [Edge(id=e["id"], src=e["src"], dst=e["dst"],
                  points=[(p[0], p[1]) for p in e["points"]], style=_style(e["style"]),
                  arrow_start=e["arrow_start"], arrow_end=e["arrow_end"],
                  labels=[EdgeLabel(x=lb["x"], y=lb["y"], w=lb["w"], h=lb["h"],
                                    lines=_lines(lb["lines"]), background=lb["background"])
                          for lb in e["labels"]],
                  data=dict(e.get("data") or {})) for e in d["edges"]]
    part = d.get("part") or (1, 1)
    return Scene(id=d["id"], type=d["type"], title=d["title"], caption=d["caption"],
                 width=d["width"], height=d["height"], nodes=nodes, edges=edges,
                 part=(int(part[0]), int(part[1])), meta=dict(d.get("meta") or {}))


def scene_json(sc: Scene) -> bytes:
    return dumps(to_doc(sc))
