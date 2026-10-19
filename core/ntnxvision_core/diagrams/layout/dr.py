"""Disaster recovery design layout (A5): availability zones as columns of clusters.

Each zone (one Prism Central) is a column: its clusters as cards, protected first (a
cluster no protection policy names is greyed; more than two collapse into one card).
Flows are drawn where the data has them, per direction, stroke weight by the strongest
type (Sync heaviest):

* between adjacent zones of a part: across the gutter, card to card (the right column's
  cards ordered by their partners' rows, so most edges run straight; the rest bend
  through their own lane), labelled "NearSync 1 min" / "Async 1 h, both ways"; a
  "2 RECOVERY PLANS" chip tops the gutter;
* inside one zone (Sync / Metro between two of its clusters): a bracket on the column's
  left, its label on the bracket;
* to a zone outside the inventory, a zone drawn in another part, or a non-adjacent zone
  of the part: down a lane on the column's right, along a channel under the columns,
  into a dashed box (or up into the zone), one label per (zone, target) on the channel.

Scaling: parts of up to 3 zones, linked zones kept together; a part that does not fit
its height gives its last linked group (or last zone) to a new part; a zone that alone
does not fit collapses every unprotected cluster and drops the card details. Text
never shrinks below 6 pt.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass, field

from .. import fmt
from .. import text as tx
from .. import tokens as K
from ..build import (
    CHIP_H,
    LabelText,
    Row,
    box,
    chip,
    chip_w,
    edge,
    edge_label,
    label_block,
    legend,
    text_h,
)
from ..dr import (
    DrCluster,
    DrFlow,
    DrModel,
    DrSite,
    cluster_line,
    flow_text,
    mutual,
    protected_line,
    remote_cluster_line,
)
from ..facts import short_name
from ..model import Repl
from ..scene import Node, Scene, Style, q
from . import parts as parts_mod
from .conceptual import Page, Refs, _deoverlap

MX = 0.5
TOP = 5.0
P = 6.0
GAP = 14.0
GUT_MIN, GUT_MAX = 64.0, 118.0
#: A gutter with bent edges: labels, then lanes.
GUT_BENT_MAX = 170.0
BEND_LANE = 8.0
MAX_COL = 230.0
CARD_GAP = 7.0
LANE = 5.0  # pitch of the down lanes on a column's right
BOX_W = 132.0
PER_PART = 3
MAX_UNPROTECTED = 2

Budget = Callable[[float], float]


# ─── plan ──────────────────────────────────────────────────────────────


@dataclass
class _Item:
    """A card in a column: a cluster, or the "+N without a policy" summary."""

    id: str
    cluster: DrCluster | None
    hidden: list[DrCluster] = field(default_factory=list)


@dataclass
class _Target:
    id: str
    name: str
    lines: list[tuple[str, str]]  # (text, colour)
    chip: str
    style: Style
    site: str | None = None  # a zone of this part (drawn above): edges end on it


@dataclass
class _Group:
    """Flows from one zone of the part to one target below."""

    site: str
    target: str
    flows: list[DrFlow]


def _endpoint_site(m: DrModel, ident: str, fallback: str) -> str:
    found = m.cluster(ident)
    return found[0].id if found else fallback


def _ends(m: DrModel, f: DrFlow) -> tuple[str, str]:
    return _endpoint_site(m, f.a, f.site_a), _endpoint_site(m, f.b, f.site_b)


def plan_parts(m: DrModel, per: int = PER_PART) -> list[list[DrSite]]:
    ids = [s.id for s in m.sites]
    pairs = [_ends(m, f) for f in m.flows]
    by_id = {s.id: s for s in m.sites}
    return [[by_id[i] for i in p] for p in parts_mod.plan(ids, pairs, per) if p] or [[]]


def _lane_order(spans: list[tuple[float, float]]) -> list[int]:
    """Left-to-right lane order of bent gutter edges ((y left, y right) each) with the
    fewest crossings: a left segment crosses every lane left of its own that spans its
    y, a right segment every lane right of its own that spans its y. Exhaustive up to 6
    edges, else by the left y."""
    n = len(spans)
    base = sorted(range(n), key=lambda i: (spans[i][0], spans[i][1], i))
    if n > 6:
        return base

    def inside(y: float, k: int) -> bool:
        a, b = sorted(spans[k])
        return a < y < b

    best, best_n = base, None
    for perm in itertools.permutations(base):
        at = {k: j for j, k in enumerate(perm)}
        crossings = 0
        for i in range(n):
            for k in range(n):
                if i == k:
                    continue
                if at[k] < at[i] and inside(spans[i][0], k):
                    crossings += 1
                if at[k] > at[i] and inside(spans[i][1], k):
                    crossings += 1
        if best_n is None or crossings < best_n:
            best, best_n = list(perm), crossings
    return best


def _strength(reps: list[Repl]) -> Style:
    s = fmt.repl_strength(reps)
    return K.REPL_SYNC if s == "sync" else K.REPL_NEARSYNC if s == "nearsync" else \
        K.REPL_ASYNC


# ─── text ──────────────────────────────────────────────────────────────


def _header_rows(m: DrModel, s: DrSite) -> list[Row]:
    sub = protected_line(s)
    inside = m.plans_between(s.id, s.id)
    if inside and inside.a == inside.b:
        sub += f" · {fmt.plural(inside.count, 'recovery plan')}"
    return [Row(s.name, K.SIZE_SITE, True, K.INK, 2, 7.5),
            Row(sub, K.SIZE_SMALL, False, K.INK_3, 2, gap_before=0.5)]


def _card_rows(it: _Item, brief: bool) -> list[Row]:
    if it.cluster is None:
        n = len(it.hidden)
        rows = [Row(f"+{fmt.plural(n, 'cluster')} without a protection policy",
                    K.SIZE_CARD_TITLE, True, K.INK_3, 2, tx.MIN_SIZE)]
        return rows
    c = it.cluster
    muted = c.protected is False
    rows = [Row(c.name, K.SIZE_CARD_TITLE, True, K.INK_3 if muted else K.INK, 3,
                tx.MIN_SIZE)]
    if not brief:
        rows.append(Row(cluster_line(c), K.SIZE_BODY, False, K.INK_3 if muted else K.INK_2,
                        2, gap_before=0.5))
    return rows


def _card_style(it: _Item) -> Style:
    if it.cluster is None or it.cluster.protected is False:
        return K.CLUSTER_MUTED
    return K.CLUSTER


def _target(m: DrModel, tid: str, part_of: dict[str, int], n_parts: int,
            in_part: set[str]) -> _Target:
    r = m.remote(tid)
    if r is not None:
        lines = [(r.name, K.INK)]
        if r.kind == "xi":
            lines.append(("Nutanix Cloud (Xi)", K.INK_2))
        if cl := remote_cluster_line(r):
            lines.append((cl, K.INK_2))
        lines.append(("not in this inventory", K.INK_3))
        return _Target(tid, r.name, lines, "REMOTE AZ", K.REMOTE)
    s = m.site(tid)
    name = s.name if s else tid
    if tid in in_part:
        return _Target(tid, name, [], "AZ", K.REF, site=tid)
    part = part_of.get(tid, 0) + 1
    return _Target(tid, name, [(name, K.INK), ("Availability zone in this inventory", K.INK_2),
                               (f"drawn in part {part} of {n_parts}", K.INK_3)],
                   "AZ", K.REF)


def _target_rows(t: _Target) -> list[Row]:
    rows = []
    for i, (text, color) in enumerate(t.lines):
        if i == 0:
            rows.append(Row(text, K.SIZE_REMOTE_TITLE, True, color, 2, tx.MIN_SIZE))
        else:
            rows.append(Row(text, K.SIZE_SMALL, False, color, 2, gap_before=0.5 if i == 1
                            else 0.0))
    return rows


def _intra_lines(f: DrFlow) -> list[LabelText]:
    reps = f.repl
    if not reps:
        return ["N/A"]
    r = reps[0]
    lines: list[LabelText] = [r.type, "RPO 0" if r.rpo_seconds == 0 else
                              f"RPO {fmt.rpo(r.rpo_seconds)}"]
    if len(reps) > 1:
        lines.append(f"+{len(reps) - 1:,} more")
    if mutual(f):
        lines.append("both ways")
    return lines


def _target_lines(m: DrModel, gs: list[_Group]) -> list[LabelText]:
    """One label per target: every schedule into (or out of) it, then the recovery
    plans between it and the zones drawing an edge to it."""
    reps = [r for g in gs for f in g.flows for r in f.repl]
    seen = {(r.type, r.rpo_seconds): r for r in reps}
    ordered = sorted(seen.values(), key=lambda r: (r.rpo_seconds if r.rpo_seconds is not None
                                                   else 10**12, r.type))
    lines: list[LabelText] = [", ".join(fmt.repl_group(ordered))]
    flows = [f for g in gs for f in g.flows]
    if flows and all(mutual(f) for f in flows):
        lines.append(("both ways", K.INK_3))
    count, witness = 0, False
    for g in gs:
        plans = m.plans_between(g.site, g.target)
        if plans:
            count += plans.count
            witness = witness or plans.witness
    if count:
        lines.append((fmt.plural(count, "recovery plan") + (", witness" if witness else ""),
                      K.INK_3))
    return lines


# ─── one part ──────────────────────────────────────────────────────────


def _items(s: DrSite, collapse_all: bool) -> list[_Item]:
    shown = [c for c in s.clusters if c.protected is not False]
    off = [c for c in s.clusters if c.protected is False]
    items = [_Item(c.id, c) for c in shown]
    if off and (collapse_all or len(off) > MAX_UNPROTECTED):
        items.append(_Item(f"{s.id}:unprotected", None, off))
    else:
        items += [_Item(c.id, c) for c in off]
    return items


def _layout_part(m: DrModel, sites: list[DrSite], parts: list[list[DrSite]], page: Page,
                 rung: int, sid: str) -> Scene:
    sc = Scene(id=sid, type="dr", title=m.title, caption="", width=page.width, height=0)
    pos = {s.id: i for i, s in enumerate(sites)}
    part_of = {s.id: pi for pi, p in enumerate(parts) for s in p}
    in_part = set(pos)

    # Classify flows.
    gutter: dict[int, list[DrFlow]] = {}
    intra: dict[str, list[DrFlow]] = {}
    below: dict[tuple[str, str], list[DrFlow]] = {}
    for f in m.flows:
        sa, sb = _ends(m, f)
        if sa not in pos and sb not in pos:
            continue
        if sa in pos and sb in pos:
            if sa == sb:
                intra.setdefault(sa, []).append(f)
            elif abs(pos[sa] - pos[sb]) == 1:
                gutter.setdefault(min(pos[sa], pos[sb]), []).append(f)
            else:
                lo, hi = (sa, sb) if pos[sa] < pos[sb] else (sb, sa)
                below.setdefault((lo, hi), []).append(f)
        else:
            here, there = (sa, sb) if sa in pos else (sb, sa)
            below.setdefault((here, there), []).append(f)
    plan_gutter: dict[int, tuple[int, bool]] = {}
    for p in m.plans:
        if p.a in pos and p.b in pos and abs(pos[p.a] - pos[p.b]) == 1:
            plan_gutter[min(pos[p.a], pos[p.b])] = (p.count, p.witness)

    # Columns: card order (right columns follow their partners on the left).
    items = {s.id: _items(s, rung >= 1) for s in sites}
    row_of: dict[str, int] = {}
    for i, s in enumerate(sites):
        its = items[s.id]
        if i > 0 and (i - 1) in gutter:
            def bary(it: _Item, i: int = i) -> float:
                rows = [row_of[other] for f in gutter[i - 1] for me, other in
                        ((f.a, f.b), (f.b, f.a)) if me == it.id and other in row_of]
                return sum(rows) / len(rows) if rows else 10**6
            first = [it for it in its if it.cluster is not None and
                     it.cluster.protected is not False]
            rest = [it for it in its if it not in first]
            # Linked cards take their partners' row when free (so edges run straight),
            # else the nearest free row; the others fill the rows left, in order.
            idx = {it.id: k for k, it in enumerate(first)}
            linked = sorted((it for it in first if bary(it) < 10**6),
                            key=lambda it: (bary(it), idx[it.id]))
            slots: list[_Item | None] = [None] * len(first)
            for it in linked:
                want_r = min(len(first) - 1, int(round(bary(it))))
                free = [r for r in range(len(first)) if slots[r] is None]
                slots[min(free, key=lambda r: (abs(r - want_r), r))] = it
            rest_first = iter(it for it in first if bary(it) >= 10**6)
            its = [it if it is not None else next(rest_first) for it in slots]
            if len(its) <= 7:  # exhaustive: least total row displacement of the edges
                partners: dict[str, list[int]] = {}
                for f in gutter[i - 1]:
                    for me, other in ((f.a, f.b), (f.b, f.a)):
                        if other in row_of:
                            partners.setdefault(me, []).append(row_of[other])

                def cost(order: tuple[_Item, ...],
                         partners: dict[str, list[int]] = partners) -> int:
                    return sum(abs(r - p) for r, it in enumerate(order)
                               for p in partners.get(it.id, []))

                best = min(itertools.permutations(its),
                           key=lambda o: (cost(o), [idx[it.id] for it in o]))
                its = list(best)
            its = its + rest
            items[s.id] = its
        for r, it in enumerate(its):
            row_of[it.id] = r

    # Widths.
    gaps: list[float] = []
    for i in range(len(sites) - 1):
        if i in gutter or i in plan_gutter:
            fl = gutter.get(i, [])
            widths = [label_block([flow_text(f)], GUT_MAX - 6)[1] for f in fl]
            n_bent = sum(1 for f in fl if row_of.get(f.a, -1) != row_of.get(f.b, -1))
            want_w = max(widths, default=0.0) + 10
            if n_bent:  # labels by the left cards, lanes to their right
                want_w = max(widths, default=0.0) + 14 + BEND_LANE * n_bent + 6
            gaps.append(q(min(GUT_BENT_MAX if n_bent else GUT_MAX, max(GUT_MIN, want_w))))
        else:
            gaps.append(GAP)
    groups: list[_Group] = [_Group(a, b, fl) for (a, b), fl in below.items()]
    groups.sort(key=lambda g: (pos[g.site], g.target))
    down_lanes = {s.id: sum(1 for g in groups if g.site == s.id) for s in sites}
    lane_w: dict[str, float] = {}
    lanes_left: dict[str, list[DrFlow]] = {}
    for s in sites:
        fl = sorted(intra.get(s.id, []),
                    key=lambda f: -abs(row_of.get(f.a, 0) - row_of.get(f.b, 0)))
        lanes_left[s.id] = fl
        lane_w[s.id] = max((label_block(_intra_lines(f), 40)[1] for f in fl), default=0.0) + 4
    avail = page.width - 2 * MX - sum(gaps)
    col_w = q(min(MAX_COL, avail / max(1, len(sites))))

    # Measure.
    def margins(s: DrSite) -> tuple[float, float]:
        left = P + len(lanes_left[s.id]) * lane_w[s.id] if lanes_left[s.id] else P
        right = P + (down_lanes[s.id] * LANE + 5 if down_lanes[s.id] else 0)
        return left, right

    card_h = 0.0
    for s in sites:
        lm, rm = margins(s)
        cw = col_w - lm - rm
        for it in items[s.id]:
            card_h = max(card_h, text_h(_card_rows(it, rung >= 2), cw - 10) + 9)
    card_h = q(max(card_h, 18.0))
    header_h = max((text_h(_header_rows(m, s), col_w - 2 * P) for s in sites), default=0.0)
    head_top = TOP + CHIP_H / 2 + 4
    cards_y = q(head_top + header_h + 7)
    most = max((len(items[s.id]) for s in sites), default=0)
    site_bottom = q(cards_y + most * (card_h + CARD_GAP) - CARD_GAP + P + 1)

    # Draw columns.
    cols: dict[str, Node] = {}
    cards: dict[str, Node] = {}
    x = MX
    used = {"protected": False, "muted": False}
    for i, s in enumerate(sites):
        cont = box(s.id, "container", x, TOP, col_w, _header_rows(m, s), K.SITE, pad_x=P,
                   pad_y=head_top - TOP, h=site_bottom - TOP, data=dict(s.data))
        sc.nodes.append(cont)
        sc.nodes.append(chip(f"{s.id}:tag", "AZ", x + 5.5, TOP, K.TAG, parent=s.id))
        cols[s.id] = cont
        lm, rm = margins(s)
        cw = col_w - lm - rm
        for r, it in enumerate(items[s.id]):
            y = cards_y + r * (card_h + CARD_GAP)
            st = _card_style(it)
            used["protected" if st is K.CLUSTER else "muted"] = True
            data = dict(it.cluster.data) if it.cluster else \
                {"clusters": str(len(it.hidden)), "kind": "unprotected"}
            node = box(it.id, "box", x + lm, y, cw, _card_rows(it, rung >= 2), st, pad_x=5,
                       pad_y=4.5, align="left", h=card_h, valign="middle", parent=s.id,
                       data=data)
            sc.nodes.append(node)
            cards[it.id] = node
        x = q(x + col_w + (gaps[i] if i < len(gaps) else 0.0))

    def anchor(ident: str, site: str) -> Node:
        return cards.get(ident) or cols[site]

    # Ports: per card side, attachments sorted by the other end's y.
    ports: dict[tuple[str, str], list[tuple[float, str]]] = {}

    def want(node: Node, side: str, other_y: float, key: str) -> None:
        ports.setdefault((node.id, side), []).append((other_y, key))

    gut_edges: list[tuple[int, DrFlow, Node, Node]] = []
    for gi, fl in sorted(gutter.items()):
        for f in fl:
            sa, sb = _ends(m, f)
            na, nb = anchor(f.a, sa), anchor(f.b, sb)
            ln, rn = (na, nb) if pos[sa] < pos[sb] else (nb, na)
            gut_edges.append((gi, f, ln, rn))
            want(ln, "R", rn.y + rn.h / 2, f.id)
            want(rn, "L", ln.y + ln.h / 2, f.id)
    for s in sites:
        for f in lanes_left[s.id]:
            ca, cb = cards.get(f.a), cards.get(f.b)
            if ca is None or cb is None:
                continue
            want(ca, "L", cb.y, f.id + "|a")
            want(cb, "L", ca.y, f.id + "|b")
    for g in groups:
        for f in g.flows:
            own = f.a if _endpoint_site(m, f.a, f.site_a) == g.site else f.b
            card = cards.get(own)
            if card is not None:
                want(card, "R", 10**6, f"{f.id}|{g.target}")

    port_y: dict[tuple[str, str, str], float] = {}
    for (nid, side), lst in ports.items():
        holder = next(n for n in sc.nodes if n.id == nid)
        lst.sort()
        k = len(lst)
        for j, (_, key) in enumerate(lst):
            port_y[(nid, side, key)] = q(holder.y + holder.h * (j + 1) / (k + 1))

    def py(node: Node, side: str, key: str) -> float:
        return port_y.get((node.id, side, key), q(node.y + node.h / 2))

    kinds: set[str] = set()

    # Gutter edges. Cards on one row join straight; the others bend through a lane.
    # With bends, every label sits by the left card and the lanes run to the right of
    # the labels, ordered to cross as little as possible.
    for gi in sorted(gutter):
        in_gut = [(f, lft, rgt) for g2, f, lft, rgt in gut_edges if g2 == gi]
        c0 = cols[sites[gi].id]
        gx0, gx1 = c0.x + c0.w, cols[sites[gi + 1].id].x
        geo: dict[str, tuple[float, float]] = {}
        for f, lft, rgt in in_gut:
            yl = q(lft.y + 18) if lft.kind == "container" else py(lft, "R", f.id)
            yr = q(rgt.y + 18) if rgt.kind == "container" else py(rgt, "L", f.id)
            if lft.kind != "container" and rgt.kind != "container" and \
                    abs(lft.y - rgt.y) < 0.5:
                yr = yl  # same row: straight
            geo[f.id] = (yl, yr)
        bent = [t for t in in_gut if abs(geo[t[0].id][0] - geo[t[0].id][1]) >= 0.5]
        lab_w = {f.id: label_block([flow_text(f)], gx1 - gx0 - 4)[1] for f, _, _ in in_gut}
        lane_of: dict[str, float] = {}
        if bent:
            lane_lo = gx0 + 8 + max(lab_w.values()) + 4
            lane_hi = gx1 - 6
            spans = [(geo[f.id][0], geo[f.id][1]) for f, _, _ in bent]
            n = len(bent)
            for j, k in enumerate(_lane_order(spans)):
                lane_of[bent[k][0].id] = q(lane_lo + (lane_hi - lane_lo) * (j + 0.5) / n if n > 1
                                           else (lane_lo + lane_hi) / 2)
        for f, lft, rgt in in_gut:
            yl, yr = geo[f.id]
            xl, xr = lft.x + lft.w, rgt.x
            if f.id in lane_of:
                lx = lane_of[f.id]
                pts = [(xl, yl), (lx, yl), (lx, yr), (xr, yr)]
            else:
                pts = [(xl, yl), (xr, yl)]
            cx = gx0 + 8 + lab_w[f.id] / 2 if bent else (gx0 + gx1) / 2
            a_left = cards.get(f.a) is lft or (lft.kind == "container" and
                                               _ends(m, f)[0] == lft.id)
            if not a_left:
                pts = pts[::-1]
            reps = f.repl
            kinds.add(fmt.repl_strength(reps))
            e = edge(f.id, f.a, f.b, pts, _strength(reps), arrow_end=bool(f.forward),
                     arrow_start=bool(f.backward), data={"kind": "replication"})
            e.labels.append(edge_label([flow_text(f)], cx, yl, gx1 - gx0 - 4))
            sc.edges.append(e)

    # Recovery plan chips on the gutters.
    for gi, (count, witness) in sorted(plan_gutter.items()):
        c0 = cols[sites[gi].id]
        gx0, gx1 = c0.x + c0.w, cols[sites[gi + 1].id].x
        label = f"{fmt.plural(count, 'recovery plan').upper()}" + (" · WITNESS" if witness
                                                                     else "")
        if chip_w(label) > gx1 - gx0 - 4:
            label = f"{count:,} PLAN{'S' if count != 1 else ''}" + (" · WITNESS" if witness else "")
        w = q(chip_w(label) + 1)
        plan = box(f"plans:{sites[gi].id}|{sites[gi + 1].id}", "tag", (gx0 + gx1 - w) / 2,
                   q(head_top + 2), w, [Row(label, K.SIZE_CHIP, True, K.INK_2)], K.TAG_PLANS,
                   pad_x=3.5, pad_y=0, h=CHIP_H, valign="middle",
                   data={"recovery_plans": str(count)})
        sc.nodes.append(plan)

    # Brackets inside a zone.
    for s in sites:
        lm, _ = margins(s)
        col = cols[s.id]
        for j, f in enumerate(lanes_left[s.id]):
            ca, cb = cards.get(f.a), cards.get(f.b)
            if ca is None or cb is None:
                continue
            lx = q(col.x + P + lane_w[s.id] * (len(lanes_left[s.id]) - j - 0.5))
            ya, yb = py(ca, "L", f.id + "|a"), py(cb, "L", f.id + "|b")
            pts = [(ca.x, ya), (lx, ya), (lx, yb), (cb.x, yb)]
            kinds.add(fmt.repl_strength(f.repl))
            e = edge(f.id, f.a, f.b, pts, _strength(f.repl), arrow_end=bool(f.forward),
                     arrow_start=bool(f.backward), data={"kind": "replication"})
            e.labels.append(edge_label(_intra_lines(f), lx, (ya + yb) / 2, lane_w[s.id]))
            sc.edges.append(e)

    content_bottom = site_bottom
    # Below: one bus per target. Each source drops down a lane on its column's right
    # into the target's channel; the bus drops once into the target, its label on that
    # drop, under every channel.
    if groups:
        targets: dict[str, _Target] = {}
        by_target: dict[str, list[int]] = {}
        for gi, g in enumerate(groups):
            if g.target not in targets:
                targets[g.target] = _target(m, g.target, part_of, len(parts), in_part)
            by_target.setdefault(g.target, []).append(gi)
        lane_x: dict[int, float] = {}
        for s in sites:
            col = cols[s.id]
            mine_g = [gi for gi, g in enumerate(groups) if g.site == s.id]
            for j, gi in enumerate(mine_g):
                lane_x[gi] = q(col.x + col.w - P - LANE * (len(mine_g) - 1 - j) - 1)
        labels = {t: _target_lines(m, [groups[gi] for gi in gis])
                  for t, gis in by_target.items()}
        n_boxes = sum(1 for t in by_target if targets[t].site is None)
        bw = q(min(BOX_W, (page.width - 2 * MX - 14 * max(0, n_boxes - 1))
                   / max(1, n_boxes)))
        tlab_w = {t: label_block(v, bw)[1] for t, v in labels.items()}
        lab_h = max(label_block(v, bw)[2] for v in labels.values())
        tx_of: dict[str, float] = {}
        for t, gis in by_target.items():
            if targets[t].site is not None:
                c = cols[t]
                tx_of[t] = c.x + c.w / 2
            else:
                tx_of[t] = sum(lane_x[gi] for gi in gis) / len(gis)
        t_order = sorted(by_target, key=lambda t: (tx_of[t], t))
        boxed = [t for t in t_order if targets[t].site is None]
        chan = {t: q(site_bottom + 7 + k * 6) for k, t in enumerate(t_order)}
        last = max(chan.values())
        label_top = q(last + 5)
        row_y = q(label_top + lab_h + 5 + CHIP_H / 2)
        boxes: dict[str, Node] = {}
        if boxed:
            xs = _deoverlap([tx_of[t] - bw / 2 for t in boxed], bw, 14, MX,
                            page.width - MX)
            for t, bx in zip(boxed, xs, strict=True):
                tg = targets[t]
                tbox = box(t, "box", bx, row_y, bw, _target_rows(tg), tg.style, pad_x=5,
                           pad_y=CHIP_H / 2 + 4, data={"availability_zone": tg.name,
                                                      "name": tg.name})
                sc.nodes.append(tbox)
                sc.nodes.append(chip(f"{t}:tag", tg.chip, bx + 5.5, row_y,
                                     K.TAG if tg.style is K.REF else K.TAG_MUTED, parent=t))
                boxes[t] = tbox
            hmax = max(b.h for b in boxes.values())
            for b in boxes.values():
                b.h = hmax
            content_bottom = max(content_bottom, row_y + hmax)
        else:
            content_bottom = max(content_bottom, label_top + lab_h)
        for t in t_order:
            tg = targets[t]
            cy = chan[t]
            if tg.site is not None:
                c = cols[t]
                dx, dy = q(c.x + c.w / 2), c.y + c.h
            else:
                b = boxes[t]
                dx, dy = q(b.x + b.w / 2), b.y
            need_label = True
            for gi in by_target[t]:
                g = groups[gi]
                lx = lane_x[gi]
                for f in g.flows:
                    here_a = _endpoint_site(m, f.a, f.site_a) == g.site
                    src_card = cards.get(f.a if here_a else f.b)
                    if src_card is not None:
                        yy = py(src_card, "R", f"{f.id}|{g.target}")
                        pts = [(src_card.x + src_card.w, yy), (lx, yy), (lx, cy), (dx, cy),
                               (dx, dy)]
                    else:  # zone-level: from the zone's bottom edge
                        col = cols[g.site]
                        pts = [(lx, col.y + col.h), (lx, cy), (dx, cy), (dx, dy)]
                    if abs(lx - dx) < 0.5:
                        pts = [pt for pt in pts if pt not in ((dx, cy), (lx, cy))]
                    reps = f.repl
                    kinds.add(fmt.repl_strength(reps))
                    fwd = f.forward if here_a else f.backward
                    bwd = f.backward if here_a else f.forward
                    src, dst = (f.a, f.b) if here_a else (f.b, f.a)
                    e = edge(f"{f.id}|{t}", src, dst, pts, _strength(reps),
                             arrow_end=bool(fwd), arrow_start=bool(bwd),
                             data={"kind": "replication"})
                    if need_label:
                        need_label = False
                        lw = tlab_w[t]
                        if tg.site is None:  # on the drop, under every channel
                            lx_c = min(max(dx, MX + lw / 2), page.width - MX - lw / 2)
                            e.labels.append(edge_label(labels[t], lx_c,
                                                       label_top + lab_h / 2, bw))
                        else:  # on the channel, beside the rise into the zone
                            e.labels.append(edge_label(labels[t], dx, cy, bw))
                    sc.edges.append(e)

    # Legend.
    items_l: list[tuple[str, Style, str]] = []
    if "sync" in kinds:
        items_l.append(("line", K.REPL_SYNC, "Sync (RPO 0)"))
    if "nearsync" in kinds:
        items_l.append(("line", K.REPL_NEARSYNC, "NearSync (1 to 15 min)"))
    if "async" in kinds:
        items_l.append(("line", K.REPL_ASYNC, "Async (1 h or more)"))
    if used["protected"]:
        items_l.append(("box", K.CLUSTER, "Protected cluster"))
    if used["muted"]:
        items_l.append(("box", K.CLUSTER_MUTED, "No protection policy"))
    styles = {n.style for n in sc.nodes if n.kind == "box"}
    if K.REMOTE in styles:
        items_l.append(("box", K.REMOTE, "Outside this inventory"))
    if K.REF in styles:
        items_l.append(("box", K.REF, "Drawn in another part"))
    right_x = max(max(n.x + n.w for n in sc.nodes),
                max((max(p[0] for p in e.points) for e in sc.edges), default=0.0),
                max((lb.x + lb.w for e in sc.edges for lb in e.labels), default=0.0))
    lg_w = min(page.width - 2 * MX, max(right_x, 300.0))
    lh = legend(sc, items_l, MX, content_bottom + 9, lg_w)
    right_x = max(right_x, max(n.x + n.w for n in sc.nodes))
    sc.width = q(min(page.width, right_x + MX))
    sc.height = q(content_bottom + 9 + lh + 1)
    sc.meta = {"sites": [s.id for s in sites], "rung": rung}
    return sc


# ─── entry point ───────────────────────────────────────────────────────


def _caption(m: DrModel, sites: list[DrSite], part: tuple[int, int]) -> str:
    names = fmt.join_names([short_name(s.name) for s in sites])
    if part[1] > 1:
        return (f"Disaster recovery design, part {part[0]} of {part[1]}: {names}, protection "
                "policies by source and target cluster, type and RPO.")
    return "Disaster recovery design: protection policies by source and target cluster, " \
        "type and RPO."


def _best(m: DrModel, sites: list[DrSite], parts: list[list[DrSite]], page: Page,
          limit: float, sid: str) -> tuple[Scene, bool]:
    """The first rung that fits: every card; unprotected clusters in one card; then
    cards without their detail line."""
    sc = _layout_part(m, sites, parts, page, 0, sid)
    for rung in (1, 2):
        if sc.height <= limit:
            return sc, True
        sc = _layout_part(m, sites, parts, page, rung, sid)
    return sc, sc.height <= limit


def layout(model: DrModel, page: Page | None = None, refs: Refs | None = None,
           first_budget: Budget | None = None) -> list[Scene]:
    """Scenes (parts) of the disaster recovery design. Every part fits `page.max_h` and
    part 1 `first_budget(width)` when it can; a part that cannot gives its last linked
    group (or zone) to a new part."""
    page = page or Page()
    if not model.sites:
        return []
    by_id = {s.id: s for s in model.sites}

    def limit(pi: int, width: float) -> float:
        if pi == 0 and first_budget is not None:
            return min(page.max_h, first_budget(width))
        return page.max_h

    def fits(ids: list[str], pi: int, plan: list[list[str]]) -> bool:
        sites = [by_id[i] for i in ids]
        grid = [[by_id[i] for i in p] for p in plan]
        probe = _layout_part(model, sites, grid, page, 0, "probe")
        return _best(model, sites, grid, page, limit(pi, probe.width), "probe")[1]

    pairs = [_ends(model, f) for f in model.flows]
    parts = [[by_id[i] for i in p] for p in
             parts_mod.fit([s.id for s in model.sites], pairs, PER_PART, fits) if p]
    scenes: list[Scene] = []
    for pi, sites in enumerate(parts):
        sid = "dr" if len(parts) == 1 else f"dr-{pi + 1}"
        probe = _layout_part(model, sites, parts, page, 0, sid)
        sc, _ = _best(model, sites, parts, page, limit(pi, probe.width), sid)
        sc.part = (pi + 1, len(parts))
        sc.caption = _caption(model, sites, sc.part)
        scenes.append(sc)
    return scenes
