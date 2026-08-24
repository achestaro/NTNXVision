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

from PIL import Image, ImageDraw, ImageFilter, ImageFont

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

# Stat tiles, donuts, and bars (executive dashboard + inventory charts).
_TRACK = (238, 242, 246)
_VM_ON = (34, 153, 79)
_VM_OFF = (148, 158, 171)
_VM_OTHER = (194, 121, 10)
#: Fixed hue order for per-cluster identity — never reassigned per render, so
#: the same cluster gets the same color across the storage/VM charts in a report.
_CLUSTER_PALETTE = [
    (0, 122, 204),    # blue (matches _ACCENT)
    (235, 104, 52),   # orange
    (27, 175, 122),   # aqua
    (237, 161, 0),    # yellow
    (232, 123, 164),  # magenta
    (0, 131, 0),      # green
    (74, 58, 167),    # violet
    (227, 73, 72),    # red
]

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

# Chart geometry (stat tiles, donuts, bars).
_TILE_W = 132
_TILE_H = 88
_TILE_GAP = 14
_DONUT_R = 74
_DONUT_THICK = 24
_DONUT_GAP_DEG = 1.6
_BAR_ROW_H = 34
_BAR_ROW_GAP = 12
_BAR_LABEL_W = 150
_BAR_VALS_W = 190
_BAR_TRACK_W = 400

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


def _size_label(value_bytes: int | None) -> str:
    """Bytes as a human GiB/TiB label, switching units above 1 TiB."""
    if not value_bytes:
        return "0 GiB"
    tib = value_bytes / (GIB * 1024)
    if tib >= 1:
        return f"{tib:.1f} TiB"
    return f"{round(value_bytes / GIB)} GiB"


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


# ─── chart primitives (stat tiles, donuts, bars) ────────────────────────


def _new_canvas(w: float, h: float) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (round(w * S), round(h * S)), "white")
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        [S, S, round(w * S) - S, round(h * S) - S],
        radius=round(12 * S), outline=_FRAME, width=S,
    )
    return img, draw


def _finish(img: Image.Image, w: float, h: float) -> Diagram:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Diagram(png=buf.getvalue(), width=round(w), height=round(h))


def _draw_tile(draw: ImageDraw.ImageDraw, x: float, y: float, w: float, h: float,
               value: str, label: str, fill: tuple[int, int, int]) -> None:
    box = [round(x * S), round(y * S), round((x + w) * S), round((y + h) * S)]
    draw.rounded_rectangle(box, radius=round(8 * S), fill=fill)
    f_val = _font(18, bold=True)
    f_lbl = _font(8.5)
    cx, cy = x + w / 2, y + h / 2 - 8
    max_w = (w - 10) * S
    draw.text((round(cx * S), round(cy * S)), _fit(draw, value, f_val, max_w),
              font=f_val, fill="white", anchor="mm")
    label_col = (191, 224, 247) if fill == _NAVY else (216, 238, 250)
    draw.text((round(cx * S), round((y + h - 18) * S)),
              _fit(draw, label.upper(), f_lbl, max_w),
              font=f_lbl, fill=label_col, anchor="mm")


def _legend_item(draw: ImageDraw.ImageDraw, x: float, y: float, label: str,
                  color: tuple[int, int, int], font: Any) -> float:
    """Draw one legend dot + label at (x, y); return the logical-px width consumed."""
    draw.ellipse([round(x * S), round((y - 4) * S), round((x + 8) * S), round((y + 4) * S)],
                 fill=color)
    draw.text((round((x + 16) * S), round(y * S)), label, font=font, fill=_INK, anchor="lm")
    return 16 + draw.textlength(label, font=font) / S


def _draw_donut(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float, thickness: float,
                 segments: list[tuple[int, tuple[int, int, int]]],
                 center_value: str | None = None, center_label: str | None = None) -> None:
    """Ring chart starting at 12 o'clock, clockwise, with a thin gap between segments."""
    total = sum(v for v, _ in segments)
    if total <= 0:
        return
    start = -90.0
    for value, color in segments:
        span = value / total * 360.0
        gap = min(_DONUT_GAP_DEG, span * 0.15) if len(segments) > 1 else 0
        a0, a1 = start + gap / 2, start + span - gap / 2
        if a1 > a0:
            bbox = [round((cx - r) * S), round((cy - r) * S),
                    round((cx + r) * S), round((cy + r) * S)]
            draw.arc(bbox, a0, a1, fill=color, width=round(thickness * S))
        start += span
    if center_value:
        draw.text((round(cx * S), round((cy - 6) * S)), center_value,
                  font=_font(22, bold=True), fill=_INK, anchor="mm")
    if center_label:
        draw.text((round(cx * S), round((cy + 14) * S)), center_label.upper(),
                  font=_font(8.5), fill=_SUB, anchor="mm")


# ─── public chart builders ──────────────────────────────────────────────


def executive_dashboard(env: Environment) -> Diagram | None:
    """Six-tile at-a-glance summary: clusters, hosts, VMs, storage, projects, DR policies."""
    if not env.clusters and not env.hosts and not env.vms:
        return None
    storage_total = sum(c.max_capacity_bytes or 0 for c in env.storage_containers)
    tiles = [
        ("Clusters", str(len(env.clusters))),
        ("Hosts", str(len(env.hosts))),
        ("Virtual Machines", str(len(env.vms))),
        ("Storage Capacity", _size_label(storage_total) if storage_total else "—"),
        ("Projects", str(len(env.projects))),
        ("Protection Policies", str(len(env.protection_policies))),
    ]
    w = 2 * _FRAME_PAD + len(tiles) * _TILE_W + (len(tiles) - 1) * _TILE_GAP
    h = 2 * _FRAME_PAD + _TILE_H
    img, draw = _new_canvas(w, h)
    x = _FRAME_PAD
    for i, (label, value) in enumerate(tiles):
        _draw_tile(draw, x, _FRAME_PAD, _TILE_W, _TILE_H, value, label,
                   _NAVY if i % 2 == 0 else _ACCENT)
        x += _TILE_W + _TILE_GAP
    return _finish(img, w, h)


def vm_power_state_chart(env: Environment) -> Diagram | None:
    """Donut of VM power state — on / off / other (suspended, paused, unknown)."""
    if not env.vms:
        return None
    total = len(env.vms)
    on = sum(1 for v in env.vms if (v.power_state or "").upper() == "ON")
    off = sum(1 for v in env.vms if (v.power_state or "").upper() == "OFF")
    other = total - on - off
    segments = [(v, c) for v, c in
                [(on, _VM_ON), (off, _VM_OFF), (other, _VM_OTHER)] if v > 0]

    legend_w = 230
    pad = _FRAME_PAD
    w = pad * 2 + _DONUT_R * 2 + 30 + legend_w
    h = pad * 2 + _DONUT_R * 2
    img, draw = _new_canvas(w, h)
    cx, cy = pad + _DONUT_R, h / 2
    _draw_donut(draw, cx, cy, _DONUT_R, _DONUT_THICK, segments,
                center_value=str(total), center_label="total VMs")

    labelled = [
        (f"Powered On — {on} ({on / total:.0%})", _VM_ON, on),
        (f"Powered Off — {off} ({off / total:.0%})", _VM_OFF, off),
        (f"Other / Suspended — {other} ({other / total:.0%})", _VM_OTHER, other),
    ]
    labelled = [item for item in labelled if item[2] > 0]
    f_leg = _font(9.5)
    lx = pad + _DONUT_R * 2 + 30
    ly = cy - (len(labelled) - 1) * 11
    for label, color, _count in labelled:
        _legend_item(draw, lx, ly, label, color, f_leg)
        ly += 22
    return _finish(img, w, h)


def storage_capacity_chart(env: Environment) -> Diagram | None:
    """Horizontal stacked bars — used vs. free capacity per storage container."""
    containers = [c for c in env.storage_containers if c.max_capacity_bytes]
    if not containers:
        return None
    containers = containers[:8]
    max_cap = max(c.max_capacity_bytes or 0 for c in containers)
    scale = _BAR_TRACK_W / max_cap if max_cap else 0

    pad = _FRAME_PAD
    w = pad * 2 + _BAR_LABEL_W + _BAR_TRACK_W + 14 + _BAR_VALS_W
    rows_h = len(containers) * (_BAR_ROW_H + _BAR_ROW_GAP) - _BAR_ROW_GAP
    h = pad * 2 + rows_h + 28
    img, draw = _new_canvas(w, h)

    f_label = _font(10.5, bold=True)
    f_vals = _font(9)
    track_x = pad + _BAR_LABEL_W
    y = pad
    for c in containers:
        used = c.used_bytes or 0
        free = max((c.max_capacity_bytes or 0) - used, 0)
        used_w, free_w = used * scale, free * scale
        mid_y = y + _BAR_ROW_H / 2
        draw.rounded_rectangle(
            [round(track_x * S), round(y * S),
             round((track_x + _BAR_TRACK_W) * S), round((y + _BAR_ROW_H) * S)],
            radius=round(4 * S), fill=_TRACK,
        )
        if used_w > 0:
            draw.rounded_rectangle(
                [round(track_x * S), round(y * S),
                 round((track_x + used_w) * S), round((y + _BAR_ROW_H) * S)],
                radius=round(4 * S), fill=_ACCENT,
            )
        if free_w > 2:
            fx0 = track_x + used_w + 2
            draw.rounded_rectangle(
                [round(fx0 * S), round(y * S),
                 round((fx0 + free_w - 2) * S), round((y + _BAR_ROW_H) * S)],
                radius=round(4 * S), fill=(207, 233, 244),
            )
        draw.text((round(pad * S), round(mid_y * S)),
                  _fit(draw, c.name or c.uuid, f_label, (_BAR_LABEL_W - 8) * S),
                  font=f_label, fill=_INK, anchor="lm")
        draw.text((round((track_x + _BAR_TRACK_W + 14) * S), round(mid_y * S)),
                  f"{_size_label(used)} used · {_size_label(free)} free",
                  font=f_vals, fill=_SUB, anchor="lm")
        y += _BAR_ROW_H + _BAR_ROW_GAP

    lx: float = track_x
    ly = y + 8
    for label, color in [("Used capacity", _ACCENT), ("Free capacity", (207, 233, 244))]:
        lx += _legend_item(draw, lx, ly, label, color, f_vals) + 24
    return _finish(img, w, h)


def vms_per_cluster_chart(env: Environment) -> Diagram | None:
    """Bar chart of VM count per cluster; bar colors are a fixed per-cluster identity."""
    if not env.clusters or not env.vms:
        return None
    counts = Counter(v.cluster_name for v in env.vms if v.cluster_name)
    bars = [(c.name or c.uuid, counts.get(c.name or c.uuid, 0)) for c in env.clusters]
    bars = sorted((b for b in bars if b[1] > 0), key=lambda b: -b[1])[:8]
    if not bars:
        return None
    max_v = max(v for _, v in bars)

    pad, bar_w, bar_gap, plot_h, label_h = _FRAME_PAD, 82, 26, 150, 34
    w = pad * 2 + len(bars) * bar_w + (len(bars) - 1) * bar_gap
    h = pad * 2 + plot_h + label_h
    img, draw = _new_canvas(w, h)
    baseline = pad + plot_h
    draw.line([round(pad * S), round(baseline * S), round((w - pad) * S), round(baseline * S)],
              fill=_FRAME, width=S)

    f_val, f_name = _font(12, bold=True), _font(9.5)
    x = pad
    for i, (name, count) in enumerate(bars):
        bh = max(6.0, count / max_v * (plot_h - 24))
        color = _CLUSTER_PALETTE[i % len(_CLUSTER_PALETTE)]
        r = min(round(5 * S), round(bh * S / 2), round(bar_w * S / 2))
        draw.rounded_rectangle(
            [round(x * S), round((baseline - bh) * S),
             round((x + bar_w) * S), round(baseline * S)],
            radius=max(1, r), fill=color,
        )
        draw.text((round((x + bar_w / 2) * S), round((baseline - bh - 12) * S)),
                  str(count), font=f_val, fill=_INK, anchor="mm")
        draw.text((round((x + bar_w / 2) * S), round((baseline + 16) * S)),
                  _fit(draw, name, f_name, bar_w * S), font=f_name, fill=_SUB, anchor="mm")
        x += bar_w + bar_gap
    return _finish(img, w, h)


# ─── cover page ──────────────────────────────────────────────────────────


def _vgradient(draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float,
               stops: list[tuple[float, tuple[int, int, int]]], steps: int = 64) -> None:
    """Fill a rectangle (already-scaled px) with a multi-stop top-to-bottom gradient.

    ``stops`` is a list of ``(t, rgb)`` with ``t`` ascending in ``[0, 1]``.
    """
    band_h = (y1 - y0) / steps
    for i in range(steps):
        t = i / max(1, steps - 1)
        color = stops[-1][1]
        for (t0, c0), (t1, c1) in zip(stops, stops[1:], strict=False):
            if t0 <= t <= t1:
                lt = (t - t0) / (t1 - t0) if t1 > t0 else 0
                color = (
                    round(c0[0] + (c1[0] - c0[0]) * lt),
                    round(c0[1] + (c1[1] - c0[1]) * lt),
                    round(c0[2] + (c1[2] - c0[2]) * lt),
                )
                break
        draw.rectangle([x0, y0 + i * band_h, x1, y0 + (i + 1) * band_h + 1], fill=color)


def _hgradient(draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float,
               stops: list[tuple[float, tuple[int, int, int]]], steps: int = 64) -> None:
    """Same as :func:`_vgradient` but left-to-right."""
    band_w = (x1 - x0) / steps
    for i in range(steps):
        t = i / max(1, steps - 1)
        color = stops[-1][1]
        for (t0, c0), (t1, c1) in zip(stops, stops[1:], strict=False):
            if t0 <= t <= t1:
                lt = (t - t0) / (t1 - t0) if t1 > t0 else 0
                color = (
                    round(c0[0] + (c1[0] - c0[0]) * lt),
                    round(c0[1] + (c1[1] - c0[1]) * lt),
                    round(c0[2] + (c1[2] - c0[2]) * lt),
                )
                break
        draw.rectangle([x0 + i * band_w, y0, x0 + (i + 1) * band_w + 1, y1], fill=color)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: Any, max_w: float) -> list[str]:
    """Greedy word-wrap `text` to fit `max_w` scaled px per line."""
    words = text.split()
    lines: list[str] = []
    cur = ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if not cur or draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def cover_page(env: Environment, tool_name: str = "NTNXVision") -> Diagram:
    """Report cover — dark navy-to-blue gradient with a soft cyan glow, a
    hexagon logomark, a large title block, and a metadata footer row. This is
    NTNXVision's own cover identity (established in the workflow-evaluation
    mockup), not a literal reproduction of the coworker script's cover.
    """
    w, h = 900, 620  # @ BASE_DPI -> 7.5in x ~5.17in.
    img = Image.new("RGBA", (w * S, h * S), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img, "RGBA")

    _vgradient(draw, 0, 0, w * S, h * S, [
        (0.0, (1, 18, 38)), (0.46, (1, 42, 94)), (1.0, (2, 77, 161)),
    ])

    # Soft cyan glow, upper right — a blurred translucent ellipse composited in.
    glow = Image.new("RGBA", (w * S, h * S), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    gcx, gcy = round(w * 0.85 * S), round(h * -0.10 * S)
    grx, gry = round(550 * S), round(250 * S)
    gdraw.ellipse([gcx - grx, gcy - gry, gcx + grx, gcy + gry], fill=(0, 163, 224, 90))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(radius=60 * S)))
    draw = ImageDraw.Draw(img, "RGBA")

    # Bottom accent bar: cyan -> light cyan -> cyan.
    bar_h = 10
    _hgradient(draw, 0, (h - bar_h) * S, w * S, h * S,
              [(0.0, (0, 163, 224)), (0.55, (95, 214, 255)), (1.0, (0, 163, 224))])

    pad_x, pad_y = 60.0, 56.0

    # Logomark: hexagon outline + filled center dot, wordmark beside it.
    hex_scale = 34 / 40
    hex_pts = [(pad_x + x * hex_scale, pad_y + y * hex_scale)
               for x, y in [(20, 2), (36, 11), (36, 29), (20, 38), (4, 29), (4, 11)]]
    draw.polygon([(round(px * S), round(py * S)) for px, py in hex_pts],
                 outline=(95, 214, 255, 255), width=round(2 * S))
    ccx, ccy, cr = pad_x + 20 * hex_scale, pad_y + 20 * hex_scale, 6.5 * hex_scale
    draw.ellipse([round((ccx - cr) * S), round((ccy - cr) * S),
                  round((ccx + cr) * S), round((ccy + cr) * S)], fill=(95, 214, 255, 255))
    draw.text((round((pad_x + 34 + 12) * S), round((pad_y + 17) * S)),
              f"{tool_name} · Nutanix Environment Report".upper(),
              font=_font(12, bold=True), fill=(191, 233, 251, 255), anchor="lm")

    # Title block, vertically centered in the space between the mark and the footer.
    max_title_w = 560 * S
    f_eyebrow, f_h1, f_h2 = _font(12, bold=True), _font(38, bold=True), _font(16)
    title_lines = _wrap(draw, "Nutanix Platform Architecture & Inventory Report",
                        f_h1, max_title_w)
    subtitle = f"Prism Central {env.prism_central.host or 'n/a'} · " \
               "generated from live API collection"

    eyebrow_h, h1_line_h, gap1, h2_h = 16, 46, 14, 22
    block_h = eyebrow_h + gap1 + len(title_lines) * h1_line_h + 10 + h2_h
    footer_top = h - pad_y - 96
    mid_zone_top, mid_zone_bottom = pad_y + 34 + 30, footer_top
    ty = mid_zone_top + max(0.0, (mid_zone_bottom - mid_zone_top - block_h) / 2)

    draw.text((round(pad_x * S), round(ty * S)), "ENVIRONMENT DOCUMENTATION",
              font=f_eyebrow, fill=(127, 212, 242, 255), anchor="lm")
    ty += eyebrow_h + gap1
    for line in title_lines:
        draw.text((round(pad_x * S), round(ty * S)), line, font=f_h1,
                  fill=(255, 255, 255, 255), anchor="lm")
        ty += h1_line_h
    ty += 6
    draw.text((round(pad_x * S), round(ty * S)), subtitle, font=f_h2,
              fill=(207, 230, 247, 255), anchor="lm")

    # Footer: metadata rows on the left, confidential label on the right, both
    # sitting on a thin translucent divider.
    fy = footer_top
    draw.line([round(pad_x * S), round(fy * S), round((w - pad_x) * S), round(fy * S)],
              fill=(255, 255, 255, 46), width=round(S))
    fy += 18
    f_dt, f_dd = _font(11), _font(11, bold=False)
    rows = [
        ("Prepared by", tool_name),
        ("Report date", (env.collected_at or "n/a")[:10]),
        ("Environment", f"{len(env.clusters)} clusters · {len(env.hosts)} hosts · "
                        f"{len(env.vms)} VMs"),
    ]
    for label, value in rows:
        draw.text((round(pad_x * S), round(fy * S)), label, font=f_dt,
                  fill=(124, 157, 176, 255), anchor="lm")
        draw.text((round((pad_x + 110) * S), round(fy * S)), value, font=f_dd,
                  fill=(234, 246, 255, 255), anchor="lm")
        fy += 20
    draw.text((round((w - pad_x) * S), round((footer_top + 18) * S)),
              "CONFIDENTIAL · INTERNAL USE ONLY", font=_font(10.5, bold=True),
              fill=(127, 212, 242, 255), anchor="rm")

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return Diagram(png=buf.getvalue(), width=w, height=h)
