"""Microsegmentation Design layout (N3): one figure per availability zone (or per set
of AZs with identical policies).

Inside the AZ container, one lane per policy type present, chip "APPLICATION POLICIES
(6)": the application lane full width with three policy cards per row; isolation,
quarantine and other lanes side by side underneath. A card is styled by state (solid:
enforced, dashed: monitor, dotted grey: saved, not applied) with a state chip, and
draws its secured group as a pill: an application policy with an arrow in ("3 inbound
rules") and out, and its rule peers at the arrow tails when collected; an isolation
policy as two pills joined by a barred line; a quarantine policy as its pill.

Density (text never under 6 pt, page 672 x 440 pt): up to 4 application rows (12
cards), then "+N more application policies"; other lanes up to 4 cards (3 and "+N
more"); a figure too tall splits into 2 parts (application lane, then the others).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable

from .. import text as tx
from .. import tokens as K
from ..build import CHIP_H, Row, box, chip, edge, legend, measure, place_lines
from ..flow import (
    LANE_TITLES,
    STATE_WORDS,
    FlowModel,
    FPolicy,
    FSite,
    Lane,
    has_peers,
    peers_text,
    rules_text,
    site_line,
    site_title,
)
from ..netcommon import MAX_AZ_FIGURES, MX, P, az_container, more_card, tag_w, user_chip
from ..scene import Node, Scene, Style, q
from .conceptual import Page, Refs

GAP = 10.0
LANE_GAP = 12.0
COLS = 3
MAX_APP_ROWS = 4
MAX_OTHER = 4
CARD_H = 46.0
CARD_H_PEERS = 52.0

Budget = Callable[[float], float]

_STATE = {"ENFORCE": ("ENFORCED", K.STATE_ENFORCE, K.WHITE, K.POLICY),
          "MONITOR": ("MONITOR", K.STATE_MONITOR, K.INK_2, K.POLICY_MONITOR),
          "SAVE": ("SAVED, NOT APPLIED", K.STATE_MONITOR, K.INK_3, K.POLICY_SAVED)}


def _group_pill(sid: str, label: str, cx: float, cy: float, parent: str, max_w: float
                ) -> Node:
    w = q(min(max_w, tx.width(label, 6.6, True) + 12))
    return box(sid, "pill", q(cx - w / 2), q(cy - 6.5), w,
               [Row(label, 6.6, True, K.IRIS, 1, tx.MIN_SIZE)], K.GROUP, pad_x=5, pad_y=0,
               h=13, valign="middle", parent=parent)


def _label(sid: str, text: str, x: float, y: float, w: float, parent: str,
           color: str = K.INK_2, align: str = "center", lines: int = 1) -> Node:
    return box(sid, "label", x, y, w, [Row(text, K.SIZE_SMALL, False, color, lines)],
               K.LABEL, pad_x=0, pad_y=0, align=align, h=8 * lines, valign="top",
               parent=parent)


def _card(sc: Scene, p: FPolicy, x: float, y: float, w: float, h: float, parent: str
          ) -> None:
    cid = p.id
    if p.kind == "note":
        rows = [Row(p.name, K.SIZE_CARD_TITLE, True, K.INK, 2, tx.MIN_SIZE)]
        if p.note:
            rows.append(Row(p.note, K.SIZE_BODY, False, K.INK_2, 3, gap_before=0.5))
        sc.nodes.append(box(cid, "box", x, y, w, rows, K.NOTE, pad_x=6, pad_y=5,
                            align="left", h=h, parent=parent,
                            data={"name": p.name, "origin": "user"}))
        return
    user = p.kind == "user"
    label, chip_style, chip_ink, style = _STATE.get(p.state or "", _STATE["SAVE"])
    if user:
        label, chip_style, chip_ink, style = "USER-ADDED", K.TAG_USER, K.WHITE, K.USER
    cw = tag_w(label)
    mt, th = measure([Row(p.name, K.SIZE_CARD_TITLE, True, K.INK, 1, tx.MIN_SIZE)],
                     w - 12 - cw - 6)
    n = box(cid, "box", x, y, w, [], style, h=h, parent=parent,
            data=dict(sorted({"name": p.name, "policy_type": p.type,
                              "state": p.state or "", **p.data}.items())))
    n.lines = place_lines(mt, w, 5, "left", 6)
    if p.note:
        mn, _ = measure([Row(p.note, K.SIZE_SMALL, False, K.INK_3, 1)], w - 12)
        n.lines += place_lines(mn, w, h - 4 - tx.line_height(K.SIZE_SMALL), "left", 6)
    sc.nodes.append(n)
    sc.nodes.append(box(f"{cid}:state", "tag", x + w - 6 - cw, y + 4.5, cw,
                        [Row(label, K.SIZE_CHIP, True, chip_ink)], chip_style, pad_x=4,
                        pad_y=0, h=CHIP_H, valign="middle", parent=cid))
    mid = q(y + 5 + th + 14)
    cx = x + w / 2
    gs = p.groups or ["Secured group not reported"]
    if p.type == "ISOLATION" and len(gs) >= 2:
        pw = (w - 12 - 30) / 2
        a = _group_pill(f"{cid}:g0", gs[0], x + 6 + pw / 2, mid, cid, pw)
        b = _group_pill(f"{cid}:g1", ", ".join(gs[1:]), x + w - 6 - pw / 2, mid, cid, pw)
        sc.nodes += [a, b]
        sc.edges.append(edge(f"{cid}:iso", a.id, b.id, [(a.x + a.w + 3, mid),
                                                        (b.x - 3, mid)], K.ISOLATE,
                             arrow_end=False, data={"kind": "isolation"}))
        sc.edges.append(edge(f"{cid}:iso:bar", a.id, b.id, [(q(cx), mid - 5),
                                                            (q(cx), mid + 5)], K.ISOLATE,
                             arrow_end=False, data={"kind": "isolation"}))
        sc.nodes.append(_label(f"{cid}:foot", "Traffic between the two groups is blocked",
                               x + 6, mid + 9, w - 12, cid, K.INK_3))
        return
    g = _group_pill(f"{cid}:g", ", ".join(gs[:2]) + (f" +{len(gs) - 2}" if len(gs) > 2
                                                     else ""), cx, mid, cid, w * 0.46)
    sc.nodes.append(g)
    if p.type == "APPLICATION" or user:
        left, right = x + 6, x + w - 6
        sc.edges.append(edge(f"{cid}:in", "", g.id, [(left, mid), (g.x - 1, mid)],
                             K.RULE_IN, data={"kind": "inbound"}))
        sc.edges.append(edge(f"{cid}:out", g.id, "", [(g.x + g.w + 1, mid), (right, mid)],
                             K.RULE_IN, data={"kind": "outbound"}))
        lw, rw = g.x - left - 2, right - g.x - g.w - 2
        if user:
            sc.nodes.append(_label(f"{cid}:in:l", "planned", left, mid - 10.5, lw, cid))
        else:
            sc.nodes.append(_label(f"{cid}:in:l", rules_text(p.inbound, "inbound"), left,
                                   mid - 10.5, lw, cid))
            sc.nodes.append(_label(f"{cid}:out:l", rules_text(p.outbound, "outbound"),
                                   g.x + g.w + 2, mid - 10.5, rw, cid))
            if (t := peers_text(p.peers_in)) is not None:
                sc.nodes.append(_label(f"{cid}:in:p", f"from {t}", left, mid + 3, lw, cid,
                                       K.IRIS, lines=2))
            if (t := peers_text(p.peers_out)) is not None:
                sc.nodes.append(_label(f"{cid}:out:p", f"to {t}", g.x + g.w + 2, mid + 3,
                                       rw, cid, K.IRIS, lines=2))
    else:
        foot = ("Quarantine policy for VMs in this category" if p.type == "QUARANTINE"
                else f"{p.type.capitalize()} policy")
        sc.nodes.append(_label(f"{cid}:foot", foot, x + 6, mid + 9, w - 12, cid, K.INK_3))


def _lane(sc: Scene, lane: Lane, items: list[FPolicy], more: list[FPolicy], x: float,
          y: float, w: float, cols: int, card_h: float, parent: str, refs: Refs) -> float:
    rows = -(-(len(items) + (1 if more else 0)) // cols)
    cw = q((w - 12 - (cols - 1) * GAP) / cols)
    h = q(CHIP_H / 2 + 6 + rows * card_h + (rows - 1) * GAP + 6)
    total = len([p for p in lane.policies if p.kind == "policy"])
    sc.nodes.append(box(lane.id, "container", x, y, w, [], K.LANE, h=h, parent=parent,
                        data={"name": LANE_TITLES.get(lane.type, lane.type),
                              "policy_type": lane.type}))
    sc.nodes.append(chip(f"{lane.id}:tag", f"{LANE_TITLES.get(lane.type, lane.type)} "
                         f"({total:,})", x + 5, y, K.TAG_POD, parent=lane.id))
    slots: list[FPolicy | None] = [*items, *([None] if more else [])]
    for i, p in enumerate(slots):
        r, c = divmod(i, cols)
        px, py = q(x + 6 + c * (cw + GAP)), q(y + CHIP_H / 2 + 6 + r * (card_h + GAP))
        if p is None:
            st = Counter(STATE_WORDS.get(m.state or "", "state N/A") for m in more)
            what = ", ".join(f"{n:,} {s}" for s, n in sorted(st.items(),
                                                              key=lambda kv: -kv[1]))
            noun = lane.type.lower() if lane.type != "OTHER" else "other"
            sc.nodes.append(more_card(
                f"{lane.id}:more", px, py, cw,
                f"+{len(more):,} more {noun} polic{'y' if len(more) == 1 else 'ies'}",
                [f"{what}; listed in {refs.clusters_table}"], lane.id, card_h,
                data={"policies": str(len(more))}))
        else:
            _card(sc, p, px, py, cw, card_h, lane.id)
    return h


def _cut(lane: Lane, cap: int) -> tuple[list[FPolicy], list[FPolicy]]:
    items = lane.policies
    if len(items) <= cap:
        return items, []
    return items[:cap - 1], items[cap - 1:]


def _scene(site: FSite, lanes: list[Lane], part: tuple[int, int], app_rows: int,
           width: float, refs: Refs) -> Scene:
    title = site_title(site)
    sid = f"flow-{site.name}" + (f"-{part[0]}" if part[1] > 1 else "")
    sc = Scene(id=sid, type="flow", title="Microsegmentation design", caption="",
               width=width, height=0)
    cid = site.id if part[1] == 1 else f"{site.id}:{part[0]}"
    head = title + (f", part {part[0]} of {part[1]}" if part[1] > 1 else "")
    cont, y = az_container(sc, cid, head, site_line(site), width, "AZ",
                           data={"prism_central": ", ".join(site.applies_to or [site.name])})
    inner = width - 2 * MX - 2 * P
    peers = has_peers(site)
    card_h = CARD_H_PEERS if peers else CARD_H
    y = q(y + 2)
    drawn: list[str] = []
    summarised: list[str] = []
    app = [x for x in lanes if x.type == "APPLICATION"]
    rest = [x for x in lanes if x.type != "APPLICATION"]
    for lane in app:
        items, more = _cut(lane, app_rows * COLS)
        h = _lane(sc, lane, items, more, MX + P, y, inner, COLS, card_h, cont.id, refs)
        drawn += [p.id for p in items]
        summarised += [p.id for p in more]
        y = q(y + h + LANE_GAP)
    if rest:
        w = q((inner - (len(rest) - 1) * LANE_GAP) / len(rest))
        cols = max(1, min(COLS, int((w - 12 + GAP) // (190 + GAP))))
        hs = []
        for i, lane in enumerate(rest):
            items, more = _cut(lane, MAX_OTHER)
            hs.append(_lane(sc, lane, items, more, q(MX + P + i * (w + LANE_GAP)), y, w,
                            min(cols, max(1, len(items) + (1 if more else 0))), card_h,
                            cont.id, refs))
            drawn += [p.id for p in items]
            summarised += [p.id for p in more]
        y = q(y + max(hs) + LANE_GAP)
    for p in (p for x in lanes for p in x.policies if p.kind == "user" and p.id in drawn):
        sc.nodes.append(user_chip(sc.node(p.id)))
    cont.h = q(y - LANE_GAP + P - cont.y)
    y = cont.y + cont.h
    lh = legend(sc, _legend_items(lanes), MX, y + 9, width - 2 * MX)
    sc.height = q(y + 9 + lh + 1)
    where = head
    sc.caption = (f"Microsegmentation design, {where}: Flow security policies by type, "
                  "secured groups and enforcement state"
                  + (", with rule peers." if peers else
                     " (a posture view: rule peers were not collected)."))
    sc.part = part
    sc.meta = {"group": site.id, "group_name": title, "sites": [site.id], "drawn": drawn,
               "summarised": summarised}
    return sc


def _legend_items(lanes: list[Lane]) -> list[tuple[str, Style, str]]:
    pols = [p for x in lanes for p in x.policies]
    real = [p for p in pols if p.kind == "policy"]
    items: list[tuple[str, Style, str]] = [("box", K.GROUP, "Secured group (category)")]
    if any(p.type == "APPLICATION" for p in real):
        items.append(("line", K.RULE_IN, "Allowed traffic (rule count)"))
    states = {p.state for p in real}
    if "ENFORCE" in states:
        items.append(("box", K.POLICY, "Enforced policy"))
    if "MONITOR" in states:
        items.append(("box", K.POLICY_MONITOR, "Monitoring only"))
    if "SAVE" in states or None in states:
        items.append(("box", K.POLICY_SAVED, "Saved, not applied"))
    if any(p.type == "ISOLATION" for p in real):
        items.append(("line", K.ISOLATE, "Isolation"))
    if any(p.kind == "user" for p in pols):
        items.append(("box", K.USER, "Added on the Diagrams page"))
    return items


def layout_site(site: FSite, page: Page | None = None, refs: Refs | None = None,
                first_budget: Budget | None = None) -> list[Scene]:
    page = page or Page()
    refs = refs or Refs(clusters_table="the security policies table")
    width = page.width

    def limit(k: int) -> float:
        if k == 0 and first_budget is not None:
            return min(page.max_h, first_budget(width))
        return page.max_h

    one = _scene(site, site.lanes, (1, 1), MAX_APP_ROWS, width, refs)
    if one.height <= limit(0):
        return [one]
    app = [x for x in site.lanes if x.type == "APPLICATION"]
    rest = [x for x in site.lanes if x.type != "APPLICATION"]
    if app and rest:
        a = _scene(site, app, (1, 2), MAX_APP_ROWS, width, refs)
        b = _scene(site, rest, (2, 2), MAX_APP_ROWS, width, refs)
        if a.height <= limit(0) and b.height <= limit(1):
            return [a, b]
    for rows in range(MAX_APP_ROWS - 1, 0, -1):
        one = _scene(site, site.lanes, (1, 1), rows, width, refs)
        if one.height <= limit(0):
            break
    return [one]


def layout(model: FlowModel, page: Page | None = None, refs: Refs | None = None,
           first_budget: Budget | None = None) -> list[Scene]:
    out: list[Scene] = []
    for i, site in enumerate(model.sites[:MAX_AZ_FIGURES]):
        if site.lanes:
            out.extend(layout_site(site, page, refs, first_budget if i == 0 else None))
    return out


__all__ = ["layout", "layout_site"]
