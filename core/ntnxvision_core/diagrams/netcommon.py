"""Shared by the per-AZ network and security figures (N1 segments, N2 virtual
networking, N3 microsegmentation): availability zones and the objects in each, sort
keys, value text, and the drawing pieces the three layouts share (AZ container, chips,
pills, the "+N more" card).

Every figure is drawn per availability zone: one per Prism Central (a combined run
attributes each object by `source_pc`), or one titled with the Prism Element on a
direct Prism Element run. AZs follow the conceptual overview's order.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ntnxvision_core.models import Environment

from . import text as tx
from . import tokens as K
from .build import CHIP_H, Row, box, chip, text_h
from .facts import _sites, conceptual_model
from .scene import Node, Scene, Style, q

#: Figures (availability zones) drawn per type; the paired table lists every AZ.
MAX_AZ_FIGURES = 8
#: Parts per AZ before the rest of the AZ is summarised in a "+N more" card.
MAX_PARTS = 2

MX = 0.5
TOP = 5.0
P = 7.5

_DIGITS = re.compile(r"(\d+)")


def natural(name: str | None) -> tuple[Any, ...]:
    """Natural sort key: vs2 before vs10, robo-9 before robo-10."""
    return tuple(int(p) if p.isdigit() else p.lower()
                 for p in _DIGITS.split(name or ""))


@dataclass(frozen=True)
class AzInfo:
    id: str  # "site:<host>"
    name: str
    kind: str  # "az" | "pe"
    #: Clusters that run this AZ's Prism Central.
    hosting: frozenset[str]


def azs(env: Environment) -> list[AzInfo]:
    """The availability zones of `env` in the conceptual overview's order."""
    is_pe = env.source_type == "prism_element"
    out = []
    default = _sites(env)[0].host
    for info in _sites(env):
        cfg = info.cfg
        hosting = {getattr(cfg, "hosting_cluster_uuid", None)} - {None}
        if not hosting:
            hosting = {v.hosting_cluster_uuid for v in env.pc_vms
                       if src_of(env, v, default) == info.host and v.hosting_cluster_uuid}
        if not hosting:
            hosting = {v.cluster_uuid for v in env.system_vms
                       if src_of(env, v, default) == info.host and v.cluster_uuid
                       and (v.vm_type or "").upper() == "PRISM_CENTRAL"}
        out.append(AzInfo(id=f"site:{info.host}", name=info.host,
                          kind="pe" if is_pe else "az",
                          hosting=frozenset() if is_pe else frozenset(
                              h for h in hosting if h)))
    order = {s.id: i for i, s in enumerate(conceptual_model(env).sites)}
    out.sort(key=lambda a: (order.get(a.id, len(order)), a.name.lower(), a.id))
    return out


def src_of(env: Environment, obj: Any, default: str) -> str:
    """The AZ (Prism Central host) an object belongs to."""
    if env.is_combined:
        return getattr(obj, "source_pc", None) or default
    return default


def default_host(env: Environment) -> str:
    return _sites(env)[0].host


# ─── value text ────────────────────────────────────────────────────────


def vlan_text(vlan: int | None) -> str:
    """"VLAN 100" (ids are identifiers: no grouping), "Untagged" for 0."""
    if vlan is None:
        return "VLAN N/A"
    return "Untagged" if vlan == 0 else f"VLAN {vlan}"


def short_gw(prefix: str | None, gateway: str | None) -> str | None:
    """"gw .1" when the gateway shares the prefix's first three octets."""
    if not gateway:
        return None
    if prefix and "." in gateway and gateway.rsplit(".", 1)[0] == \
            prefix.split("/")[0].rsplit(".", 1)[0]:
        return "gw ." + gateway.rsplit(".", 1)[1]
    return f"gw {gateway}"


def vms_text(n: int | None) -> str | None:
    if n is None:
        return None
    return f"{n:,} VM{'s' if n != 1 else ''}"


def ip_range(ips: list[str]) -> str:
    """"203.0.113.10 to .12" for addresses sharing three octets, else first to last."""
    if not ips:
        return ""
    if len(ips) == 1:
        return ips[0]
    a, b = ips[0], ips[-1]
    if "." in a and a.rsplit(".", 1)[0] == b.rsplit(".", 1)[0]:
        return f"{a} to .{b.rsplit('.', 1)[1]}"
    return f"{a} to {b}"


def ip_key(ip: str | None) -> tuple[Any, ...]:
    parts = (ip or "").split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        return (0, *(int(p) for p in parts))
    return (1, ip or "")


# ─── drawing ───────────────────────────────────────────────────────────


def az_container(sc: Scene, sid: str, title: str, sub: str, width: float, tag: str,
                 data: dict[str, str] | None = None) -> tuple[Node, float]:
    """The AZ container (its height is set by the caller) and the y content starts at."""
    cont_w = width - 2 * MX
    inner = cont_w - 2 * P
    head_top = TOP + CHIP_H / 2 + 4.5
    head = [Row(title, K.SIZE_SITE, True, K.INK, 2, 7.5),
            Row(sub, K.SIZE_SMALL, False, K.INK_3, 2, gap_before=0.5)]
    cont = box(sid, "container", MX, TOP, cont_w, head, K.SITE, pad_x=P,
               pad_y=head_top - TOP, h=10, align="left",
               data={"name": title, **(data or {})})
    sc.nodes.append(cont)
    sc.nodes.append(chip(f"{sid}:tag", tag, MX + 5.5, TOP, K.TAG, parent=sid))
    return cont, q(head_top + text_h(head, inner) + 8)


def tag_w(label: str) -> float:
    return q(tx.width(label, K.SIZE_CHIP, True) + 9)


def tag_at(sid: str, label: str, x: float, y: float, style: Style, color: str = K.WHITE,
           parent: str | None = None, h: float = CHIP_H) -> Node:
    """A chip placed at (x, y) (top left), not straddling an edge."""
    return box(sid, "tag", x, y, tag_w(label), [Row(label, K.SIZE_CHIP, True, color)],
               style, pad_x=4, pad_y=0, h=h, valign="middle", parent=parent)


def pill_w(label: str) -> float:
    return q(tx.width(label, K.SIZE_SMALL, True) + 6)


def pill_right(sid: str, label: str, right: float, y: float, style: Style, color: str,
               parent: str, h: float = 8.0) -> Node:
    """A small pill whose right edge is at `right`."""
    w = pill_w(label)
    return box(sid, "pill", right - w, y, w, [Row(label, K.SIZE_SMALL, True, color)], style,
               pad_x=3, pad_y=0, h=h, valign="middle", parent=parent)


def label_right(sid: str, text: str, right: float, y: float, h: float, parent: str,
                color: str = K.INK_2) -> Node:
    w = q(tx.width(text, K.SIZE_SMALL) + 1)
    return box(sid, "label", right - w, y, w, [Row(text, K.SIZE_SMALL, False, color)],
               K.LABEL, pad_x=0, pad_y=0, align="left", h=h, valign="middle", parent=parent)


def more_card(sid: str, x: float, y: float, w: float, title: str, lines: list[str],
              parent: str, h: float | None = None,
              data: dict[str, str] | None = None) -> Node:
    rows = [Row(title, K.SIZE_CARD_TITLE, True, K.INK_2, 2, tx.MIN_SIZE)]
    rows += [Row(t, K.SIZE_BODY, False, K.INK_3, 4, gap_before=1.0) for t in lines]
    return box(sid, "box", x, y, w, rows, K.MORE, pad_x=6, pad_y=6, align="left", h=h,
               parent=parent, data=data)


def user_chip(node: Node) -> Node:
    """The USER-ADDED chip on a user-added element's top edge."""
    return chip(f"{node.id}:user", "USER-ADDED", node.x + node.w - 6 - tag_w("USER-ADDED"),
                node.y, K.TAG_USER, parent=node.id)


__all__ = ["MAX_AZ_FIGURES", "MAX_PARTS", "MX", "P", "TOP", "AzInfo", "az_container",
           "azs", "default_host", "ip_key", "ip_range", "label_right", "more_card",
           "natural", "pill_right", "pill_w", "short_gw", "src_of", "tag_at", "tag_w",
           "user_chip", "vlan_text", "vms_text"]
