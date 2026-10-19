"""Estate map layout (A6): one tile per availability zone, one dashed frame per part.

Frames flow left to right in rows, in part order. Inside a frame, adjacent paired AZs
are joined by an AZ pairing arrow (dashed, at the Prism Central block) and replication
(solid, weight by the strongest type, at the counts line). AZs outside the inventory are
dashed boxes under the frames; with one row of frames each is fed by a bus from the
tiles replicating to it (labelled with its schedules), with several rows the box lists
its sources instead, so no connector crosses a frame.
"""

from __future__ import annotations

from typing import Any

from .. import fmt
from .. import text as tx
from .. import tokens as K
from ..build import CHIP_H, Row, box, chip, edge, edge_label, label_block, legend, measure
from ..estate import EstateModel, EstateRefs, Tile
from ..facts import short_name
from ..model import Link, Repl
from ..scene import Node, Scene, Style, q
from .conceptual import Budget, Page

MX = 0.5
TOP = 5.0
TW = 92.0  # tile width
TG = 12.0  # gap between unlinked tiles
LG = 18.0  # gap between linked tiles (room for the arrows)
FP = 7.0  # frame padding
FG = 10.0  # gap between frames
RW = 150.0  # remote box width
CH = 11.0  # bus channel pitch
HYP_COLORS = {"AHV": K.IRIS, "ESXi": K.ESXI}


def _linked(m: EstateModel, a: str, b: str) -> list[Link]:
    return [lk for lk in m.links if {lk.a, lk.b} == {a, b}]


def _frame_w(m: EstateModel, ids: list[str]) -> float:
    gaps = sum(LG if _linked(m, a, b) else TG for a, b in zip(ids, ids[1:], strict=False))
    return q(2 * FP + len(ids) * TW + gaps)


def _pc_rows(t: Tile) -> list[Row]:
    rows = []
    if t.pc_version:
        rows.append(Row(f"Prism Central {t.pc_version}", K.SIZE_BODY, True, K.WHITE, 2))
    if t.deployment:
        rows.append(Row(t.deployment, K.SIZE_SMALL, False, K.WHITE, 2))
    return rows


def _tail_rows(t: Tile, ref: str) -> list[Row]:
    hyp = " · ".join(f"{h} {n:,}" for h, n in t.hyp_clusters)
    rows = [Row(hyp, K.SIZE_SMALL, False, K.INK_2, 2)] if hyp else []
    rows.append(Row(f"Detail: {ref}", K.SIZE_SMALL, False, K.INK_3, 2))
    return rows


def _tile(sc: Scene, t: Tile, x: float, y: float, h: float, parent: str, ref: str
          ) -> dict[str, float]:
    """Draw tile `t`; returns the y of its PC block centre and of its counts line."""
    inner = TW - 10
    head = [Row(t.name, K.SIZE_CARD_TITLE, True, K.INK, 2, tx.MIN_SIZE)]
    node = box(t.id, "box", x, y, TW, head, K.SITE, pad_x=5, pad_y=CHIP_H / 2 + 3.5, h=h,
               parent=parent, data=dict(sorted(t.data.items())))
    sc.nodes.append(node)
    sc.nodes.append(chip(f"{t.id}:tag", "AZ", x + 5, y, K.TAG, parent=t.id))
    _, nh = measure(head, inner)
    cy = y + CHIP_H / 2 + 3.5 + nh + 3
    out = {"pc": cy, "counts": cy}
    pc_rows = _pc_rows(t)
    if pc_rows:
        pcn = box(f"{t.id}:pc", "box", x + 5, cy, inner, pc_rows, K.PC, pad_x=3, pad_y=2.5,
                  parent=t.id, data={"prism_central": t.name})
        sc.nodes.append(pcn)
        out["pc"] = q(pcn.y + pcn.h / 2)
        cy = pcn.y + pcn.h + 3
    counts = [Row(f"{fmt.plural(t.clusters, 'cluster')}, {fmt.plural(t.nodes, 'node')}",
                  K.SIZE_BODY, True, K.INK, 2)]
    cn = box(f"{t.id}:counts", "label", x + 5, cy, inner, counts, K.LABEL, pad_x=0, pad_y=0)
    sc.nodes.append(cn)
    out["counts"] = q(cn.y + cn.h / 2)
    cy = cn.y + cn.h + 2.5
    total = sum(n for _, n in t.hyp_nodes) or 0
    if total:
        bx = x + 5
        for h_, n in t.hyp_nodes:
            if not n:
                continue
            bw = q(inner * n / total)
            sc.nodes.append(Node(id=f"{t.id}:bar:{h_}", kind="swatch", x=q(bx), y=q(cy),
                                 w=bw, h=4.5, parent=t.id,
                                 style=Style(fill=HYP_COLORS.get(h_, K.INK_3)),
                                 data={"hypervisor": h_, "nodes": str(n)}))
            bx += bw
        cy += 4.5 + 3
    tail = box(f"{t.id}:detail", "label", x + 5, cy, inner, _tail_rows(t, ref), K.LABEL,
               pad_x=0, pad_y=0)
    sc.nodes.append(tail)
    return out


def _tile_h(t: Tile) -> float:
    probe = Scene(id="probe", type="estate", title="", caption="", width=0, height=0)
    _tile(probe, t, 0, 0, 10, "", "Figure 00-00")
    bottom = max(n.y + n.h for n in probe.nodes if n.kind != "tag")
    return q(bottom + 5)


def _repl_style(reps: list[Repl]) -> Style:
    return {"sync": K.REPL_SYNC, "nearsync": K.REPL_NEARSYNC}.get(fmt.repl_strength(reps),
                                                                  K.REPL_ASYNC)


def _rows(widths: list[float], max_w: float) -> list[list[int]]:
    rows: list[list[int]] = [[]]
    used = 0.0
    for i, w in enumerate(widths):
        if rows[-1] and used + FG + w > max_w:
            rows.append([])
            used = 0.0
        used += (FG if rows[-1] else 0.0) + w
        rows[-1].append(i)
    return rows


def layout(model: EstateModel, page: Page | None = None, refs: Any = None,
           first_budget: Budget | None = None) -> list[Scene]:
    page = page or Page()
    labels = list(refs.part_labels) if isinstance(refs, EstateRefs) else []
    n_parts = len(model.parts)
    tiles = {t.id: t for t in model.tiles}
    sc = Scene(id="estate", type="estate", title=model.title, caption="", width=page.width,
               height=0)
    th = max((_tile_h(t) for t in model.tiles), default=40.0)
    widths = [_frame_w(model, p) for p in model.parts]
    rows = _rows(widths, page.width - 2 * MX)
    frame_h = q(CHIP_H / 2 + 9 + th + FP)
    y = TOP
    centres: dict[str, tuple[float, dict[str, float], Node]] = {}
    kinds = {"pairing": False, "pairing_down": False, "replication": False}
    for row in rows:
        x = MX
        for pi in row:
            ids = model.parts[pi]
            ref = labels[pi] if pi < len(labels) else f"part {pi + 1} of {n_parts}"
            tag = f"PART {pi + 1} OF {n_parts}" + (f" · {ref.upper()}" if pi < len(labels)
                                                   else "")
            fid = f"estate:part:{pi + 1}"
            sc.nodes.append(Node(id=fid, kind="container", x=q(x), y=q(y), w=widths[pi],
                                 h=frame_h, style=K.FRAME,
                                 data={"part": f"{pi + 1} of {n_parts}", "name": tag.title()}))
            sc.nodes.append(chip(f"{fid}:tag", tag, x + 6, y, K.TAG_MUTED, parent=fid))
            tx_ = x + FP
            ty = q(y + CHIP_H / 2 + 9)
            for k, tid in enumerate(ids):
                t = tiles[tid]
                ys = _tile(sc, t, tx_, ty, th, fid, ref)
                node = sc.node(tid)
                centres[tid] = (q(tx_ + TW / 2), ys, node)
                if k + 1 < len(ids):
                    nxt = ids[k + 1]
                    gap = LG if _linked(model, tid, nxt) else TG
                    _adjacent(sc, model, tid, nxt, tx_ + TW, tx_ + TW + gap, ys, kinds)
                    tx_ += TW + gap
            x = q(x + widths[pi] + FG)
        y = q(y + frame_h + FG + 4)
    bottom = q(y - FG - 4)
    bottom = _remotes(sc, model, centres, bottom, len(rows) == 1, page, kinds)

    items: list[tuple[str, Style, str]] = [("box", K.PC, "Prism Central"),
                                           ("box", K.FRAME, "Part (figure)")]
    hyps = sorted({h for t in model.tiles for h, n in t.hyp_nodes if n},
                  key=lambda h: (h not in HYP_COLORS, h))
    for h in hyps:
        items.append(("box", Style(fill=HYP_COLORS.get(h, K.INK_3)), f"{h} nodes"))
    if model.remotes and any(n.id.startswith("az:") for n in sc.nodes):
        items.append(("box", K.REMOTE, "Outside this inventory"))
    if kinds["replication"]:
        items.append(("line", K.LEGEND_REPL, "Replication"))
    if kinds["pairing"]:
        items.append(("line", K.PAIRING, "AZ pairing"))
    if kinds["pairing_down"]:
        items.append(("line", K.PAIRING_DOWN, "AZ pairing, not connected"))
    lh = legend(sc, items, MX, bottom + 9, page.width - 2 * MX)
    right = max(n.x + n.w for n in sc.nodes)
    sc.width = q(min(page.width, right + MX))
    sc.height = q(bottom + 9 + lh + 1)
    figs = fmt.join_names(labels) if len(labels) == n_parts and labels else None
    sc.caption = (f"Estate map: {fmt.plural(len(model.tiles), 'availability zone')}, grouped "
                  + (f"into the parts drawn in {'Figures' if n_parts > 1 else 'Figure'} "
                     f"{figs.replace('Figure ', '')}." if figs else
                     f"into {fmt.plural(n_parts, 'part')}."))
    sc.meta = {"sites": [t.id for t in model.tiles], "parts": [list(p) for p in model.parts]}
    return [sc]


def _adjacent(sc: Scene, m: EstateModel, a: str, b: str, x0: float, x1: float,
              ys: dict[str, float], kinds: dict[str, bool]) -> None:
    for lk in _linked(m, a, b):
        fwd = lk.a == a
        if lk.kind == "pairing":
            up = lk.connected
            kinds["pairing" if up else "pairing_down"] = True
            sc.edges.append(edge(lk.id, a, b, [(x0 + 0.5, ys["pc"]), (x1 - 0.5, ys["pc"])],
                                 K.PAIRING if up else K.PAIRING_DOWN, arrow_start=True,
                                 arrow_end=True, data={"kind": "pairing"}))
        else:
            kinds["replication"] = True
            ahead = lk.forward if fwd else lk.backward
            back = lk.backward if fwd else lk.forward
            sc.edges.append(edge(lk.id, a, b, [(x0 + 0.5, ys["counts"]),
                                               (x1 - 0.5, ys["counts"])],
                                 _repl_style(lk.repl), arrow_end=bool(ahead),
                                 arrow_start=bool(back), data={"kind": "replication"}))


def _remotes(sc: Scene, m: EstateModel, centres: dict[str, tuple[float, dict[str, float],
                                                                Node]],
             bottom: float, bus: bool, page: Page, kinds: dict[str, bool]) -> float:
    """Remote AZs under the frames; returns the new content bottom."""
    feeds: dict[str, list[tuple[str, Link]]] = {}
    for lk in m.links:
        if lk.kind != "replication":
            continue
        for site, other in ((lk.a, lk.b), (lk.b, lk.a)):
            if site in centres and other.startswith("az:"):
                feeds.setdefault(other, []).append((site, lk))
    remotes = [r for r in m.remotes if r.id in feeds]
    if not remotes:
        return bottom
    kinds["replication"] = True
    ych0 = q(bottom + 12)
    box_y = q(ych0 + CH * (len(remotes) - 1) + 16) if bus else q(bottom + 14)
    want = [sum(centres[s][0] for s, _ in feeds[r.id]) / len(feeds[r.id]) - RW / 2
            for r in remotes]
    xs = _deoverlap(want, RW, 12, MX, page.width - MX) if bus else \
        [q(MX + i * (RW + 12)) for i in range(len(remotes))]
    for k, (r, bx) in enumerate(zip(remotes, xs, strict=True)):
        srcs = sorted(feeds[r.id], key=lambda sl: centres[sl[0]][0])
        reps = [rep for s, lk in srcs
                for rep in (lk.forward if lk.a == s else lk.backward)]
        sched = ", ".join(fmt.repl_group(reps)) if reps else "Replication"
        who = [short_name(m.tiles[[t.id for t in m.tiles].index(s)].name) for s, _ in srcs]
        names = fmt.join_names(who) if len(who) <= 3 else \
            f"{', '.join(who[:2])} and {len(who) - 2:,} more"
        sub = "Nutanix Cloud (Xi)" if r.kind == "xi" else "Physical AZ"
        if r.region:
            sub += f", {r.region}"
        rows = [Row(r.name, K.SIZE_REMOTE_TITLE, True, K.INK, 2, 6.5),
                Row(f"{sub} · not in this inventory", K.SIZE_SMALL, False, K.INK_2, 2)]
        if not bus:
            rows.append(Row(f"From {names}: {sched}", K.SIZE_SMALL, False, K.IRIS, 3))
        node = box(r.id, "box", bx, box_y, RW, rows, K.REMOTE, pad_x=5,
                   pad_y=CHIP_H / 2 + 4, data={"availability_zone": r.name, "name": r.name})
        sc.nodes.append(node)
        sc.nodes.append(chip(f"{r.id}:tag", "REMOTE AZ", bx + 5.5, box_y, K.TAG_MUTED,
                             parent=r.id))
        if not bus:
            continue
        ych = q(ych0 + CH * k)
        tx_ = q(bx + RW / 2)
        style = _repl_style(reps)
        spans = []
        for i, (s, lk) in enumerate(srcs):
            sx, _ys, tile = centres[s]
            sx = q(sx + 4 * k)
            pts = [(sx, tile.y + tile.h), (sx, ych), (tx_, ych), (tx_, box_y)]
            if abs(sx - tx_) < 0.5:
                pts = [(sx, tile.y + tile.h), (tx_, box_y)]
            e = edge(f"{lk.id}|{s}", s, r.id, pts, style, arrow_end=True,
                     data={"kind": "replication"})
            sc.edges.append(e)
            spans.append((abs(sx - tx_), i, sx, e))
        # One label on the bus, in its widest gap between drops (never over a drop),
        # attached to an edge whose horizontal run covers that gap.
        _, lw, _ = label_block([sched], 140)
        stops = sorted({sx for _, _, sx, _ in spans} | {tx_})
        gaps = sorted(((b - a, a, b) for a, b in zip(stops, stops[1:], strict=False)),
                      key=lambda g: (-g[0], g[1]))
        placed = False
        for width, a, b in gaps:
            if width < lw + 6:
                break
            owner = next((e for _, _, sx, e in spans
                          if min(sx, tx_) <= a and b <= max(sx, tx_)), None)
            if owner is not None:
                owner.labels.append(edge_label([sched], (a + b) / 2, ych, 140))
                placed = True
                break
        if not placed:  # no room on the bus: the box states the schedules
            rows.append(Row(sched, K.SIZE_SMALL, False, K.IRIS, 3))
            sc.nodes[sc.nodes.index(node)] = box(
                r.id, "box", bx, box_y, RW, rows, K.REMOTE, pad_x=5, pad_y=CHIP_H / 2 + 4,
                data={"availability_zone": r.name, "name": r.name})
    return q(max(n.y + n.h for n in sc.nodes))


def _deoverlap(xs: list[float], w: float, gap: float, lo: float, hi: float) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    last = None
    for i in order:
        x = max(lo, xs[i])
        if last is not None and x < last + w + gap:
            x = last + w + gap
        out[i] = x
        last = x
    over = max((x + w for x in out), default=0) - hi
    if over > 0:
        out = [max(lo, x - over) for x in out]
    return [q(x) for x in out]
