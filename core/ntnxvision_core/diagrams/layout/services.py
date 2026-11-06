"""Platform service designs layout (S1 to S4: NKP, NDB, Objects, Files).

Top to bottom: the management plane (a Prism Central block, or the NDB appliance), a
"manages" line to each Nutanix cluster, then one column per cluster: its name and, inside,
the product instances (boxes) with their members laid in rows under a lane label
("CONTROL PLANE", "BUCKETS"). A database clone sits indented under its source, joined by
a dashed L; the control plane VIP points at the control plane.

The figure stays uncluttered on purpose: names, one address, the share protocol. A lane
with more members than `MAX_ROWS` rows shows the first ones and a "+N more" card (the
report tables list every member). Clusters that do not fit the page width, or part 1's
height budget, go into the next part, which draws its own management plane.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import fmt
from .. import text as tx
from .. import tokens as K
from ..badges import INSET, SIZE
from ..build import LEGEND_GAP, Row, box, edge, legend, text_h
from ..scene import Node, Scene, Style, q
from ..services import NDB_ANCHOR, ServiceModel, SvcBox, SvcCluster, SvcItem, SvcLane
from .conceptual import Budget, Page, Refs
from .fit import place_first

MX = 0.5
TOP = 0.5
P = 6.0
ANCHOR_W = 172.0
#: Between the management plane and the clusters (the manages lines run here).
DROP = 24.0
CL_GAP = 14.0
BOX_GAP = 6.0
ITEM_GAP = 3.5
LANE_GAP = 4.0
CLONE_INDENT = 10.0
MAX_ROWS = 4
#: Product box width per diagram type.
BOX_W = {"nkp": 262.0, "ndb": 150.0, "objects": 236.0, "files": 236.0}
BADGE_ROOM = SIZE + INSET + 2.0

ITEM_STYLE = {"cp": K.SVC_CORE, "fsvm": K.SVC_CORE, "vip": K.SVC_ENDPOINT,
              "lb": K.SVC_ENDPOINT, "endpoint": K.SVC_ENDPOINT, "clone": K.SVC_CLONE,
              "more": K.MORE, "note": K.NOTE}


# ─── measuring ─────────────────────────────────────────────────────────


def _item_rows(it: SvcItem) -> list[Row]:
    rows = [Row(it.name, K.SIZE_BODY, it.kind != "more", K.INK if it.kind != "more"
                else K.INK_2, 2, tx.MIN_SIZE)]
    if it.sub:
        rows.append(Row(it.sub, K.SIZE_SMALL, False, K.INK_3, 2, gap_before=0.3))
    return rows


def _item_h(it: SvcItem, w: float) -> float:
    return q(text_h(_item_rows(it), w - 7) + 5)


def _shown(lane: SvcLane) -> list[SvcItem]:
    """The lane's items, the last ones folded into one "+N more" card past `MAX_ROWS`
    rows (clones count as rows of their own)."""
    cap = max(1, lane.cols) * MAX_ROWS
    if len(lane.items) <= cap:
        return list(lane.items)
    keep = lane.items[:cap - 1]
    rest = len(lane.items) - len(keep)
    return [*keep, SvcItem(id=f"{lane.id}:more", name=f"+{rest:,} more {lane.noun}",
                           kind="more", sub="listed in the report tables")]


@dataclass
class _Cell:
    item: SvcItem
    x: float  # relative to the box's inner left
    y: float  # relative to the lane's first row
    w: float
    h: float


def _lane_cells(lane: SvcLane, inner: float) -> tuple[list[_Cell], float]:
    items = _shown(lane)
    cols = max(1, lane.cols)
    cw = q((inner - ITEM_GAP * (cols - 1)) / cols)
    cells: list[_Cell] = []
    y = 0.0
    if cols == 1:
        for it in items:
            indent = CLONE_INDENT if it.kind == "clone" and it.of else 0.0
            h = _item_h(it, cw - indent)
            cells.append(_Cell(it, indent, y, q(cw - indent), h))
            y = q(y + h + ITEM_GAP)
    else:
        for r in range(0, len(items), cols):
            row = items[r:r + cols]
            h = max(_item_h(it, cw) for it in row)
            for c, it in enumerate(row):
                cells.append(_Cell(it, q(c * (cw + ITEM_GAP)), y, cw, h))
            y = q(y + h + ITEM_GAP)
    return cells, q(max(0.0, y - ITEM_GAP))


def _box_head(b: SvcBox, w: float) -> list[Row]:
    rows = [Row(b.name, K.SIZE_CARD_TITLE, True, K.INK, 2, tx.MIN_SIZE)]
    if b.sub:
        rows.append(Row(b.sub, K.SIZE_SMALL, False, K.INK_3, 2, gap_before=0.5))
    return rows


def _title_w(w: float, badge: bool) -> float:
    return w - 10 - (BADGE_ROOM if badge else 0.0)


def _box_h(b: SvcBox, w: float) -> float:
    h = 5 + text_h(_box_head(b, w), _title_w(w, "badge" in b.data))
    for lane in b.lanes:
        if not lane.items:
            continue
        h += LANE_GAP + (tx.line_height(K.SIZE_SMALL) + 2 if lane.title else 0)
        h += _lane_cells(lane, w - 10)[1]
    return q(h + 5)


def _cluster_head(c: SvcCluster) -> list[Row]:
    return [Row(c.name, K.SIZE_REMOTE_TITLE, True, K.INK, 2, tx.MIN_SIZE)]


def _cols(model: ServiceModel, c: SvcCluster, page: Page) -> int:
    bw = BOX_W.get(model.type, 236.0)
    return 2 if len(c.boxes) >= 3 and 2 * bw + BOX_GAP + 2 * P <= page.width - 2 * MX else 1


def _cluster_w(model: ServiceModel, c: SvcCluster, page: Page) -> float:
    bw = BOX_W.get(model.type, 236.0)
    if not c.boxes:
        return q(min(bw, 150.0) + 2 * P)
    n = _cols(model, c, page)
    return q(n * bw + (n - 1) * BOX_GAP + 2 * P)


def _grid(model: ServiceModel, c: SvcCluster, page: Page
          ) -> tuple[list[tuple[SvcBox, float, float, float]], float]:
    """(box, x, y, h) relative to the column's box area, and the area's height: each
    box goes into the shortest column, in model order."""
    bw = BOX_W.get(model.type, 236.0)
    n = _cols(model, c, page)
    heights = [0.0] * n
    out = []
    for b in c.boxes:
        col = min(range(n), key=lambda i: (heights[i], i))
        h = _box_h(b, bw)
        out.append((b, q(col * (bw + BOX_GAP)), heights[col], h))
        heights[col] = q(heights[col] + h + BOX_GAP)
    return out, q(max(heights, default=BOX_GAP) - BOX_GAP) if c.boxes else 0.0


def _cluster_h(model: ServiceModel, c: SvcCluster, page: Page) -> float:
    w = _cluster_w(model, c, page)
    head = text_h(_cluster_head(c), w - 2 * P - BADGE_ROOM)
    body = _grid(model, c, page)[1] if c.boxes else tx.line_height(K.SIZE_SMALL)
    return q(P + head + 5 + body + P)


# ─── drawing ───────────────────────────────────────────────────────────


def _draw_box(sc: Scene, b: SvcBox, x: float, y: float, w: float, h: float,
              parent: str, used: set[str]) -> None:
    node = box(b.id, "box", x, y, w, [], K.SVC_BOX, pad_x=5, h=h, parent=parent,
               data=dict(b.data))
    head = box(b.id, "box", x, y, _title_w(w, "badge" in b.data) + 10, _box_head(b, w),
               K.SVC_BOX, pad_x=5, pad_y=5, align="left")
    node.lines = head.lines
    sc.nodes.append(node)
    cy = q(y + 5 + text_h(_box_head(b, w), _title_w(w, "badge" in b.data)))
    inner = w - 10
    vip: Node | None = None
    for lane in b.lanes:
        if not lane.items:
            continue
        cy = q(cy + LANE_GAP)
        if lane.title:
            lw = q(min(inner, tx.width(lane.title, K.SIZE_SMALL, True) + 1))
            sc.nodes.append(box(f"{lane.id}:label", "label", x + 5, cy, lw,
                                [Row(lane.title, K.SIZE_SMALL, True, K.INK_3)], K.LABEL,
                                pad_x=0, pad_y=0, align="left", parent=b.id))
            cy = q(cy + tx.line_height(K.SIZE_SMALL) + 2)
        cells, lh = _lane_cells(lane, inner)
        drawn: dict[str, Node] = {}
        for cell in cells:
            it = cell.item
            style = ITEM_STYLE.get(it.kind, K.SVC_ITEM)
            used.add(it.kind)
            n = box(it.id, "box", x + 5 + cell.x, cy + cell.y, cell.w, _item_rows(it), style,
                    pad_x=3.5, pad_y=2.5, align="left", h=cell.h, valign="middle",
                    parent=b.id, data={**it.data, "name": it.name})
            sc.nodes.append(n)
            drawn[it.id] = n
            if it.kind == "vip":
                vip = n
            if it.kind == "clone" and it.of in drawn:
                src = drawn[it.of]
                lx = q(src.x + CLONE_INDENT / 2)
                mid = q(n.y + n.h / 2)
                sc.edges.append(edge(f"{it.of}->{it.id}", it.of, it.id,
                                     [(lx, src.y + src.h), (lx, mid), (n.x, mid)],
                                     K.CLONE_LINK, arrow_end=False, data={"kind": "clone"}))
                used.add("clone_link")
        if vip is not None and lane.id.endswith(":cp") and cells:
            first = next((drawn[c.item.id] for c in cells
                          if abs(drawn[c.item.id].x - vip.x) < 0.5), None)
            if first is not None:
                cx = q(vip.x + vip.w / 2)
                sc.edges.append(edge(f"{vip.id}->{lane.id}", vip.id, first.id,
                                     [(cx, vip.y + vip.h), (cx, first.y)], K.SERVES,
                                     data={"kind": "serves"}))
                used.add("serves")
        cy = q(cy + lh)


def _draw_cluster(sc: Scene, model: ServiceModel, c: SvcCluster, x: float, y: float,
                  h: float, page: Page, used: set[str]) -> Node:
    w = _cluster_w(model, c, page)
    cont = box(c.id, "container", x, y, w, [], K.SITE, pad_x=P, h=h, data=dict(c.data))
    head = box(c.id, "container", x, y, w - BADGE_ROOM, _cluster_head(c), K.SITE, pad_x=P,
               pad_y=P, align="left")
    cont.lines = head.lines
    sc.nodes.append(cont)
    top = q(y + P + text_h(_cluster_head(c), w - 2 * P - BADGE_ROOM) + 5)
    if not c.boxes:
        sc.nodes.append(box(f"{c.id}:empty", "label", x + P, top, w - 2 * P,
                            [Row(c.empty or "Nothing deployed", K.SIZE_SMALL, False, K.INK_3)],
                            K.LABEL, pad_x=0, pad_y=0, align="left", parent=c.id))
        return cont
    bw = BOX_W.get(model.type, 236.0)
    for b, bx, by, bh in _grid(model, c, page)[0]:
        _draw_box(sc, b, x + P + bx, top + by, bw, bh, c.id, used)
    return cont


def _anchor_rows(a_name: str, a_sub: str | None) -> list[Row]:
    rows = [Row(a_name, K.SIZE_PC_TITLE, True, K.WHITE, 2, tx.MIN_SIZE)]
    if a_sub:
        rows.append(Row(a_sub, K.SIZE_SMALL, False, K.IRIS_TINT, 2, gap_before=0.5))
    return rows


def _anchor_h(model: ServiceModel) -> float:
    return max((q(text_h(_anchor_rows(a.name, a.sub), ANCHOR_W - 10 - 2 * BADGE_ROOM) + 12)
                for a in model.anchors), default=0.0)


def _legend_items(model: ServiceModel, used: set[str]) -> list[tuple[str, Style, str]]:
    plane = "NDB appliance" if model.type == "ndb" else "Prism Central"
    product = {"nkp": "NKP cluster", "ndb": "Database engine", "objects": "Object store",
               "files": "File server"}[model.type]
    items: list[tuple[str, Style, str]] = []
    if model.anchors:
        items.append(("box", K.PC, plane))
    items.append(("box", K.SITE, "Nutanix cluster"))
    items.append(("box", K.SVC_BOX, product))
    names = {"nkp": {"cp": "Control plane node", "worker": "Worker node",
                     "vip": "Endpoint (VIP, load balancer)"},
             "ndb": {"db": "Database (on its DB server)", "clone": "Clone"},
             "objects": {"endpoint": "S3 endpoint", "bucket": "Bucket"},
             "files": {"fsvm": "File server VM", "share": "Share (protocol)"}}[model.type]
    seen: set[str] = set()
    for kind, label in names.items():
        if kind in used or (kind == "vip" and "lb" in used):
            style = ITEM_STYLE.get(kind, K.SVC_ITEM)
            if label not in seen:
                items.append(("box", style, label))
                seen.add(label)
    if "more" in used:
        items.append(("box", K.MORE, "Summary card"))
    if model.anchors:
        items.append(("line", K.MANAGES, "Manages"))
    if "serves" in used:
        items.append(("line", K.SERVES, "Serves"))
    if "clone_link" in used:
        items.append(("line", K.CLONE_LINK, "Cloned from"))
    return items


def _part(model: ServiceModel, clusters: list[SvcCluster], page: Page, sid: str) -> Scene:
    sc = Scene(id=sid, type=model.type, title=model.title, caption="", width=page.width,
               height=0)
    anchors = [a for a in model.anchors if any(c.anchor == a.id for c in clusters)]
    ah = _anchor_h(model) if anchors else 0.0
    cy = q(TOP + ah + DROP) if anchors else TOP
    heights = {c.id: _cluster_h(model, c, page) for c in clusters}
    row_h = max(heights.values(), default=0.0)
    used: set[str] = set()
    spans: dict[str, tuple[float, float]] = {}
    nodes: dict[str, Node] = {}
    x = MX
    for c in clusters:
        n = _draw_cluster(sc, model, c, x, cy, row_h, page, used)
        nodes[c.id] = n
        if c.anchor:
            lo, hi = spans.get(c.anchor, (n.x, n.x + n.w))
            spans[c.anchor] = (min(lo, n.x), max(hi, n.x + n.w))
        x = q(x + n.w + CL_GAP)
    content_r = max((n.x + n.w for n in nodes.values()), default=MX)
    prev = MX - CL_GAP
    for a in anchors:
        lo, hi = spans[a.id]
        ax = max((lo + hi) / 2 - ANCHOR_W / 2, prev + CL_GAP, MX)
        ax = min(ax, max(MX, max(content_r, page.width - MX) - ANCHOR_W))
        an = box(a.id, "box", ax, TOP, ANCHOR_W, _anchor_rows(a.name, a.sub), K.PC, pad_x=5,
                 pad_y=6, h=ah, valign="middle", data=dict(a.data))
        sc.nodes.append(an)
        prev = an.x + an.w
        acx = q(an.x + an.w / 2)
        bus = q(an.y + an.h + DROP / 2)
        for c in clusters:
            if c.anchor != a.id:
                continue
            n = nodes[c.id]
            # Straight down when the cluster is under the block, else to its centre.
            ccx = acx if n.x + 12 <= acx <= n.x + n.w - 12 else q(n.x + n.w / 2)
            pts = [(acx, an.y + an.h), (acx, n.y)] if ccx == acx else \
                [(acx, an.y + an.h), (acx, bus), (ccx, bus), (ccx, n.y)]
            sc.edges.append(edge(f"{a.id}->{c.id}", a.id, c.id, pts, K.MANAGES,
                                 data={"kind": "manages"}))
    bottom = q(cy + row_h)
    right = max(n.x + n.w for n in sc.nodes)
    lh = legend(sc, _legend_items(model, used), MX, bottom + LEGEND_GAP,
                min(page.width - 2 * MX, max(right, 300.0)))
    right = max(n.x + n.w for n in sc.nodes)
    sc.width = q(min(page.width, right + MX)) if right + MX <= page.width else q(right + MX)
    sc.height = q(bottom + LEGEND_GAP + lh + 1)
    sc.meta = {"sites": [c.id for c in clusters], "anchors": [a.id for a in anchors]}
    return sc


def _caption(model: ServiceModel, clusters: list[SvcCluster], part: tuple[int, int]) -> str:
    head = model.title[0].upper() + model.title[1:] + (" (edited)" if model.edited else "")
    names = fmt.join_names([c.name for c in clusters][:4]) + (
        f" and {len(clusters) - 4:,} more" if len(clusters) > 4 else "")
    plane = "the NDB appliance and" if model.type == "ndb" else \
        ("Prism Central and" if model.anchors else "")
    what = {"nkp": "NKP clusters", "ndb": "the databases", "objects": "object stores",
            "files": "file servers"}[model.type]
    if part[1] > 1:
        return f"{head}, part {part[0]} of {part[1]}: {names}."
    return f"{head}: {plane} {what} on {names}.".replace(":  ", ": ")


def _layout(model: ServiceModel, page: Page | None, first_budget: Budget | None
            ) -> list[Scene]:
    page = page or Page()
    if not model.clusters:
        return []
    parts: list[list[SvcCluster]] = [[]]
    for c in model.clusters:
        cur = parts[-1]
        if cur:
            limit = page.max_h if len(parts) > 1 or first_budget is None else \
                min(page.max_h, first_budget(page.width))
            probe = _part(model, [*cur, c], page, "probe")
            if probe.width > page.width + 0.01 or probe.height > limit + 0.01:
                parts.append([])
        parts[-1].append(c)
    scenes = []
    for pi, cl in enumerate(parts):
        sid = model.type if len(parts) == 1 else f"{model.type}-{pi + 1}"
        sc = _part(model, cl, page, sid)
        sc.part = (pi + 1, len(parts))
        sc.caption = _caption(model, cl, sc.part)
        scenes.append(sc)
    return scenes


def layout(model: ServiceModel, page: Page | None = None, refs: Refs | None = None,
           first_budget: Budget | None = None) -> list[Scene]:
    """The figure's parts; part 1 under the section's summary when it fits there, else
    on a page of its own (`fit.place_first`)."""
    return place_first(lambda budget: _layout(model, page, budget), first_budget)


__all__ = ["NDB_ANCHOR", "layout"]
