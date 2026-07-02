"""Topology diagram renderer for the Word / PDF reports.

Draws flat, modern node-and-connector diagrams (rounded cards, elbow connectors,
status pills) as high-resolution PNGs with Pillow — the visual language of
contemporary infrastructure dashboards. A single raster renderer embeds
identically in python-docx and fpdf2, and Pillow is already a transitive
dependency of fpdf2, so it adds no packaging weight.

Geometry is computed in logical (1x) pixels and drawn at ``S``x supersampling so
the output stays crisp in print. Every public builder returns ``None`` when the
environment lacks the data to draw, letting exporters skip the figure without
special-casing.
"""

from __future__ import annotations

import io
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from ntnxvision_core.models import Cluster, Environment

GIB = 1024**3
#: Supersampling factor for crisp print output.
S = 3
#: Logical pixels per inch when embedding (keeps body text ~7pt on the page).
BASE_DPI = 120

# ─── palette ───────────────────────────────────────────────────────────

_INK = (31, 36, 44)
_SUB = (122, 130, 142)
_NAVY = (24, 58, 92)
_ACCENT = (0, 122, 204)
_FRAME = (226, 230, 236)
_CARD_FILL = (246, 247, 249)
_CARD_BORDER = (203, 208, 216)
_DIM_FILL = (250, 250, 251)
_DIM_BORDER = (228, 231, 236)
_OK_FILL = (240, 253, 244)
_OK_BORDER = (134, 239, 172)
_OK_TEXT = (21, 128, 61)
_PILL_FILL = (219, 234, 254)
_PILL_TEXT = (29, 78, 216)
_GREY_PILL_FILL = (234, 237, 241)
_EDGE = (176, 183, 193)

# ─── geometry (logical px) ─────────────────────────────────────────────

_FRAME_PAD = 24
_COL_GAP = 88
_V_GAP = 12
_ROOT_GAP = 28
_PAD_X = 13
_PAD_Y = 9
_TITLE_H = 19
_LINE_H = 15
_RADIUS = 9
_MIN_NODE_H = 42

# (path, collection-index); checked in order, first loadable + style match wins.
_FONT_CANDIDATES: dict[bool, list[tuple[str, int]]] = {
    False: [
        ("/System/Library/Fonts/Helvetica.ttc", 0),
        ("/System/Library/Fonts/Supplemental/Arial.ttf", 0),
        ("C:/Windows/Fonts/segoeui.ttf", 0),
        ("C:/Windows/Fonts/arial.ttf", 0),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
    ],
    True: [
        ("/System/Library/Fonts/Helvetica.ttc", 1),
        ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 0),
        ("C:/Windows/Fonts/segoeuib.ttf", 0),
        ("C:/Windows/Fonts/arialbd.ttf", 0),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0),
    ],
}


@lru_cache(maxsize=32)
def _font(size: float, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    px = round(size * S)
    for path, index in _FONT_CANDIDATES[bold]:
        try:
            f = ImageFont.truetype(path, px, index=index)
        except OSError:
            continue
        style = (f.getname() or ("", ""))[1] or ""
        if bold and "bold" not in style.lower():
            continue
        return f
    try:
        return ImageFont.load_default(px)  # Pillow >= 10.1
    except TypeError:  # pragma: no cover - ancient Pillow fallback
        return ImageFont.load_default()


# ─── model ─────────────────────────────────────────────────────────────


@dataclass
class Node:
    """One card in the diagram; children connect via elbow connectors."""

    title: str
    lines: list[str] = field(default_factory=list)
    tag: str | None = None
    tag_style: str = "blue"  # blue | ok | grey
    style: str = "card"  # card | root | dim
    edge_label: str | None = None  # small label on the connector from the parent
    children: list[Node] = field(default_factory=list)


@dataclass
class Diagram:
    """Rendered PNG plus logical (1x) dimensions for layout on the page."""

    png: bytes
    width: int
    height: int

    @property
    def width_in(self) -> float:
        return self.width / BASE_DPI

    @property
    def height_in(self) -> float:
        return self.height / BASE_DPI


# ─── renderer ──────────────────────────────────────────────────────────


def _node_h(n: Node) -> float:
    return max(_MIN_NODE_H, 2 * _PAD_Y + _TITLE_H + len(n.lines) * _LINE_H)


def _subtree_h(n: Node) -> float:
    if not n.children:
        return _node_h(n)
    kids = sum(_subtree_h(c) for c in n.children) + _V_GAP * (len(n.children) - 1)
    return max(_node_h(n), kids)


def _depth(nodes: list[Node]) -> int:
    return 1 + max((_depth(n.children) for n in nodes if n.children), default=0)


def _fit(draw: ImageDraw.ImageDraw, text: str, font: Any, max_w: float) -> str:
    """Truncate `text` with an ellipsis to fit `max_w` scaled pixels."""
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


def _render(roots: list[Node], col_w: list[float], col_gap: float = _COL_GAP) -> Diagram:
    levels = _depth(roots)
    widths = (col_w + [col_w[-1]] * levels)[:levels]
    total_h = sum(_subtree_h(r) for r in roots) + _ROOT_GAP * (len(roots) - 1)
    w = 2 * _FRAME_PAD + sum(widths) + col_gap * (levels - 1)
    h = 2 * _FRAME_PAD + total_h

    img = Image.new("RGB", (round(w * S), round(h * S)), "white")
    draw = ImageDraw.Draw(img)
    # Subtle outer card so the figure reads as one component on the page.
    draw.rounded_rectangle(
        [S, S, round(w * S) - S, round(h * S) - S],
        radius=round(12 * S), outline=_FRAME, width=S,
    )

    def place(n: Node, level: int, slot_y: float) -> tuple[float, float]:
        sub_h = _subtree_h(n)
        nh = _node_h(n)
        x = _FRAME_PAD + sum(widths[:level]) + col_gap * level
        y = slot_y + (sub_h - nh) / 2
        _draw_node(draw, n, x, y, widths[level], nh)
        cy = slot_y
        for c in n.children:
            child_x, child_cy = place(c, level + 1, cy)
            _draw_elbow(draw, x + widths[level], y + nh / 2, child_x, child_cy,
                        c.edge_label)
            cy += _subtree_h(c) + _V_GAP
        return x, y + nh / 2

    ry: float = _FRAME_PAD
    for r in roots:
        place(r, 0, ry)
        ry += _subtree_h(r) + _ROOT_GAP

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Diagram(png=buf.getvalue(), width=round(w), height=round(h))


def _draw_node(draw: ImageDraw.ImageDraw, n: Node, x: float, y: float,
               w: float, h: float) -> None:
    fill, border = {
        "root": (_CARD_FILL, _CARD_BORDER),
        "dim": (_DIM_FILL, _DIM_BORDER),
    }.get(n.style, (_CARD_FILL, _CARD_BORDER))
    title_col = _NAVY if n.style == "root" else (_SUB if n.style == "dim" else _INK)

    box = [round(x * S), round(y * S), round((x + w) * S), round((y + h) * S)]
    draw.rounded_rectangle(box, radius=round(_RADIUS * S), fill=fill,
                           outline=border, width=S)
    tx = x + _PAD_X
    if n.style == "root":
        # Brand accent stripe down the left edge.
        draw.rounded_rectangle(
            [round((x + 5) * S), round((y + 7) * S),
             round((x + 8.5) * S), round((y + h - 7) * S)],
            radius=round(1.75 * S), fill=_ACCENT,
        )
        tx += 6

    f_title = _font(12.5, bold=True)
    f_line = _font(10.5)
    title_cy = round((y + _PAD_Y + _TITLE_H / 2) * S)
    max_title_w = (x + w - _PAD_X - tx) * S

    if n.tag:
        f_tag = _font(9)
        tag_w = draw.textlength(n.tag, font=f_tag) + 12 * S
        tag_h = 15 * S
        tx2 = round((x + w - _PAD_X) * S - tag_w)
        pill_fill, pill_text = {
            "ok": (_OK_FILL, _OK_TEXT),
            "grey": (_GREY_PILL_FILL, _SUB),
        }.get(n.tag_style, (_PILL_FILL, _PILL_TEXT))
        draw.rounded_rectangle(
            [tx2, title_cy - tag_h // 2, tx2 + round(tag_w), title_cy + tag_h // 2],
            radius=tag_h // 2, fill=pill_fill,
        )
        if n.tag_style == "ok":
            draw.rounded_rectangle(
                [tx2, title_cy - tag_h // 2, tx2 + round(tag_w), title_cy + tag_h // 2],
                radius=tag_h // 2, outline=_OK_BORDER, width=max(1, S // 2),
            )
        draw.text((tx2 + round(tag_w) // 2, title_cy), n.tag, font=f_tag,
                  fill=pill_text, anchor="mm")
        max_title_w -= tag_w + 8 * S

    draw.text((round(tx * S), title_cy),
              _fit(draw, n.title, f_title, max_title_w),
              font=f_title, fill=title_col, anchor="lm")

    ly = y + _PAD_Y + _TITLE_H + _LINE_H / 2
    for line in n.lines:
        draw.text((round(tx * S), round(ly * S)),
                  _fit(draw, line, f_line, (x + w - _PAD_X - tx) * S),
                  font=f_line, fill=_SUB, anchor="lm")
        ly += _LINE_H


def _draw_elbow(draw: ImageDraw.ImageDraw, x1: float, y1: float, x2: float,
                y2: float, label: str | None) -> None:
    """Parent-right → short stub → vertical trunk → horizontal into the child."""
    trunk = x1 + 24
    lw = max(1, round(1.2 * S))
    pts = [(x1, y1), (trunk, y1), (trunk, y2), (x2, y2)]
    for (ax, ay), (bx, by) in zip(pts, pts[1:], strict=False):
        draw.line([round(ax * S), round(ay * S), round(bx * S), round(by * S)],
                  fill=_EDGE, width=lw)
    if label:
        f = _font(8.5)
        cx = (trunk + x2) / 2 * S
        cy = y2 * S
        tw = draw.textlength(label, font=f)
        pw, ph = tw + 12 * S, 14 * S
        draw.rounded_rectangle(
            [round(cx - pw / 2), round(cy - ph / 2),
             round(cx + pw / 2), round(cy + ph / 2)],
            radius=round(ph / 2), fill=_GREY_PILL_FILL,
        )
        draw.text((round(cx), round(cy)), label, font=f, fill=_SUB, anchor="mm")


# ─── shared formatting ─────────────────────────────────────────────────


def _cap(nodes: list[Node], limit: int, noun: str) -> list[Node]:
    if len(nodes) <= limit:
        return nodes
    extra = len(nodes) - (limit - 1)
    return nodes[: limit - 1] + [Node(f"+ {extra} more {noun}", style="dim")]


def _gib(value: int | None) -> int | None:
    return round(value / GIB) if value else None


def _rpo(seconds: int | None) -> str:
    if seconds is None:
        return "n/a"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _az(name: str | None) -> str | None:
    """Availability-zone names can be URLs; keep them short and readable."""
    if not name:
        return None
    return name.removeprefix("https://").removeprefix("http://").rstrip("/")


# ─── public builders ───────────────────────────────────────────────────


def management_topology(env: Environment) -> Diagram | None:
    """Prism Central (or Prism Element) and the clusters it manages."""
    if not env.clusters:
        return None
    is_pe = env.source_type == "prism_element"
    lines = [line for line in (
        env.prism_central.host,
        f"version {env.prism_central.version}" if env.prism_central.version else None,
    ) if line]
    if not is_pe and env.pc_config.node_count:
        deploy = "Scale-out" if env.pc_config.is_scale_out else "Single-node"
        lines.append(f"{deploy} · {env.pc_config.node_count} PC node(s)")
    root = Node("Prism Element" if is_pe else "Prism Central", lines, style="root")

    vm_counts = Counter(v.cluster_name for v in env.vms)
    host_counts = Counter(h.cluster_name for h in env.hosts)
    for c in env.clusters:
        hyp = "/".join(c.hypervisor_types) if c.hypervisor_types else "unknown"
        nodes = c.num_nodes or host_counts.get(c.name) or "?"
        root.children.append(Node(
            c.name or c.uuid,
            [f"AOS {c.aos_version or 'unknown'} · {hyp}",
             f"{nodes} node(s) · {vm_counts.get(c.name, 0)} VM(s)"],
            tag=f"RF {c.redundancy_factor}" if c.redundancy_factor else None,
        ))
    root.children = _cap(root.children, 10, "cluster(s)")
    return _render([root], col_w=[280, 340])


def cluster_compute(env: Environment, cluster: Cluster) -> Diagram | None:
    """One cluster and its physical nodes with CPU / memory / VM density."""
    hosts = [h for h in env.hosts
             if h.cluster_uuid == cluster.uuid or
             (h.cluster_name and h.cluster_name == cluster.name)]
    if not hosts:
        return None
    root = Node(
        cluster.name or cluster.uuid,
        [f"AOS {cluster.aos_version or 'unknown'}",
         f"{cluster.num_nodes or len(hosts)} node(s)"
         + (f" · RF {cluster.redundancy_factor}" if cluster.redundancy_factor else "")],
        style="root",
    )
    for h in hosts:
        specs = " · ".join(p for p in (
            f"{h.num_cpu_cores} cores" if h.num_cpu_cores else None,
            f"{_gib(h.memory_bytes)} GiB" if h.memory_bytes else None,
            f"{h.num_vms} VM(s)" if h.num_vms is not None else None,
        ) if p)
        lines = [line for line in (h.cpu_model, specs, h.block_model) if line]
        in_maint = (h.maintenance_state is not None
                    and h.maintenance_state.lower() not in ("", "normal"))
        root.children.append(Node(
            h.name or h.uuid, lines,
            tag="maintenance" if in_maint else ("GPU" if h.gpu_list else "up"),
            tag_style="grey" if in_maint else ("blue" if h.gpu_list else "ok"),
            style="dim" if in_maint else "card",
        ))
    root.children = _cap(root.children, 10, "node(s)")
    return _render([root], col_w=[270, 390])


def network_topology(env: Environment, cluster: Cluster) -> Diagram | None:
    """Cluster → virtual switches → VLAN subnets (the cabling-diagram view)."""
    subs = [s for s in env.subnets
            if (s.cluster_uuid == cluster.uuid or
                (s.cluster_name and s.cluster_name == cluster.name))
            and (s.subnet_type or "VLAN").upper() != "OVERLAY"]
    if not subs:
        return None
    groups: dict[str, list] = {}
    for s in subs:
        groups.setdefault(s.virtual_switch or "default", []).append(s)

    root = Node(cluster.name or cluster.uuid,
                [f"{len(subs)} VLAN subnet(s)"], style="root")
    for vs_name in sorted(groups):
        vs = Node(vs_name, ["virtual switch"])
        for s in sorted(groups[vs_name], key=lambda s: (s.vlan_id or 0, s.name or "")):
            detail = s.ip_prefix or "no managed IP range"
            if s.dhcp_enabled:
                detail += " · DHCP"
            vs.children.append(Node(
                s.name or s.uuid, [detail],
                tag=f"VLAN {s.vlan_id}" if s.vlan_id is not None else (s.subnet_type or None),
            ))
        vs.children = _cap(vs.children, 8, "subnet(s)")
        root.children.append(vs)
    root.children = _cap(root.children, 6, "switch(es)")
    return _render([root], col_w=[230, 210, 330], col_gap=72)


def vpc_topology(env: Environment) -> Diagram | None:
    """Flow Virtual Networking: VPCs with their overlay and external subnets."""
    if not env.vpcs:
        return None
    by_vpc: dict[str, list] = {}
    for s in env.subnets:
        if s.vpc_name:
            by_vpc.setdefault(s.vpc_name, []).append(s)

    roots = []
    for v in env.vpcs[:6]:
        lines = []
        if v.route_count is not None or v.routing_policy_count is not None:
            lines.append(f"{v.route_count or 0} route(s) · "
                         f"{v.routing_policy_count or 0} routing policy(ies)")
        root = Node(v.name or v.uuid, lines, style="root")
        overlay = by_vpc.get(v.name or "", [])
        names_seen = {s.name for s in overlay}
        for s in overlay:
            root.children.append(Node(s.name or s.uuid,
                                      [s.ip_prefix or ""], tag="overlay"))
        for name in v.subnet_names:
            if name not in names_seen:
                root.children.append(Node(name, tag="overlay"))
        for ext in v.external_subnets:
            root.children.append(Node(ext, ["external connectivity"],
                                      tag="external", tag_style="grey"))
        if not root.children:
            root.children.append(Node("No subnets attached", style="dim"))
        root.children = _cap(root.children, 8, "subnet(s)")
        roots.append(root)
    return _render(roots, col_w=[280, 340])


def replication_topology(env: Environment) -> Diagram | None:
    """DR flows: source AZ → target AZ per protection policy, RPO on the edge."""
    if not env.protection_policies:
        return None
    groups: dict[str, list] = {}
    for p in env.protection_policies:
        groups.setdefault(_az(p.source_az) or "Local AZ", []).append(p)

    roots = []
    for src in sorted(groups):
        root = Node(src, ["source availability zone"], style="root")
        for p in groups[src]:
            target = _az(p.target_az)
            label = p.replication_type or "ASYNC"
            if p.rpo_seconds is not None:
                label += f" · RPO {_rpo(p.rpo_seconds)}"
            root.children.append(Node(
                target or "No remote target",
                [p.name or ""],
                edge_label=label,
                style="card" if target else "dim",
            ))
        root.children = _cap(root.children, 8, "policy(ies)")
        roots.append(root)
    return _render(roots, col_w=[280, 320], col_gap=150)
