"""What the Diagrams editor draws over a laid-out figure, besides connectors.

* **Icons.** Every element kind the diagram engine draws (an availability zone, a Prism
  Central, a cluster, a virtual switch, a VLAN segment, a security policy, ...) is in
  `CATALOG`, so an author can put any of them on any figure (payload ``placed``, op
  ``place``). A placed icon is drawn with the very tokens, typography and chips of the
  generated element it stands for (`draw`), at the position the author gave it, and it
  is an ordinary scene node: connectors route to and around it, every renderer draws it.
* **User-added elements look collected.** Elements the author added into a container
  of the model (palette ``add``) used to be drawn as dashed amber "USER-ADDED" boxes.
  `restyle` now draws each one like the generated element of its kind (`LOOKS`); the
  figure's summary bullet in the reports still says the figure was edited.
* **Text edits.** Any text line of a figure (zone headers, chips, legend labels, edge
  labels, ...) can be rewritten (payload ``text_edits``, op ``text``); `apply_texts`
  refits the line in its box (shrink to 6 pt, then ellipsise) or grows a chip.
* **Legend.** `legend_height` says how tall a figure is without its legend, and
  `strip_legend` removes it (the per-user "Legend" switch; exports follow it).

Everything is deterministic: same model and edits, same scene bytes.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any

from . import text as tx
from . import tokens as K
from .build import CHIP_H, Row, chip, chip_w, measure, place_lines
from .netcommon import tag_at
from .scene import Node, Scene, Style, TextLine, q

#: The chip every user-added element used to carry.
USER_CHIP = "USER-ADDED"
#: Largest canvas a placed icon may extend a figure to (pt).
MAX_EXTENT = 20000.0
#: Smallest and largest placed icon (pt).
MIN_W, MIN_H, MAX_W, MAX_H = 24.0, 10.0, 1600.0, 1600.0
#: Room kept around a placed icon at the figure's right and bottom edge.
MARGIN = 6.0


@dataclass(frozen=True)
class Look:
    """How a generated element of one kind is drawn."""

    style: Style
    #: Title and detail text: size, bold, colour.
    title: tuple[float, bool, str]
    detail: tuple[float, bool, str]
    #: Chip on the top edge (label, style), or None.
    chip: tuple[str, Style] | None = None
    #: Container (a zone, a POD): heading at the top, room below.
    container: bool = False
    #: Text alignment and padding.
    align: str = "left"
    pad_x: float = 5.0
    pad_y: float = 5.0


_CARD = (K.SIZE_CARD_TITLE, True, K.INK)
_BODY = (K.SIZE_BODY, False, K.INK_2)
_SMALL = (K.SIZE_SMALL, False, K.INK_2)

LOOKS: dict[str, Look] = {
    "az": Look(K.SITE, (K.SIZE_SITE, True, K.INK), _SMALL, ("AZ", K.TAG), container=True,
               pad_x=6.0, pad_y=CHIP_H / 2 + 4.5),
    "remote": Look(K.REMOTE, (K.SIZE_REMOTE_TITLE, True, K.INK), _SMALL),
    "pc": Look(K.PC, (K.SIZE_PC_TITLE, True, K.WHITE), (K.SIZE_SMALL, False, K.IRIS_TINT)),
    "cluster": Look(K.CLUSTER, _CARD, _BODY),
    "node": Look(K.NODES_BOX, (K.SIZE_BODY, True, K.INK), _SMALL),
    "pod": Look(K.POD, (K.SIZE_CARD_TITLE, True, K.INK_2), _SMALL, ("POD", K.TAG_POD),
                container=True, pad_x=6.0, pad_y=CHIP_H / 2 + 4.0),
    "vcenter": Look(K.VCENTER, (K.SIZE_BODY, True, K.INK), _SMALL),
    "container": Look(K.STORAGE, (K.SIZE_SMALL, True, K.LIVE), (K.SIZE_SMALL, False, K.LIVE),
                      pad_x=3.0, pad_y=2.5),
    "storage": Look(K.EXTERNAL, (K.SIZE_SMALL, True, K.WHITE), (K.SIZE_SMALL, False, K.WHITE),
                    pad_x=4.0, pad_y=3.0),
    "switch": Look(K.NET_SWITCH, (K.SIZE_BODY, True, K.INK), _SMALL, pad_y=3.5),
    "bond": Look(K.NET_BOND, (K.SIZE_SMALL, True, K.INK), _SMALL, pad_x=4.0, pad_y=3.0),
    "nic": Look(K.NET_NIC, (K.SIZE_SMALL, True, K.INK), _SMALL, pad_x=4.0, pad_y=3.0),
    "tor": Look(K.NET_NIC, (K.SIZE_BODY, True, K.INK), _SMALL, ("LLDP NEIGHBOUR",
                                                                 K.TAG_NEIGHBOUR)),
    "segment": Look(K.SEGMENT, (6.6, True, K.INK), (K.SIZE_SMALL, False, K.INK_3),
                    ("VLAN", K.VLAN_CHIP), pad_x=4.0, pad_y=3.0),
    "gateway": Look(K.EXTERNAL_NET, _CARD, _BODY),
    "vpc": Look(K.VPC, _CARD, _BODY, ("VPC", K.TAG)),
    "overlay": Look(K.OVERLAY, (K.SIZE_SMALL, True, K.INK), _SMALL, pad_x=4.0, pad_y=3.0),
    "external": Look(K.EXTERNAL_NET, _CARD, _BODY, ("EXTERNAL", K.TAG_NEIGHBOUR)),
    "policy": Look(K.POLICY, _CARD, _BODY, ("ENFORCED", K.STATE_ENFORCE)),
    "group": Look(K.GROUP, (K.SIZE_BODY, True, K.IRIS), _SMALL, pad_x=4.0, pad_y=3.0),
    "plans": Look(K.TAG_PLANS, (K.SIZE_CHIP, True, K.INK_2), (K.SIZE_CHIP, False, K.INK_2),
                  align="center", pad_x=3.5, pad_y=1.5),
    "note": Look(K.NOTE, (K.SIZE_BODY, False, K.INK_2), (K.SIZE_SMALL, False, K.INK_3)),
    "text": Look(K.LABEL, (K.SIZE_BODY, False, K.INK_2), (K.SIZE_SMALL, False, K.INK_3),
                 pad_x=0.0, pad_y=0.0),
}


@dataclass(frozen=True)
class Icon:
    id: str
    label: str
    group: str
    w: float
    h: float
    #: Default text: the label and the line under it.
    name: str
    sub: str = ""
    keywords: str = ""
    hint: str = ""

    @property
    def look(self) -> Look:
        return LOOKS[self.id]


#: Groups in picker order.
GROUPS = ("Infrastructure", "Storage", "Networking", "Security", "Data protection",
          "Annotations")

CATALOG: tuple[Icon, ...] = (
    Icon("az", "Availability zone", "Infrastructure", 220, 120, "Availability zone",
         "Not collected", "az site datacenter zone", "A zone frame with its AZ chip."),
    Icon("remote", "Remote availability zone", "Infrastructure", 130, 34,
         "Remote zone", "Outside this inventory", "remote az site dr target cloud",
         "A zone outside this inventory (dashed)."),
    Icon("pc", "Prism Central", "Infrastructure", 150, 30, "Prism Central",
         "Management plane", "pc prism central management", "The Prism Central block."),
    Icon("cluster", "Cluster", "Infrastructure", 120, 34, "Cluster", "Not collected",
         "cluster prism element pe aos", "A cluster card."),
    Icon("node", "Node", "Infrastructure", 90, 24, "Node", "Host", "host node server",
         "A host or node box."),
    Icon("pod", "POD", "Infrastructure", 180, 90, "POD", "", "pod group block",
         "A POD frame with its POD chip."),
    Icon("vcenter", "vCenter Server", "Infrastructure", 100, 24, "vCenter Server", "",
         "vcenter vmware esxi", "The vCenter box under an ESXi cluster."),
    Icon("container", "Storage container", "Storage", 110, 16, "Storage container", "",
         "storage container dsf datastore", "A DSF storage strip."),
    Icon("storage", "External storage", "Storage", 110, 18, "External storage", "",
         "external storage array san nas", "An external array (teal)."),
    Icon("switch", "Virtual switch", "Networking", 150, 18, "Virtual switch", "",
         "vswitch virtual switch vs0 bridge", "The virtual switch bar."),
    Icon("bond", "Uplink bond", "Networking", 80, 16, "Uplink bond", "",
         "bond uplink lacp", "An uplink bond."),
    Icon("nic", "Physical NIC", "Networking", 60, 16, "eth0", "", "nic port interface",
         "A physical NIC."),
    Icon("tor", "Physical switch", "Networking", 110, 26, "Top-of-rack switch", "",
         "tor switch lldp physical network", "A switch with its LLDP chip."),
    Icon("segment", "VLAN segment", "Networking", 150, 20, "Planned segment",
         "Not collected", "vlan segment subnet network", "A VLAN segment row."),
    Icon("gateway", "Gateway / firewall", "Networking", 120, 30, "Gateway / firewall",
         "Not reported by the Nutanix APIs", "gateway firewall router",
         "A gateway or firewall card."),
    Icon("vpc", "VPC", "Networking", 140, 34, "VPC", "Not collected",
         "vpc virtual private cloud flow networking", "A VPC card."),
    Icon("overlay", "Overlay subnet", "Networking", 120, 16, "Overlay subnet", "",
         "overlay subnet vpc", "An overlay subnet row."),
    Icon("external", "External network", "Networking", 130, 30, "External network",
         "Not reported by the Nutanix APIs", "external subnet upstream router transit",
         "An external network card."),
    Icon("policy", "Security policy", "Security", 140, 34, "Security policy",
         "Not collected", "security policy flow microsegmentation rule",
         "A security policy card."),
    Icon("group", "Category group", "Security", 100, 18, "Category", "",
         "category group app tier", "A category group."),
    Icon("plans", "Recovery plans", "Data protection", 80, 11, "RECOVERY PLANS", "",
         "recovery plan dr failover", "A recovery plans chip."),
    Icon("note", "Note", "Annotations", 120, 30, "Note", "", "note comment text",
         "A sticky note."),
    Icon("text", "Text", "Annotations", 100, 12, "Text", "", "text label caption title",
         "Free text."),
)

ICONS: dict[str, Icon] = {i.id: i for i in CATALOG}

#: Palette ids of in-layout additions (`EditSpec.palette`) -> the look they take.
PALETTE_LOOK = {"cluster": "cluster", "pc": "pc", "az": "az", "storage": "storage",
                "switch": "tor", "component": "cluster", "note": "note",
                "segment": "segment", "gateway": "gateway", "overlay": "overlay",
                "external": "external", "policy": "policy"}


def catalog_docs() -> list[dict[str, Any]]:
    """The icon catalog for the editor's picker: what each icon is called, its group,
    default size and text, and its swatch (the generated element's style)."""
    out = []
    for i in CATALOG:
        lk = i.look
        out.append({"id": i.id, "label": i.label, "group": i.group, "w": i.w, "h": i.h,
                    "name": i.name, "sub": i.sub, "keywords": i.keywords, "hint": i.hint,
                    "container": lk.container,
                    "style": {"fill": lk.style.fill, "stroke": lk.style.stroke,
                              "stroke_w": lk.style.stroke_w,
                              "dash": list(lk.style.dash) if lk.style.dash else None,
                              "radius": lk.style.radius},
                    "chip": ({"label": lk.chip[0], "fill": lk.chip[1].fill}
                             if lk.chip else None),
                    "ink": lk.title[2]})
    return out


# ─── drawing a placed icon ─────────────────────────────────────────────


def _rows(lk: Look, label: str, sub: str | None, max_lines: int = 2) -> list[Row]:
    size, bold, color = lk.title
    rows = [Row(label, size, bold, color, max_lines, tx.MIN_SIZE)] if label else []
    if sub:
        dsize, dbold, dcolor = lk.detail
        rows.append(Row(sub, dsize, dbold, dcolor, 2, tx.MIN_SIZE, gap_before=0.5))
    return rows


def draw(item: dict[str, Any]) -> list[Node]:
    """The nodes of one placed icon (the icon's box first, then its chip)."""
    icon = ICONS[str(item["icon"])]
    lk = icon.look
    ident = str(item["id"])
    x, y = q(float(item["x"])), q(float(item["y"]))
    w = q(float(item.get("w") or icon.w))
    h = float(item.get("h") or icon.h)
    label = str(item.get("label") if item.get("label") is not None else icon.name)
    sub = item.get("sub")
    rows = _rows(lk, label, sub if isinstance(sub, str) else None,
                 1 if lk.container else 2)
    data = {"origin": "user", "icon": icon.id, "kind": icon.label}
    kind = "container" if lk.container else ("label" if icon.id == "text" else
                                             "tag" if icon.id == "plans" else "box")
    valign = "top" if lk.container else "middle"
    # Room for a left chip inside the row (a VLAN chip sits before the segment name).
    pad_x = lk.pad_x
    lead = 0.0
    if icon.id == "segment":
        lead = q(tx.width("VLAN", K.SIZE_CHIP, True) + 9 + 4)
    measured, th = measure(rows, max(4.0, w - 2 * pad_x - lead))
    hh = q(max(h, th + 2 * lk.pad_y))
    node = Node(id=ident, kind=kind, x=x, y=y, w=w, h=hh, style=lk.style, lines=[],
                data=dict(sorted(data.items())))
    top = lk.pad_y if valign == "top" else (hh - th) / 2
    lines = place_lines(measured, w, top, lk.align, pad_x)
    if lead:
        lines = [replace(t, x=q(t.x + lead)) for t in lines]
    node.lines = lines
    out = [node]
    if lk.chip:
        clabel, cstyle = lk.chip
        if icon.id == "segment":
            out.append(tag_at(f"{ident}:tag", clabel, x + 4, q(y + (hh - 8.0) / 2), cstyle,
                              parent=ident, h=8.0))
        else:
            out.append(chip(f"{ident}:tag", clabel, x + (5.5 if lk.container else 5.0), y,
                            cstyle, parent=ident))
    return out


def place(scenes: list[Scene], placed: Iterable[dict[str, Any]]) -> None:
    """Draw the placed icons on the scenes they belong to (``scene``: the scene id),
    growing a scene to hold them."""
    by_scene: dict[str, list[dict[str, Any]]] = {}
    for item in placed:
        if isinstance(item, dict) and str(item.get("icon")) in ICONS:
            by_scene.setdefault(str(item.get("scene") or ""), []).append(item)
    if not by_scene:
        return
    for sc in scenes:
        items = by_scene.get(sc.id)
        if not items:
            continue
        frames: list[Node] = []
        rest: list[Node] = []
        for item in items:
            nodes = draw(item)
            (frames if nodes[0].kind == "container" else rest).extend(nodes)
            right = max(n.x + n.w for n in nodes)
            bottom = max(n.y + n.h for n in nodes)
            sc.width = q(min(MAX_EXTENT, max(sc.width, right + MARGIN)))
            sc.height = q(min(MAX_EXTENT, max(sc.height, bottom + MARGIN)))
        # A placed zone or POD paints under everything (first in its layer); boxes paint
        # over what was laid out.
        sc.nodes = frames + sc.nodes + rest
        _nest(sc, {str(i["id"]) for i in items})


def _contains(o: Node, n: Node) -> bool:
    return (o.x <= n.x + 0.01 and o.y <= n.y + 0.01 and n.x + n.w <= o.x + o.w + 0.01
            and n.y + n.h <= o.y + o.h + 0.01 and (o.w * o.h > n.w * n.h))


def _nest(sc: Scene, placed: set[str]) -> None:
    """Give each placed icon, and each top-level element a placed zone frames, the
    innermost zone around it as its parent, so connectors route out of it and the
    editor knows what it sits in."""
    frames = [n for n in sc.nodes if n.kind == "container"]
    for n in sc.nodes:
        if n.id in placed or (n.parent is None and frames and not n.id.startswith("legend:")):
            if n.id not in placed and not any(f.id in placed and _contains(f, n)
                                               for f in frames):
                continue
            around = [f for f in frames if f.id != n.id and _contains(f, n)]
            if around:
                n.parent = min(around, key=lambda f: f.w * f.h).id


# ─── user-added elements drawn like collected ones ─────────────────────


def _retint(lines: list[TextLine], title: str, detail: str) -> list[TextLine]:
    out = []
    for i, t in enumerate(lines):
        out.append(replace(t, color=title if (i == 0 or t.bold) else detail))
    return out


def restyle(scenes: list[Scene], looks: dict[str, str]) -> None:
    """Draw each user-added model element (id -> palette id in `looks`) like the
    generated element of its kind: the generated style and text colours, no amber
    dashes and no USER-ADDED chip (a zone gets its AZ chip back)."""
    if not looks:
        return
    for sc in scenes:
        ids = {n.id for n in sc.nodes}
        if not any(i in ids for i in looks):
            continue
        keep: list[Node] = []
        for n in sc.nodes:
            owner = n.id if n.id in looks else (n.parent if n.parent in looks else None)
            if owner is None:
                keep.append(n)
                continue
            look = LOOKS.get(PALETTE_LOOK.get(looks[owner], "cluster"), LOOKS["cluster"])
            if n.id == owner:
                if n.style in (K.USER, K.USER_SITE):
                    n.style = look.style
                    n.lines = _retint(n.lines, look.title[2], look.detail[2])
                keep.append(n)
                continue
            if n.style == K.TAG_USER:
                if n.text != USER_CHIP:  # a VLAN chip of a planned segment
                    n.style = K.VLAN_CHIP
                elif look.chip and look.container:
                    keep.append(_relabel_chip(n, look.chip[0], look.chip[1]))
                    continue
                else:
                    continue  # the USER-ADDED chip: gone
            keep.append(n)
        sc.nodes = keep


def _relabel_chip(n: Node, label: str, style: Style) -> Node:
    w = chip_w(label)
    t = n.lines[0] if n.lines else None
    lines = [TextLine(text=label, size=K.SIZE_CHIP, bold=True, color=K.WHITE,
                      x=q((w - tx.natural(label, K.SIZE_CHIP, True)) / 2) if t is None else 3.5,
                      baseline=t.baseline if t else q(CHIP_H / 2 + 2),
                      width=tx.natural(label, K.SIZE_CHIP, True))]
    return Node(id=n.id, kind=n.kind, x=n.x, y=n.y, w=w, h=n.h, style=style, lines=lines,
                parent=n.parent, data=dict(n.data))


# ─── text edits ────────────────────────────────────────────────────────

#: Node kinds whose box follows its text (a chip grows); others keep their box and the
#: text is fitted into it.
_GROW = frozenset({"tag", "pill", "label"})


def edge_key(edge_id: str, index: int) -> str:
    """The `text_edits` key of an edge's label."""
    return f"{edge_id}#{index}"


def _refit(t: TextLine, value: str, room: float) -> TextLine:
    size, lines = tx.fit(value, t.size, t.bold, max(room, 1.0), 1, min(t.size, tx.MIN_SIZE))
    text = lines[0] if lines else ""
    return replace(t, text=text, size=size, width=tx.natural(text, size, t.bold))


def _retext_node(n: Node, i: int, value: str) -> None:
    t = n.lines[i]
    if t.text == value:
        return
    centred = abs(t.x - (n.w - t.width) / 2) < 0.75 and t.x > 0.6
    right_pad = n.w - (t.x + t.width)
    if n.kind in _GROW:
        new = replace(t, text=value, width=tx.natural(value, t.size, t.bold))
        grow = q(new.width - t.width)
        if n.kind == "pill" and right_pad <= t.x + 0.6:  # a right-anchored pill
            n.x = q(n.x - grow)
        n.w = q(max(4.0, n.w + grow))
        for j, o in enumerate(n.lines):
            if j != i and centred:
                n.lines[j] = replace(o, x=q((n.w - o.width) / 2))
        n.lines[i] = replace(new, x=q((n.w - new.width) / 2) if centred else t.x)
        return
    pad = max(t.x, 0.0)
    room = n.w - 2 * pad if centred else n.w - t.x - max(min(pad, right_pad), 2.0)
    new = _refit(t, value, room)
    n.lines[i] = replace(new, x=q((n.w - new.width) / 2) if centred else t.x)


def _reflow_legend(sc: Scene, label: Node, grow: float) -> None:
    """Shift the legend items after a relabelled one along its row."""
    lo, hi = label.y, label.y + label.h
    for n in sc.nodes:
        if n is not label and n.id.startswith("legend:") and n.x > label.x and \
                n.y < hi and n.y + n.h > lo:
            n.x = q(n.x + grow)
    for e in sc.edges:
        if e.id.startswith("legend:") and e.points and e.points[0][0] > label.x and \
                lo <= e.points[0][1] <= hi:
            e.points = [(q(x + grow), y) for x, y in e.points]
    right = max((n.x + n.w for n in sc.nodes), default=0.0)
    sc.width = q(max(sc.width, right + 0.5))


def apply_texts(scenes: list[Scene], texts: dict[str, dict[str, Any]]) -> None:
    """Rewrite the text lines named in `texts` ({node id or edge label key: {line
    index: text}}); a key not drawn in a scene is skipped there."""
    if not texts:
        return
    for sc in scenes:
        for n in sc.nodes:
            edits = texts.get(n.id)
            if not edits:
                continue
            before = n.w
            for key, value in sorted(edits.items()):
                i = int(key)
                if isinstance(value, str) and 0 <= i < len(n.lines):
                    _retext_node(n, i, value)
            if n.id.startswith("legend:") and n.w != before:
                _reflow_legend(sc, n, q(n.w - before))
        for e in sc.edges:
            for li, lab in enumerate(e.labels):
                edits = texts.get(edge_key(e.id, li))
                if not edits:
                    continue
                cx = lab.x + lab.w / 2
                for key, value in sorted(edits.items()):
                    i = int(key)
                    if isinstance(value, str) and 0 <= i < len(lab.lines):
                        t = lab.lines[i]
                        lab.lines[i] = replace(t, text=value,
                                               width=tx.natural(value, t.size, t.bold))
                widest = max((t.width for t in lab.lines), default=0.0)
                lab.w = q(widest * tx.SAFETY + 4)
                lab.x = q(cx - lab.w / 2)
                lab.lines = [replace(t, x=q((lab.w - t.width) / 2)) for t in lab.lines]


# ─── the legend ────────────────────────────────────────────────────────


def _is_legend(ident: str) -> bool:
    return ident.startswith("legend:")


def _content_bottom(sc: Scene, legend: bool) -> float:
    ys = [n.y + n.h for n in sc.nodes if _is_legend(n.id) == legend]
    for e in sc.edges:
        if _is_legend(e.id) == legend:
            ys += [p[1] for p in e.points]
            ys += [lb.y + lb.h for lb in e.labels]
    return max(ys, default=0.0)


def legend_height(sc: Scene) -> float | None:
    """The figure's height without its legend (None: it has no legend). Only a legend
    under everything else is cut away; one beside the content leaves the height."""
    tops = [n.y for n in sc.nodes if _is_legend(n.id)] + \
        [p[1] for e in sc.edges if _is_legend(e.id) for p in e.points]
    if not tops:
        return None
    top = min(tops)
    bottom = _content_bottom(sc, False)
    if top < bottom - 0.5:
        return sc.height
    return q(min(sc.height, bottom + MARGIN))


def strip_legend(sc: Scene) -> Scene:
    """`sc` without its legend (in place; returns it)."""
    h = legend_height(sc)
    if h is None:
        return sc
    sc.nodes = [n for n in sc.nodes if not _is_legend(n.id)]
    sc.edges = [e for e in sc.edges if not _is_legend(e.id)]
    sc.height = h
    return sc


def mark_legend(scenes: list[Scene]) -> None:
    """Record each scene's height without its legend (``meta.legend_cut``), for the
    editor's Legend switch."""
    for sc in scenes:
        h = legend_height(sc)
        if h is not None:
            sc.meta = {**sc.meta, "legend_cut": h}


# ─── payload helpers ───────────────────────────────────────────────────


def user_looks(doc: dict[str, Any]) -> dict[str, str]:
    """User-added model elements of a payload -> the palette id they were added from
    (``data.icon``, set when the add is replayed)."""
    out: dict[str, str] = {}

    def visit(v: Any, top: bool) -> None:
        if isinstance(v, dict):
            ident = v.get("id")
            data = v.get("data")
            if isinstance(ident, str) and ident.startswith("user:") and \
                    isinstance(data, dict) and isinstance(data.get("icon"), str):
                out[ident] = str(data["icon"])
            for k, x in v.items():
                if top and k in ("placed", "text_edits", "connectors", "edge_edits"):
                    continue
                visit(x, False)
        elif isinstance(v, list):
            for x in v:
                visit(x, False)

    visit(doc, True)
    return out


__all__ = ["CATALOG", "GROUPS", "ICONS", "LOOKS", "PALETTE_LOOK", "USER_CHIP",
           "apply_texts", "catalog_docs", "draw", "edge_key", "legend_height",
           "mark_legend", "place", "restyle", "strip_legend", "user_looks"]
