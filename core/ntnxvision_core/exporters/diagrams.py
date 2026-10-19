"""Data charts for the Word / PDF reports: storage capacity, VM power state, VMs per
cluster and open alerts by severity.

Every architecture figure is an engine diagram (`ntnxvision_core.diagrams`: vector
scenes, editable on the Diagrams page). These four are quantities, not architecture, so
they stay Pillow PNG charts in the Runway palette, drawn without a frame so they sit on
the page like the scenes. Geometry is in logical pixels at `BASE_DPI` and drawn at
``S``x supersampling; every chart is narrower than the page column it is placed in, so
it prints at 1:1 and no text is under 6 pt (`_MIN_PX`). Every builder returns ``None``
when the environment lacks the data.
"""

from __future__ import annotations

import io
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from ntnxvision_core.exporters import theme as T
from ntnxvision_core.models import Cluster, Environment
from ntnxvision_core.numfmt import fmt_count

GIB = 1024**3
#: Supersampling factor for crisp print output.
S = 3
#: Logical pixels per inch when embedding.
BASE_DPI = 120
#: The smallest text (logical px): 10 px at 120 dpi is 6 pt.
_MIN_PX = 10.0

# ─── palette (Runway, light) ───────────────────────────────────────────
_INK = T.INK_RGB
_SUB = T.INK_3_RGB
_RULE = T.RULE_RGB
_VM_ON = T.CHART_RGB[0]
_VM_OFF = T.RULE_RGB
_VM_OTHER = T.CHART_RGB[1]
_ALERT_CRIT = T.STATUS_RGB["error"]
_ALERT_WARN = T.STATUS_RGB["warn"]
_ALERT_INFO = T.INK_3_RGB
_ALERT_OTHER = T.RULE_RGB
#: One series, one hue (bars of a single-series chart are never coloured per bar).
_SERIES = T.CHART_RGB[0]
_USED = T.CHART_RGB[0]
_USED_WARN = T.STATUS_RGB["warn"]
_USED_FULL = T.STATUS_RGB["error"]
_FREE = T.RULE_2_RGB
#: Used share at which a container bar turns amber, then red.
WARN_AT, FULL_AT = 0.80, 0.90

# ─── geometry (logical px) ─────────────────────────────────────────────
_PAD = 12
_DONUT_R = 74
_DONUT_THICK = 24
_DONUT_GAP_DEG = 1.6
_BAR_ROW_H = 30
_BAR_ROW_GAP = 8
_BAR_LABEL_W = 230
_BAR_VALS_W = 190
_BAR_TRACK_W = 330
#: Containers drawn in the capacity chart, clusters in the VMs per cluster chart.
MAX_CONTAINERS = 12
MAX_CLUSTERS = 8

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


def _fit(draw: ImageDraw.ImageDraw, text: str, font: Any, max_w: float) -> str:
    """`text`, ellipsised to fit `max_w` (supersampled px)."""
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "\u2026", font=font) > max_w:
        text = text[:-1]
    return text + "\u2026"


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


# ─── chart primitives ────────────────────────────────────────────────


@lru_cache(maxsize=8)
def _light_font(size: float) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Montserrat Light, the Runway figure face, for display numerals."""
    try:
        return ImageFont.truetype(str(T.MONTSERRAT_LIGHT), round(size * S))
    except OSError:  # pragma: no cover - vendored font missing
        return _font(size)


def _new_canvas(w: float, h: float) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    """A white canvas with no frame: charts sit on the page like the vector scenes."""
    img = Image.new("RGB", (round(w * S), round(h * S)), "white")
    return img, ImageDraw.Draw(img)


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
                  font=_font(_MIN_PX), fill=_SUB, anchor="mm")


# ─── public chart builders ──────────────────────────────────────────────


def _donut_chart(segments: list[tuple[int, tuple[int, int, int]]],
                 labelled: list[tuple[str, tuple[int, int, int]]], center_value: str,
                 center_label: str) -> Diagram:
    legend_w = 230
    w = _PAD * 2 + _DONUT_R * 2 + 30 + legend_w
    h = _PAD * 2 + _DONUT_R * 2
    img, draw = _new_canvas(w, h)
    cx, cy = _PAD + _DONUT_R, h / 2
    _draw_donut(draw, cx, cy, _DONUT_R, _DONUT_THICK, segments,
                center_value=center_value, center_label=center_label)
    f_leg = _font(_MIN_PX)
    lx = _PAD + _DONUT_R * 2 + 30
    ly = cy - (len(labelled) - 1) * 11
    for label, color in labelled:
        _legend_item(draw, lx, ly, label, color, f_leg)
        ly += 22
    return _finish(img, w, h)


def vm_power_state_chart(env: Environment) -> Diagram | None:
    """Donut of VM power state: on / off / other (suspended, paused, unknown)."""
    if not env.vms:
        return None
    total = len(env.vms)
    on = sum(1 for v in env.vms if T.power_label(v.power_state) == "On")
    off = sum(1 for v in env.vms if T.power_label(v.power_state) == "Off")
    other = total - on - off
    rows = [(f"Powered on: {fmt_count(on)} ({on / total:.0%})", _VM_ON, on),
            (f"Powered off: {fmt_count(off)} ({off / total:.0%})", _VM_OFF, off),
            (f"Other or suspended: {fmt_count(other)} ({other / total:.0%})", _VM_OTHER,
             other)]
    # The centre carries the share powered on: the VM total is already a headline figure.
    return _donut_chart([(v, c) for _, c, v in rows if v > 0],
                        [(t, c) for t, c, v in rows if v > 0], f"{on / total:.0%}",
                        "powered on")


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
    counts = [("Critical", _ALERT_CRIT, sev["CRITICAL"]), ("Warning", _ALERT_WARN,
                                                           sev["WARNING"]),
              ("Info", _ALERT_INFO, sev["INFO"]), ("Other", _ALERT_OTHER, other)]
    return _donut_chart([(v, c) for _, c, v in counts if v > 0],
                        [(f"{t}: {fmt_count(v)} ({v / total:.0%})", c)
                         for t, c, v in counts if v > 0],
                        fmt_count(total), "open alerts" if is_open else "alerts")


def capacity_containers(env: Environment) -> tuple[list[tuple[str, Any]], int]:
    """(label, container) for the capacity chart, and how many user containers there
    are: system containers left out (as in the Logical Cluster Design), ordered by
    cluster, then used share (fullest first), the first `MAX_CONTAINERS` kept."""
    from ntnxvision_core.diagrams.logical import _is_system

    labels = _cluster_labels(env)
    resolve = _cluster_resolver(env)
    order = {id(c): i for i, c in enumerate(sorted(
        env.clusters, key=lambda c: ((c.name or "").lower(), c.source_pc or "", c.uuid)))}
    rows = []
    for c in env.storage_containers:
        if not c.max_capacity_bytes or c.is_internal or _is_system(c.name):
            continue
        cl = resolve(c)
        share = (c.used_bytes or 0) / c.max_capacity_bytes
        label = f"{labels[id(cl)] if cl else (c.cluster_name or 'N/A')} \u00b7 " \
            f"{c.name or c.uuid}"
        rows.append(((order.get(id(cl), len(order)) if cl else len(order), -share,
                      (c.name or "").lower(), c.uuid), label, c))
    rows.sort(key=lambda r: r[0])
    return [(label, c) for _, label, c in rows[:MAX_CONTAINERS]], len(rows)


def storage_capacity_chart(env: Environment) -> Diagram | None:
    """Horizontal used / free bars per user container ("Cluster · container"); the
    used bar turns amber at 80% and red at 90% used."""
    rows, _total = capacity_containers(env)
    if not rows:
        return None
    max_cap = max(c.max_capacity_bytes or 0 for _, c in rows)
    scale = _BAR_TRACK_W / max_cap if max_cap else 0
    w = _PAD * 2 + _BAR_LABEL_W + _BAR_TRACK_W + 12 + _BAR_VALS_W
    rows_h = len(rows) * (_BAR_ROW_H + _BAR_ROW_GAP) - _BAR_ROW_GAP
    h = _PAD * 2 + rows_h + 26
    img, draw = _new_canvas(w, h)
    f_label, f_vals = _font(10.5, bold=True), _font(_MIN_PX)
    track_x = _PAD + _BAR_LABEL_W
    y: float = _PAD
    bar_h = _BAR_ROW_H * 0.6
    for label, c in rows:
        cap = c.max_capacity_bytes or 0
        used = c.used_bytes or 0
        free = max(cap - used, 0)
        share = used / cap if cap else 0
        color = _USED_FULL if share >= FULL_AT else _USED_WARN if share >= WARN_AT else _USED
        mid_y = y + _BAR_ROW_H / 2
        top, bottom = mid_y - bar_h / 2, mid_y + bar_h / 2
        draw.rectangle([round(track_x * S), round(top * S),
                        round((track_x + cap * scale) * S), round(bottom * S)], fill=_FREE)
        if used:
            draw.rectangle([round(track_x * S), round(top * S),
                            round((track_x + used * scale) * S), round(bottom * S)],
                           fill=color)
        draw.text((round(_PAD * S), round(mid_y * S)),
                  _fit(draw, label, f_label, (_BAR_LABEL_W - 8) * S),
                  font=f_label, fill=_INK, anchor="lm")
        draw.text((round((track_x + _BAR_TRACK_W + 12) * S), round(mid_y * S)),
                  f"{_size_label(used)} used ({share:.0%}) \u00b7 {_size_label(free)} free",
                  font=f_vals, fill=_SUB, anchor="lm")
        y += _BAR_ROW_H + _BAR_ROW_GAP
    lx: float = track_x
    ly = y + 6
    for label, color in [("Used", _USED), (f"Used, {WARN_AT:.0%} or more", _USED_WARN),
                         (f"Used, {FULL_AT:.0%} or more", _USED_FULL), ("Free", _FREE)]:
        lx += _legend_item(draw, lx, ly, label, color, f_vals) + 20
    return _finish(img, w, h)


def vm_cluster_counts(env: Environment) -> list[tuple[str, int]]:
    """(cluster label, user VMs) for every cluster with VMs, most first."""
    counts = _counts_per_cluster(env, env.vms)
    labels = _cluster_labels(env)
    bars = [(labels[id(c)], counts.get(id(c), 0)) for c in env.clusters]
    return sorted((b for b in bars if b[1] > 0), key=lambda b: (-b[1], b[0].lower()))


def vms_per_cluster_chart(env: Environment) -> Diagram | None:
    """Horizontal bars of user VMs per cluster, top `MAX_CLUSTERS`, one series colour."""
    if not env.clusters or not env.vms:
        return None
    bars = vm_cluster_counts(env)[:MAX_CLUSTERS]
    if not bars:
        return None
    max_v = max(v for _, v in bars)
    label_w, track_w, val_w, row_h, gap = 170.0, 220.0, 42.0, 20.0, 6.0
    w = _PAD * 2 + label_w + track_w + val_w
    h = _PAD * 2 + len(bars) * (row_h + gap) - gap
    img, draw = _new_canvas(w, h)
    f_name, f_val = _font(_MIN_PX + 0.5), _font(_MIN_PX + 0.5, bold=True)
    x0 = _PAD + label_w
    draw.line([round(x0 * S), round(_PAD * S), round(x0 * S), round((h - _PAD) * S)],
              fill=_RULE, width=S)
    y = float(_PAD)
    for name, count in bars:
        mid = y + row_h / 2
        bw = max(2.0, count / max_v * track_w)
        draw.rectangle([round(x0 * S), round((mid - row_h * 0.32) * S),
                        round((x0 + bw) * S), round((mid + row_h * 0.32) * S)], fill=_SERIES)
        draw.text((round((x0 - 8) * S), round(mid * S)),
                  _fit(draw, name, f_name, (label_w - 12) * S), font=f_name, fill=_INK,
                  anchor="rm")
        draw.text((round((x0 + bw + 6) * S), round(mid * S)), fmt_count(count), font=f_val,
                  fill=_INK, anchor="lm")
        y += row_h + gap
    return _finish(img, w, h)


__all__ = ["BASE_DPI", "Diagram", "alert_severity_chart", "capacity_containers",
           "storage_capacity_chart", "vm_cluster_counts", "vm_power_state_chart",
           "vms_per_cluster_chart"]
