"""Product badges: the Nutanix product icon on the elements that stand for a product.

A Prism Central block carries the Prism icon, a cluster card the Nutanix Cloud
Infrastructure icon and an NC2 cluster the NC2 icon, so a reader sees which product each
element of a figure is (`PRODUCT_OF`). A badge is a scene node of kind ``glyph`` (paint
layer 3, over the boxes) whose ``data.product`` names the icon (`exporters.products`);
every renderer draws the icon's art in the node's square, on a white tile when the
element it marks is dark (`Style.fill` set).

Badges are added after the layout and the author's edits (`add`), into room the element
already has: its top-right corner, else further left of whatever sits there (a chip, a
pill), else its bottom-right corner. An element with no free corner gets no badge, so
nothing is ever covered or moved. Deterministic: same scene, same badges.
"""

from __future__ import annotations

from ntnxvision_core.exporters.products import PRODUCTS

from . import text as tx
from .scene import Node, Scene, Style, q

#: Side of a badge (pt) and its distance from the element's corner.
SIZE = 9.0
INSET = 3.0
#: Room kept between a badge and its neighbours (pt).
GAP = 2.0
#: The white tile behind a badge on a dark element.
TILE = Style(fill="#FFFFFF", radius=1.6)
PLAIN = Style()
KIND = "glyph"

#: Placed-icon id (`icons.CATALOG`) -> the product it stands for.
ICON_PRODUCT = {"pc": "prism", "cluster": "nci", "nc2_cluster": "nc2"}


def product_of(n: Node) -> str | None:
    """The product a generated or placed element stands for (None: not a product)."""
    if n.kind not in ("box", "container"):
        return None
    placed = ICON_PRODUCT.get(n.data.get("icon", ""))
    if placed:
        return placed
    if n.id.endswith(":pc") and "prism_central" in n.data:
        return "prism"
    uuid = n.data.get("cluster_uuid")
    if uuid and n.id == f"cluster:{uuid}":
        return "nci"
    return None


def _dark(fill: str | None) -> bool:
    if not fill or len(fill) != 7:
        return False
    r, g, b = (int(fill[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b < 0.55


def glyph(ident: str, product: str, x: float, y: float, size: float, parent: str | None,
          tile: bool) -> Node:
    """A badge node: `product`'s icon in the `size` square at (x, y)."""
    return Node(id=ident, kind=KIND, x=q(x), y=q(y), w=q(size), h=q(size),
                style=TILE if tile else PLAIN, parent=parent,
                data={"name": PRODUCTS[product].name, "product": product})


def art_box(n: Node) -> tuple[float, float, float]:
    """(x, y, side) of the square a glyph node's icon is drawn in: the node, less a
    hair of tile around it when it has one."""
    pad = 0.9 if n.style.fill else 0.0
    return n.x + pad, n.y + pad, max(0.1, min(n.w, n.h) - 2 * pad)


def _overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]
             ) -> bool:
    return (a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3]
            and b[1] < a[1] + a[3])


def _contains(n: Node, r: tuple[float, float, float, float]) -> bool:
    return (n.x <= r[0] + 0.01 and n.y <= r[1] + 0.01 and n.x + n.w >= r[0] + r[2] - 0.01
            and n.y + n.h >= r[1] + r[3] - 0.01)


def _text_boxes(n: Node) -> list[tuple[float, float, float, float]]:
    out = []
    for t in n.lines:
        m = tx.metrics(t.bold)
        top = t.baseline - m.ascent * t.size
        out.append((n.x + t.x, n.y + top, t.width, (m.ascent + m.descent) * t.size))
    return out


def _spot(sc: Scene, n: Node, others: list[tuple[str, tuple[float, float, float, float]]]
          ) -> tuple[float, float] | None:
    """Where in `n` a badge fits without touching its text or another element."""
    if n.w < SIZE * 4 or n.h < SIZE + 2 * INSET:
        return None
    texts = _text_boxes(n)
    # Elements that hold `n` are its background, not obstacles.
    blockers = [r for ident, r in others if ident != n.id]
    floor = n.x + n.w / 2
    for y in (n.y + INSET, n.y + n.h - INSET - SIZE):
        x = n.x + n.w - INSET - SIZE
        for _ in range(6):
            if x < floor:
                break
            cand = (x - GAP / 2, y - GAP / 2, SIZE + GAP, SIZE + GAP)
            if any(_overlap(cand, t) for t in texts):
                break  # text is never moved, and further left is more text
            hit = [r for r in blockers if _overlap(cand, r)]
            if not hit:
                return q(x), q(y)
            x = min(r[0] for r in hit) - GAP - SIZE
    return None


def add(scenes: list[Scene]) -> None:
    """Badge every product element of `scenes` that has room for one."""
    for sc in scenes:
        have = {n.parent for n in sc.nodes if n.kind == KIND}
        targets = [(n, p) for n in sc.nodes if n.id not in have
                   and (p := product_of(n)) is not None]
        if not targets:
            continue
        added: list[Node] = []
        for n, product in targets:
            r_n = (n.x, n.y, n.w, n.h)
            others = [(m.id, (m.x, m.y, m.w, m.h)) for m in [*sc.nodes, *added]
                      if m is not n and not _contains(m, r_n)]
            others += [("", (lab.x, lab.y, lab.w, lab.h)) for e in sc.edges
                       for lab in e.labels]
            spot = _spot(sc, n, others)
            if spot is None:
                continue
            added.append(glyph(f"{n.id}:product", product, spot[0], spot[1], SIZE, n.id,
                               _dark(n.style.fill)))
        sc.nodes.extend(added)


__all__ = ["ICON_PRODUCT", "KIND", "SIZE", "add", "art_box", "glyph", "product_of"]
