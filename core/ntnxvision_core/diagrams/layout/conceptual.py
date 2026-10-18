"""Solution Conceptual Overview layout: availability zones side by side.

Each site (one Prism Central) is an "AZ" container holding its Prism Central block
(solid iris, services in use) and its cluster cards (hypervisor, nodes, RF, hardware,
data services, DSF storage strip, external arrays when collected). The cluster hosting
the PC comes first with a HOSTS PC chip.

Links are drawn only where the data has them:

* between adjacent sites, in a labelled gutter: AZ pairing (dashed, at the PC block)
  and replication (solid, weight by the strongest type, labelled with type and RPO);
* to availability zones outside the part (remote AZs, or a site drawn in another part):
  in a dashed column on the right when every such link starts in the right-most site
  ("side" mode), else as buses dropping to a row of dashed boxes under the sites
  ("below" mode), one channel per target, ports sorted so channels do not cross;
* between non-adjacent sites of a part, through the same below channels.

Scaling (text never shrinks below 6 pt; content splits instead):

* parts of up to 3 sites, linked sites kept together;
* per part, a density ladder: full cards in 1..N columns; then, per site, the cluster
  hosting PC plus the largest clusters by node count and a "+N more clusters" card that
  points at the clusters table.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .. import fmt
from .. import text as tx
from .. import tokens as K
from ..build import (
    CHIP_H,
    Row,
    box,
    chip,
    edge,
    edge_label,
    label_block,
    legend,
    measure,
    place_lines,
    text_h,
)
from ..model import Card, ConceptualModel, Link, Remote, Site
from ..scene import Node, Scene, Style, q

# Geometry (pt).
MX = 0.5  # outer margin (half a stroke)
TOP = 5.0  # first container top (room for the corner chips)
P = 6.0  # site padding
GAP = 13.5  # between unlinked sites
GUT = 82.0  # labelled gutter between linked sites
SIDE_GUT = 60.0  # gutter before the remote column
REMOTE_W = 84.0  # remote column width (side mode)
BELOW_W = 172.0  # remote box width (below mode)
MIN_SITE_W = 140.0
CARD_W = 135.0
CARD_MIN_W = 120.0
CG = 6.0  # card column gap
RG = 4.5  # card row gap
CHANNEL = 12.0  # below-mode channel pitch
PER_PART = 3


@dataclass(frozen=True)
class Page:
    width: float = 672.0
    max_h: float = 380.0


@dataclass(frozen=True)
class Refs:
    """Cross references the figure text points at (filled in once tables are numbered)."""

    clusters_table: str = "the clusters table"


@dataclass
class _Target:
    """Something outside the drawn sites that a part links to."""

    id: str
    name: str
    sub: str
    kind_line: str
    note: str
    chip: str


@dataclass
class _Plan:
    sites: list[Site]
    targets: list[_Target] = field(default_factory=list)
    #: (site id, target id, links) for every link leaving the drawn sites
    external: list[tuple[str, str, list[Link]]] = field(default_factory=list)
    #: (left id, right id, links) for linked adjacent sites
    gutter: list[tuple[str, str, list[Link]]] = field(default_factory=list)
    #: (site id, site id, links) for linked non-adjacent sites of the part
    inner: list[tuple[str, str, list[Link]]] = field(default_factory=list)
    side: bool = False


# ─── parts ─────────────────────────────────────────────────────────────


def plan_parts(model: ConceptualModel, per: int = PER_PART) -> list[list[Site]]:
    """Group sites into parts of up to `per`, keeping linked sites together."""
    ids = [s.id for s in model.sites]
    adj: dict[str, set[str]] = {i: set() for i in ids}
    for lk in model.links:
        if lk.a in adj and lk.b in adj:
            adj[lk.a].add(lk.b)
            adj[lk.b].add(lk.a)
    seen: set[str] = set()
    comps: list[list[str]] = []
    for sid in ids:  # components in model order, members in model order
        if sid in seen:
            continue
        found: set[str] = set()
        stack = [sid]
        while stack:
            node = stack.pop()
            if node in found:
                continue
            found.add(node)
            stack.extend(adj[node] - found)
        seen |= found
        comps.append([i for i in ids if i in found])
    parts: list[list[str]] = []
    cur: list[str] = []
    for comp in comps:
        if len(comp) > per:
            if cur:
                parts.append(cur)
                cur = []
            chunks = [comp[i:i + per] for i in range(0, len(comp), per)]
            parts.extend(chunks[:-1])
            cur = list(chunks[-1])
        elif len(cur) + len(comp) <= per:
            cur.extend(comp)
        else:
            parts.append(cur)
            cur = list(comp)
    if cur:
        parts.append(cur)
    by_id = {s.id: s for s in model.sites}
    return [[by_id[i] for i in p] for p in parts] or [[]]


def _plan(model: ConceptualModel, sites: list[Site], parts: list[list[Site]],
          page: Page) -> _Plan:
    plan = _Plan(sites=sites)
    pos = {s.id: i for i, s in enumerate(sites)}
    part_of = {s.id: pi for pi, p in enumerate(parts) for s in p}
    groups: dict[tuple[str, str], list[Link]] = {}
    for lk in model.links:
        a_in, b_in = lk.a in pos, lk.b in pos
        if not (a_in or b_in):
            continue
        if a_in and b_in:
            key = (lk.a, lk.b) if pos[lk.a] < pos[lk.b] else (lk.b, lk.a)
        else:
            key = (lk.a, lk.b) if a_in else (lk.b, lk.a)
        groups.setdefault(key, []).append(lk)
    targets: dict[str, _Target] = {}
    for (x, y), lks in groups.items():
        if y in pos:
            if pos[y] - pos[x] == 1:
                plan.gutter.append((x, y, lks))
            else:
                plan.inner.append((x, y, lks))
            continue
        plan.external.append((x, y, lks))
        if y not in targets:
            targets[y] = _target(model, y, part_of, len(parts))
    order = {r.id: i for i, r in enumerate(model.remotes)}
    plan.targets = sorted(targets.values(),
                          key=lambda t: (t.id.startswith("az:"), order.get(t.id, -1), t.id))
    plan.external.sort(key=lambda e: (pos[e[0]], [t.id for t in plan.targets].index(e[1])))
    if plan.targets and not plan.inner and all(x == sites[-1].id for x, _, _ in plan.external):
        gaps = _gaps(plan)
        avail = page.width - 2 * MX - sum(gaps) - SIDE_GUT - REMOTE_W
        plan.side = avail / len(sites) >= MIN_SITE_W
    return plan


def _target(model: ConceptualModel, tid: str, part_of: dict[str, int], n_parts: int
            ) -> _Target:
    r = model.remote(tid)
    if r is not None:
        return _remote_target(r)
    s = model.site(tid)
    name = s.name if s else tid
    part = part_of.get(tid, 0) + 1
    return _Target(id=tid, name=name, sub="Availability zone in this inventory",
                   kind_line="Prism Central", note=f"drawn in part {part} of {n_parts}",
                   chip="AZ")


def _remote_target(r: Remote) -> _Target:
    if r.kind == "xi":
        sub, kind = "Nutanix Cloud (Xi)", "Nutanix Cloud"
    else:
        sub, kind = "Physical AZ", "Prism Central"
    if r.region:
        sub += f", {r.region}"
    return _Target(id=r.id, name=r.name, sub=sub, kind_line=kind,
                   note="not in this inventory", chip="REMOTE AZ")


def _gaps(plan: _Plan) -> list[float]:
    linked = {(x, y) for x, y, _ in plan.gutter}
    ids = [s.id for s in plan.sites]
    return [GUT if (a, b) in linked else GAP for a, b in zip(ids, ids[1:], strict=False)]


# ─── text of the boxes ─────────────────────────────────────────────────


def _site_rows(s: Site) -> list[Row]:
    cards = [c for c in s.cards if c.kind == "cluster"]
    nodes = sum(c.nodes or 0 for c in cards)
    what = "Availability zone" if s.kind == "pc" else "Prism Element"
    return [Row(s.name, K.SIZE_SITE, True, K.INK, 2, 7.5),
            Row(f"{what}, {fmt.plural(len(cards), 'cluster')}, {fmt.plural(nodes, 'node')}",
                K.SIZE_SMALL, False, K.INK_3, 2, gap_before=0.5)]


def _pc_rows(s: Site) -> list[Row]:
    pc = s.pc
    assert pc is not None
    vms = pc.vms or 1
    shape = "single VM" if vms == 1 else f"scale-out, {vms:,} PCVMs"
    line1 = ", ".join(x for x in (pc.version, shape) if x)
    sizing = []
    if pc.size:
        sizing.append(pc.size)
    each = vms > 1 and (pc.vcpus or 0) % vms == 0 and (pc.mem_gib or 0) % vms == 0
    if pc.vcpus:
        sizing.append(f"{(pc.vcpus // vms if each else pc.vcpus):,} vCPU")
    if pc.mem_gib:
        sizing.append(f"{(pc.mem_gib // vms if each else pc.mem_gib):,} GiB")
    line2 = ", ".join(sizing) + ((" each" if each else " total") if vms > 1 and sizing else "")
    rows = [Row("Prism Central", K.SIZE_PC_TITLE, True, K.WHITE),
            Row(line1, K.SIZE_BODY, False, K.WHITE, 2, gap_before=0.5)]
    if line2:
        rows.append(Row(line2, K.SIZE_SMALL, False, K.WHITE, 2))
    if pc.services:
        rows.append(Row(" · ".join(f"{v.label} ({v.count:,})" for v in pc.services),
                        K.SIZE_SMALL, False, K.IRIS_TINT, 2))
    return rows


def _pills(labels: list[str]) -> list[tuple[str, float]]:
    return [(lab, q(tx.width(lab, K.SIZE_SMALL, True) + 7)) for lab in labels]


PILL_H = 8.5
STRIP_H = 8.6
PILL_GAP = 2.5


def _pill_rows(pills: list[tuple[str, float]], inner: float) -> list[list[tuple[str, float]]]:
    """Pills flowed into rows within `inner` (used when they do not sit on the title)."""
    rows: list[list[tuple[str, float]]] = [[]]
    used = 0.0
    for lab, pw in pills:
        pw = min(pw, inner)
        if rows[-1] and used + PILL_GAP + pw > inner:
            rows.append([])
            used = 0.0
        used += (PILL_GAP if rows[-1] else 0.0) + pw
        rows[-1].append((lab, pw))
    return [r for r in rows if r]


def _card_parts(c: Card, w: float) -> tuple[list[Row], list[Row], bool, float]:
    """(title rows, body rows, pills on the title row, title width) for card `c`."""
    inner = w - 10
    pills = _pills(c.services)
    pills_w = sum(pw for _, pw in pills) + 2.5 * max(0, len(pills) - 1)
    inline = bool(pills) and pills_w <= inner * 0.5
    if inline:  # a name that would lose characters beside the pills keeps the full width
        _, fitted = tx.fit(c.name, K.SIZE_CARD_TITLE, True, inner - pills_w - 4, 2, 6.5)
        inline = not fitted[-1].endswith(tx.ELLIPSIS)
    title_w = inner - (pills_w + 4 if inline else 0)
    if c.kind != "cluster":
        return ([Row(c.name, K.SIZE_CARD_TITLE, True, K.INK, 2, 6.5)],
                [Row(c.note, K.SIZE_SMALL, False, K.INK_3, 2)] if c.note else [], inline,
                title_w)
    return ([Row(c.name, K.SIZE_CARD_TITLE, True, K.INK, 2, 6.5)],
            [Row(fmt.card_body(c), K.SIZE_BODY, False, K.INK_2, 2)], inline, title_w)


def _card_h(c: Card, w: float) -> float:
    title, body, inline, title_w = _card_parts(c, w)
    h = 5.0 + text_h(title, title_w) + text_h(body, w - 10)
    if c.services and not inline:
        h += len(_pill_rows(_pills(c.services), w - 10)) * (PILL_H + 2)
    if c.kind == "cluster" and (fmt.storage_text(c) or c.external):
        h += 2.5 + STRIP_H
    return q(h + 4.0)


def _more_rows(hidden: list[Card], refs: Refs) -> list[Row]:
    nodes = sum(c.nodes or 0 for c in hidden)
    return [Row(f"+{fmt.plural(len(hidden), 'more cluster')}", K.SIZE_CARD_TITLE, True,
                K.INK_2),
            Row(f"{fmt.plural(nodes, 'node')} ({fmt.hypervisor_mix(hidden)}), listed in "
                f"{refs.clusters_table}", K.SIZE_SMALL, False, K.INK_3, 2, gap_before=0.5)]


# ─── one part ──────────────────────────────────────────────────────────


@dataclass
class _Try:
    cols: int
    keep: int | None  # None = every card; else cards kept per site (+ a summary card)


def _visible(s: Site, keep: int | None) -> tuple[list[Card], list[Card]]:
    if keep is None or len(s.cards) <= keep + 1:
        return list(s.cards), []
    chosen = [c for c in s.cards if c.hosts_pc][:keep]
    rest = sorted((c for c in s.cards if c not in chosen),
                  key=lambda c: (-(c.nodes or 0), s.cards.index(c)))
    chosen += rest[: keep - len(chosen)]
    ids = {c.id for c in chosen}
    return [c for c in s.cards if c.id in ids], [c for c in s.cards if c.id not in ids]


def _ladder(plan: _Plan, max_cols: int) -> list[_Try]:
    tries = [_Try(c, None) for c in range(1, max_cols + 1)]
    most = max((len(s.cards) for s in plan.sites), default=0)
    if most > 4:
        for c in range(max_cols, 1, -1):
            for r in range(math.ceil(most / c) - 1, 1, -1):
                tries.append(_Try(c, c * r - 1))
        # The PC host and the 2 largest clusters; when even that is too tall (long names
        # on two lines, several remote rows), fewer cards rather than smaller text.
        tries.extend([_Try(1, 3), _Try(1, 2), _Try(1, 1)])
    return tries


def _layout_part(model: ConceptualModel, plan: _Plan, page: Page, refs: Refs,
                 attempt: _Try, sid: str) -> Scene:
    sites = plan.sites
    n = max(1, len(sites))
    gaps = _gaps(plan)
    side_w = (SIDE_GUT + REMOTE_W) if plan.side else 0.0
    avail = page.width - 2 * MX - sum(gaps) - side_w
    c = attempt.cols
    site_w = q(min(avail / n, 2 * P + c * CARD_W + (c - 1) * CG))
    inner_w = site_w - 2 * P
    card_w = q((inner_w - (c - 1) * CG) / c)

    sc = Scene(id=sid, type="conceptual", title=model.title, caption="", width=page.width,
               height=0)
    vis = {s.id: _visible(s, attempt.keep) for s in sites}

    header_h = max((text_h(_site_rows(s), inner_w) for s in sites), default=0.0)
    has_pc = any(s.pc for s in sites)
    pc_h = max((text_h(_pc_rows(s), inner_w - 10) + 10 for s in sites if s.pc), default=0.0)
    card_h = 30.0
    for s in sites:
        shown, hidden = vis[s.id]
        for cd in shown:
            card_h = max(card_h, _card_h(cd, card_w))
        if hidden:
            card_h = max(card_h, text_h(_more_rows(hidden, refs), card_w - 10) + 8)
    card_h = q(card_h)

    top = TOP
    head_top = top + CHIP_H / 2 + 4.5
    pc_y = q(head_top + header_h + 5)
    cards_y = q(pc_y + pc_h + 11) if has_pc else q(head_top + header_h + 7)

    site_nodes: dict[str, Node] = {}
    pc_nodes: dict[str, Node] = {}
    first_cards: dict[str, Node] = {}
    drawn: list[str] = []
    summarised: list[str] = []
    bottoms = []
    x = MX
    used = {"more": False, "strip": False, "external": False, "user": False}
    for i, s in enumerate(sites):
        cont = box(s.id, "container", x, top, site_w, _site_rows(s), K.SITE, pad_x=P,
                   pad_y=head_top - top, h=10, data=dict(s.data))
        sc.nodes.append(cont)
        sc.nodes.append(chip(f"{s.id}:tag", "AZ" if s.kind == "pc" else "PRISM ELEMENT",
                             x + 5.5, top, K.TAG, parent=s.id))
        site_nodes[s.id] = cont
        if s.pc:
            pcn = box(s.pc.id, "box", x + P, pc_y, inner_w, _pc_rows(s), K.PC, pad_x=5,
                      pad_y=5, h=pc_h, valign="middle", parent=s.id,
                      data={"prism_central": s.name})
            sc.nodes.append(pcn)
            pc_nodes[s.id] = pcn
        shown, hidden = vis[s.id]
        k = len(shown) + (1 if hidden else 0)
        rows_n = max(1, math.ceil(k / c))
        per_row = math.ceil(k / rows_n) if k else 1
        row_tops: list[Node] = []
        for j in range(k):
            r, col = divmod(j, per_row)
            in_row = min(per_row, k - r * per_row)
            gx = x + P + (inner_w - (in_row * card_w + (in_row - 1) * CG)) / 2
            cx, cy = q(gx + col * (card_w + CG)), q(cards_y + r * (card_h + RG))
            if j < len(shown):
                cd = shown[j]
                node = _card(sc, s, cd, cx, cy, card_w, card_h, used)
                drawn.append(cd.id)
            else:
                node = box(f"{s.id}:more", "box", cx, cy, card_w, _more_rows(hidden, refs),
                           K.MORE, pad_x=5, h=card_h, valign="middle", parent=s.id,
                           data={"clusters": str(len(hidden))})
                sc.nodes.append(node)
                summarised.extend(cd.id for cd in hidden)
                used["more"] = True
            if r == 0:
                row_tops.append(node)
        if row_tops:
            first_cards[s.id] = row_tops[0]
        rows_bottom = cards_y + rows_n * card_h + (rows_n - 1) * RG if k else cards_y - 4
        bottoms.append(rows_bottom + P + 1.5)
        if s.id in pc_nodes:
            pcn = pc_nodes[s.id]
            px, py = pcn.x + pcn.w / 2, pcn.y + pcn.h
            bus = py + 5.5
            for node in row_tops:
                tx_ = node.x + node.w / 2
                pts = ([(px, py), (px, node.y)] if abs(tx_ - px) < 0.5
                       else [(px, py), (px, bus), (tx_, bus), (tx_, node.y)])
                sc.edges.append(edge(f"{s.pc.id if s.pc else s.id}->{node.id}", pcn.id,
                                     node.id, pts, K.MANAGES, arrow_end=True,
                                     data={"kind": "manages"}))
        x = x + site_w + (gaps[i] if i < len(gaps) else 0)
    site_bottom = q(max(bottoms, default=top + 40))
    for nd in site_nodes.values():
        nd.h = q(site_bottom - nd.y)

    kinds = {"pairing": False, "replication": False}
    content_bottom = site_bottom

    # Adjacent sites: the labelled gutter.
    for a, b, lks in plan.gutter:
        na, nb = site_nodes[a], site_nodes[b]
        gx0, gx1 = na.x + na.w, nb.x
        gc = (gx0 + gx1) / 2
        y_p = _pair_y(pc_nodes.get(a), na)
        pairing = [lk for lk in lks if lk.kind == "pairing"]
        repls = [lk for lk in lks if lk.kind == "replication"]
        if pairing:
            kinds["pairing"] = True
            e = edge(pairing[0].id, a, b, [(gx0 + 0.5, y_p), (gx1 - 0.5, y_p)], K.PAIRING,
                     arrow_start=True, arrow_end=True, data={"kind": "pairing"})
            e.labels.append(edge_label(["AZ pairing"], gc, y_p - 5.5, gx1 - gx0 - 6,
                                       background=False))
            sc.edges.append(e)
        for lk in repls[:1]:
            kinds["replication"] = True
            lines = fmt.repl_lines(lk)
            _, _, lh = label_block(lines, gx1 - gx0 - 6)
            card = first_cards.get(a)
            base = card.y + card.h / 2 if card else y_p + 20
            y_r = q(max(base, y_p + 7 + lh / 2 + 2))
            fwd = lk.a == a
            e = edge(lk.id, lk.a, lk.b, [(gx0 + 0.5, y_r), (gx1 - 0.5, y_r)]
                     if fwd else [(gx1 - 0.5, y_r), (gx0 + 0.5, y_r)], _repl_style(lk),
                     arrow_end=True, arrow_start=lk.both, data={"kind": "replication"})
            e.labels.append(edge_label(lines, gc, y_r, gx1 - gx0 - 6))
            if lk.both:
                e.labels.append(edge_label(["both directions"], gc, y_r + lh / 2 + 6.5,
                                           gx1 - gx0 - 4, color=K.INK_3, background=False))
            sc.edges.append(e)

    # Links leaving the drawn sites.
    if plan.targets:
        if plan.side:
            content_bottom = max(content_bottom, _side(sc, plan, site_nodes, pc_nodes,
                                                       first_cards, kinds))
        else:
            content_bottom = max(content_bottom, _below(sc, plan, site_nodes, page, kinds))
    elif plan.inner:
        content_bottom = max(content_bottom, _below(sc, plan, site_nodes, page, kinds))

    items: list[tuple[str, Style, str]] = []
    if has_pc:
        items.append(("box", K.PC, "Prism Central"))
    items.append(("box", K.CLUSTER, "Cluster (Prism Element)"))
    if used["strip"]:
        items.append(("box", K.STORAGE, "DSF storage"))
    if used["external"]:
        items.append(("box", K.EXTERNAL, "External storage"))
    if plan.targets:
        items.append(("box", K.REMOTE, "Outside this part" if all(
            not t.id.startswith("az:") for t in plan.targets) else "Outside this inventory"))
    if used["more"]:
        items.append(("box", K.MORE, "Summary card"))
    if used["user"]:
        items.append(("box", K.USER, "User-added (not collected)"))
    if kinds["replication"]:
        items.append(("line", K.LEGEND_REPL, "Replication"))
    if kinds["pairing"]:
        items.append(("line", K.PAIRING, "AZ pairing"))
    if has_pc:
        items.append(("line", K.MANAGES, "Manages"))
    content_w = max((nd.x + nd.w for nd in sc.nodes), default=200.0)
    lg_w = min(page.width - 2 * MX, max(content_w, 300.0))
    lh = legend(sc, items, MX, content_bottom + 9, lg_w)
    right = max(max(nd.x + nd.w for nd in sc.nodes),
                max((max(p[0] for p in e.points) for e in sc.edges), default=0.0),
                max((lb.x + lb.w for e in sc.edges for lb in e.labels), default=0.0))
    sc.width = q(min(page.width, right + MX))
    sc.height = q(content_bottom + 9 + lh + 1)
    sc.meta = {"drawn": drawn, "summarised": summarised,
               "sites": [s.id for s in sites]}
    return sc


def _pair_y(pcn: Node | None, site: Node) -> float:
    if pcn is not None:
        return q(pcn.y + pcn.h / 2)
    return q(site.y + 18)


def _repl_style(lk: Link) -> Style:
    return {"sync": K.REPL_SYNC, "nearsync": K.REPL_NEARSYNC}.get(fmt.repl_strength(lk),
                                                                  K.REPL_ASYNC)


def _card(sc: Scene, s: Site, cd: Card, x: float, y: float, w: float, h: float,
          used: dict[str, bool]) -> Node:
    title, body, inline, title_w = _card_parts(cd, w)
    is_cluster = cd.kind == "cluster"
    mt, th = measure(title, title_w)
    mb, bh = measure(body, w - 10)
    node = box(cd.id, "box", x, y, w, [], K.CLUSTER if is_cluster else K.USER, pad_x=5,
               h=h, parent=s.id, data=dict(cd.data))
    node.lines = (place_lines(mt, w, 5.0, "left", 5.0)
                  + place_lines(mb, w, 5.0 + th, "left", 5.0))
    sc.nodes.append(node)
    if cd.hosts_pc:
        sc.nodes.append(chip(f"{cd.id}:tag", "HOSTS PC", x + 5, y, K.TAG_ROLE, parent=cd.id))
    if not is_cluster:
        used["user"] = True
        sc.nodes.append(chip(f"{cd.id}:tag", "USER-ADDED", x + 5, y, K.TAG_USER,
                             parent=cd.id))
    pills = _pills(cd.services)
    if pills:
        if inline:
            rows = [pills]
            x0 = x + w - 5 - (sum(pw for _, pw in pills) + PILL_GAP * (len(pills) - 1))
            py = y + 5 + (tx.line_height(K.SIZE_CARD_TITLE) - PILL_H) / 2
        else:
            rows = _pill_rows(pills, w - 10)
            x0 = x + 5
            py = y + 5 + th + bh + 2
        for row in rows:
            px = x0
            for lab, pw in row:
                sc.nodes.append(box(f"{cd.id}:svc:{lab}", "pill", px, py, pw,
                                    [Row(lab, K.SIZE_SMALL, True, K.IRIS)], K.PILL, pad_x=3,
                                    pad_y=0, h=PILL_H, valign="middle", parent=cd.id))
                px += pw + PILL_GAP
            py += PILL_H + 2
    st = fmt.storage_text(cd) if is_cluster else ""
    if is_cluster and (st or cd.external):
        sy = y + h - 4 - STRIP_H
        sx, sw = x + 5, w - 10
        ext_nodes = []
        for xa in reversed(cd.external):
            label = f"{xa.product.split()[-1]}, {xa.containers:,} ctr"
            ew = q(min(tx.width(label, K.SIZE_SMALL, True) + 7, sw * 0.6))
            ext_nodes.append((xa, label, sx + sw - ew, ew))
            sw = q(sw - ew - 2.5)
        if st:
            used["strip"] = True
            sc.nodes.append(box(f"{cd.id}:storage", "strip", sx, sy, sw,
                                [Row(st, K.SIZE_SMALL, False, K.LIVE)], K.STORAGE, pad_x=3,
                                pad_y=0, h=STRIP_H, valign="middle", parent=cd.id))
        for xa, label, ex, ew in reversed(ext_nodes):
            used["external"] = True
            sc.nodes.append(box(xa.id, "strip", ex, sy, ew,
                                [Row(label, K.SIZE_SMALL, True, K.WHITE)], K.EXTERNAL,
                                pad_x=3, pad_y=0, h=STRIP_H, valign="middle", parent=cd.id,
                                data={"product": xa.product, **({"name": xa.name}
                                                                if xa.name else {})}))
    return node


def _target_rows(t: _Target, below: bool) -> list[Row]:
    if below:
        return [Row(t.name, K.SIZE_REMOTE_TITLE, True, K.INK, 1, 6.5),
                Row(f"{t.sub} · {t.note}", K.SIZE_SMALL, False, K.INK_2, 2,
                    gap_before=0.5)]
    return [Row(t.name, K.SIZE_REMOTE_TITLE, True, K.INK, 2, 6.5),
            Row(t.sub, K.SIZE_SMALL, False, K.INK_2, 2, gap_before=0.5),
            Row(t.kind_line, K.SIZE_SMALL, False, K.INK_3, 1),
            Row(t.note, K.SIZE_SMALL, False, K.INK_3, 2)]


def _target_style(t: _Target) -> Style:
    return K.REMOTE


def _side(sc: Scene, plan: _Plan, site_nodes: dict[str, Node], pc_nodes: dict[str, Node],
          first_cards: dict[str, Node], kinds: dict[str, bool]) -> float:
    src = site_nodes[plan.sites[-1].id]
    x0 = src.x + src.w
    bx = q(x0 + SIDE_GUT)
    pcn = pc_nodes.get(src.id)
    y = pcn.y if pcn else src.y + 18
    by_target = {t: lks for _, t, lks in plan.external}
    for t in plan.targets:
        lks = by_target.get(t.id, [])
        pairing = [lk for lk in lks if lk.kind == "pairing"]
        repls = [lk for lk in lks if lk.kind == "replication"]
        rows = _target_rows(t, below=False)
        th = text_h(rows, REMOTE_W - 8)
        lines = fmt.repl_lines(repls[0]) if repls else []
        _, _, lh = label_block(lines, SIDE_GUT - 6) if lines else (None, 0.0, 0.0)
        y_p = q((pcn.y + pcn.h / 2) if (pcn and y == pcn.y) else y + 14)
        if pairing and repls:
            card = first_cards.get(src.id)
            base = card.y + card.h / 2 if (card and y == (pcn.y if pcn else -1)) else 0
            y_r = q(max(base, y_p + 7 + lh + 6))
        else:
            y_r = y_p if not pairing else y_p
        need = max(th + CHIP_H / 2 + 4.5 + 6, (y_r - y) + 12)
        node = box(t.id, "box", bx, y, REMOTE_W, rows, _target_style(t), pad_x=4,
                   pad_y=CHIP_H / 2 + 4.5, h=q(need), data={"availability_zone": t.name})
        sc.nodes.append(node)
        sc.nodes.append(chip(f"{t.id}:tag", t.chip, bx + 5.5, y, K.TAG_MUTED, parent=t.id))
        if pairing:
            kinds["pairing"] = True
            e = edge(pairing[0].id, src.id, t.id, [(x0 + 0.5, y_p), (bx - 0.5, y_p)],
                     K.PAIRING, arrow_start=True, arrow_end=True, data={"kind": "pairing"})
            e.labels.append(edge_label(["AZ pairing"], x0 + SIDE_GUT / 2, y_p - 5.5,
                                       SIDE_GUT - 4, background=False))
            sc.edges.append(e)
        for lk in repls[:1]:
            kinds["replication"] = True
            fwd = lk.a == src.id
            pts = [(x0 + 0.5, y_r), (bx - 0.5, y_r)]
            e = edge(lk.id, lk.a, lk.b, pts if fwd else pts[::-1], _repl_style(lk),
                     arrow_end=True, arrow_start=lk.both, data={"kind": "replication"})
            e.labels.append(edge_label(lines, x0 + SIDE_GUT / 2, y_r - lh / 2 - 2.5,
                                       SIDE_GUT - 4))
            sc.edges.append(e)
        y = q(node.y + node.h + 12)
    return y - 12


def _below(sc: Scene, plan: _Plan, site_nodes: dict[str, Node], page: Page,
           kinds: dict[str, bool]) -> float:
    """Buses under the sites: one channel per (target, kind); remote boxes in a row."""
    site_bottom = max(n.y + n.h for n in site_nodes.values())
    # Groups: (target id, kind, [(source site id, link)])
    groups: list[tuple[str, str, list[tuple[str, Link]], bool]] = []
    by_target: dict[str, list[tuple[str, list[Link]]]] = {}
    for x, t, lks in plan.external:
        by_target.setdefault(t, []).append((x, lks))
    for t in [tg.id for tg in plan.targets]:
        by_src = by_target.get(t, [])
        rep_g: list[tuple[str, Link]] = [(x, lk) for x, lks in by_src for lk in lks
                                         if lk.kind == "replication"]
        rep_src = {x for x, _ in rep_g}
        pair_g: list[tuple[str, Link]] = [(x, lk) for x, lks in by_src for lk in lks
                                          if lk.kind == "pairing" and x not in rep_src]
        if rep_g:
            groups.append((t, "replication", rep_g, False))
        if pair_g:
            groups.append((t, "pairing", pair_g, False))
    for x, y, lks in plan.inner:
        rep_links = [lk for lk in lks if lk.kind == "replication"]
        chosen = rep_links[0] if rep_links else lks[0]
        groups.append((y, chosen.kind, [(x, chosen)], True))

    def centre(sid: str) -> float:
        n = site_nodes[sid]
        return n.x + n.w / 2

    def proxy_x(g: tuple[str, str, list[tuple[str, Link]], bool]) -> float:
        if g[3]:
            return centre(g[0])
        return sum(centre(x) for x, _ in g[2]) / len(g[2])

    groups.sort(key=lambda g: (proxy_x(g), g[1] != "replication", g[0]))
    # Ports along each site's bottom, in group order (so channels do not cross).
    ports: dict[str, list[tuple[int, str]]] = {}
    for gi, g in enumerate(groups):
        for x, _ in g[2]:
            ports.setdefault(x, []).append((gi, "src"))
        if g[3]:
            ports.setdefault(g[0], []).append((gi, "dst"))
    port_x: dict[tuple[int, str, str], float] = {}
    for sid, lst in ports.items():
        n = site_nodes[sid]
        m = len(lst)
        for i, (gi, role) in enumerate(lst):
            px = n.x + n.w / 2 if m == 1 else n.x + n.w * (0.2 + 0.6 * i / (m - 1))
            port_x[(gi, sid, role)] = q(px)

    chan0 = site_bottom + 12
    remote_groups = [g for g in groups if not g[3]]
    n_ch = len(groups)
    row_y = q(chan0 + CHANNEL * (n_ch - 1) + 14) if remote_groups else 0.0
    # Remote boxes, centred under their sources, de-overlapped.
    targets = {t.id: t for t in plan.targets}
    want: dict[str, list[float]] = {}
    for gi, g in enumerate(groups):
        if not g[3]:
            want.setdefault(g[0], []).extend(port_x[(gi, x, "src")] for x, _ in g[2])
    order = sorted(want, key=lambda t: (sum(want[t]) / len(want[t]), t))
    xs = _deoverlap([sum(want[t]) / len(want[t]) - BELOW_W / 2 for t in order], BELOW_W, 12,
                    MX, page.width - MX)
    boxes: dict[str, Node] = {}
    for t, bx in zip(order, xs, strict=True):
        tg = targets[t]
        node = box(t, "box", bx, row_y, BELOW_W, _target_rows(tg, below=True), K.REMOTE,
                   pad_x=5, pad_y=CHIP_H / 2 + 4, data={"availability_zone": tg.name})
        sc.nodes.append(node)
        sc.nodes.append(chip(f"{t}:tag", tg.chip, bx + 5.5, row_y, K.TAG_MUTED, parent=t))
        boxes[t] = node
    if boxes:
        hmax = max(b.h for b in boxes.values())
        for b in boxes.values():
            b.h = hmax
    # Target ports on each box top, spread when several groups land on one box.
    into: dict[str, list[int]] = {}
    for gi, g in enumerate(groups):
        if not g[3]:
            into.setdefault(g[0], []).append(gi)
    for gi, g in enumerate(groups):
        t, kind, members, inner = g
        cy = q(chan0 + CHANNEL * gi)
        if inner:
            tx_ = port_x[(gi, t, "dst")]
            ty = site_bottom
        else:
            b = boxes[t]
            k, m = into[t].index(gi), len(into[t])
            tx_ = q(b.x + b.w * (k + 1) / (m + 1))
            ty = b.y
        style = K.PAIRING if kind == "pairing" else _repl_style(members[0][1])
        kinds[kind] = True
        lines = (["AZ pairing"] if kind == "pairing"
                 else list(dict.fromkeys(ln for _, m in members for ln in fmt.repl_lines(m))))
        if len(lines) > 3:
            lines = [", ".join(lines[:2]), f"+{len(lines) - 2:,} more"]
        if kind == "replication" and any(m.both for _, m in members):
            lines.append("both directions")
        for si, (x, lk) in enumerate(members):
            px = port_x[(gi, x, "src")]
            pts = [(px, site_bottom), (px, cy), (tx_, cy), (tx_, ty)]
            if abs(px - tx_) < 0.5:
                pts = [(px, site_bottom), (tx_, ty)]
            fwd = lk.a == x or lk.kind == "pairing"
            e = edge(f"{lk.id}|{x}" if len(members) > 1 else lk.id, x, t,
                     pts if fwd else pts[::-1], style, arrow_end=fwd or lk.both,
                     arrow_start=(not fwd) or (inner and (lk.both or kind == "pairing")),
                     data={"kind": kind})
            if si == 0:
                text = [", ".join(lines)]
                _, lw, lh = label_block(text, 200)
                lx = tx_ + 5 + lw / 2
                if lx + lw / 2 > page.width - MX:
                    lx = tx_ - 5 - lw / 2
                e.labels.append(edge_label(text, lx, cy, 200))
            sc.edges.append(e)
    bottom = max([b.y + b.h for b in boxes.values()] +
                 [chan0 + CHANNEL * (n_ch - 1) + 6])
    return q(bottom)


def _deoverlap(xs: list[float], w: float, gap: float, lo: float, hi: float) -> list[float]:
    out: list[float] = []
    for x in xs:
        x = max(lo, x)
        if out and x < out[-1] + w + gap:
            x = out[-1] + w + gap
        out.append(x)
    over = (out[-1] + w) - hi if out else 0
    if over > 0:
        out = [max(lo, x - over) for x in out]
        for i in range(1, len(out)):
            out[i] = max(out[i], out[i - 1] + w + gap)
    return [q(x) for x in out]


# ─── entry point ───────────────────────────────────────────────────────


def _caption(sites: list[Site], part: tuple[int, int]) -> str:
    cards = [c for s in sites for c in s.cards if c.kind == "cluster"]
    nodes = sum(c.nodes or 0 for c in cards)
    if part[1] > 1:
        from ..facts import short_name

        names = fmt.join_names([short_name(s.name) for s in sites])
        return (f"Solution conceptual overview, part {part[0]} of {part[1]}: {names} "
                f"({fmt.plural(len(cards), 'cluster')}, {fmt.plural(nodes, 'node')}).")
    if sites and sites[0].kind == "pe":
        return (f"Solution conceptual overview: Prism Element {sites[0].name}, "
                f"{fmt.plural(len(cards), 'cluster')}, {fmt.plural(nodes, 'node')}.")
    return (f"Solution conceptual overview: {fmt.plural(len(sites), 'availability zone')}, "
            f"{fmt.plural(len(cards), 'cluster')}, {fmt.plural(nodes, 'node')}.")


def layout(model: ConceptualModel, page: Page | None = None,
           refs: Refs | None = None) -> list[Scene]:
    """Scenes (parts) for the model; each fits `page.width` and, whenever the density
    ladder allows, `page.max_h`."""
    page = page or Page()
    refs = refs or Refs()
    parts = plan_parts(model)
    scenes: list[Scene] = []
    for pi, sites in enumerate(parts):
        plan = _plan(model, sites, parts, page)
        gaps = _gaps(plan)
        side_w = (SIDE_GUT + REMOTE_W) if plan.side else 0.0
        avail = (page.width - 2 * MX - sum(gaps) - side_w) / max(1, len(sites))
        max_cols = max(1, int((avail - 2 * P + CG) // (CARD_MIN_W + CG)))
        sc: Scene | None = None
        sid = "conceptual" if len(parts) == 1 else f"conceptual-{pi + 1}"
        for attempt in _ladder(plan, max_cols):
            sc = _layout_part(model, plan, page, refs, attempt, sid)
            if sc.height <= page.max_h:
                break
        assert sc is not None
        sc.part = (pi + 1, len(parts))
        sc.caption = _caption(sites, sc.part)
        scenes.append(sc)
    return scenes
