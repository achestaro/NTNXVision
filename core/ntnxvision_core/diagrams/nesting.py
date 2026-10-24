"""Placed icons inside each other: the containment hierarchy and the slot rules.

The Diagrams editor lets an author put an icon *into* another one (add with a container
selected, or drop one icon on another). This module is the one place that says

* **what may contain what** (`CHILDREN`): a cloud or datacenter holds regions and zones,
  a zone holds PODs, Prism Centrals and clusters, a cluster holds nodes, a node holds its
  NICs and its CVM, a VPC its overlay subnets, a policy its category groups, ...;
* **where a child goes in its parent** (`slot`): a Prism Central sits on top of the
  clusters (``top``), a NIC is a small badge in its node's bottom-right corner
  (``badge_br``, the CVM bottom-left), storage containers and segments are full-width
  strips under the rest (``strip``), everything else flows in tidy rows (``flow``);
* **how the figure makes room** (`arrange`): a parent grows to hold its children and its
  ancestors grow with it; a collected container that receives an icon grows, and the
  generated layout around it moves aside by exactly that much (`insert_space`); a placed
  frame (a datacenter) can also wrap a collected top-level frame (the zone the snapshot
  drew), and the figure makes room for the frame's border and heading.

A placed icon stores ``parent`` (a placed icon id or a collected scene node id) and a
placed frame may store ``wraps`` (collected top-level node ids). A nested icon's own
``x`` / ``y`` are ignored while it is nested: its place follows from the rules here, so
every renderer and every export draws the same arrangement. ``w`` / ``h`` stay the
author's minimum size (a resized container), never smaller than its children need.

Everything is deterministic: same scene and items, same geometry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import text as tx
from .build import CHIP_H, Row, chip_w, measure
from .scene import Edge, Node, Scene, q

# ─── the hierarchy ─────────────────────────────────────────────────────

#: Frames: containers drawn with a heading and a chip, painted under what they hold.
FRAMES = frozenset({"datacenter", "cloud_aws", "cloud_azure", "cloud_gcp", "region", "az",
                    "pod"})
CLOUDS = ("cloud_aws", "cloud_azure", "cloud_gcp")
#: Private links and VPNs between a datacenter and a cloud.
LINKS = ("directconnect", "expressroute", "interconnect", "vpn")
#: Cloud-side services an NC2 reference architecture draws in a cloud or a region.
CLOUD_SERVICES = ("cloud_net", "transit", "flow_gateway", "nc2_portal", "cloud_storage")
_ON_PREM_NET = ("tor", "switch", "segment", "gateway", "external", "vpc")

#: Parent kind -> the kinds it may contain (icon ids; collected elements are classified
#: into the same vocabulary by `collected_kind`). The one definition of the hierarchy.
CHILDREN: dict[str, tuple[str, ...]] = {
    "datacenter": ("az", "remote", "pod", "pc", "cluster", "vcenter", "storage",
                   *_ON_PREM_NET, "policy", *LINKS, "note"),
    **{c: ("region", *CLOUD_SERVICES, *LINKS, "note") for c in CLOUDS},
    "region": ("nc2_cluster", "pc", "pod", *CLOUD_SERVICES, "note"),
    "az": ("pod", "pc", "cluster", "vcenter", "storage", *_ON_PREM_NET, "policy", "plans",
           "note"),
    "remote": ("pc", "cluster"),
    "pod": ("pc", "cluster", "nc2_cluster", "vcenter", "storage"),
    "cloud_net": ("nc2_cluster", "flow_gateway", "overlay", "segment"),
    "cluster": ("node", "bm_node", "container", "vcenter", "switch", "segment"),
    "nc2_cluster": ("bm_node", "node", "container", "switch", "segment"),
    "node": ("nic", "cvm", "bond"),
    "bm_node": ("nic", "cvm", "bond"),
    "switch": ("bond", "nic"),
    "bond": ("nic",),
    "vpc": ("overlay", "gateway", "external"),
    "policy": ("group",),
}


def can_contain(parent: str | None, child: str | None) -> bool:
    """Whether an element of kind `parent` may hold one of kind `child`."""
    return bool(parent and child) and child in CHILDREN.get(str(parent), ())


def slot(parent: str, child: str) -> str:
    """Where `child` goes inside `parent`: ``top`` (a band above the rest, full width),
    ``flow`` (rows), ``strip`` (full-width rows under the flow), ``badge_br`` /
    ``badge_bl`` (a small badge in the bottom-right / bottom-left corner)."""
    if parent in ("node", "bm_node"):
        if child == "nic":
            return "badge_br"
        if child == "cvm":
            return "badge_bl"
    if child in ("pc", "nc2_portal") and parent in FRAMES:
        return "top"
    if child in ("container", "segment", "overlay"):
        return "strip"
    if child == "switch" and parent in ("cluster", "nc2_cluster"):
        return "strip"
    return "flow"


#: Most children side by side in one row of a parent (then the row wraps).
COLS = {"datacenter": 4, "cloud_aws": 4, "cloud_azure": 4, "cloud_gcp": 4, "region": 4,
        "az": 4, "pod": 4, "cluster": 4, "nc2_cluster": 4}
DEFAULT_COLS = 3

# ─── geometry (pt) ─────────────────────────────────────────────────────

#: Inner side padding of a parent, gap between siblings, gap under a heading.
PAD = 6.0
GAP = 8.0
HEAD_GAP = 4.0
#: A NIC or CVM badge, and how far it sits in from its node's corner.
BADGE_W, BADGE_H = 30.0, 13.7
BADGE_IN = 3.0
#: Room a wrapping frame keeps around what it wraps.
WRAP_PAD = 8.0
#: How near a wrapped frame a connector end still belongs to it (an arrowhead gap).
CLEAR = 4.0
EPS = 0.05


# ─── collected elements in the hierarchy's vocabulary ──────────────────


def collected_kind(n: Node) -> str | None:
    """What a generated scene node stands for, in `CHILDREN`'s vocabulary (None: not a
    nesting target: a chip, a strip, a legend item, ...)."""
    if n.kind not in ("box", "container") or n.id.startswith(("legend:", "user:")):
        return None
    parts = n.id.split(":")
    head = parts[0]
    if head == "site":
        if len(parts) == 2:
            return "az"
        if parts[-1] == "pc":
            return "pc"
        if "pod" in parts[2:3]:
            return "pod" if n.kind == "container" else None
        return None
    if head == "az" and len(parts) == 2:
        return "remote"
    if head == "cluster":
        if len(parts) == 2:
            return "cluster"
        if parts[2] == "nodes":
            return "node"
        if parts[2] == "vcenter":
            return "vcenter"
        return None
    if head == "profile":
        if parts[-1] == "host":
            return "node"
        if "nic" in parts:
            return "nic"
        if parts[-1] == "bond":
            return "bond"
        if len(parts) == 4 and parts[2] == "vs":
            return "switch"
        if "lldp" in parts:
            return "tor"
        return None
    return {"vpc": "vpc", "ext": "external", "policy": "policy", "subnet": "segment",
            "vs": "switch", "pg": "switch"}.get(head) if len(parts) <= 3 else None


def collected_kinds(sc: Scene) -> dict[str, str]:
    """Scene node id -> kind, for every generated node that can hold an icon or be
    wrapped by one (the editor asks before it adds into a selection)."""
    out = {}
    for n in sc.nodes:
        k = collected_kind(n)
        if k:
            out[n.id] = k
    return out


# ─── the arrangement ───────────────────────────────────────────────────


@dataclass
class Box:
    """One placed icon in the nesting tree, with its resolved geometry."""

    item: dict[str, Any]
    kind: str
    #: The placed or collected parent's id (None: free on the figure).
    parent: str | None = None
    slot: str = "free"
    kids: list[Box] = field(default_factory=list)
    #: Collected top-level nodes this frame wraps.
    wraps: list[str] = field(default_factory=list)
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0
    min_w: float = 0.0
    min_h: float = 0.0
    head: float = 0.0
    #: Text at the top (a row sibling holds badges under its text).
    top_text: bool = False

    @property
    def id(self) -> str:
        return str(self.item["id"])

    @property
    def holder(self) -> bool:
        """Drawn as a parent: heading on top, its children below (a node holding only
        its corner badges keeps its own look)."""
        return bool(self.wraps) or any(not k.slot.startswith("badge") for k in self.kids)


def _label(icon: Any, item: dict[str, Any]) -> str:
    return str(item.get("label") if item.get("label") is not None else icon.name)


def text_rows(icon: Any, item: dict[str, Any], holder: bool) -> list[Row]:
    lk = icon.look
    label = _label(icon, item)
    sub = item.get("sub")
    size, bold, color = lk.title
    rows = [Row(label, size, bold, color, 1 if (lk.container or holder) else 2,
                tx.MIN_SIZE)] if label else []
    if isinstance(sub, str) and sub:
        dsize, dbold, dcolor = lk.detail
        rows.append(Row(sub, dsize, dbold, dcolor, 1 if holder else 2, tx.MIN_SIZE,
                        gap_before=0.5))
    return rows


def lead(icon: Any) -> float:
    """Room before the text of a row that starts with a chip (a VLAN segment)."""
    from . import tokens as K

    return q(tx.width("VLAN", K.SIZE_CHIP, True) + 9 + 4) if icon.id == "segment" else 0.0


def leaf_h(icon: Any, item: dict[str, Any], w: float, h: float, holder: bool = False
           ) -> float:
    """A placed icon's drawn height for a box `w` wide asked `h` tall: never shorter than
    its text (the rule `icons.draw` applies)."""
    lk = icon.look
    _, th = measure(text_rows(icon, item, holder), max(4.0, w - 2 * lk.pad_x - lead(icon)))
    return q(max(h, th + 2 * lk.pad_y))


def _head(icon: Any, item: dict[str, Any], w: float) -> float:
    lk = icon.look
    _, th = measure(text_rows(icon, item, True), max(4.0, w - 2 * lk.pad_x))
    return q(lk.pad_y + th + HEAD_GAP)


def _title_w(icon: Any, item: dict[str, Any]) -> float:
    lk = icon.look
    size, bold, _ = lk.title
    out = tx.natural(_label(icon, item), size, bold) + 2 * lk.pad_x
    if lk.chip:
        out = max(out, chip_w(lk.chip[0]) + 12)
    return q(out)


def _base(icon: Any, item: dict[str, Any]) -> tuple[float, float]:
    return float(item.get("w") or icon.w), float(item.get("h") or icon.h)


def _sections(kids: list[Box], cols: int) -> tuple[list[list[Box]], ...]:
    """The kids by slot: top band, flow rows, strips, bottom-right and bottom-left badges."""
    top = [k for k in kids if k.slot == "top"]
    flow = [k for k in kids if k.slot == "flow"]
    strip = [k for k in kids if k.slot == "strip"]
    rows = [flow[i:i + cols] for i in range(0, len(flow), cols)]
    return ([[k] for k in top], rows, [[k] for k in strip],
            [[k for k in kids if k.slot == "badge_br"]],
            [[k for k in kids if k.slot == "badge_bl"]])


def _body(kids: list[Box], cols: int) -> tuple[float, float, float]:
    """(content width, body height, badge band height) of `kids` (already measured)."""
    top, rows, strips, br, bl = _sections(kids, cols)
    lines = top + rows + strips
    width = 0.0
    for line in lines:
        width = max(width, sum(k.w for k in line) + GAP * (len(line) - 1))
    badges = br[0] + bl[0]
    if badges:
        width = max(width, sum(k.w for k in badges) + 2 * (len(badges) - 1) + 2 * BADGE_IN
                    - 2 * PAD)
    height = sum(max(k.h for k in line) for line in lines) + GAP * max(0, len(lines) - 1)
    band = (BADGE_H + BADGE_IN + 1.5) if badges else 0.0
    return q(width), q(height), band


def measure_box(b: Box, icons: dict[str, Any]) -> None:
    """Size `b` and its subtree, children first (a parent never smaller than they need)."""
    icon = icons[b.kind]
    if b.slot.startswith("badge"):
        b.w = BADGE_W
        b.h = max(BADGE_H, leaf_h(icon, b.item, BADGE_W, BADGE_H))
        b.min_w, b.min_h = b.w, b.h
        return
    for k in b.kids:
        measure_box(k, icons)
    base_w, base_h = _base(icon, b.item)
    if not b.holder:
        min_w, min_h = 24.0, 10.0
        if b.kids:  # corner badges only: a band for them under the text
            lk = icon.look
            text_w = max((tx.natural(r.text, r.size, r.bold) for r in
                          text_rows(icon, b.item, False)), default=0.0)
            min_w = q(max(text_w + 2 * lk.pad_x,
                          sum(k.w + 2 for k in b.kids) - 2 + 2 * BADGE_IN))
            _, th = measure(text_rows(icon, b.item, False),
                            max(4.0, max(base_w, min_w) - 2 * lk.pad_x))
            min_h = q(lk.pad_y + th + 2.0 + BADGE_H + BADGE_IN)
        b.w = q(max(base_w, min_w))
        b.h = q(max(leaf_h(icon, b.item, b.w, base_h), min_h))
        b.min_w, b.min_h = min_w, q(max(leaf_h(icon, b.item, b.w, 10.0), min_h))
        return
    width, body, band = _body(b.kids, COLS.get(b.kind, DEFAULT_COLS))
    need_w = q(max(width + 2 * PAD, _title_w(icon, b.item), 24.0))
    b.head = _head(icon, b.item, max(base_w, need_w))
    need_h = q(b.head + body + (PAD if body else 0.0) + band)
    if not body and not band:
        need_h = q(b.head + PAD)
    b.min_w, b.min_h = need_w, need_h
    b.w = q(max(base_w, need_w))
    b.h = q(max(base_h, need_h))


def layout_kids(kids: list[Box], kind: str, x0: float, y0: float, inner_w: float,
                right: float, bottom: float, icons: dict[str, Any]) -> None:
    """Put `kids` in their slots: sections from (x0, y0) down, `inner_w` wide; badges in
    the corners of the parent box ending at (`right`, `bottom`)."""
    top, rows, strips, br, bl = _sections(kids, COLS.get(kind, DEFAULT_COLS))
    cy = y0
    for line in top + rows + strips:
        cx = x0
        row_h = max(k.h for k in line)
        for k in line:
            if k.slot == "flow":  # siblings in a row share its height and text line
                k.top_text = any(o.kids for o in line)
                k.h = q(row_h)
            if k.slot in ("top", "strip"):
                k.w = q(max(k.w, inner_w))
                if not k.holder:
                    k.h = leaf_h(icons[k.kind], k.item, k.w, _base(icons[k.kind], k.item)[1])
            layout_box(k, cx, cy, icons)
            cx += k.w + GAP
        cy += max(k.h for k in line) + GAP
    bx = right - BADGE_IN
    for k in br[0]:
        bx -= k.w
        layout_box(k, bx, bottom - BADGE_IN - k.h, icons)
        bx -= 2.0
    bx = x0 - PAD + BADGE_IN
    for k in bl[0]:
        layout_box(k, bx, bottom - BADGE_IN - k.h, icons)
        bx += k.w + 2.0


def layout_box(b: Box, x: float, y: float, icons: dict[str, Any]) -> None:
    b.x, b.y = q(x), q(y)
    if b.kids and not b.wraps:
        layout_kids(b.kids, b.kind, b.x + PAD, b.y + b.head, b.w - 2 * PAD, b.x + b.w,
                    b.y + b.h, icons)


# ─── making room in the generated layout ───────────────────────────────


def insert_space(sc: Scene, axis: str, at: float, d: float,
                 log: list[tuple[str, float, float]] | None = None) -> None:
    """Open a band `d` wide at `at` along `axis` ("x": a vertical band, "y": a horizontal
    one): what starts at or after it moves by `d`, boxes and frames across it grow by
    `d`, connectors stretch (their points at or after it move). The orthogonal routes
    stay orthogonal and nothing that did not overlap starts to."""
    d = q(d)
    if d <= 0:
        return
    if log is not None:
        log.append((axis, q(at), d))
    horizontal = axis == "x"
    for n in sc.nodes:
        lo, size = (n.x, n.w) if horizontal else (n.y, n.h)
        if lo >= at - EPS:
            if horizontal:
                n.x = q(n.x + d)
            else:
                n.y = q(n.y + d)
        elif lo + size >= at - EPS and n.kind in ("box", "container"):
            if horizontal:
                n.w = q(n.w + d)
            else:
                n.h = q(n.h + d)
    for e in sc.edges:
        _shift_edge(e, horizontal, at, d)
    if horizontal:
        sc.width = q(sc.width + d)
    else:
        sc.height = q(sc.height + d)


def _shift_edge(e: Edge, horizontal: bool, at: float, d: float) -> None:
    if horizontal:
        e.points = [(q(x + d) if x >= at - EPS else x, y) for x, y in e.points]
    else:
        e.points = [(x, q(y + d) if y >= at - EPS else y) for x, y in e.points]
    for lab in e.labels:
        if horizontal and lab.x >= at - EPS:
            lab.x = q(lab.x + d)
        elif not horizontal and lab.y >= at - EPS:
            lab.y = q(lab.y + d)


def warp_point(warps: list[tuple[str, float, float]], x: float, y: float
               ) -> tuple[float, float]:
    """Where a point of the generated layout lands once `warps` are opened (the editor
    undoes this with the inverse, `scene.meta["warp"]`)."""
    for axis, at, d in warps:
        if axis == "x" and x >= at - EPS:
            x += d
        elif axis == "y" and y >= at - EPS:
            y += d
    return q(x), q(y)


def subtree(sc: Scene, roots: list[str]) -> list[Node]:
    """`roots` and every node whose parent chain reaches one of them."""
    by_id = {n.id: n for n in sc.nodes}
    inside = set(roots)
    out = []
    for n in sc.nodes:
        cur: str | None = n.id
        seen: set[str] = set()
        while cur is not None and cur not in inside and cur not in seen:
            seen.add(cur)
            p = by_id.get(cur)
            cur = p.parent if p is not None else None
        if cur is not None and cur in inside:
            out.append(n)
    return out


def bbox(nodes: list[Node]) -> tuple[float, float, float, float]:
    return (min(n.x for n in nodes), min(n.y for n in nodes),
            max(n.x + n.w for n in nodes), max(n.y + n.h for n in nodes))


def content_bottom(sc: Scene, node: Node) -> float:
    """Where the content of a collected container ends (its children, else its text)."""
    kids = [n for n in sc.nodes if n.parent == node.id and n.kind in ("box", "container",
                                                                        "strip", "label")]
    if kids:
        return max(n.y + n.h for n in kids)
    if node.lines:
        t = node.lines[-1]
        return q(node.y + t.baseline + tx.metrics(t.bold).descent * t.size)
    return q(node.y + 16.0)


@dataclass
class Arranged:
    """The result of `arrange`: every placed icon's box, in paint order."""

    boxes: list[Box]
    #: Top-level placed icons (the roots), for the legacy geometric nesting.
    roots: list[Box]
    #: The bands opened in the generated layout: (axis, at, width), in order.
    warps: list[tuple[str, float, float]] = field(default_factory=list)


def build_tree(sc: Scene, items: list[dict[str, Any]], icons: dict[str, Any]
               ) -> tuple[list[Box], dict[str, list[Box]]]:
    """(roots, collected parent id -> its placed children): each item under the parent
    it names when the hierarchy allows it, else free on the figure."""
    boxes = {str(i["id"]): Box(item=i, kind=str(i["icon"])) for i in items}
    nodes = {n.id: n for n in sc.nodes}
    kinds = {i: k for i, k in ((n.id, collected_kind(n)) for n in sc.nodes) if k}

    def chain_ok(b: Box, pid: str) -> bool:
        seen = {b.id}
        cur: str | None = pid
        while cur is not None and cur in boxes:
            if cur in seen:
                return False
            seen.add(cur)
            cur = boxes[cur].item.get("parent")
            cur = str(cur) if isinstance(cur, str) and cur else None
        return True

    roots: list[Box] = []
    collected: dict[str, list[Box]] = {}
    for b in boxes.values():
        pid = b.item.get("parent")
        if isinstance(pid, str) and pid in boxes and pid != b.id and \
                can_contain(boxes[pid].kind, b.kind) and chain_ok(b, pid):
            b.parent = pid
            b.slot = slot(boxes[pid].kind, b.kind)
            boxes[pid].kids.append(b)
        elif isinstance(pid, str) and pid in nodes and can_contain(kinds.get(pid), b.kind):
            b.parent = pid
            b.slot = slot(kinds[pid], b.kind)
            collected.setdefault(pid, []).append(b)
        else:
            roots.append(b)
    # A free frame may wrap collected top-level frames it can hold (each wrapped once).
    taken: set[str] = set()
    for b in roots:
        for w in b.item.get("wraps") or []:
            n = nodes.get(str(w))
            if n is not None and n.parent is None and w not in taken and \
                    can_contain(b.kind, kinds.get(n.id)):
                b.wraps.append(n.id)
                taken.add(n.id)
    return roots, collected


def arrange(sc: Scene, items: list[dict[str, Any]], icons: dict[str, Any]) -> Arranged:
    """Resolve the nesting of the placed `items` on `sc`: size and place every one, grow
    collected containers that receive icons and wrap collected frames, moving the
    generated layout aside only as much as that needs. Mutates the scene's nodes."""
    roots, collected = build_tree(sc, items, icons)
    for b in roots:
        measure_box(b, icons)
    for kids in collected.values():
        for k in kids:
            measure_box(k, icons)
    by_id = {n.id: n for n in sc.nodes}
    #: The bands opened in the generated layout, in order (`warp_point`).
    warps: list[tuple[str, float, float]] = []

    # 1. Collected containers that received icons grow (down, then right if too narrow).
    for pid, kids in collected.items():
        node = by_id[pid]
        width, body, band = _body(kids, COLS.get(collected_kind(node) or "", DEFAULT_COLS))
        if body:
            need_r = node.x + PAD + width + PAD
            insert_space(sc, "x", node.x + node.w - EPS * 2, need_r - (node.x + node.w), warps)
            need_b = content_bottom(sc, node) + GAP + body + PAD + band
        else:
            need_b = node.y + max(node.h, BADGE_H + 2 * BADGE_IN)
            need_r = node.x + sum(k.w + 2 for k in kids) + 2 * BADGE_IN
            insert_space(sc, "x", node.x + node.w - EPS * 2, need_r - (node.x + node.w), warps)
        insert_space(sc, "y", node.y + node.h - EPS * 2, need_b - (node.y + node.h), warps)

    # 2. Frames that wrap collected top-level frames: room for the border and heading.
    anchors: dict[str, tuple[str, float, float]] = {}
    for b in roots:
        if not b.wraps:
            continue
        icon = icons[b.kind]
        x0, y0, x1, y1 = bbox(subtree(sc, b.wraps))
        insert_space(sc, "x", x0 - CLEAR, WRAP_PAD, warps)
        b.head = _head(icon, b.item, max(x1 - x0, _base(icon, b.item)[0]))
        insert_space(sc, "y", y0 - CLEAR, CHIP_H / 2 + b.head, warps)
        fx, fy = x0, y0 + CHIP_H / 2
        x0, y0, x1, y1 = bbox(subtree(sc, b.wraps))
        kw, _, _ = _body([k for k in b.kids], 99)
        kids_h = max((k.h for k in b.kids), default=0.0)
        right = x1 + (GAP + kw if b.kids else 0.0) + WRAP_PAD
        bottom = max(y1, min(by_id[w].y for w in b.wraps) + kids_h) + WRAP_PAD
        base_w, base_h = _base(icon, b.item)
        need_w = max(right - fx, _title_w(icon, b.item))
        b.min_w, b.min_h = q(need_w), q(bottom - fy)
        b.w, b.h = q(max(need_w, base_w)), q(max(bottom - fy, base_h))
        insert_space(sc, "x", x1 + CLEAR, fx + b.w - x1, warps)
        insert_space(sc, "y", y1 + CLEAR, fy + b.h - y1, warps)
        anchor = by_id[b.wraps[0]]
        anchors[b.id] = (anchor.id, q(anchor.x - fx), q(anchor.y - fy))
        for w in b.wraps:
            by_id[w].parent = b.id

    # 3. Final places: free icons where the author put them, wrapping frames around
    #    what they wrap, children of collected containers in their new room.
    for b in roots:
        if b.wraps:
            aid, dx, dy = anchors[b.id]
            a = by_id[aid]
            b.x, b.y = q(a.x - dx), q(a.y - dy)
            _, _, x1, _ = bbox(subtree(sc, b.wraps))
            y0 = min(by_id[w].y for w in b.wraps)  # aligned with the wrapped frames
            cx = x1 + GAP
            for k in b.kids:
                layout_box(k, cx, y0, icons)
                cx += k.w + GAP
        else:
            # A free icon keeps its place relative to the layout around it: the bands
            # opened before it move it too (its stored place stays in the unopened one).
            fx, fy = warp_point(warps, max(0.0, float(b.item.get("x") or 0)),
                                max(0.0, float(b.item.get("y") or 0)))
            layout_box(b, fx, fy, icons)
    for pid, kids in collected.items():
        node = by_id[pid]
        y0 = content_bottom(sc, node) + GAP if any(not k.slot.startswith("badge")
                                                   for k in kids) else node.y
        layout_kids(kids, collected_kind(node) or "", node.x + PAD, y0, node.w - 2 * PAD,
                    node.x + node.w, node.y + node.h, icons)

    out: list[Box] = []

    def visit(b: Box) -> None:
        out.append(b)
        for k in b.kids:
            visit(k)

    for b in roots:
        visit(b)
    for kids in collected.values():
        for k in kids:
            visit(k)
    return Arranged(out, roots, warps)


def meta(arranged: Arranged) -> dict[str, dict[str, Any]]:
    """Per placed icon, what the editor needs: its parent, its minimum size (a resize
    never goes below what its children need) and whether its place follows collected
    content it wraps (``anchored``: it cannot be dragged, only resized)."""
    out = {}
    for b in arranged.boxes:
        out[b.id] = {"parent": b.parent, "slot": b.slot, "min_w": q(b.min_w),
                     "min_h": q(b.min_h), "anchored": bool(b.wraps),
                     "wraps": list(b.wraps), "kind": b.kind}
    return dict(sorted(out.items()))


__all__ = ["CHILDREN", "FRAMES", "LINKS", "CLOUDS", "Arranged", "Box", "arrange",
           "can_contain", "collected_kind", "collected_kinds", "insert_space", "meta",
           "slot"]
