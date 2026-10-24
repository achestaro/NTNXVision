"""Host network configuration layout (A4): one figure per host network profile.

Inside an "AHV HOST" container, one column per virtual switch, top down: what uses the
switch (host management, CVM, guest subnets: only what the data shows), the switch bar
(name, DEFAULT chip, bridge), the uplink bond (name, mode, uplinks, MTU chip) and one
box per uplink NIC (speed, link state, the LLDP neighbour it reports). Under the
container: the LLDP neighbours, then, in profile A's figure, the note that ESXi host
networking is not reported by the Nutanix APIs (decision 8).

Width: 480 pt (design summary beside the figure, decision 3) for up to 2 switches of up
to 4 uplinks, else 672 pt. More switches than fit split into parts; text never goes
under 6 pt.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from .. import text as tx
from .. import tokens as K
from ..build import CHIP_H, LEGEND_GAP, Row, box, chip, edge, legend, measure, place_lines, text_h
from ..network import (
    MAX_DRAWN,
    Neighbour,
    NetworkModel,
    Profile,
    VSwitch,
    bond_text,
    esxi_lines,
    mtu_text,
    name_list,
    nic_short,
    speed_text,
    uplinks_text,
)
from ..scene import Scene, Style, q
from .conceptual import Page, Refs
from .fit import place_first

MX = 0.5
TOP = 5.0
P = 7.5  # container padding
COL_GAP = 15.0
NARROW = 480.0
TRAFFIC_GAP = 5.0
NIC_GAP = 6.0
NIC_MIN_W = 46.0
NIC_MAX_W = 64.0
MIN_COL_W = 150.0

Budget = Callable[[float], float]


def _width(p: Profile, page: Page) -> float:
    narrow = len(p.switches) <= 2 and all(len(v.uplinks) <= 4 for v in p.switches)
    return min(page.width, NARROW) if narrow else page.width


def _traffic_rows(label: str, sub: str) -> list[Row]:
    return [Row(label, K.SIZE_BODY, True, K.INK, 1, tx.MIN_SIZE),
            Row(sub, K.SIZE_SMALL, False, K.INK_2, 2)]


def _bond_rows(v: VSwitch) -> tuple[list[Row], list[Row]]:
    title = [Row(f"Uplink bond {v.bond}" if v.bond else "Uplink bond", 6.6, True, K.INK, 2,
                 tx.MIN_SIZE)]
    body = [Row(bond_text(v.bond_mode, v.lacp), K.SIZE_BODY, False, K.INK_2, 2),
            Row(uplinks_text(v), K.SIZE_BODY, False, K.INK_2, 3)]
    return title, body


def _nic_rows(v: VSwitch, i: int) -> list[Row]:
    u = v.uplinks[i]
    state = ", ".join(x for x in (speed_text(u.speed_mbps),
                                  None if u.up is None else ("up" if u.up else "down")) if x)
    rows = [Row(u.name, 6.5, True, K.INK, 1, tx.MIN_SIZE),
            Row(state or "Speed N/A", K.SIZE_SMALL, False, K.INK_2, 2)]
    if u.switch:
        rows.append(Row(f"LLDP: switch {u.switch}", K.SIZE_SMALL, False, K.INK_3, 1))
    return rows


def _nic_grid(n: int, col_w: float) -> tuple[int, float]:
    """(NICs per row, NIC width) for `n` uplinks in a column."""
    per = max(1, min(n, int((col_w + NIC_GAP) // (NIC_MIN_W + NIC_GAP))))
    w = min(NIC_MAX_W, (col_w - (per - 1) * NIC_GAP) / per)
    return per, q(w)


def _footer(p: Profile) -> str:
    parts = []
    if m := nic_short(p.nic_model):
        parts.append(f"NICs: {m}")
    if p.nic_driver:
        parts.append(f"driver {p.nic_driver}")
    if p.nic_firmware:
        parts.append(f"firmware {p.nic_firmware}")
    if p.host:
        parts.append(f"as reported on host {p.host}")
    return " · ".join(parts)


def _neighbour_rows(nb: Neighbour) -> list[Row]:
    head = f"Switch {nb.key}: {nb.model}" if nb.model else f"Switch {nb.key}"
    seen = ", ".join(nb.seen_on)
    line2 = " · ".join(x for x in (f"Chassis {nb.chassis}" if nb.chassis else None,
                                   f"seen on {seen}" if seen else None) if x)
    rows = [Row(head, 6.6, True, K.INK, 2, tx.MIN_SIZE)]
    if line2:
        rows.append(Row(line2, K.SIZE_SMALL, False, K.INK_2, 2))
    if nb.port:
        label = "Switch ports" if ", " in nb.port else "Switch port"
        rows.append(Row(f"{label}: {nb.port}", K.SIZE_SMALL, False, K.INK_2, 2))
    else:
        rows.append(Row("Switch port: not reported", K.SIZE_SMALL, False, K.INK_3, 2))
    return rows


def _part(m: NetworkModel, p: Profile, switches: list[VSwitch], width: float,
          part: tuple[int, int], with_esxi: bool, sid: str) -> Scene:
    sc = Scene(id=sid, type="network", title=m.title, caption="", width=width, height=0)
    cont_w = width - 2 * MX
    inner = cont_w - 2 * P
    n = max(1, len(switches))
    col_w = q((inner - (n - 1) * COL_GAP) / n)
    cid = f"{p.id}:host" if part[1] == 1 else f"{p.id}:host:{part[0]}"
    head_top = TOP + CHIP_H / 2 + 4.5
    title = f"Host network profile {p.letter}"
    if part[1] > 1:
        title += f", part {part[0]} of {part[1]}"
    head = [Row(title, K.SIZE_SITE, True, K.INK, 2, 7.5),
            Row(f"Applies to {p.hosts:,} host{'s' if p.hosts != 1 else ''}: "
                f"{name_list(p.clusters)}", K.SIZE_SMALL, False, K.INK_3, 2, gap_before=0.5)]
    header_h = text_h(head, inner)

    # Row heights shared by every column, so the columns line up.
    traffic_h = max((text_h(_traffic_rows(t.label, t.sub),
                            (col_w - (len(v.traffic) - 1) * TRAFFIC_GAP) / len(v.traffic) - 6)
                     for v in switches for t in v.traffic), default=0.0)
    traffic_h = q(traffic_h + 6) if traffic_h else 0.0
    bar_h = 16.0
    mtu_w = {v.id: q(tx.width(mtu_text(v.mtu), K.SIZE_SMALL, True) + 7) for v in switches}
    bond_h = 0.0
    for v in switches:
        b_title, b_body = _bond_rows(v)
        bond_h = max(bond_h, text_h(b_title, col_w - 12 - mtu_w[v.id] - 4)
                     + text_h(b_body, col_w - 12))
    bond_h = q(bond_h + 9)
    grids = {v.id: _nic_grid(len(v.uplinks), col_w) for v in switches}
    nic_h = max((text_h(_nic_rows(v, i), grids[v.id][1] - 4) for v in switches
                 for i in range(len(v.uplinks))), default=0.0)
    nic_h = q(nic_h + 6) if nic_h else 0.0
    footer = _footer(p)

    y = q(head_top + header_h + 7)
    traffic_y = y
    bar_y = q(traffic_y + traffic_h + 10) if traffic_h else y
    bond_y = q(bar_y + bar_h + 9)
    nic_y0 = q(bond_y + bond_h + 16)

    cont = box(cid, "container", MX, TOP, cont_w, head, K.SITE, pad_x=P,
               pad_y=head_top - TOP, h=10, align="left",
               data={"profile": p.letter, "name": title})
    sc.nodes.append(cont)
    sc.nodes.append(chip(f"{cid}:tag", "AHV HOST", MX + 5.5, TOP, K.TAG, parent=cid))
    bottom = nic_y0
    multi_rows = False
    for ci, v in enumerate(switches):
        cx = q(MX + P + ci * (col_w + COL_GAP))
        mid = cx + col_w / 2
        # Traffic boxes and their edges to the switch.
        if v.traffic:
            tw = q((col_w - (len(v.traffic) - 1) * TRAFFIC_GAP) / len(v.traffic))
            for ti, t in enumerate(v.traffic):
                tx_ = q(cx + ti * (tw + TRAFFIC_GAP))
                sc.nodes.append(box(t.id, "box", tx_, traffic_y, tw,
                                    _traffic_rows(t.label, t.sub), K.NET_TRAFFIC, pad_x=3,
                                    h=traffic_h, valign="middle", parent=cid,
                                    data={"name": f"{t.label} {t.sub}"}))
                tm = tx_ + tw / 2
                sc.edges.append(edge(f"{t.id}->{v.id}", t.id, v.id,
                                     [(tm, traffic_y + traffic_h), (tm, bar_y)], K.NET_LINK,
                                     arrow_end=False, data={"kind": "uses"}))
        # Switch bar: name, DEFAULT chip, bridge on the right.
        bridge = f"bridge {v.bridge}" if v.bridge else ""
        bridge_w = tx.width(bridge, K.SIZE_SMALL) if bridge else 0.0
        chip_w = tx.width("DEFAULT", K.SIZE_CHIP, True) + 7 + 4 if v.default else 0.0
        name_rows = [Row(v.name, 7.5, True, K.IRIS, 1, tx.MIN_SIZE)]
        name_w = col_w - 12 - bridge_w - chip_w - 4
        mn, name_h = measure(name_rows, name_w)
        bar = box(v.id, "box", cx, bar_y, col_w, [], K.NET_SWITCH, h=bar_h, parent=cid,
                  data={"virtual_switch": v.name, "name": v.name,
                        **({"bridge": v.bridge} if v.bridge else {})})
        bar.lines = place_lines(mn, col_w, (bar_h - name_h) / 2, "left", 6.0)
        sc.nodes.append(bar)
        if v.default:
            nx = cx + 6 + (mn[0].width if mn else 0) + 4
            sc.nodes.append(chip(f"{v.id}:default", "DEFAULT", nx, bar_y + bar_h / 2, K.TAG,
                                 parent=v.id))
        if bridge:
            bw = q(bridge_w + 1)
            sc.nodes.append(box(f"{v.id}:bridge", "label", cx + col_w - 6 - bw, bar_y, bw,
                                [Row(bridge, K.SIZE_SMALL, False, K.INK_2)], K.LABEL,
                                pad_x=0, pad_y=0, align="left", h=bar_h, valign="middle",
                                parent=v.id))
        # Bond box with its MTU chip.
        bid = f"{v.id}:bond"
        trows, brows = _bond_rows(v)
        mt, th = measure(trows, col_w - 12 - mtu_w[v.id] - 4)
        mb, _ = measure(brows, col_w - 12)
        bond = box(bid, "box", cx, bond_y, col_w, [], K.NET_BOND, h=bond_h, parent=cid,
                   data={"name": f"Uplink bond {v.bond}" if v.bond else "Uplink bond",
                         **({"bond_mode": v.bond_mode} if v.bond_mode else {}),
                         **({"mtu": str(v.mtu)} if v.mtu else {})})
        bond.lines = (place_lines(mt, col_w, 4.5, "left", 6.0)
                      + place_lines(mb, col_w, 4.5 + th, "left", 6.0))
        sc.nodes.append(bond)
        sc.nodes.append(box(f"{bid}:mtu", "pill", cx + col_w - 6 - mtu_w[v.id], bond_y + 4,
                            mtu_w[v.id], [Row(mtu_text(v.mtu), K.SIZE_SMALL, True, K.INK)],
                            K.NET_MTU, pad_x=3, pad_y=0, h=9.5, valign="middle",
                            parent=bid))
        sc.edges.append(edge(f"{v.id}->{bid}", v.id, bid, [(mid, bar_y + bar_h),
                                                           (mid, bond_y)], K.NET_LINK,
                             arrow_end=False, data={"kind": "uplink"}))
        # Uplink NICs.
        per, nw = grids[v.id]
        rows_n = math.ceil(len(v.uplinks) / per) if v.uplinks else 0
        multi = rows_n > 1
        multi_rows = multi_rows or multi
        trunk = q(cx + 2.5)
        for i, u in enumerate(v.uplinks):
            r, c = divmod(i, per)
            count = min(per, len(v.uplinks) - r * per)
            row_w = count * nw + (count - 1) * NIC_GAP
            x0 = cx + (col_w - row_w) / 2 if not multi else cx + 6
            nx_ = q(x0 + c * (nw + NIC_GAP))
            ny = q(nic_y0 + r * (nic_h + 14))
            sc.nodes.append(box(u.id, "box", nx_, ny, nw, _nic_rows(v, i), K.NET_NIC,
                                pad_x=2, h=nic_h, valign="middle", parent=cid,
                                data={"name": u.name, **({"speed_mbps": str(u.speed_mbps)}
                                                         if u.speed_mbps else {}),
                                      **({"lldp_switch": u.switch} if u.switch else {})}))
            ncx = nx_ + nw / 2
            bus = ny - 8
            if multi:
                pts = [(trunk, bond_y + bond_h), (trunk, bus), (ncx, bus), (ncx, ny)]
            elif abs(ncx - mid) < 0.5:
                pts = [(mid, bond_y + bond_h), (mid, ny)]
            else:
                pts = [(mid, bond_y + bond_h), (mid, bus), (ncx, bus), (ncx, ny)]
            sc.edges.append(edge(f"{bid}->{u.id}", bid, u.id, pts, K.NET_LINK,
                                 arrow_end=False, data={"kind": "uplink"}))
            bottom = max(bottom, ny + nic_h)
    y = bottom
    if footer:
        fy = q(y + 4)
        fn = box(f"{cid}:nics", "label", MX + P, fy, inner,
                 [Row(footer, K.SIZE_SMALL, False, K.INK_3, 2)], K.LABEL, pad_x=0, pad_y=0,
                 align="center", parent=cid)
        sc.nodes.append(fn)
        y = fn.y + fn.h
    cont.h = q(y + P - cont.y)
    y = cont.y + cont.h

    # LLDP neighbours referenced by this part's NICs.
    keys = {u.switch for v in switches for u in v.uplinks if u.switch}
    nbs = [nb for nb in p.neighbours if nb.key in keys]
    used_neighbour = bool(nbs)
    if nbs:
        per = min(len(nbs), 2 if width <= NARROW else 3)
        nb_w = q((inner - (per - 1) * COL_GAP) / per)
        hs = [text_h(_neighbour_rows(nb), nb_w - 12) + CHIP_H / 2 + 8 for nb in nbs]
        nb_h = q(max(hs))
        for i, nb in enumerate(nbs):
            r, c = divmod(i, per)
            bx = q(MX + P + c * (nb_w + COL_GAP))
            by = q(y + 11 + r * (nb_h + 13))
            sc.nodes.append(box(nb.id, "box", bx, by, nb_w, _neighbour_rows(nb), K.NET_BOND,
                                pad_x=6, pad_y=CHIP_H / 2 + 3.5, h=nb_h, align="left",
                                data={"name": f"Switch {nb.key}",
                                      **({"model": nb.model} if nb.model else {}),
                                      **({"chassis": nb.chassis} if nb.chassis else {})}))
            sc.nodes.append(chip(f"{nb.id}:tag", "LLDP NEIGHBOUR", bx + 6, by,
                                 K.TAG_NEIGHBOUR, parent=nb.id))
        y = q(y + 11 + math.ceil(len(nbs) / per) * (nb_h + 13) - 13)

    if with_esxi and m.esxi:
        lines = esxi_lines(m.esxi)
        rows = [Row(lines[0], 6.6, True, K.INK, 3, tx.MIN_SIZE)] + \
            [Row(t, K.SIZE_BODY, False, K.INK_2, 3) for t in lines[1:]]
        note = box(f"{p.id}:esxi", "box", MX, q(y + 11), cont_w, rows, K.NOT_REPORTED,
                   pad_x=6, pad_y=CHIP_H / 2 + 3.5, align="left",
                   data={"name": "ESXi host networking", "clusters": ", ".join(m.esxi.clusters)})
        sc.nodes.append(note)
        sc.nodes.append(chip(f"{p.id}:esxi:tag", "NOT REPORTED BY NUTANIX APIS", MX + 5.5,
                             note.y, K.TAG_MUTED, parent=note.id))
        y = note.y + note.h

    items: list[tuple[str, Style, str]] = [("box", K.NET_SWITCH, "Virtual switch"),
                                          ("box", K.NET_BOND, "Bond, NIC or switch (reported)")]
    if with_esxi and m.esxi:
        items.append(("box", K.NOT_REPORTED, "Not reported"))
    lh = legend(sc, items, MX, y + LEGEND_GAP, cont_w)
    sc.height = q(y + LEGEND_GAP + lh + 1)
    what = "virtual switches, bonds, uplinks and MTU"
    if used_neighbour:
        what = "virtual switches, bonds, uplinks, MTU and LLDP neighbours"
    sc.caption = (f"Host network profile {p.letter}" + (" (edited)" if m.edited else "")
                  + (f", part {part[0]} of {part[1]}" if part[1] > 1 else "") + f": {what}.")
    sc.part = part
    sc.meta = {"group": p.id, "group_name": f"Profile {p.letter}", "profile": p.letter,
               "switches": [v.name for v in switches], "multi_row_nics": multi_rows}
    return sc


def layout(model: NetworkModel, page: Page | None = None, refs: Refs | None = None,
           first_budget: Budget | None = None) -> list[Scene]:
    """The figure's parts; part 1 under the section's summary when it fits there, else
    on a page of its own (`fit.place_first`)."""
    return place_first(lambda budget: _layout(model, page, refs, budget), first_budget)


def _layout(model: NetworkModel, page: Page | None = None, refs: Refs | None = None,
            first_budget: Budget | None = None) -> list[Scene]:
    """Scenes for the first `MAX_DRAWN` profiles (each split into parts when its
    switches do not fit side by side or under the height budget)."""
    page = page or Page()
    del refs
    scenes: list[Scene] = []
    for pi, p in enumerate(model.profiles[:MAX_DRAWN]):
        width = _width(p, page)
        inner = width - 2 * MX - 2 * P
        cols = max(1, min(len(p.switches) or 1,
                          int((inner + COL_GAP) // (MIN_COL_W + COL_GAP)),
                          2 if width <= NARROW else 4))

        def limit(first: bool, width: float = width) -> float:
            if first and first_budget is not None:
                return min(page.max_h, first_budget(width))
            return page.max_h

        while True:
            chunks = [p.switches[i:i + cols] for i in range(0, len(p.switches), cols)] or [[]]
            parts = []
            for k, chunk in enumerate(chunks):
                sid = f"network-{p.letter.lower()}" + (f"-{k + 1}" if len(chunks) > 1 else "")
                parts.append(_part(model, p, chunk, width, (k + 1, len(chunks)),
                                   pi == 0 and k == len(chunks) - 1, sid))
            fits = all(sc.height <= limit(not scenes and k == 0)
                       for k, sc in enumerate(parts))
            if fits or cols == 1:
                break
            cols -= 1
        scenes.extend(parts)
    return scenes
