"""Solution Conceptual Overview layout: availability zones side by side.

Each site (one Prism Central) is an "AZ" container holding its Prism Central block
(solid iris, deployment and services in use) and its cluster cards (hypervisor, nodes,
RF, hardware, data services, DSF storage, external arrays). The cluster hosting the PC
comes first with a HOSTS PC chip.

Links are drawn only where the data has them:

* between adjacent sites, in a labelled gutter: AZ pairing (dashed, at the PC block;
  muted when reported not connected) and replication (solid, weight by the strongest
  type), labelled per direction: schedules present both ways read "both directions",
  one-way schedules sit under "a to b";
* to availability zones outside the part (remote AZs, or a site drawn in another part):
  in a dashed column on the right when every such link starts in the right-most site
  ("side" mode), else as buses dropping to a row of boxes under the sites ("below"
  mode): one channel per (target, kind), one edge and one label per source;
* between non-adjacent sites of a part, through the same below channels.

Scaling (text never shrinks below 6 pt; content splits instead):

* parts of up to 3 sites, linked sites kept together;
* per part, a density ladder: full cards in 1..N columns; then, per site, the cluster
  hosting PC plus at least the 2 largest clusters and a short "+N more clusters" card
  pointing at the clusters table;
* a part that still does not fit its height budget gives up its last site to a new part.
"""

from __future__ import annotations

import math
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
    edge,
    edge_label,
    label_block,
    legend,
    measure,
    place_lines,
    text_h,
)
from ..facts import short_name
from ..model import Card, ConceptualModel, Link, Remote, Repl, Site
from ..scene import Node, Scene, Style, q
from . import parts as parts_mod

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
CHANNEL = 9.0  # below-mode channel pitch
PER_PART = 3
#: Clusters each summarised site keeps drawn: the PC host plus the 2 largest.
MIN_KEEP = 3
PILL_H = 8.5
PILL_GAP = 2.5


@dataclass(frozen=True)
class Page:
    """The page a figure is laid out for (pt): 672 pt fits 1:1 on Letter and A4
    landscape; 440 pt leaves room for a caption on a landscape page."""

    width: float = 672.0
    max_h: float = 440.0


@dataclass(frozen=True)
class Refs:
    """Cross references the figure text points at (filled in once tables are numbered)."""

    clusters_table: str = "the clusters table"


#: Height budget for part 1 as a function of its width (the report fits the heading,
#: the design summary and the caption on the same page).
Budget = Callable[[float], float]


@dataclass
class _Target:
    """Something outside the drawn sites that a part links to."""

    id: str
    name: str
    sub: str
    kind_line: str
    note: str
    chip: str
    ref: bool = False  # a site of this inventory drawn in another part


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


def plan_parts(model: ConceptualModel, per: int = PER_PART,
               sites: list[Site] | None = None) -> list[list[Site]]:
    """Group sites (default: all, in model order) into parts of up to `per`, by the rule
    every per-AZ figure shares (`parts.plan`)."""
    pool = sites if sites is not None else model.sites
    by_id = {s.id: s for s in pool}
    out = parts_mod.plan([s.id for s in pool], _pairs(model), per)
    return [[by_id[i] for i in p] for p in out] or [[]]


def _pairs(model: ConceptualModel) -> list[tuple[str, str]]:
    return [(lk.a, lk.b) for lk in model.links]


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
                          key=lambda t: (not t.ref, order.get(t.id, -1), t.id))
    tids = [t.id for t in plan.targets]
    plan.external.sort(key=lambda e: (pos[e[0]], tids.index(e[1])))
    if plan.targets and not plan.inner and all(x == sites[-1].id for x, _, _ in plan.external):
        avail = page.width - 2 * MX - sum(_gaps(plan)) - SIDE_GUT - REMOTE_W
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
                   chip="AZ", ref=True)


def _remote_target(r: Remote) -> _Target:
    if r.kind == "xi":
        sub, kind = "Nutanix Cloud (Xi)", "Nutanix Cloud"
    else:
        sub, kind = "Physical AZ", "Prism Central"
    if region := fmt.region_label(r.region):
        sub += f", {region}"
    return _Target(id=r.id, name=r.name, sub=sub, kind_line=kind,
                   note="not in this inventory", chip="REMOTE AZ")


def _gaps(plan: _Plan) -> list[float]:
    linked = {(x, y) for x, y, _ in plan.gutter}
    ids = [s.id for s in plan.sites]
    return [GUT if (a, b) in linked else GAP for a, b in zip(ids, ids[1:], strict=False)]


# ─── text of the boxes ─────────────────────────────────────────────────


def _site_counts(s: Site) -> tuple[int, int, int]:
    """(clusters, nodes, clusters not drawn) of a site, as collected: an edited model
    keeps the collected counts, so a cluster removed from the figure is "not drawn"."""
    drawn = [c for c in s.cards if c.kind == "cluster"]
    if s.cluster_count is None:
        return len(drawn), sum(c.nodes or 0 for c in drawn), 0
    nodes = s.node_count if s.node_count is not None else sum(c.nodes or 0 for c in drawn)
    return s.cluster_count, nodes, max(0, s.cluster_count - len(drawn))


def _site_rows(s: Site) -> list[Row]:
    if s.kind == "user":
        return [Row(s.name, K.SIZE_SITE, True, K.INK, 2, 7.5),
                Row(s.note or "Added by the author, not collected", K.SIZE_SMALL, False,
                    K.INK_3, 2, gap_before=0.5)]
    n_clusters, nodes, missing = _site_counts(s)
    what = "Availability zone" if s.kind == "pc" else "Prism Element"
    # A count stays with its noun ("47 nodes", "1 not drawn"): a wrap falls between them.
    def keep(text: str) -> str:
        return text.replace(" ", fmt.NBSP)

    gone = f" ({keep(f'{missing:,} not drawn')})" if missing else ""
    rows = [Row(s.name, K.SIZE_SITE, True, K.INK, 2, 7.5),
            Row(f"{what}, {keep(fmt.plural(n_clusters, 'cluster'))}{gone}, "
                f"{keep(fmt.plural(nodes, 'node'))}",
                K.SIZE_SMALL, False, K.INK_3, 2, gap_before=0.5)]
    if s.note:  # a line the author added in the editor
        rows.append(Row(s.note, K.SIZE_SMALL, False, K.INK_3, 2))
    return rows


def _pc_rows(s: Site, inner_w: float) -> list[Row]:
    pc = s.pc
    assert pc is not None
    vms = pc.vms or 1
    shape = "single VM" if vms == 1 else f"scale-out, {vms:,} PCVMs"
    line1 = ", ".join(x for x in (pc.version, shape) if x)
    sizing = [pc.size] if pc.size else []
    if pc.vm_vcpus or pc.vm_mem_gib:  # one PC VM's configured size
        if pc.vm_vcpus:
            sizing.append(f"{pc.vm_vcpus:,} vCPU")
        if pc.vm_mem_gib:
            sizing.append(f"{pc.vm_mem_gib:,} GiB")
        suffix = " each" if vms > 1 else ""
    else:  # older snapshots: totals across the PC VMs
        if pc.vcpus:
            sizing.append(f"{pc.vcpus:,} vCPU")
        if pc.mem_gib:
            sizing.append(f"{pc.mem_gib:,} GiB")
        suffix = " total" if vms > 1 else ""
    line2 = ", ".join(sizing) + (suffix if len(sizing) > (1 if pc.size else 0) else "")
    rows = [Row("Prism Central", K.SIZE_PC_TITLE, True, K.WHITE),
            Row(line1, K.SIZE_BODY, False, K.WHITE, 2, gap_before=0.5)]
    if line2:
        rows.append(Row(line2, K.SIZE_SMALL, False, K.WHITE, 2))
    items = [f"{v.label}{fmt.NBSP}({v.count:,})" if v.count else v.label for v in pc.services]
    for line in _pack(items, " · ", K.SIZE_SMALL, inner_w)[:2]:
        rows.append(Row(line, K.SIZE_SMALL, False, K.IRIS_TINT, 1))
    return rows


def _pack(items: list[str], sep: str, size: float, max_w: float) -> list[str]:
    """Items joined by `sep` into lines, breaking between items (never after a
    separator)."""
    lines: list[str] = []
    cur = ""
    for it in items:
        cand = f"{cur}{sep}{it}" if cur else it
        if cur and tx.width(cand, size) > max_w:
            lines.append(cur)
            cur = it
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return lines


def _pills(labels: list[str]) -> list[tuple[str, float]]:
    return [(lab, q(tx.width(lab, K.SIZE_SMALL, True) + 7)) for lab in labels]


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


def _title_row(c: Card, width: float) -> Row:
    """Two lines when the name fits in two down to the 6 pt floor, else three; never
    ellipsised before that. A note reads as body text, up to four lines."""
    if c.kind == "note":
        return Row(c.name, K.SIZE_BODY, False, K.INK_2, 4, tx.MIN_SIZE)
    _, two = tx.fit(c.name, K.SIZE_CARD_TITLE, True, width, 2, tx.MIN_SIZE)
    lines = 2 if not two[-1].endswith(tx.ELLIPSIS) else 3
    return Row(c.name, K.SIZE_CARD_TITLE, True, K.INK, lines, tx.MIN_SIZE)


#: Chip per editor role ("workload" draws none).
_ROLE_CHIP = {"hosts_pc": "HOSTS PC", "mgmt": "MGMT", "dr_target": "DR TARGET"}


def _role_chip(c: Card) -> str | None:
    if c.kind != "cluster":
        return None
    if c.role is not None:
        return _ROLE_CHIP.get(c.role)
    return "HOSTS PC" if c.hosts_pc else None


def _card_parts(c: Card, w: float) -> tuple[list[Row], list[Row], bool, float]:
    """(title rows, body rows, pills on the title row, title width) for card `c`."""
    inner = w - 10
    pills = _pills(c.services)
    pills_w = sum(pw for _, pw in pills) + PILL_GAP * max(0, len(pills) - 1)
    inline = bool(pills) and pills_w <= inner * 0.5
    if inline:  # a name that would wrap or lose characters keeps the full width
        _, fitted = tx.fit(c.name, K.SIZE_CARD_TITLE, True, inner - pills_w - 4, 1)
        inline = len(fitted) == 1 and not fitted[0].endswith(tx.ELLIPSIS)
    title_w = inner - (pills_w + 4 if inline else 0)
    if c.kind != "cluster":
        return ([_title_row(c, title_w)],
                [Row(c.note, K.SIZE_SMALL, False, K.INK_3, 2)] if c.note else [], inline,
                title_w)
    body = [Row(fmt.card_body(c), K.SIZE_BODY, False, K.INK_2, 3)]
    if c.note:  # a note the author added to a collected cluster
        body.append(Row(c.note, K.SIZE_SMALL, False, K.INK_3, 2))
    return ([_title_row(c, title_w)], body, inline, title_w)


def _strips(c: Card, w: float) -> list[list[tuple[str, str, float]]]:
    """Storage strip rows: [(kind, text, width)] per row, kind "dsf" or an array id.
    The DSF strip and the arrays share a row when they fit, else stack."""
    sw = w - 10
    items: list[tuple[str, str, float]] = []
    st = fmt.storage_text(c)
    if st:
        items.append(("dsf", st, q(tx.width(st, K.SIZE_SMALL) + 7)))
    for xa in c.external:
        # The product as named (decision 5), never shortened; the array's own name is
        # in the shape data and drawn in full in the logical cluster design.
        label = xa.product
        items.append((xa.id, label, q(tx.width(label, K.SIZE_SMALL, True) + 7)))
    if not items:
        return []
    if sum(iw for _, _, iw in items) + PILL_GAP * (len(items) - 1) <= sw:
        return [items]
    return [[it] for it in items]


def _strip_h(text: str, w: float, bold: bool) -> float:
    return q(text_h([Row(text, K.SIZE_SMALL, bold, K.LIVE, 2)], w - 6) + 1.6)


def _strips_h(c: Card, w: float) -> float:
    rows = _strips(c, w)
    h = 0.0
    for row in rows:
        widths = _row_widths(row, w - 10)
        h += 2.5 + max(_strip_h(t, rw, k != "dsf") for (k, t, _), rw in zip(row, widths,
                                                                              strict=True))
    return q(h)


def _row_widths(row: list[tuple[str, str, float]], sw: float) -> list[float]:
    """Widths of the strips in one row: arrays at their text width, DSF takes the rest."""
    if len(row) == 1:
        return [sw]
    fixed = sum(iw for k, _, iw in row if k != "dsf")
    rest = sw - fixed - PILL_GAP * (len(row) - 1)
    return [rest if k == "dsf" else iw for k, _, iw in row]


def _card_h(c: Card, w: float) -> float:
    title, body, inline, title_w = _card_parts(c, w)
    h = 5.0 + text_h(title, title_w) + text_h(body, w - 10)
    if c.services and not inline:
        h += len(_pill_rows(_pills(c.services), w - 10)) * (PILL_H + 2)
    if c.kind == "cluster":
        h += _strips_h(c, w)
    return q(h + 4.0)


def _more_rows(hidden: list[Card], refs: Refs, inner_w: float) -> list[Row]:
    """"+N more clusters", the nodes (with the hypervisor split when it fits on the
    line), and "listed in Table 5-2" on a line of its own."""
    nodes = fmt.plural(sum(c.nodes or 0 for c in hidden), "node")
    mixed = f"{nodes} ({fmt.hypervisor_mix(hidden)})"
    ref = refs.clusters_table.replace(" ", fmt.NBSP)
    return [Row(f"+{fmt.plural(len(hidden), 'more cluster')}", K.SIZE_CARD_TITLE, True,
                K.INK_2),
            Row(mixed if tx.width(mixed, K.SIZE_SMALL) <= inner_w else nodes, K.SIZE_SMALL,
                False, K.INK_3, 2, gap_before=0.5),
            Row(f"listed in {ref}", K.SIZE_SMALL, False, K.INK_3, 2)]


# ─── one part ──────────────────────────────────────────────────────────


@dataclass
class _Try:
    cols: int
    keep: int | None  # None = every card; else cards kept per site (+ a summary card)


def _visible(s: Site, keep: int | None) -> tuple[list[Card], list[Card]]:
    """(drawn, summarised) cards. Only collected clusters are summarised: what the
    author added in the editor is always drawn."""
    clusters = [c for c in s.cards if c.kind == "cluster"]
    if keep is None or len(clusters) <= keep + 1:
        return list(s.cards), []
    chosen = [c for c in clusters if c.hosts_pc][:keep]
    rest = sorted((c for c in clusters if c not in chosen),
                  key=lambda c: (-(c.nodes or 0), s.cards.index(c)))
    chosen += rest[: keep - len(chosen)]
    ids = {c.id for c in chosen} | {c.id for c in s.cards if c.kind != "cluster"}
    return [c for c in s.cards if c.id in ids], [c for c in s.cards if c.id not in ids]


def _ladder(plan: _Plan, max_cols: int, summarise: bool) -> list[_Try]:
    """Full detail in 1..N columns; or, when the estate is summarised, decision 7 for
    every AZ: the PC host plus the 2 largest clusters, then "+N more" (AZs with up to
    4 clusters show them all), so equal AZs look equal in every part."""
    if summarise:
        return [_Try(1, MIN_KEEP)]
    return [_Try(c, None) for c in range(1, max_cols + 1)]


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
    pc_h = max((text_h(_pc_rows(s, inner_w - 10), inner_w - 10) + 10 for s in sites if s.pc),
               default=0.0)

    # Grid per site; one height per grid row across the part (rows line up between
    # sites), cluster cards stretch to their row, the "+N more" card keeps its own height.
    grids: dict[str, list[list[Card | None]]] = {}
    row_h: list[float] = []
    for s in sites:
        shown, hidden = vis[s.id]
        cells: list[Card | None] = [*shown, *([None] if hidden else [])]
        rows_n = max(1, math.ceil(len(cells) / c))
        per_row = math.ceil(len(cells) / rows_n) if cells else 1
        grid = [cells[i:i + per_row] for i in range(0, len(cells), per_row)]
        grids[s.id] = grid
        for r, row in enumerate(grid):
            hs = [_card_h(cd, card_w) for cd in row if cd is not None]
            while len(row_h) <= r:
                row_h.append(0.0)
            row_h[r] = max(row_h[r], *hs, 26.0) if hs else max(row_h[r], 26.0)
    more_h = {s.id: q(text_h(_more_rows(vis[s.id][1], refs, card_w - 10), card_w - 10) + 9)
              for s in sites if vis[s.id][1]}

    top = TOP
    head_top = top + CHIP_H / 2 + 4.5
    pc_y = q(head_top + header_h + 5)
    cards_y = q(pc_y + pc_h + 11) if has_pc else q(head_top + header_h + 7)
    row_y = [cards_y]
    for h in row_h:
        row_y.append(q(row_y[-1] + h + RG))

    site_nodes: dict[str, Node] = {}
    own_h: dict[str, float] = {}  # bottom of empty user-added zones
    pc_nodes: dict[str, Node] = {}
    first_cards: dict[str, Node] = {}
    drawn: list[str] = []
    summarised: list[str] = []
    bottoms = []
    x = MX
    used = {"more": False, "strip": False, "external": False, "user": False,
            "compute": False}
    for i, s in enumerate(sites):
        user_site = s.kind == "user"
        cont = box(s.id, "container", x, top, site_w, _site_rows(s),
                   K.USER_SITE if user_site else K.SITE, pad_x=P,
                   pad_y=head_top - top, h=10, data=dict(s.data))
        sc.nodes.append(cont)
        if user_site:
            used["user"] = True
            sc.nodes.append(chip(f"{s.id}:tag", "USER-ADDED", x + 5.5, top, K.TAG_USER,
                                 parent=s.id))
        else:
            sc.nodes.append(chip(f"{s.id}:tag", "AZ" if s.kind == "pc" else "PRISM ELEMENT",
                                 x + 5.5, top, K.TAG, parent=s.id))
        site_nodes[s.id] = cont
        if s.pc:
            pcn = box(s.pc.id, "box", x + P, pc_y, inner_w, _pc_rows(s, inner_w - 10), K.PC,
                      pad_x=5, pad_y=5, h=pc_h, valign="middle", parent=s.id,
                      data={"prism_central": s.name})
            sc.nodes.append(pcn)
            pc_nodes[s.id] = pcn
        shown, hidden = vis[s.id]
        row_tops: list[Node] = []
        bottom = cards_y - 4
        for r, row in enumerate(grids[s.id]):
            gx = x + P + (inner_w - (len(row) * card_w + (len(row) - 1) * CG)) / 2
            for col, cd in enumerate(row):
                cx, cy = q(gx + col * (card_w + CG)), row_y[r]
                if cd is not None:
                    node = _card(sc, s, cd, cx, cy, card_w, row_h[r], used)
                    drawn.append(cd.id)
                else:
                    node = box(f"{s.id}:more", "box", cx, cy, card_w,
                               _more_rows(hidden, refs, card_w - 10), K.MORE, pad_x=5,
                               h=more_h[s.id],
                               valign="middle", parent=s.id,
                               data={"clusters": str(len(hidden))})
                    sc.nodes.append(node)
                    summarised.extend(h.id for h in hidden)
                    used["more"] = True
                bottom = max(bottom, node.y + node.h)
                if r == 0:
                    row_tops.append(node)
        if row_tops:
            first_cards[s.id] = row_tops[0]
        if s.kind == "user" and not s.cards:
            # An empty zone the author added is just its header, not a full-height box.
            own_h[s.id] = q(head_top + text_h(_site_rows(s), inner_w) + P)
        else:
            bottoms.append(bottom + P + 1.5)
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
    site_bottom = q(max(bottoms, default=max(own_h.values(), default=top + 40)))
    for sid_, nd in site_nodes.items():
        nd.h = q(own_h.get(sid_, site_bottom) - nd.y)

    kinds: dict[str, bool] = {"pairing": False, "pairing_down": False, "replication": False}
    content_bottom = site_bottom

    names = {s.id: short_name(s.name) for s in model.sites}
    names.update({r.id: r.name for r in model.remotes})

    # Adjacent sites: the labelled gutter.
    for a, b, lks in plan.gutter:
        na, nb = site_nodes[a], site_nodes[b]
        gx0, gx1 = na.x + na.w, nb.x
        gc = (gx0 + gx1) / 2
        y_p = _pair_y(pc_nodes.get(a), na)
        _pairing_edge(sc, [lk for lk in lks if lk.kind == "pairing"], a, b,
                      (gx0 + 0.5, gx1 - 0.5), y_p, gc, gx1 - gx0 - 6, kinds)
        for lk in [lk for lk in lks if lk.kind == "replication"][:1]:
            kinds["replication"] = True
            lines = _colored(fmt.link_lines(lk, names[lk.a], names[lk.b]))
            _, _, lh = label_block(lines, gx1 - gx0 - 6)
            card = first_cards.get(a)
            base = card.y + card.h / 2 if card else y_p + 20
            y_r = q(max(base, y_p + 7 + lh / 2 + 2))
            left_to_right = [(gx0 + 0.5, y_r), (gx1 - 0.5, y_r)]
            pts = left_to_right if lk.a == a else left_to_right[::-1]
            e = edge(lk.id, lk.a, lk.b, pts, _repl_style(lk.repl),
                     arrow_end=bool(lk.forward), arrow_start=bool(lk.backward),
                     data={"kind": "replication"})
            e.labels.append(edge_label(lines, gc, y_r, gx1 - gx0 - 6))
            sc.edges.append(e)

    # Links leaving the drawn sites.
    if plan.targets and plan.side:
        content_bottom = max(content_bottom, _side(sc, plan, site_nodes, pc_nodes,
                                                   first_cards, kinds, names))
    elif plan.targets or plan.inner:
        content_bottom = max(content_bottom, _below(sc, plan, site_nodes, page, kinds, names))

    items: list[tuple[str, Style, str]] = []
    if has_pc:
        items.append(("box", K.PC, "Prism Central"))
    items.append(("box", K.CLUSTER, "Cluster (Prism Element)"))
    if used["strip"]:
        items.append(("box", K.STORAGE, "DSF storage"))
    if used["external"]:
        items.append(("box", K.EXTERNAL, "External storage"))
    if any(not t.ref for t in plan.targets):
        items.append(("box", K.REMOTE, "Outside this inventory"))
    if any(t.ref for t in plan.targets):
        items.append(("box", K.REF, "Drawn in another part"))
    if used["more"]:
        items.append(("box", K.MORE, "Summary card"))
    if kinds["replication"]:
        items.append(("line", K.LEGEND_REPL, "Replication"))
    if kinds["pairing"]:
        items.append(("line", K.PAIRING, "AZ pairing"))
    if kinds["pairing_down"]:
        states = sorted(k.split(":", 1)[1] for k in kinds if k.startswith("state:"))
        items.append(("line", K.PAIRING_DOWN, f"AZ pairing, {' or '.join(states)}"))
    if has_pc:
        items.append(("line", K.MANAGES, "Manages"))
    content_w = max((nd.x + nd.w for nd in sc.nodes), default=200.0)
    # A figure already wider than a summary column may take the page width for its
    # legend (one row instead of two); a narrow one keeps it within its own width.
    lg_w = min(page.width - 2 * MX, max(content_w, 300.0) if content_w <= 480.0
               else page.width)
    lh = legend(sc, items, MX, content_bottom + 9, lg_w)
    right = max(max(nd.x + nd.w for nd in sc.nodes),
                max((max(p[0] for p in e.points) for e in sc.edges), default=0.0),
                max((lb.x + lb.w for e in sc.edges for lb in e.labels), default=0.0))
    sc.width = q(min(page.width, right + MX))
    sc.height = q(content_bottom + 9 + lh + 1)
    sc.meta = {"drawn": drawn, "summarised": summarised,
               "sites": [s.id for s in sites]}
    return sc


_ROLE_COLOR = {"type": K.IRIS, "direction": K.INK_2, "note": K.INK_3}


def _colored(lines: list[fmt.LabelLine]) -> list[LabelText]:
    return [(ln.text, _ROLE_COLOR[ln.role]) for ln in lines]


def _pairing_edge(sc: Scene, pairing: list[Link], a: str, b: str, xs: tuple[float, float],
                  y: float, label_x: float, label_w: float, kinds: dict[str, bool]) -> None:
    """A horizontal AZ pairing edge, its label above the line (never crossed by it)."""
    if not pairing:
        return
    lk = pairing[0]
    up = lk.connected
    kinds["pairing" if up else "pairing_down"] = True
    if not up:
        kinds[f"state:{lk.state or 'not connected'}"] = True
    e = edge(lk.id, a, b, [(xs[0], y), (xs[1], y)], K.PAIRING if up else K.PAIRING_DOWN,
             arrow_start=True, arrow_end=True, data={"kind": "pairing"})
    text: list[LabelText] = ["AZ pairing" if up else f"AZ pairing, {lk.state or 'not connected'}"]
    _, _, lh = label_block(text, label_w)
    e.labels.append(edge_label(text, label_x, y - 1.5 - lh / 2, label_w, background=False,
                               color=K.IRIS if up else K.INK_3))
    sc.edges.append(e)


def _pair_y(pcn: Node | None, site: Node) -> float:
    if pcn is not None:
        return q(pcn.y + pcn.h / 2)
    return q(site.y + 18)


def _repl_style(reps: list[Repl]) -> Style:
    return {"sync": K.REPL_SYNC, "nearsync": K.REPL_NEARSYNC}.get(fmt.repl_strength(reps),
                                                                  K.REPL_ASYNC)


def _card(sc: Scene, s: Site, cd: Card, x: float, y: float, w: float, h: float,
          used: dict[str, bool]) -> Node:
    title, body, inline, title_w = _card_parts(cd, w)
    is_cluster = cd.kind == "cluster"
    mt, th = measure(title, title_w)
    mb, bh = measure(body, w - 10)
    style = K.CLUSTER if is_cluster else K.NOTE if cd.kind == "note" else K.USER
    node = box(cd.id, "box", x, y, w, [], style, pad_x=5, h=h, parent=s.id,
               data=dict(cd.data))
    node.lines = (place_lines(mt, w, 5.0, "left", 5.0)
                  + place_lines(mb, w, 5.0 + th, "left", 5.0))
    sc.nodes.append(node)
    role = _role_chip(cd)
    if role:
        sc.nodes.append(chip(f"{cd.id}:tag", role, x + 5, y, K.TAG_ROLE, parent=cd.id))
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
    if is_cluster:
        # Strips pinned to the bottom of the card, so they line up along a grid row.
        rows_s = _strips(cd, w)
        heights = []
        for srow in rows_s:
            widths = _row_widths(srow, w - 10)
            heights.append(max(_strip_h(t, rw, k != "dsf")
                               for (k, t, _), rw in zip(srow, widths, strict=True)))
        sy = y + h - 4 - sum(heights) - 2.5 * (len(heights) - 1)
        arrays = {xa.id: xa for xa in cd.external}
        for srow, rh in zip(rows_s, heights, strict=True):
            sx = x + 5
            for (k, t, _), rw in zip(srow, _row_widths(srow, w - 10), strict=True):
                if k == "dsf":
                    used["strip"] = True
                    sc.nodes.append(box(f"{cd.id}:storage", "strip", sx, sy, rw,
                                        [Row(t, K.SIZE_SMALL, False, K.LIVE, 2)], K.STORAGE,
                                        pad_x=3, pad_y=0, h=rh, valign="middle",
                                        parent=cd.id))
                else:
                    used["external"] = True
                    xa = arrays[k]
                    sc.nodes.append(box(k, "strip", sx, sy, rw,
                                        [Row(t, K.SIZE_SMALL, True, K.WHITE, 2)], K.EXTERNAL,
                                        pad_x=3, pad_y=0, h=rh, valign="middle",
                                        parent=cd.id,
                                        data={"product": xa.product,
                                              **({"name": xa.name} if xa.name else {})}))
                sx += rw + PILL_GAP
            sy += rh + 2.5
    return node


def _target_rows(t: _Target, below: bool, paired: list[str] | None = None) -> list[Row]:
    if below:
        rows = [Row(t.name, K.SIZE_REMOTE_TITLE, True, K.INK, 2, 6.5),
                Row(f"{t.sub} · {t.note}", K.SIZE_SMALL, False, K.INK_2, 2,
                    gap_before=0.5)]
        if paired:
            text = f"AZ pairing with {fmt.join_names(paired)}"
            if tx.width(text, K.SIZE_SMALL) > BELOW_W - 10:
                text = f"AZ pairing with {fmt.plural(len(paired), 'AZ')} of this part"
            rows.append(Row(text, K.SIZE_SMALL, False, K.IRIS, 2))
        return rows
    return [Row(t.name, K.SIZE_REMOTE_TITLE, True, K.INK, 3, tx.MIN_SIZE),
            Row(t.sub, K.SIZE_SMALL, False, K.INK_2, 2, gap_before=0.5),
            Row(t.kind_line, K.SIZE_SMALL, False, K.INK_3, 1),
            Row(t.note, K.SIZE_SMALL, False, K.INK_3, 2)]


def _target_style(t: _Target) -> Style:
    return K.REF if t.ref else K.REMOTE


def _side(sc: Scene, plan: _Plan, site_nodes: dict[str, Node], pc_nodes: dict[str, Node],
          first_cards: dict[str, Node], kinds: dict[str, bool], names: dict[str, str]
          ) -> float:
    src = site_nodes[plan.sites[-1].id]
    x0 = src.x + src.w
    bx = q(x0 + SIDE_GUT)
    pcn = pc_nodes.get(src.id)
    y = pcn.y if pcn else src.y + 18
    by_target = {t: lks for _, t, lks in plan.external}
    for ti, t in enumerate(plan.targets):
        lks = by_target.get(t.id, [])
        pairing = [lk for lk in lks if lk.kind == "pairing"]
        repls = [lk for lk in lks if lk.kind == "replication"]
        rows = _target_rows(t, below=False)
        th = text_h(rows, REMOTE_W - 8)
        lines = _colored(fmt.link_lines(repls[0], names[repls[0].a], names[repls[0].b])) \
            if repls else []
        lh = label_block(lines, SIDE_GUT - 4)[2] if lines else 0.0
        first = ti == 0 and pcn is not None
        y_p = q(pcn.y + pcn.h / 2) if first and pcn else q(y + 14)
        if repls:
            card = first_cards.get(src.id)
            base = card.y + card.h / 2 if (card and first) else 0.0
            y_r = q(max(base, (y_p + 6 if pairing else y + 6) + lh + 2.5))
        else:
            y_r = y_p
        need = max(th + CHIP_H / 2 + 4.5 + 6, (y_r - y) + 12)
        node = box(t.id, "box", bx, y, REMOTE_W, rows, _target_style(t), pad_x=4,
                   pad_y=CHIP_H / 2 + 4.5, h=q(need),
                   data={"availability_zone": t.name, "name": t.name})
        sc.nodes.append(node)
        sc.nodes.append(chip(f"{t.id}:tag", t.chip, bx + 5.5, y,
                             K.TAG if t.ref else K.TAG_MUTED, parent=t.id))
        _pairing_edge(sc, pairing, src.id, t.id, (x0 + 0.5, bx - 0.5), y_p,
                      x0 + SIDE_GUT / 2, SIDE_GUT - 4, kinds)
        for lk in repls[:1]:
            kinds["replication"] = True
            pts = [(x0 + 0.5, y_r), (bx - 0.5, y_r)]
            e = edge(lk.id, lk.a, lk.b, pts if lk.a == src.id else pts[::-1],
                     _repl_style(lk.repl), arrow_end=bool(lk.forward),
                     arrow_start=bool(lk.backward), data={"kind": "replication"})
            # Above the line, so the line never crosses its own label.
            e.labels.append(edge_label(lines, x0 + SIDE_GUT / 2, y_r - lh / 2 - 2,
                                       SIDE_GUT - 4))
            sc.edges.append(e)
        y = q(node.y + node.h + 12)
    return y - 12


@dataclass
class _Group:
    target: str
    kind: str  # replication | pairing
    sources: list[tuple[str, Link]]
    inner: bool  # the target is a site of this part


def _below(sc: Scene, plan: _Plan, site_nodes: dict[str, Node], page: Page,
           kinds: dict[str, bool], names: dict[str, str]) -> float:
    """Buses under the sites: one channel per (target, kind), one edge and one label
    per source; remote boxes in a row below."""
    site_bottom = max(n.y + n.h for n in site_nodes.values())
    groups: list[_Group] = []
    by_target: dict[str, list[tuple[str, list[Link]]]] = {}
    for x, t, lks in plan.external:
        by_target.setdefault(t, []).append((x, lks))
    # A source that both replicates to and pairs with a target draws the replication;
    # the pairing is noted in the target box ("AZ pairing with ..."), so no dashed drop
    # crosses a replication bus. Pairing-only sources keep their dashed bus.
    noted: dict[str, list[str]] = {}
    for t in [tg.id for tg in plan.targets]:
        srcs = by_target.get(t, [])
        rep_src = [(x, lk) for x, lks in srcs for lk in lks if lk.kind == "replication"]
        pair_src = [(x, lk) for x, lks in srcs for lk in lks if lk.kind == "pairing"]
        replicating = {x for x, _ in rep_src}
        noted[t] = [names[x] for x, _ in pair_src if x in replicating]
        if rep_src:
            groups.append(_Group(t, "replication", rep_src, False))
        alone = [(x, lk) for x, lk in pair_src if x not in replicating]
        if alone:
            groups.append(_Group(t, "pairing", alone, False))
        if noted[t]:
            kinds["pairing"] = True
    for x, y, lks in plan.inner:
        for kind in ("replication", "pairing"):
            for lk in lks:
                if lk.kind == kind:
                    groups.append(_Group(y, kind, [(x, lk)], True))

    def centre(sid: str) -> float:
        n = site_nodes[sid]
        return n.x + n.w / 2

    def proxy_x(g: _Group) -> float:
        return centre(g.target) if g.inner else sum(centre(x) for x, _ in g.sources) / len(
            g.sources)

    # Pairing channels run nearest the sites, replication below them.
    groups.sort(key=lambda g: (g.kind != "pairing", proxy_x(g), g.target))
    ports: dict[str, list[tuple[int, str]]] = {}
    for gi, g in enumerate(groups):
        for x, _ in g.sources:
            ports.setdefault(x, []).append((gi, "src"))
        if g.inner:
            ports.setdefault(g.target, []).append((gi, "dst"))
    port_x: dict[tuple[int, str, str], float] = {}
    port_i: dict[tuple[int, str, str], int] = {}
    for sid, lst in ports.items():
        n = site_nodes[sid]
        m = len(lst)
        # Along the bottom edge in the order of where each bus heads (its target's
        # estimated x), so the drops of one zone never cross each other's channels.
        lst.sort(key=lambda p: (proxy_x(groups[p[0]]) if p[1] == "src"
                                else centre(groups[p[0]].sources[0][0]), p[0]))
        for i, (gi, role) in enumerate(lst):
            px = n.x + n.w / 2 if m == 1 else n.x + n.w * (0.1 + 0.8 * i / (m - 1))
            port_x[(gi, sid, role)] = q(px)
            port_i[(gi, sid, role)] = i

    # Labels on each source's drop, just under the site; alternate rows when ports are
    # close together.
    labels: dict[tuple[int, str], tuple[list[LabelText], float, float]] = {}
    for gi, g in enumerate(groups):
        if g.kind != "replication":
            continue
        for x, lk in g.sources:
            text = _colored(fmt.link_lines(lk, names[lk.a], names[lk.b]))
            _, lw, lh = label_block(text, 150)
            labels[(gi, x)] = (text, lw, lh)
    label_h = max((lh for _, _, lh in labels.values()), default=0.0)
    stagger = any(len(lst) > 2 for lst in ports.values())
    chan0 = q(site_bottom + 3 + label_h * (2 if stagger else 1) + (3 if stagger else 0)
              + (4 if labels else 0))
    remote_groups = [g for g in groups if not g.inner]
    n_ch = len(groups)
    row_y = q(chan0 + CHANNEL * (n_ch - 1) + 11) if remote_groups else 0.0
    targets = {t.id: t for t in plan.targets}
    want: dict[str, list[float]] = {}
    for gi, g in enumerate(groups):
        if not g.inner:
            want.setdefault(g.target, []).extend(port_x[(gi, x, "src")] for x, _ in g.sources)
    order = sorted(want, key=lambda t: (sum(want[t]) / len(want[t]), t))
    xs = _deoverlap([sum(want[t]) / len(want[t]) - BELOW_W / 2 for t in order], BELOW_W, 12,
                    MX, page.width - MX)
    boxes: dict[str, Node] = {}
    for t, bx in zip(order, xs, strict=True):
        tg = targets[t]
        node = box(t, "box", bx, row_y, BELOW_W, _target_rows(tg, True, noted.get(t)),
                   _target_style(tg), pad_x=5, pad_y=CHIP_H / 2 + 4,
                   data={"availability_zone": tg.name, "name": tg.name})
        sc.nodes.append(node)
        sc.nodes.append(chip(f"{t}:tag", tg.chip, bx + 5.5, row_y,
                             K.TAG if tg.ref else K.TAG_MUTED, parent=t))
        boxes[t] = node
    if boxes:
        hmax = max(b.h for b in boxes.values())
        for b in boxes.values():
            b.h = hmax
    into: dict[str, list[int]] = {}
    for gi, g in enumerate(groups):
        if not g.inner:
            into.setdefault(g.target, []).append(gi)
    for gi, g in enumerate(groups):
        cy = q(chan0 + CHANNEL * gi)
        if g.inner:
            tx_ = port_x[(gi, g.target, "dst")]
            ty = site_bottom
        else:
            b = boxes[g.target]
            k, m = into[g.target].index(gi), len(into[g.target])
            tx_ = q(b.x + b.w * (k + 1) / (m + 1))
            ty = b.y
        pairing = g.kind == "pairing"
        if pairing:
            up = all(lk.connected for _, lk in g.sources)
            kinds["pairing" if up else "pairing_down"] = True
            for _, lk in g.sources:
                if not lk.connected:
                    kinds[f"state:{lk.state or 'not connected'}"] = True
        else:
            kinds["replication"] = True
        for x, lk in g.sources:
            px = port_x[(gi, x, "src")]
            pts = [(px, site_bottom), (px, cy), (tx_, cy), (tx_, ty)]
            if abs(px - tx_) < 0.5:
                pts = [(px, site_bottom), (tx_, ty)]
            if pairing:
                style = K.PAIRING if lk.connected else K.PAIRING_DOWN
                a_end, a_start = True, True
            else:
                fwd = lk.a == x
                style = _repl_style(lk.repl)
                a_end = bool(lk.forward if fwd else lk.backward)
                a_start = bool(lk.backward if fwd else lk.forward)
            e = edge(f"{lk.id}|{x}" if len(g.sources) > 1 else lk.id, x, g.target, pts,
                     style, arrow_end=a_end, arrow_start=a_start, data={"kind": g.kind})
            if (gi, x) in labels:
                text, lw, lh = labels[(gi, x)]
                row = port_i[(gi, x, "src")] % 2 if stagger else 0
                ly = site_bottom + 3 + lh / 2 + row * (label_h + 3)
                lx = min(max(px, MX + lw / 2), page.width - MX - lw / 2)
                e.labels.append(edge_label(text, lx, ly, 150))
            sc.edges.append(e)
    bottom = max([b.y + b.h for b in boxes.values()] + [chan0 + CHANNEL * (n_ch - 1) + 6])
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


def _caption(sites: list[Site], part: tuple[int, int], edited: bool = False) -> str:
    """Counts as collected (an edited figure says "(edited)"); zones added in the
    Diagrams editor are named as such, never counted as collected."""
    collected = [s for s in sites if s.kind != "user"]
    counts = [_site_counts(s) for s in collected]
    n_clusters = sum(c for c, _, _ in counts)
    nodes = sum(n for _, n, _ in counts)
    added = len(sites) - len(collected)
    extra = f", plus {fmt.plural(added, 'user-added zone')}" if added and collected else ""
    head = "Solution conceptual overview" + (" (edited)" if edited else "")
    if part[1] > 1:
        names = fmt.join_names([short_name(s.name) for s in sites])
        what = (f"{fmt.plural(n_clusters, 'cluster')}, {fmt.plural(nodes, 'node')}{extra}"
                if collected else "added by the author, not collected")
        return f"{head}, part {part[0]} of {part[1]}: {names} ({what})."
    if sites and sites[0].kind == "pe":
        return (f"{head}: Prism Element {sites[0].name}, "
                f"{fmt.plural(n_clusters, 'cluster')}, {fmt.plural(nodes, 'node')}{extra}.")
    return (f"{head}: {fmt.plural(len(collected), 'availability zone')}, "
            f"{fmt.plural(n_clusters, 'cluster')}, {fmt.plural(nodes, 'node')}{extra}.")


def _best(model: ConceptualModel, sites: list[Site], parts: list[list[Site]], page: Page,
          refs: Refs, limit: Budget, sid: str, summarise: bool) -> tuple[Scene, bool]:
    """The first ladder rung that fits `limit`, else the most compact one."""
    plan = _plan(model, sites, parts, page)
    side_w = (SIDE_GUT + REMOTE_W) if plan.side else 0.0
    avail = (page.width - 2 * MX - sum(_gaps(plan)) - side_w) / max(1, len(sites))
    max_cols = max(1, int((avail - 2 * P + CG) // (CARD_MIN_W + CG)))
    sc: Scene | None = None
    for attempt in _ladder(plan, max_cols, summarise):
        sc = _layout_part(model, plan, page, refs, attempt, sid)
        if sc.height <= limit(sc.width):
            return sc, True
    assert sc is not None
    return sc, False


def _split(model: ConceptualModel, sites: list[Site]) -> tuple[list[Site], list[Site]]:
    """Split a part that does not fit (`parts.split`)."""
    by_id = {s.id: s for s in sites}
    keep, move = parts_mod.split([s.id for s in sites], _pairs(model))
    return [by_id[i] for i in keep], [by_id[i] for i in move]


def layout(model: ConceptualModel, page: Page | None = None, refs: Refs | None = None,
           first_budget: Budget | None = None) -> list[Scene]:
    """Scenes (parts) for the model, up to 3 AZs each (linked AZs together). Every part
    fits `page.max_h`, and part 1 fits `first_budget(width)` (the room the report leaves
    under the headings and summary).

    The detail level is one decision for the whole estate: every AZ in full when every
    part fits that way, else decision 7 everywhere ("+N more" cards). A part that still
    does not fit gives up its last linked group (or last AZ) to the parts after it."""
    page = page or Page()
    refs = refs or Refs()
    own_page = False  # part 1 starts on a page of its own (the report breaks before it)

    def limit(pi: int, own: bool | None = None) -> Budget:
        own = own_page if own is None else own
        if pi == 0 and first_budget is not None and not own:
            return lambda w: min(page.max_h, first_budget(w))
        return lambda _w: page.max_h

    def fits(parts: list[list[Site]], summarise: bool, own: bool) -> bool:
        return all(_best(model, part, parts, page, refs, limit(pi, own), "probe",
                         summarise)[1] for pi, part in enumerate(parts))

    # Paired AZs stay in one part (decision 7). In order: (a) every part in full, part 1
    # under the headings and summary; (b) the same with part 1 on a page of its own;
    # (c) "+N more" cards for collected clusters (never for what the author added),
    # under the summary, else on a page of its own; only then is a part split.
    def split_to_fit(parts: list[list[Site]], own: bool) -> list[list[Site]]:
        pi = 0
        while pi < len(parts):
            _, ok = _best(model, parts[pi], parts, page, refs, limit(pi, own), "probe", True)
            if not ok and len(parts[pi]) > 1:
                keep, move = _split(model, parts[pi])
                rest = [s for p in [move, *parts[pi + 1:]] for s in p]
                parts = parts[:pi] + [keep] + plan_parts(model, sites=rest)
                continue
            pi += 1
        return parts

    def breaks_pairs(parts: list[list[Site]]) -> bool:
        part_of = {s.id: i for i, p in enumerate(parts) for s in p}
        return any(lk.a in part_of and lk.b in part_of and part_of[lk.a] != part_of[lk.b]
                   for lk in model.links)

    parts = plan_parts(model)
    summarise = False
    if not fits(parts, False, False):
        if first_budget is not None and fits(parts, False, True):
            own_page = True
        else:
            summarise = True
            under = split_to_fit(parts, False)
            if first_budget is not None and breaks_pairs(under) and \
                    not breaks_pairs(alone := split_to_fit(parts, True)):
                own_page, parts = True, alone
            else:
                parts = under
    scenes: list[Scene] = []
    for pi, sites in enumerate(parts):
        sid = "conceptual" if len(parts) == 1 else f"conceptual-{pi + 1}"
        sc, _ = _best(model, sites, parts, page, refs, limit(pi), sid, summarise)
        sc.part = (pi + 1, len(parts))
        sc.caption = _caption(sites, sc.part, model.edited)
        sc.meta["summarise"] = summarise
        if pi == 0 and own_page:
            sc.meta["own_page"] = True
        scenes.append(sc)
    return scenes
