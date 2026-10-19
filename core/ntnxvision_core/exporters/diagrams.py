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

The architecture diagrams (the Solution Conceptual Overview, which replaced the old
management and replication topology figures) live in `ntnxvision_core.diagrams`:
vector scenes drawn natively in the PDF. This module keeps the charts and the
per-cluster raster figures.
"""

from __future__ import annotations

import io
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from ntnxvision_core.exporters import theme as T
from ntnxvision_core.models import Cluster, Environment
from ntnxvision_core.numfmt import NA, fmt_count

GIB = 1024**3
#: Supersampling factor for crisp print output.
S = 3
#: Logical pixels per inch when embedding (keeps body text ~7pt on the page).
BASE_DPI = 120

# ─── palette (Runway, light) ───────────────────────────────────────────
# Neutral cards on white with cool grey rules; the iris accent only marks the root card;
# colour otherwise means a status (ok / warn / error) or a chart series in Runway order.

_INK = T.INK_RGB
_SUB = T.INK_3_RGB
_NAVY = T.INK_RGB
_ACCENT = T.IRIS_RGB
_FRAME = T.RULE_2_RGB
_CARD_FILL = T.SURFACE_RGB
_CARD_BORDER = T.RULE_RGB
_DIM_FILL = (255, 255, 255)
_DIM_BORDER = T.RULE_2_RGB
_OK_TEXT = T.INK_2_RGB
_OK_GLYPH = T.STATUS_RGB["ok"]
_PILL_FILL = T.BODY_RGB
_PILL_TEXT = T.INK_2_RGB
_GREY_PILL_FILL = T.BODY_RGB
_EDGE = (178, 192, 204)

# Figure strip, donuts, and bars (executive summary + inventory charts).
_TRACK = T.RULE_2_RGB
_VM_ON = T.CHART_RGB[0]
_VM_OFF = T.RULE_RGB
_VM_OTHER = T.CHART_RGB[1]
_ALERT_CRIT = T.STATUS_RGB["error"]
_ALERT_WARN = T.STATUS_RGB["warn"]
_ALERT_INFO = T.INK_3_RGB
_ALERT_OTHER = T.RULE_RGB
_USED = T.CHART_RGB[0]
_FREE = T.RULE_2_RGB
#: Fixed hue order for per-cluster identity (the Runway chart palette), never reassigned
#: per render, so the same cluster keeps its colour across a report's charts.
_CLUSTER_PALETTE = list(T.CHART_RGB)

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

# (path, collection-index); checked in order, first loadable + style match wins. The
# vendored Figtree comes first so figures look the same on every platform and match the
# PDF's body font; the system fonts remain as a fallback.
_FONT_CANDIDATES: dict[bool, list[tuple[str, int]]] = {
    False: [
        (str(T.FIGTREE), 0),
        ("/System/Library/Fonts/Helvetica.ttc", 0),
        ("/System/Library/Fonts/Supplemental/Arial.ttf", 0),
        ("C:/Windows/Fonts/segoeui.ttf", 0),
        ("C:/Windows/Fonts/arial.ttf", 0),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
    ],
    True: [
        (str(T.FIGTREE_SEMIBOLD), 0),
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

    return Diagram(png=_png(img), width=round(w), height=round(h))


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
        if n.tag_style == "ok":
            # A status, Runway style: a small square glyph and an ink label, no pill.
            glyph = 7 * S
            tag_w = draw.textlength(n.tag, font=f_tag) + glyph + 5 * S
            tx2 = round((x + w - _PAD_X) * S - tag_w)
            draw.rectangle([tx2, title_cy - glyph // 2, tx2 + glyph, title_cy + glyph // 2],
                           fill=_OK_GLYPH)
            draw.text((tx2 + glyph + 5 * S, title_cy), n.tag, font=f_tag, fill=_OK_TEXT,
                      anchor="lm")
        else:
            tag_w = draw.textlength(n.tag, font=f_tag) + 12 * S
            tag_h = 15 * S
            tx2 = round((x + w - _PAD_X) * S - tag_w)
            pill_text = _SUB if n.tag_style == "grey" else _PILL_TEXT
            draw.rounded_rectangle(
                [tx2, title_cy - tag_h // 2, tx2 + round(tag_w), title_cy + tag_h // 2],
                radius=round(3 * S), fill=_PILL_FILL,
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
    return nodes[: limit - 1] + [Node(f"+ {fmt_count(extra)} more {noun}", style="dim")]


def _gib(value: int | None) -> int | None:
    return round(value / GIB) if value else None


def _size_label(value_bytes: int | None) -> str:
    """Bytes as a human GiB/TiB label, switching units above 1 TiB."""
    if not value_bytes:
        return "0 GiB"
    tib = value_bytes / (GIB * 1024)
    if tib >= 1:
        return f"{tib:,.1f} TiB"
    return f"{round(value_bytes / GIB):,} GiB"


# ─── cluster membership ────────────────────────────────────────────────
# Hosts/VMs/subnets are matched to their cluster by UUID first. A name is only trusted
# as a fallback (some payloads carry the name but no cluster UUID) and then only within
# the same source Prism Central: a combined multi-PC run routinely holds two unrelated
# clusters both called "Cluster01", and matching on name alone silently merged them.


def _cluster_resolver(env: Environment) -> Callable[[Any], Cluster | None]:
    """Build a function mapping an object to the Cluster it belongs to, or None."""
    by_uuid = {c.uuid: c for c in env.clusters if c.uuid}
    by_name: dict[tuple[str | None, str | None], Cluster] = {}
    for c in env.clusters:
        by_name.setdefault((c.name, c.source_pc), c)
    # A Prism Element collection holds exactly one cluster, and its v2.0 networks carry no
    # cluster reference at all: they belong to that cluster.
    only = (env.clusters[0] if env.source_type == "prism_element" and len(env.clusters) == 1
            else None)

    def resolve(obj: Any) -> Cluster | None:
        uuid = getattr(obj, "cluster_uuid", None)
        if uuid and uuid in by_uuid:
            return by_uuid[uuid]
        name = getattr(obj, "cluster_name", None)
        if not name:
            return only
        return by_name.get((name, getattr(obj, "source_pc", None))) or only

    return resolve


def _counts_per_cluster(env: Environment, objs: list[Any]) -> dict[int, int]:
    """Count `objs` per cluster, keyed by `id(cluster)` (clusters are not hashable)."""
    resolve = _cluster_resolver(env)
    counts: dict[int, int] = {}
    for obj in objs:
        cluster = resolve(obj)
        if cluster is not None:
            counts[id(cluster)] = counts.get(id(cluster), 0) + 1
    return counts


def _cluster_labels(env: Environment) -> dict[int, str]:
    """Display label per cluster, disambiguated by source PC only where names repeat."""
    names = Counter(c.name or c.uuid for c in env.clusters)
    labels: dict[int, str] = {}
    for c in env.clusters:
        label = c.name or c.uuid or "cluster"
        if env.is_combined and names[c.name or c.uuid] > 1 and c.source_pc:
            label = f"{label} · {c.source_pc}"
        labels[id(c)] = label
    return labels


# ─── public builders ───────────────────────────────────────────────────


def cluster_compute(env: Environment, cluster: Cluster) -> Diagram | None:
    """One cluster and its physical nodes with CPU / memory / VM density."""
    resolve = _cluster_resolver(env)
    hosts = [h for h in env.hosts if resolve(h) is cluster]
    if not hosts:
        return None
    root = Node(
        cluster.name or cluster.uuid,
        [f"AOS {cluster.aos_version or NA}",
         f"{fmt_count(cluster.num_nodes or len(hosts))} node(s)"
         + (f" · RF {cluster.redundancy_factor}" if cluster.redundancy_factor else "")],
        style="root",
    )
    for h in hosts:
        specs = " · ".join(p for p in (
            f"{fmt_count(h.num_cpu_cores)} cores" if h.num_cpu_cores else None,
            f"{fmt_count(_gib(h.memory_bytes))} GiB" if h.memory_bytes else None,
            f"{fmt_count(h.num_vms)} VM(s)" if h.num_vms is not None else None,
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
    resolve = _cluster_resolver(env)
    subs = [s for s in env.subnets
            if resolve(s) is cluster and (s.subnet_type or "VLAN").upper() != "OVERLAY"]
    if not subs:
        return None
    groups: dict[str, list] = {}
    for s in subs:
        groups.setdefault(s.virtual_switch or "default", []).append(s)

    root = Node(cluster.name or cluster.uuid,
                [f"{fmt_count(len(subs))} VLAN subnet(s)"], style="root")
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
            lines.append(f"{fmt_count(v.route_count or 0)} route(s) · "
                         f"{fmt_count(v.routing_policy_count or 0)} routing policy(ies)")
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


# ─── chart primitives (figure strip, donuts, bars) ──────────────────────


@lru_cache(maxsize=8)
def _light_font(size: float) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Montserrat Light, the Runway figure face, for display numerals."""
    try:
        return ImageFont.truetype(str(T.MONTSERRAT_LIGHT), round(size * S))
    except OSError:  # pragma: no cover - vendored font missing
        return _font(size)


def _new_canvas(w: float, h: float, frame: bool = True
                ) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (round(w * S), round(h * S)), "white")
    draw = ImageDraw.Draw(img)
    if frame:
        draw.rounded_rectangle(
            [S, S, round(w * S) - S, round(h * S) - S],
            radius=round(6 * S), outline=_FRAME, width=S,
        )
    return img, draw


def _png(img: Image.Image) -> bytes:
    """PNG with an adaptive palette (median cut, no dither): these charts are flat
    colours on white, so they look the same at a fraction of the size, which keeps
    large reports mailable."""
    buf = io.BytesIO()
    img.quantize(colors=256, method=Image.Quantize.MEDIANCUT,
                 dither=Image.Dither.NONE).save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _finish(img: Image.Image, w: float, h: float) -> Diagram:
    return Diagram(png=_png(img), width=round(w), height=round(h))


def _legend_item(draw: ImageDraw.ImageDraw, x: float, y: float, label: str,
                 color: tuple[int, int, int], font: Any) -> float:
    """Draw one legend square + label at (x, y); return the logical-px width consumed."""
    draw.rectangle([round(x * S), round((y - 4) * S), round((x + 8) * S), round((y + 4) * S)],
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
                  font=_light_font(24), fill=_INK, anchor="mm")
    if center_label:
        draw.text((round(cx * S), round((cy + 15) * S)), center_label,
                  font=_font(8.5), fill=_SUB, anchor="mm")


# ─── public chart builders ──────────────────────────────────────────────


def executive_dashboard(env: Environment) -> Diagram | None:
    """At-a-glance figure strip: clusters, hosts, VMs, storage, open alerts, projects.

    Runway draws headline numbers as one ruled row of light numerals with small labels
    above, separated by hairlines (never a grid of coloured KPI tiles). The PDF and Word
    exporters draw this strip natively; this raster version serves any other consumer.
    """
    if not env.clusters and not env.hosts and not env.vms:
        return None
    storage_total = sum(c.max_capacity_bytes or 0 for c in env.storage_containers)
    items = [
        ("Clusters", fmt_count(len(env.clusters))),
        ("Hosts", fmt_count(len(env.hosts))),
        ("Virtual machines", fmt_count(len(env.vms))),
        ("Storage capacity", _size_label(storage_total) if storage_total else NA),
        ("Open alerts", fmt_count(sum(1 for a in env.alerts if not a.resolved))),
        ("Projects", fmt_count(len(env.projects))),
    ]
    cell_w, h = 150.0, 78.0
    w = cell_w * len(items)
    img, draw = _new_canvas(w, h, frame=False)
    draw.line([0, S, round(w * S), S], fill=_FRAME, width=S)
    draw.line([0, round((h - 1) * S), round(w * S), round((h - 1) * S)], fill=_FRAME, width=S)
    for i, (label, value) in enumerate(items):
        x = i * cell_w
        if i:
            draw.line([round(x * S), round(12 * S), round(x * S), round((h - 12) * S)],
                      fill=_CARD_BORDER, width=S)
        draw.text((round((x + 14) * S), round(22 * S)), label, font=_font(9.5), fill=_SUB,
                  anchor="lm")
        draw.text((round((x + 14) * S), round(50 * S)),
                  _fit(draw, value, _light_font(26), (cell_w - 20) * S),
                  font=_light_font(26), fill=_INK, anchor="lm")
    return _finish(img, w, h)


def vm_power_state_chart(env: Environment) -> Diagram | None:
    """Donut of VM power state: on / off / other (suspended, paused, unknown)."""
    if not env.vms:
        return None
    total = len(env.vms)
    on = sum(1 for v in env.vms if T.power_label(v.power_state) == "On")
    off = sum(1 for v in env.vms if T.power_label(v.power_state) == "Off")
    other = total - on - off
    segments = [(v, c) for v, c in
                [(on, _VM_ON), (off, _VM_OFF), (other, _VM_OTHER)] if v > 0]

    legend_w = 230
    pad = _FRAME_PAD
    w = pad * 2 + _DONUT_R * 2 + 30 + legend_w
    h = pad * 2 + _DONUT_R * 2
    img, draw = _new_canvas(w, h)
    cx, cy = pad + _DONUT_R, h / 2
    # The centre carries the share powered on: the VM total is already a headline figure.
    _draw_donut(draw, cx, cy, _DONUT_R, _DONUT_THICK, segments,
                center_value=f"{on / total:.0%}", center_label="powered on")

    labelled = [
        (f"Powered on: {fmt_count(on)} ({on / total:.0%})", _VM_ON, on),
        (f"Powered off: {fmt_count(off)} ({off / total:.0%})", _VM_OFF, off),
        (f"Other or suspended: {fmt_count(other)} ({other / total:.0%})", _VM_OTHER, other),
    ]
    labelled = [item for item in labelled if item[2] > 0]
    f_leg = _font(9.5)
    lx = pad + _DONUT_R * 2 + 30
    ly = cy - (len(labelled) - 1) * 11
    for label, color, _count in labelled:
        _legend_item(draw, lx, ly, label, color, f_leg)
        ly += 22
    return _finish(img, w, h)


def alert_severity_chart(env: Environment) -> Diagram | None:
    """Donut of alert severity (critical / warning / info / other) over the open alerts,
    or over every alert when none is open. None for an environment with no alerts.

    Severity is a status, so the segments use the status colours (error, warn) and grey
    for informational alerts, not chart series colours.
    """
    if not env.alerts:
        return None
    pool = [a for a in env.alerts if not a.resolved] or list(env.alerts)
    is_open = any(not a.resolved for a in env.alerts)
    total = len(pool)
    sev = Counter((a.severity or "").upper() for a in pool)
    other = total - sev["CRITICAL"] - sev["WARNING"] - sev["INFO"]
    counts = [
        ("Critical", _ALERT_CRIT, sev["CRITICAL"]),
        ("Warning", _ALERT_WARN, sev["WARNING"]),
        ("Info", _ALERT_INFO, sev["INFO"]),
        ("Other", _ALERT_OTHER, other),
    ]
    segments = [(v, c) for _label, c, v in counts if v > 0]

    legend_w = 230
    pad = _FRAME_PAD
    w = pad * 2 + _DONUT_R * 2 + 30 + legend_w
    h = pad * 2 + _DONUT_R * 2
    img, draw = _new_canvas(w, h)
    cx, cy = pad + _DONUT_R, h / 2
    _draw_donut(draw, cx, cy, _DONUT_R, _DONUT_THICK, segments,
                center_value=fmt_count(total), center_label="open alerts" if is_open
                else "alerts")

    labelled = [(f"{label}: {fmt_count(v)} ({v / total:.0%})", c)
                for label, c, v in counts if v > 0]
    f_leg = _font(9.5)
    lx = pad + _DONUT_R * 2 + 30
    ly = cy - (len(labelled) - 1) * 11
    for label, color in labelled:
        _legend_item(draw, lx, ly, label, color, f_leg)
        ly += 22
    return _finish(img, w, h)


def storage_capacity_chart(env: Environment) -> Diagram | None:
    """Horizontal stacked bars: used vs. free capacity per storage container."""
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
    y: float = pad
    bar_h = _BAR_ROW_H * 0.6
    for c in containers:
        used = c.used_bytes or 0
        free = max((c.max_capacity_bytes or 0) - used, 0)
        used_w = used * scale
        mid_y = y + _BAR_ROW_H / 2
        top, bottom = mid_y - bar_h / 2, mid_y + bar_h / 2
        cap_w = (c.max_capacity_bytes or 0) * scale
        draw.rectangle([round(track_x * S), round(top * S),
                        round((track_x + cap_w) * S), round(bottom * S)], fill=_FREE)
        if used_w > 0:
            draw.rectangle([round(track_x * S), round(top * S),
                            round((track_x + used_w) * S), round(bottom * S)], fill=_USED)
        draw.text((round(pad * S), round(mid_y * S)),
                  _fit(draw, c.name or c.uuid, f_label, (_BAR_LABEL_W - 8) * S),
                  font=f_label, fill=_INK, anchor="lm")
        draw.text((round((track_x + _BAR_TRACK_W + 14) * S), round(mid_y * S)),
                  f"{_size_label(used)} used · {_size_label(free)} free",
                  font=f_vals, fill=_SUB, anchor="lm")
        y += _BAR_ROW_H + _BAR_ROW_GAP

    lx: float = track_x
    ly = y + 8
    for label, color in [("Used capacity", _USED), ("Free capacity", _FREE)]:
        lx += _legend_item(draw, lx, ly, label, color, f_vals) + 24
    return _finish(img, w, h)


def vms_per_cluster_chart(env: Environment) -> Diagram | None:
    """Bar chart of VM count per cluster; bar colours follow the Runway chart order."""
    if not env.clusters or not env.vms:
        return None
    counts = _counts_per_cluster(env, env.vms)
    labels = _cluster_labels(env)
    bars = [(labels[id(c)], counts.get(id(c), 0)) for c in env.clusters]
    bars = sorted((b for b in bars if b[1] > 0), key=lambda b: -b[1])[:8]
    if not bars:
        return None
    max_v = max(v for _, v in bars)

    pad, bar_w, bar_gap, plot_h, label_h = _FRAME_PAD, 82, 26, 150, 34
    w = max(pad * 2 + len(bars) * bar_w + (len(bars) - 1) * bar_gap, 300)
    h = pad * 2 + plot_h + label_h
    img, draw = _new_canvas(w, h)
    baseline = pad + plot_h
    draw.line([round(pad * S), round(baseline * S), round((w - pad) * S), round(baseline * S)],
              fill=_CARD_BORDER, width=S)

    f_val, f_name = _font(11), _font(9.5)
    x = pad
    for i, (name, count) in enumerate(bars):
        bh = max(6.0, count / max_v * (plot_h - 24))
        color = _CLUSTER_PALETTE[i % len(_CLUSTER_PALETTE)]
        draw.rectangle(
            [round(x * S), round((baseline - bh) * S),
             round((x + bar_w) * S), round(baseline * S)],
            fill=color,
        )
        draw.text((round((x + bar_w / 2) * S), round((baseline - bh - 12) * S)),
                  fmt_count(count), font=f_val, fill=_INK, anchor="mm")
        draw.text((round((x + bar_w / 2) * S), round((baseline + 16) * S)),
                  _fit(draw, name, f_name, bar_w * S), font=f_name, fill=_SUB, anchor="mm")
        x += bar_w + bar_gap
    return _finish(img, w, h)
