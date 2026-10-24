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

from .. import fmt, route
from .. import text as tx
from .. import tokens as K
from ..build import (
    CHIP_H,
    LEGEND_GAP,
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
from ..check import ANCHOR, _gap_to_path
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
from ..scene import Edge, EdgeLabel, Node, Scene, Style, q
from . import parts as parts_mod
from .conceptual import Page, Refs, _deoverlap
from .fit import place_first

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
#: Pitch of the down lanes beside a column: parallel trunks read as separate lines.
LANE = 12.0
#: Smallest pitch of the trunk channels under the columns.
CHANNEL_MIN = 10.0
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


def _strength(reps: list[Repl]) -> Style:
    s = fmt.repl_strength(reps)
    return K.REPL_SYNC if s == "sync" else K.REPL_NEARSYNC if s == "nearsync" else \
        K.REPL_ASYNC


def _order_columns(sites: list[DrSite], items: dict[str, list[_Item]],
                   gutter: dict[int, list[DrFlow]]) -> dict[str, list[_Item]]:
    """Card order per column, both sides of every gutter moving: start from the
    barycentre order (the average row of each card's partners), then improve column by
    column (exhaustively up to 7 protected cards) on the cost of the gutters next to it:
    3 per crossing plus 1 per bent flow. Cards without a policy stay at the bottom."""
    order = {s.id: list(items[s.id]) for s in sites}
    row = {it.id: r for s in sites for r, it in enumerate(order[s.id])}
    col_of = {it.id: i for i, s in enumerate(sites) for it in order[s.id]}

    def pairs(g: int) -> list[tuple[str, str]]:
        out = []
        for f in gutter.get(g, []):
            a, b = (f.a, f.b) if col_of.get(f.a) == g else (f.b, f.a)
            if col_of.get(a) == g and col_of.get(b) == g + 1:
                out.append((a, b))
        return out

    links = {g: pairs(g) for g in gutter}

    def cost(g: int) -> int:
        es = [(row[a], row[b]) for a, b in links.get(g, [])]
        bent = sum(1 for ra, rb in es if ra != rb)
        cross = sum(1 for (a1, b1), (a2, b2) in itertools.combinations(es, 2)
                    if (a1 - a2) * (b1 - b2) < 0)
        return 3 * cross + bent

    def split(i: int) -> tuple[list[_Item], list[_Item]]:
        its = order[sites[i].id]
        first = [it for it in its if it.cluster is not None and
                 it.cluster.protected is not False]
        return first, [it for it in its if it not in first]

    def apply(i: int, first: list[_Item], rest: list[_Item]) -> None:
        order[sites[i].id] = first + rest
        for r, it in enumerate(order[sites[i].id]):
            row[it.id] = r

    # Barycentre sweeps first.
    for _ in range(2):
        for i in [*range(1, len(sites)), *range(len(sites) - 2, -1, -1)]:
            first, rest = split(i)

            def bary(it: _Item, i: int = i) -> float:
                rs = [row[o] for g in (i - 1, i) for a, b in links.get(g, [])
                      for me, o in ((a, b), (b, a)) if me == it.id]
                return sum(rs) / len(rs) if rs else float(row[it.id])

            apply(i, sorted(first, key=lambda it: (bary(it), row[it.id], it.id)), rest)
    # Then exhaustive improvement per column.
    for _ in range(2):
        for i in range(len(sites)):
            first, rest = split(i)
            if len(first) > 7 or len(first) < 2:
                continue
            here = [g for g in (i - 1, i) if g in links]
            if not here:
                continue
            best, best_c = list(first), None
            for perm in itertools.permutations(first):
                apply(i, list(perm), rest)
                c = sum(cost(g) for g in here)
                if best_c is None or c < best_c:
                    best, best_c = list(perm), c
            apply(i, best, rest)
    return order


def _label_sides(m: DrModel, flows: list[DrFlow], pos: dict[str, int],
                 left_site: str) -> dict[str, str]:
    """Which side of the gutter each flow's label goes: beside its arrowhead (the target
    end; for a two-way flow, the end of its second cluster)."""
    out = {}
    for f in flows:
        sa, sb = _ends(m, f)
        target_left = (sb == left_site) if f.forward else (sa == left_site)
        out[f.id] = "L" if target_left else "R"
    return out


def _zone(flows: list[DrFlow], row_of: dict[str, int]) -> float:
    """Width of one side's label zone: the widest label (labels on one row stack)."""
    widths = [label_block([flow_text(f)], GUT_BENT_MAX)[1] for f in flows]
    if not widths:
        return 0.0
    rows: dict[int, float] = {}
    for f, w in zip(flows, widths, strict=True):
        r = row_of.get(f.a, row_of.get(f.b, -1))
        rows[r] = rows.get(r, 0.0) + w + 2
    return min(GUT_BENT_MAX - 20, max(max(widths), max(rows.values()) - 2))


# ─── text ──────────────────────────────────────────────────────────────


def _header_rows(m: DrModel, s: DrSite) -> list[Row]:
    sub = protected_line(s)
    inside = m.plans_between(s.id, s.id)
    if inside and inside.a == inside.b:
        sub += f" · {fmt.plural(inside.count, 'recovery plan')}"
    return [Row(s.name, K.SIZE_SITE, True, K.INK, 2, 7.5),
            Row(sub, K.SIZE_SMALL, False, K.INK_3, 2, gap_before=0.5)]


#: Second line of a component the author added in the Diagrams editor.
NOT_COLLECTED = "Added by the author, not collected"


def _card_rows(it: _Item, brief: bool) -> list[Row]:
    if it.cluster is not None and it.cluster.kind == "note":  # free text
        return [Row(it.cluster.name, K.SIZE_BODY, False, K.INK_2, 3, tx.MIN_SIZE)]
    if it.cluster is not None and it.cluster.kind == "user":
        return [Row(it.cluster.name, K.SIZE_CARD_TITLE, True, K.INK, 2, tx.MIN_SIZE),
                Row(it.cluster.note or NOT_COLLECTED, K.SIZE_SMALL, False, K.INK_3, 2,
                    gap_before=0.5)]
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
        if c.note:  # a line the author added to a collected cluster
            rows.append(Row(c.note, K.SIZE_SMALL, False, K.INK_3, 2))
    return rows


def _card_style(it: _Item) -> Style:
    if it.cluster is not None and it.cluster.kind != "cluster":
        return K.NOTE if it.cluster.kind == "note" else K.USER
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


def _on_last_run(e: Edge, lab: EdgeLabel, others: list[EdgeLabel]) -> EdgeLabel:
    """`lab` moved onto the run that ends at `e`'s arrowhead (its first run for a
    flow drawn backwards), just above the line, or below it when that side is taken."""
    pts = e.points if e.arrow_end or not e.arrow_start else e.points[::-1]
    (x0, y0), (x1, _) = pts[-2], pts[-1]
    lo, hi = sorted((x0, x1))
    cx = min(max((lo + hi) / 2, lo + lab.w / 2), hi - lab.w / 2)

    def clash(y: float) -> bool:
        return any(o.x < cx + lab.w / 2 and cx - lab.w / 2 < o.x + o.w and o.y < y + lab.h
                   and y < o.y + o.h for o in others)

    above, below = q(y0 - lab.h - 1), q(y0 + 1)
    y = below if clash(above) and not clash(below) else above
    return EdgeLabel(x=q(cx - lab.w / 2), y=y, w=lab.w, h=lab.h, lines=lab.lines,
                     background=lab.background)


def _down_sides(m: DrModel, sites: list[DrSite], pos: dict[str, int],
                gutter: dict[int, list[DrFlow]], groups: list[_Group],
                row_of: dict[str, int], lanes_left: dict[str, list[DrFlow]]
                ) -> dict[str, str]:
    """The side ("L" or "R") of each column the trunks to targets below run down. A
    lane crosses every gutter flow that reaches a card below the lane's highest stub on
    that side, so the side with fewer such flows wins (right on a tie); a column with
    flows between its own cards keeps the left for their lanes."""
    out: dict[str, str] = {}
    for s in sites:
        mine = [g for g in groups if g.site == s.id]
        rows = [row_of[c] for g in mine for f in g.flows for c in (f.a, f.b)
                if c in row_of and _endpoint_site(m, c, "") == s.id]
        if not rows or lanes_left[s.id]:
            out[s.id] = "R"
            continue
        top = min(rows)

        def hits(gi: int, sid: str = s.id, top: int = top) -> int:
            return sum(1 for f in gutter.get(gi, []) for c in (f.a, f.b)
                       if _endpoint_site(m, c, "") == sid and row_of.get(c, -1) > top)

        right, left = hits(pos[s.id]), hits(pos[s.id] - 1)
        out[s.id] = "L" if left < right else "R"
    return out


# ─── one part ──────────────────────────────────────────────────────────


def _items(s: DrSite, collapse_all: bool) -> list[_Item]:
    # What the author added is always drawn (never folded into the unprotected card).
    shown = [c for c in s.clusters if c.protected is not False or c.kind != "cluster"]
    off = [c for c in s.clusters if c.protected is False and c.kind == "cluster"]
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

    # Columns: card order. Protected cards are ordered by the average row of their
    # partners across the neighbouring gutters, sweeping right then left a few times
    # (both sides move), so most flows run straight and the rest cross little; cards
    # without a policy stay at the bottom.
    items = {s.id: _items(s, rung >= 1) for s in sites}
    order = _order_columns(sites, items, gutter)
    row_of = {it.id: r for s in sites for r, it in enumerate(order[s.id])}
    items = order

    # Widths. A gutter holds each flow's label beside its arrowhead (left or right
    # side) and, between the two label zones, a lane per bent flow.
    gaps: list[float] = []
    for i in range(len(sites) - 1):
        if i in gutter or i in plan_gutter:
            fl = gutter.get(i, [])
            sides = _label_sides(m, fl, pos, sites[i].id)
            zl = _zone([f for f in fl if sides[f.id] == "L"], row_of)
            zr = _zone([f for f in fl if sides[f.id] == "R"], row_of)
            n_bent = sum(1 for f in fl if row_of.get(f.a, -1) != row_of.get(f.b, -1))
            want_w = 8 + zl + zr + (BEND_LANE * n_bent + 8 if n_bent else 4)
            gaps.append(q(min(GUT_BENT_MAX, max(GUT_MIN, want_w))))
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
    down_side = _down_sides(m, sites, pos, gutter, groups, row_of, lanes_left)

    # Measure.
    def margins(s: DrSite) -> tuple[float, float]:
        left = P + len(lanes_left[s.id]) * lane_w[s.id] if lanes_left[s.id] else P
        down = (down_lanes[s.id] - 1) * LANE + 10 if down_lanes[s.id] else 0
        if down_side[s.id] == "L":
            return left + down, P
        return left, P + down

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
    used = {"protected": False, "muted": False, "user": False}
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
            used["protected" if st is K.CLUSTER else "muted" if st is K.CLUSTER_MUTED
                 else "user"] = True
            data = dict(it.cluster.data) if it.cluster else \
                {"clusters": str(len(it.hidden)), "kind": "unprotected"}
            node = box(it.id, "box", x + lm, y, cw, _card_rows(it, rung >= 2), st, pad_x=5,
                       pad_y=4.5, align="left", h=card_h, valign="middle", parent=s.id,
                       data=data)
            sc.nodes.append(node)
            cards[it.id] = node
            if st is K.USER or st is K.NOTE:
                sc.nodes.append(chip(f"{it.id}:tag", "USER-ADDED", node.x + 5, node.y,
                                     K.TAG_USER, parent=it.id))
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
    # Lanes beside a column: the trunk whose first card is highest takes the outermost
    # lane, so no stub has to cross another trunk's lane; on a card with stubs to
    # several trunks, the stub to the outer lane leaves highest.
    def top_y(gi: int) -> float:
        ys = [cards[c].y for f in groups[gi].flows for c in (f.a, f.b)
              if c in cards and _endpoint_site(m, c, "") == groups[gi].site]
        return min(ys, default=cols[groups[gi].site].y + cols[groups[gi].site].h)

    # Trunks from one card: the one heading farther out (away from the column, on the
    # lanes' side) takes the outer lane, so the channels can nest without a crossing.
    # A target's x is estimated before the boxes are placed: its column, or the mean
    # of the columns its trunks come from.
    centre = {s.id: cols[s.id].x + cols[s.id].w / 2 for s in sites}
    feeds: dict[str, list[float]] = {}
    for g in groups:
        feeds.setdefault(g.target, []).append(centre[g.site])
    target_x = {t: centre.get(t, sum(xs) / len(xs)) for t, xs in feeds.items()}

    def outward(gi: int) -> float:
        g = groups[gi]
        return target_x[g.target] if down_side[g.site] == "R" else -target_x[g.target]

    lane_rank: dict[int, tuple[int, int]] = {}  # trunk -> (rank, lanes in its column)
    for s in sites:
        mine_g = sorted((gi for gi, g in enumerate(groups) if g.site == s.id),
                        key=lambda gi: (-top_y(gi), outward(gi), gi))
        for j, gi in enumerate(mine_g):
            lane_rank[gi] = (j, len(mine_g))
    for gi, g in enumerate(groups):
        for f in g.flows:
            own = f.a if _endpoint_site(m, f.a, f.site_a) == g.site else f.b
            card = cards.get(own)
            if card is not None:
                want(card, down_side[g.site], 10**6 - lane_rank[gi][0],
                     f"{f.id}|{g.target}")

    # Ports: attachments spread along each card side by the other end's y. A flow
    # between two cards on one row runs straight: it takes its slot on the busier side,
    # and the other side spreads its remaining attachments around that y.
    holders = {n.id: n for n in sc.nodes}
    straight = {f.id: (ln.id, rn.id) for _, f, ln, rn in gut_edges
                if ln.kind != "container" and rn.kind != "container"
                and abs(ln.y - rn.y) < 0.5}
    port_y: dict[tuple[str, str, str], float] = {}

    def slots(nid: str, side: str, fixed: dict[str, float]) -> None:
        """Attachments in the order of their other ends; a fixed (straight) flow keeps
        its y and the others spread evenly above and below it, in order."""
        holder = holders[nid]
        keys = [key for _, key in sorted(ports.get((nid, side), []))]
        bounds = [(-1, holder.y)] + sorted(((keys.index(k), y) for k, y in fixed.items()),
                                           key=lambda t: t[1]) + \
            [(len(keys), holder.y + holder.h)]
        for (i0, y0), (i1, y1) in zip(bounds, bounds[1:], strict=False):
            between = keys[i0 + 1:i1]
            for j, key in enumerate(between):
                port_y[(nid, side, key)] = q(y0 + (y1 - y0) * (j + 1) / (len(between) + 1))
        for key, y in fixed.items():
            port_y[(nid, side, key)] = y

    fixed_at: dict[tuple[str, str], dict[str, float]] = {}
    for fid, (l_id, r_id) in sorted(straight.items()):
        busy_left = len(ports.get((l_id, "R"), [])) >= len(ports.get((r_id, "L"), []))
        side_key = (l_id, "R") if busy_left else (r_id, "L")
        holder = holders[side_key[0]]
        keys = [key for _, key in sorted(ports.get(side_key, []))]
        y = q(holder.y + holder.h * (keys.index(fid) + 1) / (len(keys) + 1))
        fixed_at.setdefault(side_key, {})
        other = (r_id, "L") if busy_left else (l_id, "R")
        fixed_at.setdefault(other, {})[fid] = y
    for nid, side in ports:
        slots(nid, side, fixed_at.get((nid, side), {}))

    def py(node: Node, side: str, key: str) -> float:
        return port_y.get((node.id, side, key), q(node.y + node.h / 2))

    kinds: set[str] = set()

    # Gutter edges. Cards on one row join straight; the others bend through a lane.
    # Each flow's label sits beside its arrowhead (labels on one side stack outwards
    # when they would touch); the lanes run between the two label zones, ordered to
    # cross as little as possible.
    for gi in sorted(gutter):
        in_gut = [(f, lft, rgt) for g2, f, lft, rgt in gut_edges if g2 == gi]
        c0 = cols[sites[gi].id]
        gx0, gx1 = c0.x + c0.w, cols[sites[gi + 1].id].x
        sides = _label_sides(m, [f for f, _, _ in in_gut], pos, sites[gi].id)
        geo: dict[str, tuple[float, float]] = {}
        for f, lft, rgt in in_gut:
            yl = q(lft.y + 18) if lft.kind == "container" else py(lft, "R", f.id)
            yr = q(rgt.y + 18) if rgt.kind == "container" else py(rgt, "L", f.id)
            if f.id in straight:
                yr = yl = py(lft, "R", f.id)  # same row: straight (ports agree)
            geo[f.id] = (yl, yr)
        span = gx1 - gx0 - 8
        place: dict[str, tuple[float, float]] = {}  # flow -> (label centre x, y)
        zone = {"L": 0.0, "R": 0.0}
        for side in ("L", "R"):
            mine = sorted((f for f, _, _ in in_gut if sides[f.id] == side),
                          key=lambda f: (geo[f.id][0 if side == "L" else 1], f.id))
            placed: list[tuple[float, float, float, float]] = []  # y0, y1, off0, off1
            for f in mine:
                y = geo[f.id][0 if side == "L" else 1]
                _, lw, lh = label_block([flow_text(f)], span)
                off = 0.0
                for y0, y1, _o0, o1 in placed:
                    if y - lh / 2 < y1 + 1 and y0 - 1 < y + lh / 2 and off < o1 + 2:
                        off = max(off, o1 + 2)
                placed.append((y - lh / 2, y + lh / 2, off, off + lw))
                zone[side] = max(zone[side], off + lw)
                cx = gx0 + 4 + off + lw / 2 if side == "L" else gx1 - 4 - off - lw / 2
                place[f.id] = (q(cx), y)
        bent = [t for t in in_gut if abs(geo[t[0].id][0] - geo[t[0].id][1]) >= 0.5]
        lane_of: dict[str, float] = {}
        if bent:
            lane_lo = gx0 + 6 + zone["L"]
            lane_hi = gx1 - 6 - zone["R"]
            if lane_hi - lane_lo < 4:  # crowded: lanes share the middle of the gutter
                lane_lo, lane_hi = gx0 + 6, gx1 - 6
            spans = [(geo[f.id][0], geo[f.id][1]) for f, _, _ in bent]
            n = len(bent)
            for j, k in enumerate(route.lane_order(spans)):
                lane_of[bent[k][0].id] = q(lane_lo + (lane_hi - lane_lo) * (j + 0.5) / n)
        placed_labels: list[EdgeLabel] = []
        for f, lft, rgt in in_gut:
            yl, yr = geo[f.id]
            xl, xr = lft.x + lft.w, rgt.x
            if f.id in lane_of:
                lx = lane_of[f.id]
                pts = [(xl, yl), (lx, yl), (lx, yr), (xr, yr)]
            else:
                pts = [(xl, yl), (xr, yl)]
            a_left = cards.get(f.a) is lft or (lft.kind == "container" and
                                               _ends(m, f)[0] == lft.id)
            if not a_left:
                pts = pts[::-1]
            reps = f.repl
            kinds.add(fmt.repl_strength(reps))
            e = edge(f.id, f.a, f.b, pts, _strength(reps), arrow_end=bool(f.forward),
                     arrow_start=bool(f.backward), data={"kind": "replication"})
            cx, cy = place[f.id]
            lab = edge_label([flow_text(f)], cx, cy, span)
            if _gap_to_path(lab.x, lab.y, lab.w, lab.h, e.points) > ANCHOR:
                # Stacked off its own line (several flows into one card): beside the
                # arrowhead on its last run instead, above or below the line.
                lab = _on_last_run(e, lab, placed_labels)
            placed_labels.append(lab)
            e.labels.append(lab)
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
    # Below: one trunk per (source zone, target). Each trunk drops down a lane on its
    # column's right into its own channel (nested, so no trunk cuts another's run),
    # enters the target at its own point along the top edge, and carries one label on
    # its source stub, so the direction is never ambiguous.
    if groups:
        targets: dict[str, _Target] = {}
        by_target: dict[str, list[int]] = {}
        for gi, g in enumerate(groups):
            if g.target not in targets:
                targets[g.target] = _target(m, g.target, part_of, len(parts), in_part)
            by_target.setdefault(g.target, []).append(gi)
        lane_x: dict[int, float] = {}
        for gi, g in enumerate(groups):
            col = cols[g.site]
            j, n = lane_rank[gi]
            if down_side[g.site] == "L":  # mirrored: the highest trunk outermost
                lane_x[gi] = q(col.x + P + LANE * (n - 1 - j) + 1)
            else:
                lane_x[gi] = q(col.x + col.w - P - LANE * (n - 1 - j) - 1)
        labels = {gi: _target_lines(m, [g]) for gi, g in enumerate(groups)}
        n_boxes = sum(1 for t in by_target if targets[t].site is None)
        bw = q(min(BOX_W, (page.width - 2 * MX - 14 * max(0, n_boxes - 1))
                   / max(1, n_boxes)))
        tx_of: dict[str, float] = {}
        for t, gis in by_target.items():
            if targets[t].site is not None:
                c = cols[t]
                tx_of[t] = c.x + c.w / 2
            else:
                tx_of[t] = sum(lane_x[gi] for gi in gis) / len(gis)
        t_order = sorted(by_target, key=lambda t: (tx_of[t], t))
        boxed = [t for t in t_order if targets[t].site is None]
        xs = _deoverlap([tx_of[t] - bw / 2 for t in boxed], bw, 14, MX,
                        page.width - MX) if boxed else []
        box_x = dict(zip(boxed, xs, strict=True))
        # Entry points along each target's top (or a zone's bottom), by source x.
        entry: dict[int, float] = {}
        for t, gis in by_target.items():
            if targets[t].site is not None:
                c = cols[t]
                e_lo, e_hi = c.x + 10, c.x + c.w - 10
            else:
                e_lo = box_x[t] + 5.5 + chip_w(targets[t].chip) + 6
                e_hi = box_x[t] + bw - 8
            spread = {int(k): v for k, v in route.spread(
                e_lo, e_hi, [(lane_x[gi], str(gi)) for gi in gis]).items()}
            # A drop never runs beside another trunk's lane (closer than a lane
            # pitch): it moves under its own lane, or the least distance along the
            # target, staying between its neighbours' entries.
            seq = sorted(gis, key=lambda gi: spread[gi])
            for k, gi in enumerate(seq):
                w0 = max(e_lo, spread[seq[k - 1]] + LANE) if k else e_lo
                w1 = min(e_hi, spread[seq[k + 1]] - LANE) if k + 1 < len(seq) else e_hi
                others = [x for gj, x in lane_x.items() if gj != gi]

                def ok(x: float, w0: float = w0, w1: float = w1,
                       others: list[float] = others) -> bool:
                    return w0 <= x <= w1 and all(abs(x - o) >= LANE - 0.5 for o in others)

                at = spread[gi]
                if not ok(at):
                    tries = [lane_x[gi]] + sorted(
                        (at + d * sgn for d in range(1, int(e_hi - e_lo) + 1)
                         for sgn in (1, -1)), key=lambda x: (abs(x - at), x))
                    spread[gi] = q(next((x for x in tries if ok(x)), at))
            entry.update(spread)
        lab_h = max(label_block(v, 120)[2] for v in labels.values())
        pitch = q(max(CHANNEL_MIN, lab_h + 3))
        chan_top = q(site_bottom + 6 + lab_h / 2)
        trunks = []
        for gi, g in enumerate(groups):
            left = down_side[g.site] == "L"
            stubs = tuple(sorted({(cards[c].x if left else cards[c].x + cards[c].w,
                                   cards[c].y + cards[c].h / 2)
                                  for f in g.flows for c in (f.a, f.b) if c in cards
                                  and _endpoint_site(m, c, "") == g.site}))
            col = cols[g.site]
            trunks.append(route.Trunk(stubs=stubs, lane=lane_x[gi],
                                      lane_top=min((y for _, y in stubs),
                                                   default=col.y + col.h),
                                      entry=entry[gi], bottom=site_bottom + 200))
        level = route.channel_order(trunks, chan_top, pitch)
        chan = {gi: q(chan_top + level[gi] * pitch) for gi in range(len(groups))}
        row_y = q(max(chan.values()) + lab_h / 2 + 8 + CHIP_H / 2)
        boxes: dict[str, Node] = {}
        for t in boxed:
            tg, bx = targets[t], box_x[t]
            tbox = box(t, "box", bx, row_y, bw, _target_rows(tg), tg.style, pad_x=5,
                       pad_y=CHIP_H / 2 + 4, data={"availability_zone": tg.name,
                                                  "name": tg.name})
            sc.nodes.append(tbox)
            sc.nodes.append(chip(f"{t}:tag", tg.chip, bx + 5.5, row_y,
                                 K.TAG if tg.style is K.REF else K.TAG_MUTED, parent=t))
            boxes[t] = tbox
        if boxes:
            hmax = max(b.h for b in boxes.values())
            for b in boxes.values():
                b.h = hmax
            content_bottom = max(content_bottom, row_y + hmax)
        else:
            content_bottom = max(content_bottom, max(chan.values()) + lab_h / 2 + 4)
        trunk_edges: list[tuple[int, Edge, float, float, float, float]] = []
        group_of: dict[str, int] = {}  # trunk edge -> its trunk (they share the run)
        for gi, g in enumerate(groups):
            t = g.target
            tg = targets[t]
            cy = chan[gi]
            lx = lane_x[gi]
            dx = entry[gi]
            dy = cols[t].y + cols[t].h if tg.site is not None else boxes[t].y
            need_label = True
            for f in g.flows:
                here_a = _endpoint_site(m, f.a, f.site_a) == g.site
                src_card = cards.get(f.a if here_a else f.b)
                if src_card is not None:
                    side = down_side[g.site]
                    yy = py(src_card, side, f"{f.id}|{g.target}")
                    sx = src_card.x if side == "L" else src_card.x + src_card.w
                    pts = [(sx, yy), (lx, yy), (lx, cy), (dx, cy), (dx, dy)]
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
                    trunk_edges.append((gi, e, lx, dx, cy, dy))
                group_of[e.id] = gi
                sc.edges.append(e)
        # Each trunk's label goes on its own channel, in the free stretch (between the
        # vertical runs that cross that channel) nearest its source; with no stretch
        # wide enough, beside its drop into the target, on the side and at the height
        # where no other trunk's run, no label and no card lies under it.
        segs = [(group_of.get(e.id), p0, p1) for e in sc.edges
                if e.data.get("kind") == "replication"
                for p0, p1 in zip(e.points, e.points[1:], strict=False)]
        verticals = [(p0[0], min(p0[1], p1[1]), max(p0[1], p1[1]))
                     for _, p0, p1 in segs if abs(p0[0] - p1[0]) < 0.01]
        taken: list[tuple[float, float, float, float]] = [
            (lb.x, lb.y, lb.x + lb.w, lb.y + lb.h) for e in sc.edges for lb in e.labels]
        solid = [(n.x, n.y, n.x + n.w, n.y + n.h) for n in sc.nodes if n.kind == "box"]
        for gi, e, lx, dx, cy, dy in trunk_edges:
            _, lw, _ = label_block(labels[gi], 120)
            run_lo, run_hi = sorted((lx, dx))
            busy = [(x0, x1) for x0, y0, x1, y1 in taken if y0 < cy < y1]
            cuts = sorted({x for x, y0, y1 in verticals
                           if run_lo < x < run_hi and y0 < cy < y1})
            stops = [run_lo, *cuts, run_hi]
            # The stretches between those runs, less what labels already cover there.
            pieces = list(zip(stops, stops[1:], strict=False))
            for b0, b1 in sorted(busy):
                pieces = [p for g0, g1 in pieces
                          for p in ((g0, min(g1, b0)), (max(g0, b1), g1)) if p[1] > p[0]]
            free = [(g0, g1) for g0, g1 in pieces if g1 - g0 >= lw + 8]
            if free:
                g0, g1 = min(free, key=lambda g: abs((g[0] + g[1]) / 2 - lx))
                start = g0 if abs(g0 - lx) <= abs(g1 - lx) else g1 - lw - 8
                lab = edge_label(labels[gi], start + 4 + lw / 2, cy, 120)
            else:
                lab = _beside_drop(labels[gi], lw, dx, cy, dy, gi, segs, taken, solid,
                                   page.width)
            e.labels.append(lab)
            taken.append((lab.x, lab.y, lab.x + lab.w, lab.y + lab.h))
        route.deoverlap(sc.edges, page.width - MX)

    # Glue: an end whose cluster is not drawn here (a remote AZ's cluster, a zone in
    # another part, a cluster in the "+N" card) attaches to what stands for it.
    present = {n.id: n for n in sc.nodes}
    for e in sc.edges:
        if e.data.get("kind") != "replication":
            continue
        target = e.id.split("|", 1)[1] if "|" in e.id else None
        for end in ("src", "dst"):
            ident = getattr(e, end)
            if ident in present:
                continue
            home = _endpoint_site(m, ident, "")
            if home in present:
                stand_in = home
            elif target in present:
                stand_in = str(target)
            else:
                stand_in = f"{home}:unprotected"
            if stand_in not in present:
                continue
            setattr(e, end, stand_in)
            extra = {f"{end}_cluster": ident}
            if home in part_of and home not in pos:
                extra["continues_in"] = f"part {part_of[home] + 1} of {len(parts)}"
            e.data = dict(sorted({**e.data, **extra}.items()))

    # Any crossing left gets a hop, so lines that cross are never read as joined.
    hops = route.hop([e for e in sc.edges if e.data.get("kind") == "replication"])

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
    lh = legend(sc, items_l, MX, content_bottom + LEGEND_GAP, lg_w)
    right_x = max(right_x, max(n.x + n.w for n in sc.nodes))
    sc.width = q(min(page.width, right_x + MX))
    sc.height = q(content_bottom + LEGEND_GAP + lh + 1)
    sc.meta = {"sites": [s.id for s in sites], "rung": rung, "hops": hops}
    return sc


#: A run of a flow: (its trunk, or None for a gutter or bracket flow, start, end).
Seg = tuple[int | None, tuple[float, float], tuple[float, float]]


def _beside_drop(lines: list[LabelText], lw: float, dx: float, cy: float, dy: float,
                 gi: int, segs: list[Seg], taken: list[tuple[float, float, float, float]],
                 solid: list[tuple[float, float, float, float]], width: float) -> EdgeLabel:
    """Trunk `gi`'s label beside its drop (x `dx`, from channel `cy` down to `dy`): the
    middle first, then nearer either end, right side before left; the first spot that
    no other trunk's run, no label and no card touches; with none clear, the spot
    touching the fewest."""
    probe = edge_label(lines, dx + 3 + lw / 2, (cy + dy) / 2, 120, background=False)
    lo, hi = cy + 1 + probe.h / 2, dy - 1 - probe.h / 2
    mid = (cy + dy) / 2
    ys = [mid]
    if hi > lo:
        steps = max(1, int((hi - lo) // 2))
        ys += sorted((lo + (hi - lo) * k / steps for k in range(steps + 1)),
                     key=lambda y: (abs(y - mid), y))
    best: tuple[int, EdgeLabel] | None = None
    for y in ys:
        for c in (dx + 3 + lw / 2, dx - 3 - lw / 2):
            lb = edge_label(lines, c, y, 120, background=False)
            if lb.x < MX or lb.x + lb.w > width - MX:
                continue
            x0, y0, x1, y1 = lb.x, lb.y, lb.x + lb.w, lb.y + lb.h
            hits = sum(1 for g, p0, p1 in segs if g != gi and
                       min(p0[0], p1[0]) < x1 and x0 < max(p0[0], p1[0]) and
                       min(p0[1], p1[1]) < y1 and y0 < max(p0[1], p1[1]))
            hits += sum(1 for a0, b0, a1, b1 in [*taken, *solid]
                        if a0 < x1 and x0 < a1 and b0 < y1 and y0 < b1)
            if hits == 0:
                return lb
            if best is None or hits < best[0]:
                best = (hits, lb)
    return best[1] if best else probe


# ─── entry point ───────────────────────────────────────────────────────


def _caption(m: DrModel, sites: list[DrSite], part: tuple[int, int]) -> str:
    names = fmt.join_names([short_name(s.name) for s in sites])
    head = "Disaster recovery design" + (" (edited)" if m.edited else "")
    if part[1] > 1:
        return (f"{head}, part {part[0]} of {part[1]}: {names}, protection "
                "policies by source and target cluster, type and RPO.")
    return f"{head}: protection policies by source and target cluster, type and RPO."


def _best(m: DrModel, sites: list[DrSite], parts: list[list[DrSite]], page: Page,
          limit: float, sid: str, least: int = 0) -> tuple[Scene, bool, int]:
    """The first rung from `least` that fits (scene, fits, rung): every card;
    unprotected clusters in one card; then cards without their detail line."""
    rung = least
    sc = _layout_part(m, sites, parts, page, rung, sid)
    while sc.height > limit and rung < 2:
        rung += 1
        sc = _layout_part(m, sites, parts, page, rung, sid)
    return sc, sc.height <= limit, rung


def layout(model: DrModel, page: Page | None = None, refs: Refs | None = None,
           first_budget: Budget | None = None) -> list[Scene]:
    """The figure's parts; part 1 under the section's summary when it fits there, else
    on a page of its own (`fit.place_first`)."""
    return place_first(lambda budget: _layout(model, page, refs, budget), first_budget)


def _layout(model: DrModel, page: Page | None = None, refs: Refs | None = None,
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
    # One detail level for the estate: every part at the rung the tightest one needs.
    least = max((_best(model, sites, parts, page,
                       limit(pi, _layout_part(model, sites, parts, page, 0, "probe").width),
                       "probe")[2] for pi, sites in enumerate(parts)), default=0)
    scenes: list[Scene] = []
    for pi, sites in enumerate(parts):
        sid = "dr" if len(parts) == 1 else f"dr-{pi + 1}"
        probe = _layout_part(model, sites, parts, page, 0, sid)
        sc, _, _ = _best(model, sites, parts, page, limit(pi, probe.width), sid, least)
        sc.part = (pi + 1, len(parts))
        sc.caption = _caption(model, sites, sc.part)
        scenes.append(sc)
    return scenes
