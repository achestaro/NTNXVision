"""Management Plane Design layout (A3): one column per Prism Central.

Each column is an "AZ" container: its name and registered-cluster count, the Prism
Central block (solid iris: version, deployment, per-PCVM sizing and addresses, the PC
products enabled, one chip per PC VM), a "runs on" connector to the cluster hosting the
PC VMs, then the registered Prism Element clusters (addresses, AOS, a HOSTS PC / MGMT
role chip, a hypervisor chip for non-AHV clusters, the vCenter Server of an ESXi cluster
in a box joined by an L connector) and a version strip (LCM, NCC) when collected.

AZ pairing is drawn where the data has it: between adjacent columns as a dashed line
through a pairing glyph, "AZ pairing" above and the state below; to availability zones
outside this inventory as dashed boxes under the columns. A pairing to a zone in this
inventory that is not adjacent (or drawn in another part) is named in the column header.

Scaling (text never under 6 pt; content splits instead): parts of up to 3 Prism Centrals,
paired ones kept together; per part, every registered cluster, then the cluster hosting
the PC plus the next ones and a "+N more registered clusters" card naming the table that
lists them all; a part that still does not fit gives its last column to a new part.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import fmt
from .. import text as tx
from .. import tokens as K
from ..build import (
    CHIP_H,
    Row,
    box,
    chip,
    chip_w,
    edge,
    edge_label,
    label_block,
    legend,
    measure,
    place_lines,
    text_h,
)
from ..facts import short_name
from ..management import (
    ManagementModel,
    MgmtCluster,
    MgmtLink,
    MgmtRemote,
    MgmtSite,
    cluster_detail,
    deployment,
    per_pcvm,
)
from ..scene import Node, Scene, Style, q
from .conceptual import Budget, Page, Refs
from .parts import group

MX = 0.5
TOP = 5.0
P = 6.0
GAP = 13.5
GUT = 62.0
SITE_MAX = 208.0
ROW_GAP = 3.5
GLYPH = 15.0
BELOW_W = 160.0
CHANNEL = 8.0
PER_PART = 3
#: Clusters a summarised column keeps: the PC host and the next one.
MIN_KEEP = 2
PCVM_CHIP_H = 9.0


@dataclass
class _Plan:
    sites: list[MgmtSite]
    #: (left id, right id, link) for paired adjacent columns
    gutter: list[tuple[str, str, MgmtLink]] = field(default_factory=list)
    #: (site id, remote id, link) for pairings with zones outside the inventory
    below: list[tuple[str, str, MgmtLink]] = field(default_factory=list)
    #: site id -> names of paired zones drawn elsewhere (not adjacent, another part)
    notes: dict[str, list[str]] = field(default_factory=dict)


def _plan(model: ManagementModel, sites: list[MgmtSite], part_of: dict[str, int],
          n_parts: int) -> _Plan:
    plan = _Plan(sites=sites)
    pos = {s.id: i for i, s in enumerate(sites)}
    for lk in model.links:
        for me, other in ((lk.a, lk.b), (lk.b, lk.a)):
            if me not in pos:
                continue
            if model.remote(other) is not None:
                plan.below.append((me, other, lk))
            elif other in pos:
                if abs(pos[me] - pos[other]) == 1:
                    if pos[me] < pos[other]:
                        plan.gutter.append((me, other, lk))
                else:
                    plan.notes.setdefault(me, []).append(short_name(model.name_of(other)))
            else:
                where = f" (part {part_of.get(other, 0) + 1} of {n_parts})" \
                    if n_parts > 1 else ""
                plan.notes.setdefault(me, []).append(short_name(model.name_of(other)) + where)
    order = {r.id: i for i, r in enumerate(model.remotes)}
    plan.below.sort(key=lambda t: (pos[t[0]], order.get(t[1], 0)))
    for k in plan.notes:
        plan.notes[k] = sorted(set(plan.notes[k]))
    return plan


def _gaps(plan: _Plan) -> list[float]:
    linked = {(a, b) for a, b, _ in plan.gutter}
    ids = [s.id for s in plan.sites]
    return [GUT if (a, b) in linked else GAP for a, b in zip(ids, ids[1:], strict=False)]


# ─── text ──────────────────────────────────────────────────────────────


def _header_rows(s: MgmtSite, notes: list[str]) -> list[Row]:
    rows = [Row(s.name, K.SIZE_SITE, True, K.INK, 2, 7.5),
            Row(f"Management plane, {fmt.plural(s.registered, 'registered cluster')}",
                K.SIZE_SMALL, False, K.INK_3, 2, gap_before=0.5)]
    if notes:
        rows.append(Row(f"AZ pairing also with {fmt.join_names(notes)}", K.SIZE_SMALL,
                        False, K.IRIS, 2))
    return rows


def _pc_rows(s: MgmtSite, drawn_hosts: bool) -> list[Row]:
    pc = s.pc
    assert pc is not None
    title = "Prism Central" + (f" {pc.version}" if pc.version else "")
    rows = [Row(title, K.SIZE_PC_TITLE, True, K.WHITE, 2, tx.MIN_SIZE),
            Row(deployment(pc), K.SIZE_BODY, False, K.WHITE, 2, gap_before=0.5)]
    sizing = per_pcvm(pc)
    if sizing and pc.vms > 1 and pc.vm_vcpus:
        sizing += " each"
    addr = f"VIP {pc.virtual_ip}" if pc.virtual_ip else (pc.ips[0] if len(pc.ips) == 1
                                                           else None)
    line = " · ".join(x for x in (sizing, addr) if x)
    if line:
        rows.append(Row(line, K.SIZE_SMALL, False, K.IRIS_TINT, 2))
    if pc.hosting and not drawn_hosts:
        rows.append(Row(f"Runs on {fmt.join_names(pc.hosting)}", K.SIZE_SMALL, False,
                        K.IRIS_TINT, 2))
    if pc.products:
        rows.append(Row(" · ".join(pc.products), K.SIZE_SMALL, False, K.IRIS_TINT, 3,
                        gap_before=1.0))
    return rows


def _chips(c: MgmtCluster) -> list[tuple[str, Style]]:
    out: list[tuple[str, Style]] = []
    if c.dedicated:
        out.append(("MGMT", K.TAG_ROLE))
    if c.hosts_pc:
        out.append(("HOSTS PC", K.TAG_ROLE))
    if c.hypervisor and c.hypervisor != "AHV":
        out.append((c.hypervisor, K.TAG_MUTED))
    if not c.connected:
        out.append(((c.connectivity or "NOT CONNECTED").replace("_", " "), K.TAG_MUTED))
    return out


def _tag_w(label: str) -> float:
    return q(chip_w(label) + 0.5)


def _chips_w(c: MgmtCluster) -> float:
    ch = _chips(c)
    return sum(_tag_w(lab) for lab, _ in ch) + 2.5 * max(0, len(ch) - 1)


def _cluster_rows(c: MgmtCluster) -> list[Row]:
    return [Row(c.name, K.SIZE_CARD_TITLE, True, K.INK, 2, tx.MIN_SIZE)]


def _detail_rows(c: MgmtCluster) -> list[Row]:
    return [Row(cluster_detail(c), K.SIZE_SMALL, False, K.INK_2, 2, gap_before=0.5)]


def _cluster_h(c: MgmtCluster, w: float) -> float:
    cw = _chips_w(c)
    title_w = w - 10 - (cw + 4 if cw else 0)
    return q(text_h(_cluster_rows(c), title_w) + text_h(_detail_rows(c), w - 10) + 8)


def _vcenter_rows(c: MgmtCluster) -> list[Row]:
    return [Row(f"vCenter {c.vcenter} · manages the ESXi hosts", K.SIZE_SMALL, False,
                K.INK_2, 2)]


VC_INDENT = 12.0


def _vcenter_h(c: MgmtCluster, w: float) -> float:
    return q(text_h(_vcenter_rows(c), w - VC_INDENT - 8) + 4)


def _foot_text(s: MgmtSite) -> str:
    pc = s.pc
    if pc is None:
        return ""
    parts = [f"LCM {pc.lcm}" if pc.lcm else None, f"NCC {pc.ncc}" if pc.ncc else None]
    return " · ".join(p for p in parts if p)


def _more_rows(hidden: list[MgmtCluster], refs: Refs) -> list[Row]:
    ref = refs.clusters_table.replace(" ", fmt.NBSP)
    return [Row(f"+{fmt.plural(len(hidden), 'more registered cluster')}", K.SIZE_CARD_TITLE,
                True, K.INK_2, 2),
            Row(f"listed in {ref}", K.SIZE_SMALL, False, K.INK_3, 2, gap_before=0.5)]


def _visible(s: MgmtSite, keep: int | None) -> tuple[list[MgmtCluster], list[MgmtCluster]]:
    if keep is None or len(s.clusters) <= keep + 1:
        return list(s.clusters), []
    return s.clusters[:keep], s.clusters[keep:]


# ─── one part ──────────────────────────────────────────────────────────


def _layout_part(model: ManagementModel, plan: _Plan, page: Page, refs: Refs,
                 keep: int | None, sid: str) -> Scene:
    sites = plan.sites
    n = max(1, len(sites))
    gaps = _gaps(plan)
    site_w = q(min((page.width - 2 * MX - sum(gaps)) / n, SITE_MAX))
    inner = q(site_w - 2 * P)
    sc = Scene(id=sid, type="management", title=model.title, caption="", width=page.width,
               height=0)
    vis = {s.id: _visible(s, keep) for s in sites}

    header_h = max((text_h(_header_rows(s, plan.notes.get(s.id, [])), inner)
                    for s in sites), default=0.0)
    head_top = TOP + CHIP_H / 2 + 4.5

    def pc_h(s: MgmtSite) -> float:
        if s.pc is None:
            return 0.0
        drawn = any(c.hosts_pc for c in vis[s.id][0])
        th = text_h(_pc_rows(s, drawn), inner - 10)
        return th + 10 + (PCVM_CHIP_H + 4 if s.pc.vms else 0)

    pc_block_h = max((pc_h(s) for s in sites), default=0.0)
    pc_y = q(head_top + header_h + 5)
    label_y = q(pc_y + pc_block_h + 15)
    list_y = q(label_y + tx.line_height(K.SIZE_SMALL) + 2)

    site_nodes: dict[str, Node] = {}
    pc_nodes: dict[str, Node] = {}
    drawn: list[str] = []
    summarised: list[str] = []
    used = {"vcenter": False, "more": False, "runs_on": False, "foot": False}
    bottoms: list[float] = []
    xs: list[float] = []
    x = MX
    for i, s in enumerate(sites):
        xs.append(x)
        cont = box(s.id, "container", x, TOP, site_w, _header_rows(s, plan.notes.get(s.id, [])),
                   K.SITE, pad_x=P, pad_y=head_top - TOP, h=10, data=dict(s.data))
        sc.nodes.append(cont)
        sc.nodes.append(chip(f"{s.id}:tag", "AZ", x + 5.5, TOP, K.TAG, parent=s.id))
        site_nodes[s.id] = cont
        shown, hidden = vis[s.id]
        y = list_y
        host_node: Node | None = None
        if shown or hidden:
            sc.nodes.append(box(f"{s.id}:clusters-label", "label", x + P, label_y, inner,
                                [Row("REGISTERED CLUSTERS", K.SIZE_SMALL, True, K.INK_3)],
                                K.LABEL, pad_x=0, pad_y=0, align="left", parent=s.id))
        for c in shown:
            node = _cluster(sc, s, c, x + P, y, inner)
            drawn.append(c.id)
            if c.hosts_pc and host_node is None:
                host_node = node
            y = node.y + node.h + ROW_GAP
            if c.vcenter:
                used["vcenter"] = True
                vh = _vcenter_h(c, inner)
                vy = q(y - ROW_GAP + 3)
                vc = box(f"{c.id}:vcenter", "box", x + P + VC_INDENT, vy, inner - VC_INDENT,
                         _vcenter_rows(c), K.VCENTER, pad_x=4, pad_y=2, h=vh, valign="middle",
                         align="left", parent=s.id,
                         data={"vcenter": c.vcenter, "cluster": c.name, "name": "vCenter"})
                sc.nodes.append(vc)
                lx = x + P + VC_INDENT / 2
                sc.edges.append(edge(f"{c.id}->vcenter", c.id, vc.id,
                                     [(lx, node.y + node.h), (lx, vy + vh / 2),
                                      (vc.x, vy + vh / 2)], K.MANAGES, arrow_end=False,
                                     data={"kind": "vcenter"}))
                y = q(vy + vh + ROW_GAP)
        if hidden:
            used["more"] = True
            mh = q(text_h(_more_rows(hidden, refs), inner - 10) + 8)
            more = box(f"{s.id}:more", "box", x + P, y, inner, _more_rows(hidden, refs),
                       K.MORE, pad_x=5, h=mh, valign="middle", parent=s.id,
                       data={"clusters": str(len(hidden))})
            sc.nodes.append(more)
            summarised.extend(c.id for c in hidden)
            y = q(more.y + more.h + ROW_GAP)
        bottoms.append(y)
        if s.pc is not None:
            pcn = box(s.pc.id, "box", x + P, pc_y, inner,
                      _pc_rows(s, any(c.hosts_pc for c in shown)), K.PC, pad_x=5, pad_y=5,
                      h=pc_block_h, valign="top", parent=s.id,
                      data={"prism_central": s.name, "name": "Prism Central"})
            sc.nodes.append(pcn)
            pc_nodes[s.id] = pcn
            _pcvm_chips(sc, s, pcn)
            if host_node is not None:
                used["runs_on"] = True
                cx = q(pcn.x + pcn.w / 2)
                e = edge(f"{s.pc.id}->{host_node.id}", pcn.id, host_node.id,
                         [(cx, pcn.y + pcn.h), (cx, host_node.y)], K.MANAGES,
                         data={"kind": "runs_on"})
                _, lw, lh = label_block(["runs on"], 60, color=K.INK_3)
                mid = (pcn.y + pcn.h + label_y) / 2
                e.labels.append(edge_label(["runs on"], cx + 2.5 + lw / 2, mid, 60,
                                           color=K.INK_3, background=False))
                sc.edges.append(e)
        x = x + site_w + (gaps[i] if i < len(gaps) else 0)

    foot_h = q(tx.line_height(K.SIZE_SMALL) + 3)
    has_foot = any(_foot_text(s) for s in sites)
    content_bottom = max(bottoms, default=list_y)
    site_bottom = q(content_bottom + (foot_h + 2 if has_foot else 0) + P - ROW_GAP + 1)
    for s, x0 in zip(sites, xs, strict=True):
        site_nodes[s.id].h = q(site_bottom - TOP)
        ft = _foot_text(s)
        if ft:
            used["foot"] = True
            sc.nodes.append(box(f"{s.id}:versions", "strip", x0 + P, site_bottom - P - foot_h,
                                inner, [Row(ft, K.SIZE_SMALL, False, K.INK_2)], K.FOOT_STRIP,
                                pad_x=3, pad_y=0, h=foot_h, valign="middle", parent=s.id))

    kinds = {"pairing": False, "pairing_down": False}
    states: set[str] = set()
    for a, b, lk in plan.gutter:
        _gutter(sc, site_nodes[a], site_nodes[b], pc_nodes.get(a), pc_nodes.get(b), lk)
        kinds["pairing" if lk.connected else "pairing_down"] = True
        if not lk.connected:
            states.add(lk.state_text)
    bottom = site_bottom
    if plan.below:
        bottom = _below(sc, model, plan, site_nodes, page, kinds, states)

    items: list[tuple[str, Style, str]] = []
    if pc_nodes:
        items.append(("box", K.PC, "Prism Central (PCVMs)"))
    items.append(("box", K.CLUSTER, "Prism Element cluster"))
    if used["vcenter"]:
        items.append(("box", K.VCENTER, "vCenter"))
    if plan.below:
        items.append(("box", K.REMOTE, "Outside this inventory"))
    if used["more"]:
        items.append(("box", K.MORE, "Summary card"))
    if kinds["pairing"]:
        items.append(("line", K.PAIRING, "AZ pairing"))
    if kinds["pairing_down"]:
        items.append(("line", K.PAIRING_DOWN, f"AZ pairing, {' or '.join(sorted(states))}"))
    if used["runs_on"] or used["vcenter"]:
        items.append(("line", K.MANAGES, "Runs on" if used["runs_on"] else "Manages"))
    content_w = max(nd.x + nd.w for nd in sc.nodes)
    lh = legend(sc, items, MX, bottom + 9, min(page.width - 2 * MX, max(content_w, 300.0)))
    right = max(max(nd.x + nd.w for nd in sc.nodes),
                max((max(p[0] for p in e.points) for e in sc.edges), default=0.0),
                max((lb.x + lb.w for e in sc.edges for lb in e.labels), default=0.0))
    sc.width = q(min(page.width, right + MX))
    sc.height = q(bottom + 9 + lh + 1)
    sc.meta = {"drawn": drawn, "summarised": summarised, "sites": [s.id for s in sites]}
    return sc


def _cluster(sc: Scene, s: MgmtSite, c: MgmtCluster, x: float, y: float, w: float) -> Node:
    chips = _chips(c)
    cw = _chips_w(c)
    title_w = w - 10 - (cw + 4 if cw else 0)
    h = _cluster_h(c, w)
    node = box(c.id, "box", x, y, w, [], K.CLUSTER, pad_x=5, h=h, parent=s.id,
               data=dict(c.data))
    mt, th = measure(_cluster_rows(c), title_w)
    md, _ = measure(_detail_rows(c), w - 10)
    node.lines = place_lines(mt, w, 4.0, "left", 5.0) + place_lines(md, w, 4.0 + th, "left",
                                                                      5.0)
    sc.nodes.append(node)
    cx = x + w - 4 - cw
    for lab, st in chips:
        cwi = _tag_w(lab)
        sc.nodes.append(box(f"{c.id}:tag:{lab}", "tag", cx, y + 3.5, cwi,
                            [Row(lab, K.SIZE_CHIP, True, K.WHITE)], st, pad_x=3.5, pad_y=0,
                            h=CHIP_H, valign="middle", parent=c.id))
        cx += cwi + 2.5
    return node


def _pcvm_chips(sc: Scene, s: MgmtSite, pcn: Node) -> None:
    pc = s.pc
    assert pc is not None
    n = pc.vms
    if not n:
        return
    gap = 3.0
    cw = q(min(64.0, (pcn.w - 10 - gap * (n - 1)) / n))
    total = n * cw + gap * (n - 1)
    x0 = pcn.x + (pcn.w - total) / 2
    y = pcn.y + pcn.h - 5 - PCVM_CHIP_H
    for i in range(n):
        label = f"PCVM {i + 1} of {n}"
        sc.nodes.append(box(f"{pc.id}:pcvm:{i + 1}", "pill", x0 + i * (cw + gap), y, cw,
                            [Row(label, K.SIZE_CHIP, True, K.WHITE)], K.PCVM_CHIP, pad_x=2,
                            pad_y=0, h=PCVM_CHIP_H, valign="middle", parent=pc.id))


def _gutter(sc: Scene, na: Node, nb: Node, pa: Node | None, pb: Node | None,
            lk: MgmtLink) -> None:
    """Dashed pairing line between two PC blocks through the pairing glyph, "AZ pairing"
    above it and the state below."""
    y = q(pa.y + pa.h / 2) if pa else q(na.y + 18)
    x0 = (pa.x + pa.w) if pa else (na.x + na.w)
    x1 = pb.x if pb else nb.x
    gx = q((na.x + na.w + nb.x) / 2)
    style = K.PAIRING if lk.connected else K.PAIRING_DOWN
    color = K.IRIS if lk.connected else K.INK_3
    r = GLYPH / 2
    glyph = Node(id=f"{lk.id}:glyph", kind="pill", x=q(gx - r), y=q(y - r), w=GLYPH, h=GLYPH,
                 style=K.PAIR_GLYPH if lk.connected else Style(
                     stroke=K.RULE_STRONG, stroke_w=0.8, radius=r),
                 data={"kind": "pairing"})
    sc.nodes.append(glyph)
    sc.edges.append(edge(f"{lk.id}|a", lk.a, glyph.id, [(x0, y), (gx - r, y)], style,
                         arrow_end=False, data={"kind": "pairing"}))
    sc.edges.append(edge(f"{lk.id}|b", glyph.id, lk.b, [(gx + r, y), (x1, y)], style,
                         arrow_end=False, data={"kind": "pairing"}))
    arrow = Style(stroke=color, stroke_w=0.6)
    sc.edges.append(edge(f"{lk.id}:glyph:1", "", "", [(gx - 4.6, y - 2), (gx + 4.6, y - 2)],
                         arrow, data={"kind": "glyph"}))
    sc.edges.append(edge(f"{lk.id}:glyph:2", "", "", [(gx + 4.6, y + 2), (gx - 4.6, y + 2)],
                         arrow, data={"kind": "glyph"}))
    lw = nb.x - (na.x + na.w) - 4
    lh = tx.line_height(K.SIZE_SMALL)
    sc.nodes.append(box(f"{lk.id}:label", "label", gx - lw / 2, y - r - 2 - lh, lw,
                        [Row("AZ pairing", K.SIZE_SMALL, False, color)], K.LABEL, pad_x=0,
                        pad_y=0, h=lh))
    sc.nodes.append(box(f"{lk.id}:state", "label", gx - lw / 2, y + r + 2, lw,
                        [Row(lk.state_text, K.SIZE_SMALL, False,
                             K.GOOD if lk.connected else K.INK_3)], K.LABEL, pad_x=0,
                        pad_y=0, h=lh))


def _remote_rows(r: MgmtRemote, sources: list[str], links: list[MgmtLink]) -> list[Row]:
    sub = "Nutanix Cloud (Xi)" if r.kind == "xi" else "Prism Central, physical AZ"
    if r.region:
        sub += f", {r.region}"
    rows = [Row(r.name, K.SIZE_REMOTE_TITLE, True, K.INK, 2, 6.5),
            Row(f"{sub} · not in this inventory", K.SIZE_SMALL, False, K.INK_2, 2,
                gap_before=0.5)]
    if len(sources) > 1:
        states = {lk.state_text for lk in links}
        state = f" ({states.pop()})" if len(states) == 1 else ""
        rows.append(Row(f"AZ pairing with {fmt.join_names(sources)}{state}", K.SIZE_SMALL,
                        False, K.IRIS, 3, gap_before=0.5))
    return rows


def _below(sc: Scene, model: ManagementModel, plan: _Plan, site_nodes: dict[str, Node],
           page: Page, kinds: dict[str, bool], states: set[str]) -> float:
    """Remote AZ boxes under the columns; one bus channel per remote."""
    site_bottom = max(n.y + n.h for n in site_nodes.values())
    targets: list[str] = []
    for _, t, _ in plan.below:
        if t not in targets:
            targets.append(t)
    sources = {t: [s for s, tt, _ in plan.below if tt == t] for t in targets}
    single = all(len(v) == 1 for v in sources.values())
    label_w = 96.0
    lab_h = label_block(["AZ pairing, not connected"], label_w)[2] if single else 0.0
    chan0 = q(site_bottom + 8)
    row_y = q(chan0 + CHANNEL * (len(targets) - 1) + (lab_h + 12 if single else 12))

    def centre(s: str) -> float:
        return site_nodes[s].x + site_nodes[s].w / 2

    want = {t: sum(centre(s) for s in sources[t]) / len(sources[t]) for t in targets}
    order = sorted(targets, key=lambda t: (want[t], targets.index(t)))
    # Channels and ports follow the boxes left to right, so buses cross as little as
    # the pairings allow.
    targets = order
    by_site: dict[str, list[str]] = {}
    for sid_, t, _ in plan.below:
        by_site.setdefault(sid_, []).append(t)
    port: dict[tuple[str, str], float] = {}
    for sid_, lst in by_site.items():
        n = site_nodes[sid_]
        lst.sort(key=order.index)
        m = len(lst)
        for i, t in enumerate(lst):
            port[(sid_, t)] = q(n.x + n.w / 2 if m == 1 else n.x + n.w * (0.25 + 0.5 * i /
                                                                            (m - 1)))
    want = {t: sum(port[(s, t)] for s in sources[t]) / len(sources[t]) for t in targets}
    xs: list[float] = []
    for t in order:
        bx = max(MX, want[t] - BELOW_W / 2)
        if xs and bx < xs[-1] + BELOW_W + 12:
            bx = xs[-1] + BELOW_W + 12
        xs.append(bx)
    over = (xs[-1] + BELOW_W) - (page.width - MX) if xs else 0
    if over > 0:
        xs = [max(MX, v - over) for v in xs]
        for i in range(1, len(xs)):
            xs[i] = max(xs[i], xs[i - 1] + BELOW_W + 12)
    boxes: dict[str, Node] = {}
    for t, bx in zip(order, xs, strict=True):
        r = model.remote(t)
        assert r is not None
        lks = [lk for s, tt, lk in plan.below if tt == t]
        names = [short_name(model.name_of(s)) for s in sources[t]]
        node = box(t, "box", bx, row_y, BELOW_W, _remote_rows(r, names, lks), K.REMOTE,
                   pad_x=5, pad_y=CHIP_H / 2 + 2,
                   data={"availability_zone": r.name, "name": r.name})
        sc.nodes.append(node)
        sc.nodes.append(chip(f"{t}:tag", "REMOTE AZ", bx + 5.5, row_y, K.TAG_MUTED, parent=t))
        boxes[t] = node
    hmax = max(b.h for b in boxes.values())
    for b in boxes.values():
        b.h = hmax
    for ti, t in enumerate(targets):
        b = boxes[t]
        srcs = sources[t]
        cy = q(chan0 + CHANNEL * ti)
        for s in srcs:
            lk = next(lk for ss, tt, lk in plan.below if ss == s and tt == t)
            px = port[(s, t)]
            # One bus per remote: every source joins the channel and drops into the box at
            # one point (under the source when there is only one and it is above the box).
            tx_ = q(min(max(want[t], b.x + 5.5 + chip_w("REMOTE AZ") + 6), b.x + b.w - 12))
            if len(srcs) == 1 and b.x + 6 <= px <= b.x + b.w - 6:
                tx_ = px
            pts = [(px, site_bottom), (tx_, b.y)] if abs(px - tx_) < 0.5 else \
                [(px, site_bottom), (px, cy), (tx_, cy), (tx_, b.y)]
            style = K.PAIRING if lk.connected else K.PAIRING_DOWN
            kinds["pairing" if lk.connected else "pairing_down"] = True
            if not lk.connected:
                states.add(lk.state_text)
            e = edge(f"{lk.id}|{s}" if len(srcs) > 1 else lk.id, s, t, pts, style,
                     arrow_end=True, arrow_start=len(srcs) == 1, data={"kind": "pairing"})
            if len(srcs) == 1:
                text = f"AZ pairing, {lk.state_text}"
                _, lw, lh = label_block([text], label_w)
                lx = min(tx_ + 2.5 + lw / 2, page.width - MX - lw / 2)
                ly = b.y - 2 - lh / 2
                e.labels.append(edge_label([text], lx, ly, label_w,
                                           color=K.IRIS if lk.connected else K.INK_3,
                                           background=False))
            sc.edges.append(e)
    return q(max(b.y + b.h for b in boxes.values()))


# ─── entry point ───────────────────────────────────────────────────────


def _caption(model: ManagementModel, sites: list[MgmtSite], part: tuple[int, int]) -> str:
    regs = sum(s.registered for s in sites)
    if part[1] > 1:
        names = fmt.join_names([short_name(s.name) for s in sites])
        return (f"Management plane, part {part[0]} of {part[1]}: {names} "
                f"({fmt.plural(regs, 'registered cluster')}).")
    pcs = fmt.plural(len(sites), "Prism Central")
    return (f"Management plane: {pcs}, their deployment and hosting clusters, "
            f"{fmt.plural(regs, 'registered cluster')}"
            + (" and AZ pairing." if model.links else "."))


def _ladder(sites: list[MgmtSite]) -> list[int | None]:
    most = max((len(s.clusters) for s in sites), default=0)
    return [None, *range(most - 2, MIN_KEEP - 1, -1)]


def _best(model: ManagementModel, sites: list[MgmtSite], parts: list[list[str]], page: Page,
          refs: Refs, limit: Budget, sid: str) -> tuple[Scene, bool]:
    part_of = {i: pi for pi, p in enumerate(parts) for i in p}
    plan = _plan(model, sites, part_of, len(parts))
    sc: Scene | None = None
    for keep in _ladder(sites):
        sc = _layout_part(model, plan, page, refs, keep, sid)
        if sc.height <= limit(sc.width):
            return sc, True
    assert sc is not None
    return sc, False


def _split(ids: list[str], pairs: list[tuple[str, str]]) -> tuple[list[str], list[str]]:
    """A part that does not fit gives its last paired group to a new part (paired zones
    stay together), or its last column when the whole part is one group."""
    seen = _components(ids, pairs)
    if len(seen) > 1:
        move = seen[-1]
        return [i for i in ids if i not in move], move
    return ids[:-1], ids[-1:]


def _components(ids: list[str], pairs: list[tuple[str, str]]) -> list[list[str]]:
    adj: dict[str, set[str]] = {i: set() for i in ids}
    for a, b in pairs:
        if a in adj and b in adj:
            adj[a].add(b)
            adj[b].add(a)
    out: list[list[str]] = []
    seen: set[str] = set()
    for i in ids:
        if i in seen:
            continue
        stack, found = [i], set()
        while stack:
            n = stack.pop()
            if n not in found:
                found.add(n)
                stack.extend(adj[n] - found)
        seen |= found
        out.append([x for x in ids if x in found])
    return out


def layout(model: ManagementModel, page: Page | None = None, refs: Refs | None = None,
           first_budget: Budget | None = None) -> list[Scene]:
    """Scenes (parts) for the model; every part fits `page.max_h`, part 1 also
    `first_budget(width)`."""
    page = page or Page()
    refs = refs or Refs(clusters_table="the registered clusters table")
    if not model.sites:
        return []
    by_id = {s.id: s for s in model.sites}
    pairs = [(lk.a, lk.b) for lk in model.links if lk.a in by_id and lk.b in by_id]
    parts = group([s.id for s in model.sites], pairs, PER_PART)

    def limit(pi: int) -> Budget:
        if pi == 0 and first_budget is not None:
            return lambda w: min(page.max_h, first_budget(w))
        return lambda _w: page.max_h

    pi = 0
    while pi < len(parts):
        _, ok = _best(model, [by_id[i] for i in parts[pi]], parts, page, refs, limit(pi),
                      "probe")
        if not ok and len(parts[pi]) > 1:
            keep_ids, move = _split(parts[pi], pairs)
            parts = parts[:pi] + [keep_ids, move] + parts[pi + 1:]
            continue
        pi += 1
    # Splits can leave small neighbouring parts: join them again where they fit.
    pi = 0
    while pi < len(parts) - 1:
        joined = parts[pi] + parts[pi + 1]
        if len(joined) <= PER_PART:
            trial = parts[:pi] + [joined] + parts[pi + 2:]
            _, ok = _best(model, [by_id[i] for i in joined], trial, page, refs, limit(pi),
                          "probe")
            if ok:
                parts = trial
                continue
        pi += 1
    scenes = []
    for pi, ids in enumerate(parts):
        sid = "management" if len(parts) == 1 else f"management-{pi + 1}"
        sites = [by_id[i] for i in ids]
        sc, _ = _best(model, sites, parts, page, refs, limit(pi), sid)
        sc.part = (pi + 1, len(parts))
        sc.caption = _caption(model, sites, sc.part)
        scenes.append(sc)
    return scenes
