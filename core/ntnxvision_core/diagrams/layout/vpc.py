"""Flow Virtual Networking Design layout (N2): one figure per availability zone.

Top down inside the AZ container: the external subnets band (up to 3 boxes, then a
"+N more external subnets" box), one bus channel per external subnet, then the VPCs
three to a row with their overlay subnets on a trunk. Each VPC connects to each of its
external subnets on that subnet's channel: solid when routed, dashed for NAT. A VPC in
the second row climbs through the gutter between the first row's columns, each on its
own lane, so no connector crosses a box.

Density ladder (text never under 6 pt, page 672 x 440 pt): up to 5 overlays per VPC
(4 and "+N more"), then 3 (2 and "+N more"); up to 2 VPC rows per part (the external
band repeats in each part), 2 parts per AZ, then a "+N more VPCs" card.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .. import text as tx
from .. import tokens as K
from ..build import CHIP_H, Row, box, chip, edge, legend, measure, place_lines, text_h, trunk_list
from ..netcommon import (
    MAX_AZ_FIGURES,
    MAX_PARTS,
    MX,
    P,
    az_container,
    label_right,
    more_card,
    pill_right,
    pill_w,
    short_gw,
    tag_w,
    vms_text,
)
from ..scene import Node, Scene, Style, q
from ..vpc import (
    ExtNet,
    Overlay,
    VpcModel,
    VSite,
    VVpc,
    ext_detail,
    fips_text,
    mode,
    mode_text,
    routing_text,
    site_line,
)
from .conceptual import Page, Refs

COLS = 3
ROWS_PER_PART = 2
OV_H = 20.5
OV_GAP = 3.5
MAX_OV = (5, 3)
MAX_EXT = 3
CHANNEL = 6.0
ROW_GAP = 18.0

Budget = Callable[[float], float]


def _gap(rows: int) -> float:
    return 24.0 if rows > 1 else 12.0


def _ov_shown(n: int, level: int) -> tuple[int, int]:
    cap = MAX_OV[level]
    return (n, 0) if n <= cap else (cap - 1, n - (cap - 1))


def _overlay_nodes(o: Overlay, parent: str, x: float, y: float, w: float) -> list[Node]:
    user = o.kind == "user"
    lab = "USER-ADDED" if user else "IPAM" if o.ipam else "No IPAM" if o.ipam is False \
        else "IPAM N/A"
    vms = None if user else vms_text(o.vms)
    rw = max(pill_w(lab), tx.width(vms, K.SIZE_SMALL) if vms else 0.0)
    node = box(o.id, "box", x, y, w, [], K.USER if user else K.OVERLAY, h=OV_H, parent=parent,
               data=dict(sorted({"name": o.name, **({"prefix": o.prefix} if o.prefix else {}),
                                 **o.data}.items())))
    detail = (o.note or "Added by the author, not collected") if user else \
        " · ".join(p for p in (o.prefix or "IP range N/A", short_gw(o.prefix, o.gateway),
                               o.note) if p)
    mt, th = measure([Row(o.name, 6.6, True, K.INK, 1, tx.MIN_SIZE)], w - 8 - rw - 4)
    mb, _ = measure([Row(detail, K.SIZE_SMALL, False, K.INK_3, 1)], w - 8 - rw - 4)
    node.lines = place_lines(mt, w, 2.6, "left", 4) + place_lines(mb, w, 2.6 + th, "left", 4)
    out = [node]
    if user:
        out.append(pill_right(f"{o.id}:ipam", lab, x + w - 4, y + 2.5, K.TAG_USER, K.WHITE,
                              o.id))
    else:
        out.append(pill_right(f"{o.id}:ipam", lab, x + w - 4, y + 2.5,
                              K.IPAM_PILL if o.ipam else K.NOIPAM_PILL,
                              K.IRIS if o.ipam else K.INK_3, o.id))
    if vms:
        out.append(label_right(f"{o.id}:vms", vms, x + w - 4, y + OV_H - 9.5, 8.0, o.id))
    return out


def _vpc_rows(v: VVpc, exts: dict[str, ExtNet]) -> list[Row]:
    if v.kind == "note":
        rows = [Row(v.name, K.SIZE_CARD_TITLE, True, K.INK, 2, tx.MIN_SIZE)]
        if v.note:
            rows.append(Row(v.note, K.SIZE_BODY, False, K.INK_2, 4, gap_before=0.5))
        return rows
    rows = [Row(v.name, 7.6, True, K.IRIS, 1, tx.MIN_SIZE),
            Row(mode_text(v, exts), K.SIZE_BODY, False, K.INK_2, 2, gap_before=0.5),
            Row(routing_text(v), K.SIZE_BODY, False, K.INK_2, 2)]
    if v.dns:
        rows.append(Row("DNS " + ", ".join(v.dns), K.SIZE_SMALL, False, K.INK_3, 2))
    if v.note:
        rows.append(Row(v.note, K.SIZE_SMALL, False, K.INK_3, 2))
    return rows


def _column(sc: Scene, v: VVpc, x: float, y: float, w: float, vh: float, parent: str,
            exts: dict[str, ExtNet], level: int, refs: Refs) -> float:
    """Draw a VPC box (height `vh`) and its overlays; returns the column's bottom."""
    if v.kind == "note":
        n = box(v.id, "box", x, y, w, _vpc_rows(v, exts), K.NOTE, pad_x=6, pad_y=6,
                align="left", h=vh, parent=parent, data={"name": v.name, "origin": "user"})
        sc.nodes.append(n)
        return n.y + n.h
    vb = box(v.id, "box", x, y, w, _vpc_rows(v, exts), K.VPC, pad_x=6, pad_y=5,
             align="left", h=vh, parent=parent, data=dict(sorted({"name": v.name,
                                                                 **v.data}.items())))
    sc.nodes.append(vb)
    if v.fips:
        lab = f"{v.fips:,} FLOATING IP" + ("S" if v.fips != 1 else "")
        sc.nodes.append(chip(f"{v.id}:fip", lab, x + w - 6 - tag_w(lab), y, K.EXTERNAL,
                             parent=v.id))
    shown, more = _ov_shown(len(v.overlays), level)
    rows: list[tuple[float, Any]] = []
    for o in v.overlays[:shown]:
        rows.append((OV_H, lambda sx, sy, sw, o=o: _overlay_nodes(o, parent, sx, sy, sw)))
    if more:
        text = f"+{more:,} more overlay subnet{'s' if more != 1 else ''}, listed in " \
            f"{refs.clusters_table}"
        mh = q(text_h([Row(text, K.SIZE_SMALL, True, K.INK_2, 2)], w - 11 - 8) + 6)
        rows.append((mh, lambda sx, sy, sw, t=text, mh=mh: [box(
            f"{v.id}:more", "box", sx, sy, sw, [Row(t, K.SIZE_SMALL, True, K.INK_2, 2)],
            K.MORE, pad_x=4, pad_y=3, align="left", h=mh, parent=parent,
            data={"more": str(more)})]))
    if not rows:
        return y + vh
    end = trunk_list(sc, v.id, q(y + vh), x, w, rows, gap=OV_GAP, indent=14.0)
    return q(end - OV_GAP)


def _ext_rows(e: ExtNet) -> list[Row]:
    rows = [Row(e.name, 7.5, True, K.INK, 1, tx.MIN_SIZE),
            Row(ext_detail(e), K.SIZE_BODY, False, K.INK_2, 2, gap_before=0.5)]
    if (t := fips_text(e)) is not None:
        rows.append(Row(t, K.SIZE_BODY, False, K.LIVE, 2, gap_before=0.5))
    if e.note and e.kind != "user":
        rows.append(Row(e.note, K.SIZE_SMALL, False, K.INK_3, 2))
    return rows


def _scene(site: VSite, rows: list[list[VVpc]], part: tuple[int, int], level: int,
           more: list[VVpc], width: float, refs: Refs) -> Scene:
    sid = f"vpc-{site.name}" + (f"-{part[0]}" if part[1] > 1 else "")
    sc = Scene(id=sid, type="vpc", title="Flow virtual networking design", caption="",
               width=width, height=0)
    title = site.name + (f", part {part[0]} of {part[1]}" if part[1] > 1 else "")
    cid = site.id if part[1] == 1 else f"{site.id}:{part[0]}"
    cont, y = az_container(sc, cid, title, site_line(site), width, "AZ",
                           data={"prism_central": site.name})
    inner = width - 2 * MX - 2 * P
    exts = {e.id: e for e in site.externals}
    vpcs = [v for r in rows for v in r]
    # External subnets this part draws: those its VPCs use, then unused ones (part 1).
    used = [e for e in site.externals if any(e.id in v.externals for v in vpcs)]
    if part[0] == 1:
        used += [e for e in site.externals if e not in used]
    shown_ext = used if len(used) <= MAX_EXT else used[:MAX_EXT - 1]
    hidden = [e for e in used if e not in shown_ext]
    boxes = len(shown_ext) + (1 if hidden else 0)
    gap = _gap(len(rows))
    anchor: dict[str, Node] = {}
    band_bottom = y
    if boxes:
        ew = q(min(260.0, (inner - (boxes - 1) * gap) / boxes))
        x0 = MX + P + (inner - boxes * ew - (boxes - 1) * gap) / 2
        eh = max([text_h(_ext_rows(e), ew - 14) for e in shown_ext] or [0.0])
        eh = q(eh + CHIP_H / 2 + 3.5 + 4)
        if hidden:
            eh = max(eh, 34.0)
        ey = q(y + 4)
        for i, e in enumerate(shown_ext):
            ex = q(x0 + i * (ew + gap))
            user = e.kind == "user"
            n = box(e.id, "box", ex, ey, ew, _ext_rows(e), K.USER if user else K.EXTERNAL_NET,
                    pad_x=7, pad_y=CHIP_H / 2 + 3.5, align="left", h=eh, parent=cont.id,
                    data=dict(sorted({"name": e.name, "kind": "external_subnet",
                                      **e.data}.items())))
            sc.nodes.append(n)
            sc.nodes.append(chip(f"{e.id}:tag", "EXTERNAL NETWORK" if user else
                                 "EXTERNAL SUBNET", ex + 6, ey,
                                 K.TAG_USER if user else K.TAG_NEIGHBOUR, parent=e.id))
            anchor[e.id] = n
        if hidden:
            ex = q(x0 + len(shown_ext) * (ew + gap))
            mc = more_card(f"{cid}:more-ext", ex, ey, ew,
                           f"+{len(hidden):,} more external subnet"
                           f"{'s' if len(hidden) != 1 else ''}",
                           [", ".join(e.name for e in hidden[:4])], cont.id, eh)
            sc.nodes.append(mc)
            for e in hidden:
                anchor[e.id] = mc
        band_bottom = ey + eh
    # One channel per drawn external box that a VPC uses.
    targets = list(dict.fromkeys(anchor[e.id].id for e in used
                                 if any(e.id in v.externals for v in vpcs) and e.id in anchor))
    channel = {t: q(band_bottom + 12 + k * CHANNEL) for k, t in enumerate(targets)}
    top = q(band_bottom + 12 + max(0, len(targets) - 1) * CHANNEL + 14) if targets else \
        q(band_bottom + (14 if boxes else 0))
    cw = q((inner - (COLS - 1) * gap) / COLS)
    xs = [q(MX + P + i * (cw + gap)) for i in range(COLS)]
    drawn: list[str] = []
    row_tops: list[float] = []
    bottom = top
    for ri, row in enumerate(rows):
        slots: list[VVpc | None] = list(row)
        last = ri == len(rows) - 1
        if more and last:
            slots.append(None)
        vh = q(max([text_h(_vpc_rows(v, exts), cw - 12) + 10 for v in row] or [24.0]))
        ry = bottom if ri == 0 else q(bottom + ROW_GAP)
        row_tops.append(ry)
        col_bottom = ry
        for ci, v in enumerate(slots):
            if v is None:
                nm = len(more)
                sc.nodes.append(more_card(
                    f"{cid}:more", xs[ci], ry, cw, f"+{nm:,} more VPC{'s' if nm != 1 else ''}",
                    [", ".join(m.name for m in more[:5]) + (" and others" if nm > 5 else ""),
                     f"Listed with their subnets in {refs.clusters_table}"], cont.id,
                    data={"vpcs": str(nm)}))
                col_bottom = max(col_bottom, sc.nodes[-1].y + sc.nodes[-1].h)
                continue
            b = _column(sc, v, xs[ci], ry, cw, vh, cont.id, exts, level, refs)
            col_bottom = max(col_bottom, b)
            drawn.append(v.id)
            _links(sc, v, ri, ci, xs, cw, gap, ry, bottom, anchor, channel, exts)
        bottom = col_bottom
    cont.h = q(bottom + P - cont.y)
    y = cont.y + cont.h
    lh = legend(sc, _legend_items(site, vpcs, exts), MX, y + 9, width - 2 * MX)
    sc.height = q(y + 9 + lh + 1)
    where = site.name + (f", part {part[0]} of {part[1]}" if part[1] > 1 else "")
    sc.caption = (f"Flow virtual networking design, {where}: external subnets, VPCs, "
                  "overlay subnets and floating IPs.")
    sc.part = part
    sc.meta = {"group": site.id, "group_name": site.name, "sites": [site.id],
               "drawn": drawn, "summarised": [v.id for v in more], "level": level}
    return sc


def _links(sc: Scene, v: VVpc, ri: int, ci: int, xs: list[float], cw: float, gap: float,
           ry: float, prev_bottom: float, anchor: dict[str, Node],
           channel: dict[str, float], exts: dict[str, ExtNet]) -> None:
    if v.kind != "vpc":
        return
    targets = list({anchor[e].id: anchor[e] for e in v.externals if e in anchor}.values())
    style = K.NAT_LINK if mode(v, exts) == "nat" else K.UPLINK
    for k, t in enumerate(targets):
        off = (k - (len(targets) - 1) / 2) * 8
        ax = q(xs[ci] + cw / 2 + off)
        ex = q(t.x + t.w / 2)
        ch = channel[t.id]
        if ri == 0:
            pts = [(ax, ry), (ax, ch), (ex, ch), (ex, t.y + t.h)]
        else:
            # Up through a gutter of the first row, on this column's own lane.
            g = min(ci, COLS - 2)
            lane = q(xs[g] + cw + gap * (1 / 3 if ci <= g else 2 / 3) + k * 4)
            turn = q(ry - ROW_GAP / 2)
            pts = [(ax, ry), (ax, turn), (lane, turn), (lane, ch), (ex, ch), (ex, t.y + t.h)]
        clean = [pts[0]] + [p for i, p in enumerate(pts[1:], 1) if p != pts[i - 1]]
        sc.edges.append(edge(f"{v.id}->{t.id}", v.id, t.id, clean, style, arrow_end=False,
                             data={"kind": "nat" if style is K.NAT_LINK else "routed"}))


def _legend_items(site: VSite, vpcs: list[VVpc], exts: dict[str, ExtNet]
                  ) -> list[tuple[str, Style, str]]:
    items: list[tuple[str, Style, str]] = []
    if any(e.kind != "user" for e in site.externals):
        items.append(("box", K.EXTERNAL_NET, "External subnet (VLAN-backed)"))
    real = [v for v in vpcs if v.kind == "vpc"]
    if real:
        items.append(("box", K.VPC, "VPC (distributed router)"))
    if any(o.kind != "user" for v in real for o in v.overlays):
        items.append(("box", K.OVERLAY, "Overlay subnet"))
    modes = {mode(v, exts) for v in real if v.externals}
    if "routed" in modes:
        items.append(("line", K.UPLINK, "Routed, no NAT"))
    if "nat" in modes:
        items.append(("line", K.NAT_LINK, "NAT"))
    if any(v.fips for v in real):
        items.append(("box", K.EXTERNAL, "Floating IPs"))
    if any(e.kind == "user" for e in site.externals) or \
            any(o.kind == "user" for v in real for o in v.overlays):
        items.append(("box", K.USER, "Added on the Diagrams page"))
    return items


def _plan(site: VSite, level: int, width: float, refs: Refs, per_part: int,
          limit: Callable[[int], float]) -> tuple[list[Scene], bool]:
    vpcs = site.vpcs
    rows = [vpcs[i:i + COLS] for i in range(0, len(vpcs), COLS)] or [[]]
    parts = [rows[i:i + per_part] for i in range(0, len(rows), per_part)]
    more: list[VVpc] = []
    if len(parts) > MAX_PARTS:
        more = [v for p in parts[MAX_PARTS:] for r in p for v in r]
        parts = parts[:MAX_PARTS]
        last = parts[-1][-1]
        if len(last) >= COLS:
            more.insert(0, last.pop())
    n = len(parts)
    scenes = [_scene(site, p, (k + 1, n), level, more if k == n - 1 else [], width, refs)
              for k, p in enumerate(parts)]
    return scenes, not more and all(sc.height <= limit(k) for k, sc in enumerate(scenes))


def layout_site(site: VSite, page: Page | None = None, refs: Refs | None = None,
                first_budget: Budget | None = None) -> list[Scene]:
    page = page or Page()
    refs = refs or Refs(clusters_table="the VPC table")
    width = page.width

    def limit(k: int) -> float:
        if k == 0 and first_budget is not None:
            return min(page.max_h, first_budget(width))
        return page.max_h

    tried: list[list[Scene]] = []
    for per_part, level in ((ROWS_PER_PART, 0), (ROWS_PER_PART, 1), (1, 0), (1, 1)):
        scenes, ok = _plan(site, level, width, refs, per_part, limit)
        if ok:
            return scenes
        tried.append(scenes)
    return min(tried, key=lambda s: (len(s[-1].meta["summarised"]),
                                     max(sc.height for sc in s)))


def layout(model: VpcModel, page: Page | None = None, refs: Refs | None = None,
           first_budget: Budget | None = None, site: str | None = None) -> list[Scene]:
    """Every AZ's scenes (the first `MAX_AZ_FIGURES` AZs) in model order, or only the
    AZ whose id is `site` (a report section per AZ)."""
    out: list[Scene] = []
    if site is not None:
        one = next((s for s in model.sites if s.id == site), None)
        return layout_site(one, page, refs, first_budget) if one is not None and \
            (one.vpcs or one.externals) else []
    for i, s in enumerate(model.sites[:MAX_AZ_FIGURES]):
        if s.vpcs or s.externals:
            out.extend(layout_site(s, page, refs, first_budget if i == 0 else None))
    return out


__all__ = ["layout", "layout_site"]
