"""Network Segment Design layout (N1): one figure per availability zone.

Inside the AZ container, cluster cards three to a row (a row shares its height). A card
has its title and a "AHV, 8 nodes, 5 VLAN subnets" line, then per virtual switch a bar
(name, DEFAULT chip, MTU at the right; ESXi: the grey "vSphere port groups" bar, "Switch
and teaming not reported") and one row per segment on a trunk: VLAN chip, name,
"prefix · gw .1", an IPAM / No IPAM pill and the VMs attached.

Density ladder (text never under 6 pt, page 672 x 440 pt): full rows, up to 6 per
switch (5 and "+N more"); compact single-line rows, up to 4 per switch (3 and "+N
more"); up to 2 card rows per part and 2 parts per AZ, then a "+N more clusters" card.
What is not drawn is listed in the segments table (`Refs.clusters_table`).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .. import text as tx
from .. import tokens as K
from ..build import Row, box, chip, legend, measure, place_lines, text_h, trunk_list
from ..netcommon import (
    MAX_AZ_FIGURES,
    MAX_PARTS,
    MX,
    P,
    az_container,
    label_right,
    mark_edited,
    more_card,
    pill_right,
    pill_w,
    tag_at,
    tag_w,
    user_chip,
    vms_text,
)
from ..scene import Node, Scene, Style, q
from ..segments import (
    PG_SWITCH,
    SCluster,
    Segment,
    SegmentsModel,
    SSite,
    SSwitch,
    applies_text,
    card_line,
    segment_detail,
    segment_vlan,
    site_line,
)
from .conceptual import Page, Refs

GAP = 10.0
COLS = 3
ROWS_PER_PART = 2
BAR_H = 13.0
SEG_H = 20.5
SEG_H_COMPACT = 11.0
SEG_GAP = 3.5
#: Rows per switch before "+N more" (full, compact).
MAX_SEGS = (6, 4)

Budget = Callable[[float], float]


def _rows_shown(n: int, level: int) -> tuple[int, int]:
    """(rows drawn, rows summarised) for a switch with `n` segments."""
    cap = MAX_SEGS[level]
    if n <= cap:
        return n, 0
    return cap - 1, n - (cap - 1)


def _seg_nodes(s: Segment, x: float, y: float, w: float, h: float, level: int
               ) -> list[Node]:
    user = s.kind == "user"
    vlab = segment_vlan(s)
    vw = tag_w(vlab)
    right_lab: str | None
    if s.kind == "subnet" and s.external:
        right_lab = "EXTERNAL"
    elif s.kind == "subnet":
        right_lab = "IPAM" if s.ipam else "No IPAM" if s.ipam is False else "IPAM N/A"
    else:
        right_lab = "USER-ADDED" if user else None
    vms = vms_text(s.vms) if s.kind != "user" else None
    rw = max(pill_w(right_lab) if right_lab else 0.0,
             tx.width(vms, K.SIZE_SMALL) if vms else 0.0)
    data = {"name": s.name, **({"vlan": str(s.vlan)} if s.vlan is not None else {}),
            **({"prefix": s.prefix} if s.prefix else {}), **s.data,
            **({"origin": "user"} if user else {})}
    node = box(s.id, "box", x, y, w, [], K.USER if user else K.SEGMENT, h=h,
               data=dict(sorted(data.items())))
    out = [node]
    name = s.name
    if level == 0:
        mt, th = measure([Row(name, 6.6, True, K.INK, 1, tx.MIN_SIZE)], w - 8 - vw - 4 - rw - 4)
        mb, _ = measure([Row(segment_detail(s), K.SIZE_SMALL, False, K.INK_3, 1)],
                        w - 8 - (tx.width(vms, K.SIZE_SMALL) + 4 if vms else 0))
        node.lines = place_lines(mt, w, 2.6, "left", 4 + vw + 4) + \
            place_lines(mb, w, 2.6 + th, "left", 4)
        out.append(tag_at(f"{s.id}:vlan", vlab, x + 4, y + 2.6 + 0.2,
                          K.TAG_USER if user else K.VLAN_CHIP, parent=s.id, h=8.0))
        right = x + w - 4
        if right_lab:
            out.append(pill_right(f"{s.id}:ipam", right_lab, right, y + 2.5,
                                  *_pill_style(s)))
        if vms:
            out.append(label_right(f"{s.id}:vms", vms, right, y + h - 9.5, 8.0, s.id))
    else:
        text = compact_text(s, w - 8 - vw - 4 - rw - 4)
        mt, _ = measure([Row(text, K.SIZE_SMALL, False, K.INK, 1)], w - 8 - vw - 4 - rw - 4)
        node.lines = place_lines(mt, w, (h - tx.line_height(K.SIZE_SMALL)) / 2, "left",
                                 4 + vw + 4)
        out.append(tag_at(f"{s.id}:vlan", vlab, x + 4, y + 1.5,
                          K.TAG_USER if user else K.VLAN_CHIP, parent=s.id, h=8.0))
        if right_lab:
            out.append(pill_right(f"{s.id}:ipam", right_lab, x + w - 4, y + 1.5,
                                  *_pill_style(s)))
    if s.external:
        node.data["external"] = "true"
    return out


def compact_text(s: Segment, max_w: float) -> str:
    """A compact row's text after the VLAN chip: ``prefix · name`` (a port group: its VM
    count, a planned segment: its range text). The gateway is left out, and only the
    name is shortened, so the prefix is never clipped."""
    if s.kind == "portgroup":
        lead = vms_text(s.vms)
    elif s.kind == "user":
        lead = s.prefix_text
    else:
        lead = s.prefix or "IP range N/A"
    if not lead:
        return tx.ellipsize(s.name, K.SIZE_SMALL, False, max_w)
    head = f"{lead} · "
    room = max_w - tx.width(head, K.SIZE_SMALL, False)
    if room < tx.width("ab" + tx.ELLIPSIS, K.SIZE_SMALL, False):
        return tx.ellipsize(lead, K.SIZE_SMALL, False, max_w)
    return head + tx.ellipsize(s.name, K.SIZE_SMALL, False, room)


def _pill_style(s: Segment) -> tuple[Style, str, str]:
    if s.kind == "user":
        return K.TAG_USER, K.WHITE, s.id
    if s.external:
        return K.TAG_NEIGHBOUR, K.WHITE, s.id
    return (K.IPAM_PILL, K.IRIS, s.id) if s.ipam else (K.NOIPAM_PILL, K.INK_3, s.id)


def _card(sc: Scene, c: SCluster, x: float, y: float, w: float, parent: str, level: int,
          refs: Refs, h: float | None = None) -> float:
    """Draw one card (onto `sc`); returns its height (at least `h`)."""
    pad = 6.0
    inner = w - 2 * pad
    if c.kind in ("user", "note"):
        rows = [Row(c.name, K.SIZE_CARD_TITLE, True, K.INK, 2, tx.MIN_SIZE)]
        if c.note:
            rows.append(Row(c.note, K.SIZE_BODY, False, K.INK_2, 4, gap_before=0.5))
        node = box(c.id, "box", x, y, w, rows, K.NOTE if c.kind == "note" else K.USER,
                   pad_x=pad, pad_y=6.0, align="left", parent=parent,
                   data={"name": c.name, "origin": "user"})
        if h is not None:
            node.h = q(max(node.h, h))
        sc.nodes.append(node)
        if c.kind == "user":
            sc.nodes.append(user_chip(node))
        return node.h
    head = [Row(c.name, K.SIZE_CARD_TITLE, True, K.INK, 1, tx.MIN_SIZE),
            Row(card_line(c), K.SIZE_BODY, False, K.INK_2, 2, gap_before=0.5)]
    if c.applies_to:
        head.append(Row(applies_text(c.applies_to), K.SIZE_SMALL, False, K.INK_3, 2))
    if c.note:
        head.append(Row(c.note, K.SIZE_SMALL, False, K.INK_3, 2))
    hh = text_h(head, inner)
    data = dict(c.data)
    if c.applies_to:
        data["applies_to"] = ", ".join(c.applies_to)
    card = box(c.id, "box", x, y, w, head, K.CLUSTER, pad_x=pad, pad_y=5.0, align="left",
               h=10, parent=parent, data=dict(sorted(data.items())))
    sc.nodes.append(card)
    if c.hosts_pc:
        sc.nodes.append(chip(f"{c.id}:role", "HOSTS PC", x + 5, y, K.TAG_ROLE, parent=c.id))
    cy = q(y + 5 + hh + 6)
    seg_h = SEG_H if level == 0 else SEG_H_COMPACT
    for sw in c.switches:
        cy = _switch(sc, c, sw, x + pad, cy, inner, level, seg_h, refs)
    card.h = q(max(h or 0, cy + 2 - y))
    return card.h


def _switch(sc: Scene, c: SCluster, sw: SSwitch, x: float, cy: float, w: float, level: int,
            seg_h: float, refs: Refs) -> float:
    pg = sw.kind == "portgroups"
    right = [f"MTU {sw.mtu:,}"] if sw.mtu else []
    if pg:
        right.append("Switch and teaming not reported")
    if sw.note:
        right.append(sw.note)
    rtext = " · ".join(right)
    dw = tag_w("DEFAULT") + 4 if sw.default else 0.0
    color = K.INK_2 if pg else K.IRIS
    name_w = tx.width(sw.name, 7.0, True)
    room = w - 10 - dw
    rw = min(tx.width(rtext, K.SIZE_SMALL), max(0.0, room - min(name_w, room * 0.5) - 6)) \
        if rtext else 0.0
    mn, nh = measure([Row(sw.name, 7.0, True, color, 1, tx.MIN_SIZE)], room - rw - 6)
    bar = box(sw.id, "box", x, cy, w, [], K.PORTGROUP_BAR if pg else K.NET_SWITCH, h=BAR_H,
              parent=c.id, data=dict(sorted({"name": sw.name, **sw.data}.items())))
    bar.lines = place_lines(mn, w, (BAR_H - nh) / 2, "left", 5.0)
    sc.nodes.append(bar)
    if sw.default and mn:
        sc.nodes.append(chip(f"{sw.id}:default", "DEFAULT", x + 5 + mn[0].width + 4,
                             cy + BAR_H / 2, K.TAG, parent=sw.id))
    if rtext and rw > 8:
        mr, _ = measure([Row(rtext, K.SIZE_SMALL, False, K.INK_2, 1)], rw)
        lw = q(max(m.width for m in mr) + 1)
        lab = box(f"{sw.id}:mtu", "label", x + w - 5 - lw, cy, lw, [], K.LABEL, h=BAR_H,
                  parent=sw.id)
        lab.lines = place_lines(mr, lw, (BAR_H - tx.line_height(K.SIZE_SMALL)) / 2,
                                "left", 0.0)
        sc.nodes.append(lab)
    top = q(cy + BAR_H)
    if not sw.segments:
        what = "No port groups reported" if pg else "No subnets on this switch"
        sc.nodes.append(box(f"{sw.id}:none", "label", x + 11, top + 3, w - 11,
                            [Row(what, K.SIZE_SMALL, False, K.INK_3)], K.LABEL, pad_x=0,
                            pad_y=0, align="left", h=9, valign="middle", parent=c.id))
        return q(top + 3 + 9 + 6)
    shown, more = _rows_shown(len(sw.segments), level)
    rows: list[tuple[float, Any]] = []
    for s in sw.segments[:shown]:
        def draw(sx: float, sy: float, sw_: float, s: Segment = s) -> list[Node]:
            nodes = _seg_nodes(s, sx, sy, sw_, seg_h, level)
            nodes[0].parent = c.id
            return nodes
        rows.append((seg_h, draw))
    if more:
        noun = "port group" if pg else "subnet"
        more_lab = f"+{more:,} more {noun}{'s' if more != 1 else ''}, listed in " \
            f"{refs.clusters_table}"
        mh = q(text_h([Row(more_lab, K.SIZE_SMALL, True, K.INK_2, 2)], w - 11 - 8) + 6)

        def draw_more(sx: float, sy: float, sw_: float, text: str = more_lab,
                      mh: float = mh) -> list[Node]:
            return [box(f"{sw.id}:more", "box", sx, sy, sw_,
                        [Row(text, K.SIZE_SMALL, True, K.INK_2, 2)], K.MORE, pad_x=4,
                        pad_y=3, align="left", h=mh, parent=c.id,
                        data={"more": str(more)})]
        rows.append((mh, draw_more))
    end = trunk_list(sc, sw.id, top, x, w, rows,
                     gap=SEG_GAP if level == 0 else 2.5)
    return q(end + 4)


def _legend_items(cards: list[SCluster]) -> list[tuple[str, Style, str]]:
    sws = [w for c in cards for w in c.switches]
    segs = [s for w in sws for s in w.segments]
    items: list[tuple[str, Style, str]] = []
    if any(w.kind == "ahv" for w in sws):
        items.append(("box", K.NET_SWITCH, "AHV virtual switch"))
    if any(w.kind == "portgroups" for w in sws):
        items.append(("box", K.PORTGROUP_BAR, "vSphere port groups (ESXi)"))
    kinds = {s.kind for s in segs}
    if kinds & {"subnet", "portgroup"}:
        label = ("VLAN subnet or port group" if {"subnet", "portgroup"} <= kinds
                 else "VLAN subnet" if "subnet" in kinds else "Port group")
        items.append(("box", K.SEGMENT, label))
    if any(s.kind == "subnet" and s.ipam for s in segs):
        items.append(("box", K.IPAM_PILL, "Nutanix IPAM (managed)"))
    if any(c.kind == "user" for c in cards) or "user" in kinds:
        items.append(("box", K.USER, "Added on the Diagrams page"))
    return items


def _scene(site: SSite, rows: list[list[SCluster]], part: tuple[int, int], level: int,
           more: list[SCluster], width: float, refs: Refs) -> Scene:
    sid = f"segments-{site.name}" + (f"-{part[0]}" if part[1] > 1 else "")
    sc = Scene(id=sid, type="segments", title="Network segment design", caption="",
               width=width, height=0)
    title = site.name + (f", part {part[0]} of {part[1]}" if part[1] > 1 else "")
    cid = site.id if part[1] == 1 else f"{site.id}:{part[0]}"
    cont, y0 = az_container(sc, cid, title, site_line(site), width,
                            "PRISM ELEMENT" if site.kind == "pe" else "AZ",
                            data={"prism_central": site.name} if site.kind != "pe" else
                            {"prism_element": site.name})
    inner = width - 2 * MX - 2 * P
    cw = q((inner - (COLS - 1) * GAP) / COLS)
    y = y0
    drawn: list[str] = []
    for ri, row in enumerate(rows):
        scratch = Scene(id="x", type="x", title="", caption="", width=width, height=0)
        slots: list[SCluster | None] = list(row)
        last = ri == len(rows) - 1
        if more and last:
            slots.append(None)
        hmax = max([_card(scratch, c, 0, 0, cw, "x", level, refs) for c in row] or [0.0])
        if more and last:
            mc = _more(site, more, 0, 0, cw, "x", refs)
            hmax = max(hmax, mc.h)
        for i, c in enumerate(slots):
            x = q(MX + P + i * (cw + GAP))
            if c is None:
                sc.nodes.append(_more(site, more, x, y, cw, cont.id, refs, hmax))
            else:
                _card(sc, c, x, y, cw, cont.id, level, refs, hmax)
                drawn.append(c.id)
        y = q(y + hmax + GAP)
    cont.h = q(y - GAP + P - cont.y)
    y = cont.y + cont.h
    cards = [c for r in rows for c in r]
    lh = legend(sc, _legend_items(cards), MX, y + 9, width - 2 * MX)
    sc.height = q(y + 9 + lh + 1)
    where = site.name + (f", part {part[0]} of {part[1]}" if part[1] > 1 else "")
    sc.caption = (f"Network segment design, {where}: virtual switches, VLANs, subnets"
                  + (" and port groups" if any(w.kind == "portgroups" for c in cards
                                               for w in c.switches) else "")
                  + " with IPAM.")
    sc.part = part
    sc.meta = {"group": site.id, "group_name": site.name, "sites": [site.id],
               "drawn": drawn, "summarised": [c.id for c in more], "level": level,
               "more_segments": sum(_rows_shown(len(w.segments), level)[1] for c in cards
                                    for w in c.switches)}
    return sc


def _more(site: SSite, more: list[SCluster], x: float, y: float, w: float, parent: str,
          refs: Refs, h: float | None = None) -> Node:
    n = sum(len(sw.segments) for c in more for sw in c.switches)
    names = [c.name for c in more]
    shown = ", ".join(names[:6]) + (f" and {len(names) - 6:,} more" if len(names) > 6 else "")
    return more_card(f"{site.id}:more", x, y, w,
                     f"+{len(more):,} more cluster{'s' if len(more) != 1 else ''}",
                     [shown, f"{n:,} segment{'s' if n != 1 else ''}, listed in "
                     f"{refs.clusters_table}"], parent, h,
                     data={"clusters": str(len(more))})


def _plan(site: SSite, level: int, width: float, refs: Refs,
          limit: Callable[[int], float]) -> tuple[list[Scene], bool]:
    """Scenes for one AZ at a detail level; (scenes, fits)."""
    cards = site.clusters
    rows = [cards[i:i + COLS] for i in range(0, len(cards), COLS)] or [[]]
    parts: list[list[list[SCluster]]] = []
    cur: list[list[SCluster]] = []
    fits = True
    for row in rows:
        trial = cur + [row]
        ok = len(trial) <= ROWS_PER_PART and \
            _scene(site, trial, (1, 1), level, [], width, refs).height <= limit(len(parts))
        if ok or not cur:
            cur = trial
            if not ok:
                fits = False
            continue
        parts.append(cur)
        cur = [row]
        if _scene(site, cur, (1, 1), level, [], width, refs).height > limit(len(parts)):
            fits = False
    parts.append(cur)
    more: list[SCluster] = []
    if len(parts) > MAX_PARTS:
        fits = False
        rest = parts[MAX_PARTS:]
        parts = parts[:MAX_PARTS]
        more = [c for p in rest for r in p for c in r]
        last = parts[-1][-1]
        if len(last) >= COLS:
            more.insert(0, last.pop())
    n = len(parts)
    scenes = [_scene(site, p, (k + 1, n), level, more if k == n - 1 else [], width, refs)
              for k, p in enumerate(parts)]
    return scenes, fits and all(sc.height <= limit(k) for k, sc in enumerate(scenes))


def layout_site(site: SSite, page: Page | None = None, refs: Refs | None = None,
                first_budget: Budget | None = None) -> list[Scene]:
    page = page or Page()
    refs = refs or Refs(clusters_table="the network segments table")
    width = page.width

    def limit(k: int) -> float:
        if k == 0 and first_budget is not None:
            return min(page.max_h, first_budget(width))
        return page.max_h

    # The ladder: full rows in one part, compact rows in one part, then parts (full
    # rows when they fit), and the "+N more clusters" card last.
    full, ok_full = _plan(site, 0, width, refs, limit)
    if ok_full and len(full) == 1:
        return full
    compact, ok_compact = _plan(site, 1, width, refs, limit)
    if ok_compact and len(compact) == 1:
        return compact
    if ok_full:
        return full
    if ok_compact:
        return compact
    lost_full = len(full[-1].meta["summarised"])
    lost_compact = len(compact[-1].meta["summarised"])
    return full if lost_full < lost_compact else compact


def layout(model: SegmentsModel, page: Page | None = None, refs: Refs | None = None,
           first_budget: Budget | None = None, site: str | None = None) -> list[Scene]:
    """Every AZ's scenes (the first `MAX_AZ_FIGURES` AZs) in model order, or only the
    AZ whose id is `site` (a report section per AZ)."""
    out: list[Scene] = []
    if site is not None:
        one = next((s for s in model.sites if s.id == site), None)
        return mark_edited(layout_site(one, page, refs, first_budget), model.edited) \
            if one is not None and (one.clusters) else []
    for i, s in enumerate(model.sites[:MAX_AZ_FIGURES]):
        if s.clusters:
            out.extend(layout_site(s, page, refs, first_budget if i == 0 else None))
    return mark_edited(out, model.edited)


__all__ = ["PG_SWITCH", "compact_text", "layout", "layout_site"]
