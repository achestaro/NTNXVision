"""Logical Cluster Design layout (A2): one availability zone per diagram.

The site container holds POD containers (only when the clusters carry a POD category),
each a grid of cluster cards: name and data-service pills, hypervisor / AOS / nodes / RF,
the fault domain, a NODES box (hardware groups, blocks and racks, CVM sizing), the
Prism Central role, and the user storage containers as chips (teal tint on DSF, solid
teal on an external array). External arrays sit in a row under the PODs, each joined to
its containers by a connector routed through the card gutters.

Width: 480 pt (design summary beside, decision 3) for up to 4 clusters, else 672 pt with
three columns. A site that does not fit the height budget continues in parts, cut
between grid rows (a POD can continue in the next part); text never shrinks below 6 pt.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .. import fmt
from .. import text as tx
from .. import tokens as K
from ..build import CHIP_H, LEGEND_GAP, Row, box, chip, chip_w, edge, legend, measure, place_lines
from ..facts import short_name
from ..logical import (
    Array,
    LCluster,
    LogicalModel,
    LSite,
    Pod,
    array_lines,
    array_title,
    blocks_text,
    container_flags,
    cvm_text,
    fault_text,
    group_lines,
    headline,
    pc_role_text,
    site_line,
)
from ..scene import Node, Scene, q
from .conceptual import Budget, Page, Refs
from .fit import place_first

MX = 0.5
TOP = 5.0
P = 6.0  # site padding
PP = 6.0  # POD padding
CG = 8.0  # card column gap
RG = 8.0  # card row gap (room for chips straddling a card's top edge)
POD_GAP = 12.0
NARROW = 480.0
WIDE = 672.0
NARROW_MAX_CLUSTERS = 4
ARRAY_W = 250.0
ARRAY_MIN_W = 200.0
ARRAY_GAP = 10.0
CHIPS_MAX = 6
#: Container chips a compact card keeps (the rest: "+N more in the table").
CHIPS_COMPACT = 3
#: Title, body and box text sizes (pt): one step above the overview, which packs whole
#: estates; this figure draws one availability zone.
SIZE_TITLE = 8.0
SIZE_LINE = 6.6
SIZE_NOTE = 6.3
SIZE_GROUP = 6.8


def _cols(site: LSite, page: Page) -> tuple[float, int]:
    n = len(site.clusters)
    if n <= NARROW_MAX_CLUSTERS or page.width < WIDE:
        return min(NARROW, page.width), 2
    return WIDE, 3


# ─── one cluster card ──────────────────────────────────────────────────


def _pills(labels: list[str]) -> list[tuple[str, float]]:
    return [(lab, q(tx.width(lab, K.SIZE_SMALL, True) + 7)) for lab in labels]


#: Second line of a component the author added in the Diagrams editor.
NOT_COLLECTED = "Added by the author, not collected"


def _card(c: LCluster, x: float, y: float, w: float, parent: str, compact: bool = False,
          arrays: dict[str, str] | None = None) -> tuple[Node, list[Node], dict[str, Node]]:
    """(card, children, container chip per container id); the card's height follows
    its content. A compact card (the density step for a site that would otherwise need
    more parts) folds the NODES box into plain lines and drops the containers label."""
    inner = w - 10
    arrays = arrays or {}
    if c.summary:
        node = box(c.id, "box", x, y, w,
                   [Row(f"+{fmt.plural(c.summary, 'more cluster')}", SIZE_TITLE, True,
                        K.INK_2),
                    Row(f"{fmt.plural(c.nodes or 0, 'node')} in {c.hypervisor} clusters, "
                        "listed in the registered clusters and containers tables", SIZE_NOTE,
                        False,
                        K.INK_3, 3, gap_before=1.0)],
                   K.MORE, pad_x=5, pad_y=5, align="left", parent=parent,
                   data={"clusters": str(c.summary)})
        return node, [], {}
    if c.kind != "cluster":  # added in the Diagrams editor: a component or a note
        rows = ([Row(c.name, SIZE_LINE, False, K.INK_2, 4, tx.MIN_SIZE)] if c.kind == "note"
                else [Row(c.name, SIZE_TITLE, True, K.INK, 2, 6.5),
                      Row(c.note or NOT_COLLECTED, SIZE_NOTE, False, K.INK_3, 3,
                          gap_before=1.0)])
        node = box(c.id, "box", x, y, w, rows, K.NOTE if c.kind == "note" else K.USER,
                   pad_x=5, pad_y=5.5, align="left", parent=parent,
                   data=dict(sorted(c.data.items())))
        return node, [chip(f"{c.id}:tag", "USER-ADDED", x + 5, y, K.TAG_USER,
                           parent=c.id)], {}
    pills = _pills(c.services)
    pills_w = sum(pw for _, pw in pills) + 2.5 * max(0, len(pills) - 1)
    inline = bool(pills) and pills_w <= inner * 0.45
    title_w = inner - (pills_w + 4 if inline else 0)
    mt, th = measure([Row(c.name, SIZE_TITLE, True, K.INK, 2, 6.5)], title_w)
    body = [Row(headline(c), SIZE_LINE, False, K.INK_2, 2, gap_before=0.5)]
    if ft := fault_text(c):
        body.append(Row(ft, SIZE_NOTE, False, K.INK_3, 2))
    if c.note:  # a line the author added to a collected cluster
        body.append(Row(c.note, SIZE_NOTE, False, K.INK_3, 2))
    mb, bh = measure(body, inner)
    card = Node(id=c.id, kind="box", x=q(x), y=q(y), w=q(w), h=0, style=K.CLUSTER,
                parent=parent, data=dict(sorted(c.data.items())))
    card.lines = place_lines(mt, w, 5.0, "left", 5.0) + place_lines(mb, w, 5.0 + th,
                                                                   "left", 5.0)
    kids: list[Node] = []
    if c.hosts_pc:
        kids.append(chip(f"{c.id}:tag", "HOSTS PC", x + 5, y, K.TAG_ROLE, parent=c.id))
    cy = y + 5.0 + th + bh
    if pills:
        if inline:
            px = x + w - 5 - pills_w
            py = y + 5 + (tx.line_height(SIZE_TITLE) - 8.5) / 2
        else:
            px, py = x + 5, cy + 2
            cy += 10.5
        for lab, pw in pills:
            pw = min(pw, inner)
            kids.append(box(f"{c.id}:svc:{lab}", "pill", px, py, pw,
                            [Row(lab, K.SIZE_SMALL, True, K.IRIS)], K.PILL, pad_x=3, pad_y=0,
                            h=8.5, valign="middle", parent=c.id))
            px += pw + 2.5
    rows = [Row("NODES", K.SIZE_SMALL, True, K.INK_3)]
    for g in c.groups[:2]:
        first, spec = group_lines(g)
        rows.append(Row(first, SIZE_GROUP, True, K.INK, 2, gap_before=1.0))
        if spec:
            rows.append(Row(spec, SIZE_NOTE, False, K.INK_2, 2))
        if g.gpus:
            rows.append(Row(f"GPU: {g.gpus} per node", SIZE_NOTE, False, K.INK_2, 2))
        if g.maintenance:
            rows.append(Row(f"{fmt.plural(g.maintenance, 'node')} in maintenance", SIZE_NOTE,
                            False, K.INK_3, 1))
        if g.note:
            rows.append(Row(g.note, SIZE_NOTE, False, K.INK_3, 2))
    if len(c.groups) > 2:
        rows.append(Row(f"+{fmt.plural(len(c.groups) - 2, 'more node type')}, listed in "
                        "the hosts table", SIZE_NOTE, False, K.INK_3, 2))
    if bt := blocks_text(c):
        rows.append(Row(bt[0].upper() + bt[1:], SIZE_NOTE, False, K.INK_2, 2,
                        gap_before=1.0))
    if ct := cvm_text(c):
        rows.append(Row(ct, SIZE_NOTE, False, K.INK_2, 2, gap_before=0.0 if bt else 1.0))
    if compact:
        rows = []
        for g in c.groups[:2]:
            first, spec = group_lines(g)
            extra = [x for x in (spec, f"GPU: {g.gpus}" if g.gpus else None,
                                 f"{g.maintenance:,} in maintenance" if g.maintenance
                                 else None) if x]
            rows.append(Row(", ".join([first, *extra]), SIZE_NOTE, False, K.INK_2, 2))
        if len(c.groups) > 2:
            rows.append(Row(f"+{fmt.plural(len(c.groups) - 2, 'more node type')}",
                            SIZE_NOTE, False, K.INK_3))
        extra = [t for t in (cvm_text(c), blocks_text(c)) if t]
        if extra:
            rows.append(Row(" · ".join(extra), SIZE_NOTE, False, K.INK_3, 2))
        if rows:
            nb = box(f"{c.id}:nodes", "label", x + 5, cy + 1, inner, rows, K.LABEL,
                     pad_x=0, pad_y=0, align="left", parent=c.id)
            kids.append(nb)
            cy = nb.y + nb.h
    elif len(rows) > 1:
        cy += 3
        nb = box(f"{c.id}:nodes", "box", x + 5, cy, inner, rows, K.NODES_BOX, pad_x=4,
                 pad_y=3, align="left", parent=c.id)
        kids.append(nb)
        cy = nb.y + nb.h
    if role := pc_role_text(c):
        cy += 3
        st = box(f"{c.id}:role", "strip", x + 5, cy, inner,
                 [Row(role, K.SIZE_BODY, True, K.IRIS, 2)], K.ROLE_STRIP, pad_x=4,
                 pad_y=1.6, align="left", parent=c.id)
        kids.append(st)
        cy = st.y + st.h
    chips: dict[str, Node] = {}
    if c.containers:
        cy += 4 if not compact else 3
        if not compact:
            head = box(f"{c.id}:ctr-label", "label", x + 5, cy, inner,
                       [Row("CONTAINERS (USER)", K.SIZE_SMALL, True, K.INK_3)], K.LABEL,
                       pad_x=0, pad_y=0, align="left")
            kids.append(head)
            cy = head.y + head.h + 1.5
        # Containers on an external array are always drawn (their connector shows
        # where they live); the others fill the remaining chips.
        cap = CHIPS_COMPACT if compact else CHIPS_MAX
        on_array = [ct for ct in c.containers if ct.array]
        dsf = [ct for ct in c.containers if not ct.array][:max(0, cap - len(on_array))]
        shown = dsf + on_array
        items: list[tuple[str, str, bool]] = [(ct.id,
                                               container_flags(ct, arrays.get(ct.array or "")),
                                               ct.array is not None)
                                              for ct in shown]
        if len(c.containers) > len(shown):
            items.append((f"{c.id}:ctr-more",
                          f"+{len(c.containers) - len(shown):,} more in the table", False))
        # Chips flowed into rows; a name too long for the card wraps (at its hyphens
        # first) rather than being shortened, so the drawn name is the collected one.
        names = {ct.id: ct.name for ct in shown}
        cx, row_bottom = x + 5, cy
        for cid, text, ext in items:
            bold = ext
            cw = min(inner, q(tx.width(text, K.SIZE_SMALL, bold) + 7))
            if cx > x + 5 and cx + cw > x + 5 + inner:
                cx = x + 5
                cy = row_bottom + 2.5
            more = cid.endswith(":ctr-more")
            style = K.EXTERNAL if ext else (K.MORE_CHIP if more else K.STORAGE)
            color = K.WHITE if ext else (K.INK_3 if more else K.LIVE)
            lines = 2 if compact else 3
            node = box(cid, "strip", cx, cy, cw, [Row(text, K.SIZE_SMALL, bold, color, lines)],
                       style, pad_x=3, pad_y=1.4, align="left", valign="middle",
                       parent=c.id, data={"name": names[cid]} if cid in names else None)
            kids.append(node)
            if ext:
                chips[cid] = node
            cx += cw + 3
            row_bottom = max(row_bottom, node.y + node.h)
        cy = row_bottom
    card.h = q(cy + 5 - y)
    return card, kids, chips


# ─── grid lines: PODs side by side ─────────────────────────────────────


@dataclass
class _Seg:
    """A POD's clusters on one grid line (a POD longer than a line continues)."""

    pod: Pod
    clusters: list[LCluster]
    first: bool  # the POD's first segment


def _lines(site: LSite, cols: int) -> list[list[_Seg]]:
    """Grid lines of up to `cols` cards. With PODs, small PODs share a line side by
    side; a POD wider than a line is cut into consecutive segments."""
    lines: list[list[_Seg]] = []
    if not site.has_pods:
        cl = site.clusters
        pod = site.pods[0] if site.pods else Pod(id=f"{site.id}:pod:-", name=None)
        return [[_Seg(pod, cl[i:i + cols], i == 0)] for i in range(0, len(cl), cols)]
    cur: list[_Seg] = []
    used = 0
    for pod in site.pods:
        for i in range(0, len(pod.clusters), cols):
            chunk = pod.clusters[i:i + cols]
            if cur and used + len(chunk) > cols:
                lines.append(cur)
                cur, used = [], 0
            cur.append(_Seg(pod, chunk, i == 0))
            used += len(chunk)
    if cur:
        lines.append(cur)
    return lines


def _card_w(inner: float, cols: int, pods_on: bool, line: list[_Seg] | None = None
            ) -> float:
    """Card width on a grid line. A full line fills the width; any other line uses the
    width of the tightest full line (`cols` PODs of one card), so cards line up."""
    if not pods_on:
        return q((inner - (cols - 1) * CG) / cols)
    k = sum(len(s.clusters) for s in line) if line else 0
    n = len(line) if line and k == cols else cols
    return q((inner - n * 2 * PP - (cols - 1) * CG) / cols)


# ─── one part ──────────────────────────────────────────────────────────


def _array_rows(a: Array) -> list[Row]:
    rows = [Row(array_title(a), K.SIZE_PC_TITLE, True, K.WHITE, 2, 6.5)]
    rows += [Row(t, K.SIZE_SMALL, False, K.LIVE_TINT, 2) for t in array_lines(a)]
    return rows


def _caption(site: LSite, part: tuple[int, int], pods: list[str], drawn: list[LCluster],
             arrays: bool, edited: bool = False) -> str:
    what = []
    if site.has_pods:
        what.append("PODs")
    what.append("nodes")
    if any(c.cvm_vcpus for c in site.clusters):
        what.append("CVMs")
    if any(c.containers for c in site.clusters):
        what.append("containers")
    if arrays:
        what.append("external storage")
    body = ", ".join(what[:-1]) + (" and " if len(what) > 1 else "") + what[-1]
    head = ("Logical cluster design" + (" (edited)" if edited else "")
            + f", {short_name(site.name)}")
    if part[1] > 1:
        scope = (fmt.join_names([f"POD {p}" for p in pods]) if pods else
                 fmt.join_names([c.name for c in drawn]) if len(drawn) <= 3 else
                 fmt.plural(len(drawn), "cluster"))
        return f"{head}, part {part[0]} of {part[1]}: {scope}."
    return f"{head}: {body}."


def _layout_part(site: LSite, lines: list[list[_Seg]], width: float, cols: int, sid: str,
                 compact: bool = False, whole: LSite | None = None) -> Scene:
    """One part of `site`; `whole` is the AZ as collected when `site` is its summarised
    form (the header counts every cluster)."""
    sc = Scene(id=sid, type="logical", title="Logical cluster design", caption="",
               width=width, height=0)
    site_w = q(width - 2 * MX)
    inner = site_w - 2 * P
    head_rows = [Row(site.name, K.SIZE_SITE, True, K.INK, 2, 7.5),
                 Row(site_line(whole or site), K.SIZE_SMALL, False, K.INK_3, 2,
                     gap_before=0.5)]
    head_top = TOP + CHIP_H / 2 + 4.0
    _, hh = measure(head_rows, inner)
    cont = box(site.id, "container", MX, TOP, site_w, head_rows, K.SITE, pad_x=P,
               pad_y=head_top - TOP, align="left", h=10, data=dict(site.data))
    sc.nodes.append(cont)
    sc.nodes.append(chip(f"{site.id}:tag", "AZ" if site.kind == "pc" else "PRISM ELEMENT",
                         MX + 5.5, TOP, K.TAG, parent=site.id))
    pods_on = site.has_pods
    names = {a.id: array_title(a) for a in site.arrays}
    pod_head = q(3 + tx.line_height(K.SIZE_SMALL) + 7) if pods_on else 0.0
    # ctr id -> (chip, card, (left gutter x, right gutter x))
    chips: dict[str, tuple[Node, Node, tuple[float, float]]] = {}
    drawn: list[LCluster] = []
    pods_drawn: list[str] = []
    used = {"dsf": False, "ext": False}
    summarised_n = 0
    y = q(head_top + hh + 9)
    for line in lines:
        card_w = _card_w(inner, cols, pods_on, line)
        # Card height per line, so cards (and PODs) on a line align.
        row_h = max(_card(c, 0, 0, card_w, "", compact, names)[0].h
                    for seg in line for c in seg.clusters)
        x = MX + P
        for seg in line:
            k = len(seg.clusters)
            parent = site.id
            gx = x
            if pods_on and seg.pod.id.endswith(MORE_POD):
                gx = x + PP  # the summary card stands outside any POD
            elif pods_on:
                seg_w = q(k * card_w + (k - 1) * CG + 2 * PP)
                pid = seg.pod.id if seg.first else f"{seg.pod.id}:{seg.clusters[0].id}"
                pod_node = Node(id=pid, kind="container", x=q(x), y=y, w=seg_w,
                                h=q(pod_head + row_h + PP), style=K.POD, parent=site.id,
                                data={"name": seg.pod.name or "No POD category",
                                      **({"pod": seg.pod.name} if seg.pod.name else {})})
                sc.nodes.append(pod_node)
                label = (f"POD {seg.pod.name}" if seg.pod.name else "NO POD CATEGORY") + \
                    ("" if seg.first else " (CONTINUED)")
                sc.nodes.append(chip(f"{pid}:tag", label, pod_node.x + 5, y,
                                     K.TAG_POD if seg.pod.name else K.TAG_MUTED,
                                     parent=pid))
                note = f"from category POD: {seg.pod.name}" if seg.pod.name else ""
                nw = q(tx.width(note, K.SIZE_SMALL) + 1)
                if note and seg.first and nw <= seg_w - 2 * PP - chip_w(label) - 6:
                    sc.nodes.append(box(f"{pid}:note", "label", x + seg_w - PP - nw, y + 3,
                                        nw, [Row(note, K.SIZE_SMALL, False, K.INK_3)],
                                        K.LABEL, pad_x=0, pad_y=0, align="left",
                                        parent=pid))
                if seg.first and seg.pod.name:
                    pods_drawn.append(seg.pod.id)
                parent, gx = pid, x + PP
            cy = q(y + pod_head)
            for col, c in enumerate(seg.clusters):
                cx = q(gx + col * (card_w + CG))
                card, kids, cmap = _card(c, cx, cy, card_w, parent, compact, names)
                card.h = q(row_h)
                sc.nodes.append(card)
                sc.nodes.extend(kids)
                if c.summary:
                    summarised_n += c.summary
                else:
                    drawn.append(c)
                edge_pad = PP / 2 if pods_on else P / 2
                right = q(cx + card_w + (CG / 2 if col < k - 1 else edge_pad))
                left = q(cx - (CG / 2 if col > 0 else edge_pad))
                for ctr_id, node in cmap.items():
                    chips[ctr_id] = (node, card, (left, right))
                used["dsf"] |= any(ct.array is None for ct in c.containers)
                used["ext"] |= any(ct.array is not None for ct in c.containers)
            x = q(x + (k * card_w + (k - 1) * CG + 2 * PP if pods_on else 0) + CG)
        y = q(y + pod_head + row_h + (PP if pods_on else 0) + (POD_GAP if pods_on else RG))
    y = q(y - (POD_GAP if pods_on else RG))
    # External arrays of the drawn clusters, in rows under the grid.
    ids = {c.id for c in drawn}
    arrays = [a for a in site.arrays if a.cluster in ids]
    arr_nodes: dict[str, Node] = {}
    if arrays:
        y = q(y + 16)
        # Up to three arrays side by side, at least ARRAY_MIN_W wide each.
        per_row = max(1, min(len(arrays), int((inner + ARRAY_GAP) // (ARRAY_MIN_W + ARRAY_GAP))))
        aw = q(min(ARRAY_W if per_row == 1 or len(arrays) == 1 else inner,
                   (inner - (per_row - 1) * ARRAY_GAP) / per_row))
        heights = [box("probe", "box", 0, 0, aw, _array_rows(a), K.EXTERNAL, pad_x=6,
                       pad_y=CHIP_H / 2 + 4).h for a in arrays]
        ay = y
        for start in range(0, len(arrays), per_row):
            row = arrays[start:start + per_row]
            rh = max(heights[start:start + per_row])
            for col, a in enumerate(row):
                ax = q(MX + P + col * (aw + ARRAY_GAP))
                node = box(a.id, "box", ax, ay, aw, _array_rows(a), K.EXTERNAL, pad_x=6,
                           pad_y=CHIP_H / 2 + 4, align="left", h=rh, parent=site.id,
                           data=dict(sorted(a.data.items())))
                sc.nodes.append(node)
                sc.nodes.append(box(f"{a.id}:tag", "tag", ax + 6, ay - CHIP_H / 2,
                                    chip_w("EXTERNAL STORAGE"),
                                    [Row("EXTERNAL STORAGE", K.SIZE_CHIP, True, K.LIVE)],
                                    K.TAG_EXTERNAL, pad_x=3.5, pad_y=0, h=CHIP_H,
                                    valign="middle", parent=a.id))
                arr_nodes[a.id] = node
            ay = q(ay + rh + 14)
        y = q(max(n.y + n.h for n in arr_nodes.values()))
    cont.h = q(y + P + 1.5 - cont.y)
    _array_edges(sc, site, chips, arr_nodes)

    items = [("box", K.CLUSTER, "Cluster")]
    if pods_on:
        items.append(("box", K.POD, "POD (from a category)"))
    if used["dsf"]:
        items.append(("box", K.STORAGE, "DSF container"))
    if used["ext"] or arr_nodes:
        items.append(("box", K.EXTERNAL, "External storage"))
    if sc.edges:
        items.append(("line", K.ARRAY_LINK, "Container on array"))
    if summarised_n:
        items.append(("box", K.MORE, "Summary card"))
    bottom = cont.y + cont.h
    lh = legend(sc, items, MX, bottom + LEGEND_GAP, site_w)
    sc.height = q(bottom + LEGEND_GAP + lh + 1)
    sc.meta = {"group": site.id, "group_name": site.name,
               "drawn": [c.id for c in drawn], "pods": pods_drawn}
    return sc


def _array_edges(sc: Scene, site: LSite,
                 chips: dict[str, tuple[Node, Node, tuple[float, float]]],
                 arrays: dict[str, Node]) -> None:
    """Container chip -> its array: down out of the card, along the gap under it, down
    the gutter on the array's side, across a channel above the arrays, into the
    array."""
    by_ctr = {ct.id: ct.array for c in site.clusters for ct in c.containers if ct.array}
    links = [(cid, by_ctr[cid]) for cid in chips if by_ctr.get(cid) in arrays]
    if not links:
        return
    into: dict[str, list[str]] = {}
    for cid, aid in links:
        into.setdefault(aid, []).append(cid)
    top = min(a.y for a in arrays.values())
    for k, (cid, aid) in enumerate(links):
        node, card, (left, right) = chips[cid]
        a = arrays[aid]
        gutter = left if a.x + a.w / 2 < card.x + card.w / 2 else right
        cx = q(node.x + node.w / 2)
        y1 = q(card.y + card.h + 2.5)
        ych = q(top - 8 - 3 * k)
        m = into[aid]
        tx_ = q(a.x + a.w * (0.5 + 0.12 * (m.index(cid) - (len(m) - 1) / 2)))
        pts = [(cx, node.y + node.h), (cx, y1), (gutter, y1), (gutter, ych), (tx_, ych),
               (tx_, a.y)]
        sc.edges.append(edge(f"link:array:{cid}->{aid}", cid, aid, _clean(pts), K.ARRAY_LINK,
                             arrow_end=True, data={"kind": "array"}))


def _clean(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Drop repeated points and merge collinear runs."""
    out: list[tuple[float, float]] = []
    for p in pts:
        p = (q(p[0]), q(p[1]))
        if out and p == out[-1]:
            continue
        if len(out) >= 2 and ((out[-2][0] == out[-1][0] == p[0])
                              or (out[-2][1] == out[-1][1] == p[1])):
            out[-1] = p
            continue
        out.append(p)
    return out


# ─── entry point ───────────────────────────────────────────────────────


MORE_POD = ":pod:more"


def _summarised(site: LSite, cols: int) -> LSite:
    """`site` with the cluster hosting Prism Central and the largest clusters by node
    count (decision 7), and one "+N more clusters" card for the rest."""
    keep_n = 2 * cols - 1
    every = site.clusters
    collected = [c for c in every if c.kind == "cluster"]  # what the author added stays
    if len(collected) <= keep_n + 1:
        return site
    chosen = [c for c in collected if c.hosts_pc][:keep_n]
    rest = sorted((c for c in collected if c not in chosen),
                  key=lambda c: (-(c.nodes or 0), every.index(c)))
    chosen += rest[:keep_n - len(chosen)]
    ids = {c.id for c in chosen} | {c.id for c in every if c.kind != "cluster"}
    hidden = [c for c in every if c.id not in ids]
    mix = Counter(c.hypervisor or "N/A" for c in hidden)
    more = LCluster(id=f"{site.id}:more", name="", summary=len(hidden),
                    nodes=sum(c.nodes or 0 for c in hidden),
                    hypervisor=fmt.join_names([f"{n:,} {h}" for h, n in
                                               sorted(mix.items(),
                                                      key=lambda kv: (-kv[1], kv[0]))]))
    pods = [Pod(id=p.id, name=p.name, clusters=[c for c in p.clusters if c.id in ids])
            for p in site.pods]
    pods = [p for p in pods if p.clusters]
    pods.append(Pod(id=f"{site.id}{MORE_POD}", name=None, clusters=[more]))
    return LSite(id=site.id, name=site.name, kind=site.kind, pods=pods,
                 arrays=[a for a in site.arrays if a.cluster in ids], pc_vms=site.pc_vms,
                 data=site.data)


#: Detail levels (one per estate): every card in full, compact cards, compact cards
#: with the PC host and the largest clusters plus a "+N more clusters" card per AZ.
FULL, COMPACT, SUMMARY = 0, 1, 2
#: Parts per AZ beyond which the summary level is used.
MAX_PARTS = 2


class _Site:
    """One site's layout at a given detail level, split into parts."""

    def __init__(self, site: LSite, page: Page, first_budget: Budget | None,
                 edited: bool = False) -> None:
        self.site, self.page, self.first_budget = site, page, first_budget
        self.edited = edited
        self.width, self.cols = _cols(site, page)
        self.sid = f"logical-{site.name}"

    def limit(self, first: bool) -> float:
        if first and self.first_budget is not None:
            return min(self.page.max_h, self.first_budget(self.width))
        return self.page.max_h

    def split(self, site: LSite, compact: bool) -> list[list[list[_Seg]]]:
        parts: list[list[list[_Seg]]] = []
        cur: list[list[_Seg]] = []
        for line in _lines(site, self.cols):
            trial = [*cur, line]
            if cur and _layout_part(site, trial, self.width, self.cols, self.sid,
                                    compact).height > self.limit(not parts):
                parts.append(cur)
                cur = [line]
            else:
                cur = trial
        parts.append(cur)
        return parts

    def over(self, site: LSite, parts: list[list[list[_Seg]]], compact: bool) -> bool:
        return any(_layout_part(site, p, self.width, self.cols, self.sid, compact).height
                   > self.limit(i == 0) for i, p in enumerate(parts))

    def level(self) -> int:
        """The least detail this site needs: compact when that saves a part or a single
        line of full cards is too tall; the summary when compact still needs more than
        `MAX_PARTS` parts."""
        full = self.split(self.site, False)
        if len(full) == 1 and not self.over(self.site, full, False):
            return FULL
        tight = self.split(self.site, True)
        if len(tight) > MAX_PARTS:
            return SUMMARY
        if len(tight) < len(full) or (self.over(self.site, full, False)
                                      and not self.over(self.site, tight, True)):
            return COMPACT
        return FULL

    def scenes(self, level: int) -> list[Scene]:
        site = _summarised(self.site, self.cols) if level == SUMMARY else self.site
        compact = level >= COMPACT
        parts = self.split(site, compact)
        out = []
        for i, part in enumerate(parts):
            sc = _layout_part(site, part, self.width, self.cols,
                              self.sid if len(parts) == 1 else f"{self.sid}-{i + 1}",
                              compact, self.site)
            sc.part = (i + 1, len(parts))
            drawn_ids = set(sc.meta["drawn"])
            drawn = [c for c in site.clusters if c.id in drawn_ids]
            pods = list(dict.fromkeys(s.pod.name for line in part for s in line
                                      if s.pod.name))
            sc.caption = _caption(self.site, sc.part, pods, drawn,
                                  any(a.cluster in drawn_ids for a in site.arrays),
                                  self.edited)
            shown = {c.id for c in site.clusters}
            sc.meta["summarised"] = [c.id for c in self.site.clusters if c.id not in shown] \
                if i == len(parts) - 1 else []
            out.append(sc)
        return out


def layout_site(site: LSite, page: Page | None = None, first_budget: Budget | None = None,
                level: int | None = None, edited: bool = False) -> list[Scene]:
    """One site's parts at `level` (default: the least detail it needs): grid lines are
    added while the part fits its height budget (the first part also fits
    `first_budget`); a line that does not starts a part."""
    pg = page or Page()

    def run(budget: Budget | None) -> list[Scene]:
        s = _Site(site, pg, budget, edited)
        return s.scenes(s.level() if level is None else level)

    # Part 1 under the section's summary, else on a page of its own.
    return place_first(run, first_budget)


def layout(model: LogicalModel, page: Page | None = None, refs: Refs | None = None,
           first_budget: Budget | None = None, *, site: str | None = None,
           level: int | None = None) -> list[Scene]:
    """Scenes for every site, in model order, at ONE detail level for the estate (the
    least detail any of its sites needs), so like AZs are drawn alike. Each site opens
    its own report section, so each site's first part fits `first_budget`.

    With `site`, only that site's parts (at `level`, default its own): the report lays
    out one section per availability zone."""
    page = page or Page()
    if site is not None:
        one = next((s for s in model.sites if s.id == site), None)
        return [] if one is None else layout_site(one, page, first_budget, level,
                                                  model.edited)
    sites = [_Site(s, page, first_budget, model.edited) for s in model.sites]
    level = max((s.level() for s in sites), default=FULL)
    return [sc for s in sites for sc in s.scenes(level)]


def site_level(site: LSite, page: Page | None = None,
               first_budget: Budget | None = None) -> int:
    """The least detail `site` needs (`FULL`, `COMPACT` or `SUMMARY`)."""
    return _Site(site, page or Page(), first_budget).level()


__all__ = ["COMPACT", "FULL", "SUMMARY", "site_level", "layout", "layout_site"]
