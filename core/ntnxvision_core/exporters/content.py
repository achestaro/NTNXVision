"""The report's content, built once and rendered by both the PDF and the Word exporter.

Keeping one content model is what makes the two documents agree: the same chapters in the
same order, the same section and caption numbering, the same tables with the same columns
and counts, the same wording for a skipped or not-collected area. The renderers only
decide how a block looks on their page.

Structure: a `Report` has a `Cover` and a list of `Chapter`s; a chapter has lead blocks
and `Section`s; blocks are paragraphs, bullet lists, tables, figures, figure rows and a
figure strip. Optional chapters (anything gated behind a licensed or deployed feature) are
left out when the environment has no data for them; Licensing & Feature Usage and
Assumptions & Constraints say why. Each chapter belongs to one of the export dialog's
section ids (`ExportOptions.sections`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ntnxvision_core.exporters import diagrams
from ntnxvision_core.exporters.diagrams import Diagram
from ntnxvision_core.exporters.sourcing import source_hosts, sources
from ntnxvision_core.exporters.tables import Ctx
from ntnxvision_core.exporters.theme import (
    clean,
    collected_label,
    is_missing,
    parse_datetime,
    plural,
    power_label,
    report_date,
    tone_for,
)
from ntnxvision_core.models import Environment
from ntnxvision_core.numfmt import NA, PLAIN_HEADERS, fmt_count

GIB = 1024**3
TIB = GIB * 1024
#: Diagrams drawn per chapter before the rest are summarised in a sentence.
MAX_CLUSTER_FIGURES = 8

# ─── model ─────────────────────────────────────────────────────────────────────


@dataclass
class Status:
    """A status cell: rendered as a small coloured square glyph beside an ink label."""

    text: str
    tone: str  # ok | warn | error

    def __str__(self) -> str:
        return self.text


@dataclass
class Paragraph:
    text: str
    style: str = "body"  # body | note


@dataclass
class Bullets:
    items: list[str]
    intro: str | None = None


@dataclass
class Table:
    headers: list[str]
    rows: list[list[Any]]
    caption: str
    align: list[str] = field(default_factory=list)   # "L" | "R" per column
    mono: list[bool] = field(default_factory=list)
    weights: list[float] = field(default_factory=list)
    label: str = ""  # "Table 4-1." (assigned by `_number`)

    @property
    def title(self) -> str:
        return f"{self.label} {self.caption}".strip()


@dataclass
class Figure:
    diagram: Diagram
    caption: str
    label: str = ""

    @property
    def title(self) -> str:
        return f"{self.label} {self.caption}".strip()


@dataclass
class FigureRow:
    """Two or more small figures side by side (stacked where the page is narrow)."""

    figures: list[Figure]


@dataclass
class StripItem:
    label: str
    value: str
    qualifier: str | None = None
    tone: str | None = None


@dataclass
class FigureStrip:
    """Headline numbers in one ruled row (Runway: never a grid of KPI cards)."""

    items: list[StripItem]


Block = Paragraph | Bullets | Table | Figure | FigureRow | FigureStrip


@dataclass
class Section:
    title: str
    blocks: list[Block] = field(default_factory=list)
    number: str = ""

    @property
    def heading(self) -> str:
        return f"{self.number} {self.title}".strip()


@dataclass
class Chapter:
    key: str
    title: str
    group: str  # an ExportOptions section id
    blocks: list[Block] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    appendix: bool = False
    number: str = ""

    @property
    def heading(self) -> str:
        if self.appendix:
            return f"Appendix {self.number}. {self.title}"
        return f"{self.number}. {self.title}"

    def add(self, title: str, *blocks: Block | None) -> Section | None:
        """Append a section holding the non-empty `blocks`; skipped when there are none."""
        kept = [b for b in blocks if b is not None]
        if not kept:
            return None
        section = Section(title, kept)
        self.sections.append(section)
        return section

    @property
    def empty(self) -> bool:
        return not self.blocks and not self.sections


@dataclass
class Cover:
    eyebrow: str
    title: str
    subtitle: str
    meta: list[tuple[str, str]]
    note: str


@dataclass
class Report:
    cover: Cover
    chapters: list[Chapter]
    title: str
    subject: str
    keywords: str
    running_title: str  # page header text
    scope: str  # e.g. "pc01.demo.local" for the page header
    prepared_for: str = ""
    author: str = ""

    def outline(self) -> list[tuple[int, str]]:
        out: list[tuple[int, str]] = []
        for ch in self.chapters:
            out.append((0, ch.heading))
            out.extend((1, s.heading) for s in ch.sections)
        return out


# ─── cell formatting shared by the renderers ───────────────────────────────────

#: Columns whose values read best in the monospaced figure font (versions, addresses, ids).
MONO_HEADERS = frozenset({
    "AOS", "Version", "Current Version", "Current", "Available Versions", "Active Version",
    "IP Prefix", "IP address(es)", "IP Addresses", "Gateway", "VLAN", "Routable Prefixes",
    "NCC Version", "API",
})


def cell_text(value: Any, header: str | None = None) -> str:
    """A cell as display text: N/A when missing, comma grouping, one decimal for sizes."""
    if is_missing(value):
        return NA
    if isinstance(value, Status):
        return clean(value.text)
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, int):
        return str(value) if header in PLAIN_HEADERS else f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.1f}"
    return clean(value)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _timestamps(rows: list[list[Any]], headers: list[str]) -> None:
    """Format timestamp columns in place, one format per column: date only when every
    value in the column is a midnight date (licence expiry), else ``yyyy-mm-dd hh:mm``."""
    for i in range(len(headers)):
        stamps = [parse_datetime(r[i]) for r in rows]
        if not any(stamps):
            continue
        dates_only = all((s.hour, s.minute, s.second) == (0, 0, 0) for s in stamps if s)
        # A no-break space keeps a timestamp on one line when a narrow column wraps.
        fmt = "%Y-%m-%d" if dates_only else "%Y-%m-%d\u00a0%H:%M"
        for r, s in zip(rows, stamps, strict=True):
            if s is not None:
                r[i] = s.strftime(fmt)


def _prepare(value: Any, header: str) -> Any:
    """Normalise a raw value for display: statuses, cleaned strings."""
    if isinstance(value, str):
        tone = tone_for(header, value)
        if tone:
            return Status(clean(value), tone)
        return clean(value) if value.strip() else None
    return value


def make_table(headers: list[str], rows: list[list[Any]], caption: str,
               srcs: list[str] | None = None, prune: tuple[str, ...] = ()) -> Table | None:
    """A `Table` from raw rows, with per-column alignment, font and width weights.

    `srcs` (combined runs) prepends the "Prism Central" attribution column; `prune` names
    columns dropped when every row is missing a value (an all-N/A column is noise).
    """
    if not rows:
        return None
    if srcs is not None:
        headers = ["Prism Central", *headers]
        rows = [[s, *r] for s, r in zip(srcs, rows, strict=True)]
    rows = [list(r) for r in rows]
    _timestamps(rows, headers)
    rows = [[_prepare(v, h) for v, h in zip(r, headers, strict=True)] for r in rows]
    if prune:
        keep = [i for i, h in enumerate(headers)
                if h not in prune or any(not is_missing(r[i]) for r in rows)]
        headers = [headers[i] for i in keep]
        rows = [[r[i] for i in keep] for r in rows]
    align, mono, weights = [], [], []
    for i, h in enumerate(headers):
        present = [r[i] for r in rows if not is_missing(r[i])]
        numeric = bool(present) and all(_is_number(v) for v in present)
        align.append("R" if numeric else "L")
        mono.append(numeric or h in MONO_HEADERS)
        lengths = sorted(len(cell_text(r[i], h)) for r in rows)
        p90 = lengths[min(len(lengths) - 1, int(len(lengths) * 0.9))]
        weights.append(float(max(min(max(p90, len(h) * 0.85), 46), 4)))
    caption = f"{caption.rstrip('.')} ({fmt_count(len(rows))})."
    return Table(headers, rows, caption, align, mono, weights)


# ─── value helpers ─────────────────────────────────────────────────────────────


def gib(value: int | None) -> float | None:
    if not value:
        return None
    return round(value / GIB, 1)


def size_label(value: int | None) -> str:
    if not value:
        return "0 GiB"
    return f"{value / TIB:,.1f} TiB" if value >= TIB else f"{value / GIB:,.0f} GiB"


def yes_no(value: Any) -> str | None:
    if value is None:
        return None
    return "Yes" if value else "No"


def update_flag(value: bool | None) -> str:
    """Yes / No / Unknown: None means LCM did not say, and "No" would claim it is current."""
    if value is None:
        return "Unknown"
    return "Yes" if value else "No"


def versions(values: list[str], limit: int = 3) -> str | None:
    """The `limit` newest versions LCM offers, plus a count of the rest.

    LCM returns up to a dozen upgrade targets for some components; joined in full they
    push the table past the page width, and a cut mid-string reads as a version that does
    not exist.
    """
    if not values:
        return None
    extra = len(values) - limit
    head = ", ".join(values[:limit])
    return head if extra <= 0 else f"{head} (+{extra} more)"


def rpo_label(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    if seconds < 86400:
        return f"{seconds // 3600} h"
    return f"{seconds // 86400} d"


def deployment(cfg: Any) -> str | None:
    if cfg.is_scale_out is None:
        return None
    return "Scale-out (multi-node)" if cfg.is_scale_out else "Single-node"


_OUTCOME = {"collected": "Collected", "notes": "Collected with notes", "skipped": "Skipped",
            "failed": "Failed", "cancelled": "Cancelled"}
_API_LABEL = {"v2": "v2.0", "era": "Era"}


def run_areas(env: Environment) -> list[dict[str, Any]]:
    """The run's per-area outcome, as the app's Results screen shows it."""
    from ntnxvision_core.runinfo import areas_from_notes

    if env.run and env.run.get("areas"):
        return list(env.run["areas"])
    return areas_from_notes(env)


# ─── builder ───────────────────────────────────────────────────────────────────


class _Builder:
    def __init__(self, env: Environment, opts: Any) -> None:
        from ntnxvision_core.runinfo import api_routing, coverage, figures, sunset_areas

        self.env = env
        self.opts = opts
        self.ctx = Ctx(env)
        self.is_pe = env.source_type == "prism_element"
        self.areas = run_areas(env)
        self.coverage = coverage(self.areas)
        self.figures = figures(env, self.areas)
        self.routing = api_routing(env, self.areas, limit=None)
        self.sunset = sunset_areas(self.routing)

    # ─ helpers ─
    def src(self, objs: list[Any]) -> list[str] | None:
        return sources(self.env, objs)

    def cluster(self, obj: Any) -> str | None:
        return self.ctx.cluster_name(obj)

    def note(self, key: str) -> str | None:
        notes = self.env.collection_notes or {}
        return notes.get(key)

    def management_label(self) -> str:
        env = self.env
        if env.is_combined:
            return (f"{fmt_count(len(env.source_environments))} Prism Centrals "
                    f"({', '.join(source_hosts(env))})")
        host, ver = env.prism_central.host or NA, env.prism_central.version or NA
        if self.is_pe:
            return f"Prism Element {host} (AOS {ver})"
        return f"Prism Central {host} (version {ver})"

    def compat_sentence(self) -> str:
        from ntnxvision_core.runinfo import compat_info

        info = compat_info(self.env)
        rng = info.get("range") or NA
        if self.env.is_combined:
            parts = [f"{s.prism_central.host} runs {s.prism_central.version or NA}"
                     for s in self.env.source_environments]
            verdict = ("all within" if info.get("supported")
                       else "not all within")
            return f"{'; '.join(parts)}: {verdict} the supported range ({rng})."
        what = "AOS" if self.is_pe else "Prism Central"
        ver = info.get("version") or NA
        if info.get("supported"):
            return f"{what} {ver} is within the supported range ({rng})."
        return (f"{what} {ver} is outside the tested range ({rng}); collection proceeded and "
                "results should be checked.")

    # ─ cover ─
    def cover(self, eyebrow: str) -> Cover:
        env, opts = self.env, self.opts
        host, ver = env.prism_central.host or NA, env.prism_central.version or NA
        if env.is_combined:
            subtitle = (f"{fmt_count(len(env.source_environments))} Prism Centrals combined: "
                        f"{', '.join(source_hosts(env))}")
            source = ("Prism Centrals", ", ".join(
                f"{s.prism_central.host} ({s.prism_central.version or NA})"
                for s in env.source_environments))
            mode = f"Combined run of {fmt_count(len(env.source_environments))} Prism Centrals"
        elif self.is_pe:
            subtitle = f"Prism Element {host} · AOS {ver}"
            source = ("Prism Element", f"{host}, AOS {ver}")
            mode = "Prism Element (v2.0 API), cluster infrastructure only"
        else:
            subtitle = f"Prism Central {host} · {ver}"
            source = ("Prism Central", f"{host}, {ver}")
            mode = "Prism Central (v4 APIs, v3 where v4 is unavailable)"
        meta: list[tuple[str, str]] = []
        if opts.prepared_for:
            meta.append(("Prepared for", opts.prepared_for))
        meta += [
            ("Prepared by", opts.author or "NTNXVision"),
            ("Report date", report_date()),
            ("Data collected", collected_label(env)),
            source,
            ("Compatibility", self.compat_sentence()),
            ("Collection mode", mode),
            ("Environment", " · ".join([
                plural(len(env.clusters), "cluster"), plural(len(env.hosts), "host"),
                plural(len(env.vms), "VM")])),
            ("Generated by", f"NTNXVision {env.tool_version}"),
        ]
        return Cover(
            eyebrow=eyebrow,
            title="Nutanix Platform Architecture & Inventory Report",
            subtitle=subtitle,
            meta=meta,
            note=("Confidential. Generated from a read-only inventory snapshot; no "
                  "configuration was changed."),
        )

    # ─ chapters ─
    def executive_summary(self) -> Chapter:
        env = self.env
        ch = Chapter("executive_summary", "Executive Summary", "executive_summary")
        noun = "environment spanning" if env.is_combined else "environment managed by"
        if self.is_pe:
            noun = "environment managed directly through"
        ch.blocks.append(Paragraph(
            f"This document presents the architecture of the Nutanix Cloud Platform "
            f"{noun} {self.management_label()}. The environment consists of "
            f"{plural(len(env.clusters), 'cluster')} totalling "
            f"{plural(len(env.hosts), 'node')} and hosting "
            f"{plural(len(env.vms), 'virtual machine')}."
        ))
        if self.is_pe:
            ch.blocks.append(Paragraph(
                "This is a Prism Element collection: it documents the cluster's compute, "
                "storage, networks and workloads. Prism Central scoped services (data "
                "protection, Flow, Files, Objects, NDB, IAM, licensing and LCM) are outside "
                "its scope and are not covered here."
            ))
        else:
            ch.blocks.append(Paragraph(
                "Each section documents the as-built configuration discovered during "
                "inventory collection. Sections for features not present in this "
                "environment are omitted; Licensing & Feature Usage and Assumptions & "
                "Constraints record why."
            ))
        strip = [StripItem(f["label"], fmt_count(f["value"]), f.get("qualifier"),
                           f.get("qualifier_tone")) for f in self.figures]
        ch.blocks.append(FigureStrip(strip[:6]))
        ch.blocks.append(Bullets(self.highlights(), intro="Key findings"))
        return ch

    def highlights(self) -> list[str]:
        env, cov = self.env, self.coverage
        items: list[str] = []
        if cov["total"]:
            parts = [f"{fmt_count(cov['collected'] + cov['with_notes'])} of "
                     f"{plural(cov['total'], 'collection area')} collected"]
            if cov["with_notes"]:
                parts.append(f"{fmt_count(cov['with_notes'])} with notes")
            if cov["skipped"]:
                parts.append(f"{fmt_count(cov['skipped'])} skipped as not deployed")
            if cov["failed"]:
                parts.append(f"{fmt_count(cov['failed'])} failed")
            items.append(", ".join(parts) + ".")
        items.append(self.compat_sentence())
        if self.sunset:
            items.append(
                f"{plural(len(self.sunset), 'area')} still "
                f"{'uses a v3 API' if len(self.sunset) == 1 else 'use v3 APIs'} that Nutanix "
                f"retires in Q4 2026 ({', '.join(self.sunset)}) and will stop collecting "
                "after the sunset (see Appendix A)."
            )
        cap = sum(c.max_capacity_bytes or 0 for c in env.storage_containers)
        used = sum(c.used_bytes or 0 for c in env.storage_containers)
        if cap:
            items.append(
                f"{plural(len(env.storage_containers), 'storage container')} provide "
                f"{size_label(cap)} of capacity, {size_label(used)} used "
                f"({used / cap:.0%})."
            )
        if env.protection_policies or env.recovery_plans:
            pp = plural(len(env.protection_policies), "protection policy", "protection policies")
            items.append(
                f"{pp} and {plural(len(env.recovery_plans), 'recovery plan')} provide data "
                "protection and disaster recovery."
            )
        if env.security_policies:
            enforced = sum(1 for p in env.security_policies
                           if (p.state or "").upper() in ("ENFORCE", "APPLY"))
            sp = plural(len(env.security_policies), "Flow security policy",
                        "Flow security policies")
            items.append(f"{sp} ({fmt_count(enforced)} enforced) provide microsegmentation.")
        nus = [plural(len(objs), noun) for objs, noun in (
            (env.file_servers, "file server"), (env.object_stores, "object store"),
            (env.volume_groups, "volume group")) if objs]
        if nus:
            joined = ", ".join(nus[:-1]) + (" and " if len(nus) > 1 else "") + nus[-1]
            items.append(f"{joined} deliver unified storage services.")
        if env.databases:
            items.append(f"{plural(len(env.databases), 'database')} under NDB lifecycle "
                         "management.")
        if env.alerts:
            open_ = [a for a in env.alerts if not a.resolved]
            crit = sum(1 for a in open_ if (a.severity or "").upper() == "CRITICAL")
            items.append(f"{plural(len(open_), 'open alert')} ({fmt_count(crit)} critical) of "
                         f"{fmt_count(len(env.alerts))} returned by the alerts API.")
        if env.lcm_entities:
            pending = sum(1 for e in env.lcm_entities if e.update_available)
            items.append(f"{fmt_count(pending)} of {plural(len(env.lcm_entities), 'LCM component')}"
                         " have a newer version available.")
        return items

    def licensing(self) -> Chapter:
        from ntnxvision_core.analysis import analyze_feature_usage

        env = self.env
        ch = Chapter("licensing", "Licensing & Feature Usage", "licensing_lcm")
        ch.blocks.append(Paragraph(
            "Licenses installed on the estate and the licensable features found in use. "
            "\"Not collected\" means the feature's area was outside this collection, not that "
            "the feature is unused."
        ))
        lic = make_table(
            ["Product", "Edition", "Category", "Quantity", "Expiry", "Compliance"],
            [[x.product, x.edition, x.category, x.quantity, x.expiry, x.compliance]
             for x in env.licenses],
            "Installed licenses", self.src(env.licenses))
        if lic is None and not self.is_pe:
            reason = self.note("licenses")
            text = ("No license information was collected"
                    + (f": {clean(reason.split(':', 1)[-1].strip())}." if reason and ":" in reason
                       else "."))
            ch.add("Licenses", Paragraph(text, "note"))
        else:
            ch.add("Licenses", lic)
        ch.add("Feature Usage", make_table(
            ["Product", "Feature", "In Use", "Detail"],
            [[fu.product, fu.feature, fu.status, fu.detail]
             for fu in analyze_feature_usage(env)],
            "Feature usage mapped to licensing product"))
        return ch

    def assumptions(self) -> Chapter:
        env = self.env
        ch = Chapter("assumptions", "Assumptions & Constraints", "collection_notes")
        if self.is_pe:
            apis = "the Prism Element v2.0 API"
        else:
            apis = "the Prism Central v4 and v3 APIs"
            if any(a.get("api") == "era" for a in self.areas):
                apis += " and the NDB (Era) API"
        items = [
            f"Inventory was collected read-only via {apis}; no configuration changes were "
            "made.",
            f"Data reflects the environment state at the collection time ({collected_label(env)}).",
            "Feature availability depends on installed licenses and the platform version; "
            "areas not deployed are listed below rather than shown as empty sections.",
            "Capacities use binary units (GiB and TiB; 1 GiB = 1,073,741,824 bytes), "
            "timestamps are UTC, and N/A marks a value the API did not return.",
        ]
        if self.is_pe:
            items.append("Prism Element mode collects cluster infrastructure only (cluster, "
                         "hosts, storage containers, networks and VMs).")
        if env.is_combined:
            items.append(
                f"This document combines {fmt_count(len(env.source_environments))} "
                "independently managed Prism Centrals. Every table attributes its rows to "
                "the Prism Central they came from; object names can repeat across them."
            )
        items += [clean(w) for w in env.warnings]
        ch.blocks.append(Bullets(items))
        skipped = [a for a in self.areas if a.get("status") == "skipped"]
        failed = [a for a in self.areas if a.get("status") in ("failed", "cancelled")]
        if skipped:
            ch.blocks.append(Bullets(
                [f"{clean(a.get('label'))}: {clean(a.get('detail') or 'Not available')}"
                 for a in skipped],
                intro="Areas not present or not deployed in this environment:"))
        if failed:
            ch.blocks.append(Bullets(
                [f"{clean(a.get('label'))}: {clean(a.get('detail') or 'Failed')}"
                 for a in failed],
                intro="Areas that could not be fully collected:"))
        return ch

    def prism_central(self) -> Chapter | None:
        env = self.env
        if self.is_pe:
            return None
        if env.is_combined:
            ch = Chapter("prism_central", "Prism Centrals: Management & Sizing",
                         "clusters_hosts")
            ch.blocks.append(Paragraph(
                f"This document covers {fmt_count(len(env.source_environments))} "
                "independently managed Prism Central instances, inventoried in a single run "
                "and combined into one report. Each management plane is recorded below; the "
                "remaining sections present the combined estate, with every table "
                "attributing its rows to the Prism Central they came from."
            ))
            ch.blocks.append(make_table(
                ["Prism Central", "Version", "Deployment", "PC nodes", "Clusters", "Hosts",
                 "VMs"],
                [[s.prism_central.host or "unknown",
                  s.prism_central.version or s.pc_config.version, deployment(s.pc_config),
                  s.pc_config.node_count, s.summary.get("clusters"), s.summary.get("hosts"),
                  s.summary.get("vms")] for s in env.source_environments],
                "Source Prism Centrals combined into this report") or Paragraph(""))
            return ch
        cfg = env.pc_config
        ch = Chapter("prism_central", "Prism Central: Management & Sizing", "clusters_hosts")
        ch.blocks.append(Paragraph(
            "Prism Central is the management plane for this environment. The table below "
            "records its version, sizing, and the number of clusters it manages."
        ))
        ch.blocks.append(make_table(
            ["Property", "Value"],
            [["Version", cfg.version or env.prism_central.version],
             ["Deployment", deployment(cfg)],
             ["PC nodes", fmt_count(cfg.node_count) if cfg.node_count else None],
             ["Registered clusters", fmt_count(cfg.registered_cluster_count)
              if cfg.registered_cluster_count else None],
             ["Total vCPU", fmt_count(cfg.num_vcpus) if cfg.num_vcpus else None],
             ["Total memory (GiB)", f"{gib(cfg.memory_bytes):,.1f}" if cfg.memory_bytes
              else None],
             ["NCC version", cfg.ncc_version],
             ["PC VM(s)", ", ".join(cfg.pc_vm_names)],
             ["IP address(es)", ", ".join(cfg.ip_addresses)]],
            "Prism Central management-plane configuration") or Paragraph(""))
        return ch

    def logical_design(self) -> Chapter:
        env = self.env
        ch = Chapter("logical_design", "Logical Design", "clusters_hosts")
        manager = ("Prism Element" if self.is_pe
                   else "the contributing Prism Centrals" if env.is_combined
                   else "Prism Central")
        ch.blocks.append(Paragraph(
            f"The environment is managed centrally through {manager}, which registers and "
            "orchestrates the clusters below."
            + ("" if self.is_pe else " Workloads are organised using categories and projects, "
               "protected by protection policies, and secured by Flow policies.")
        ))
        topo = diagrams.management_topology(env)
        if topo:
            ch.blocks.append(Figure(topo, "Management topology: " + (
                "Prism Element and its cluster." if self.is_pe else
                "the contributing Prism Centrals and their clusters." if env.is_combined else
                "Prism Central and its registered clusters.")))
        ch.add("Clusters Overview", make_table(
            ["Cluster", "AOS", "Hypervisor", "Nodes", "RF", "Encryption"],
            [[c.name, c.aos_version, ", ".join(c.hypervisor_types), c.num_nodes,
              c.redundancy_factor, "Yes" if c.encryption_enabled else "No"]
             for c in env.clusters],
            "Registered clusters", self.src(env.clusters)))
        return ch

    def physical_design(self) -> Chapter:
        env = self.env
        ch = Chapter("physical_design", "Physical Design", "clusters_hosts")
        figs: list[Block] = []
        drawn = 0
        for c in env.clusters:
            if drawn >= MAX_CLUSTER_FIGURES:
                break
            d = diagrams.cluster_compute(env, c)
            if d:
                drawn += 1
                figs.append(Figure(d, f"Cluster {c.name}: node composition and capacity."))
        if len(env.clusters) > MAX_CLUSTER_FIGURES:
            figs.append(Paragraph(
                f"Compute diagrams are shown for the first {MAX_CLUSTER_FIGURES} of "
                f"{plural(len(env.clusters), 'cluster')}; the hosts table lists every node.",
                "note"))
        ch.add("Nodes / Hosts", make_table(
            ["Host", "Cluster", "Hypervisor", "CPU Model", "Cores", "Memory (GiB)", "VMs",
             "GPUs"],
            [[h.name, self.cluster(h), h.hypervisor_type, h.cpu_model, h.num_cpu_cores,
              gib(h.memory_bytes), h.num_vms, ", ".join(h.gpu_list)] for h in env.hosts],
            "Physical hosts", self.src(env.hosts), prune=("GPUs",)), *figs)
        cap = diagrams.storage_capacity_chart(env)
        shown = len([c for c in env.storage_containers if c.max_capacity_bytes][:8])
        total = len([c for c in env.storage_containers if c.max_capacity_bytes])
        ch.add("Storage Containers", make_table(
            ["Container", "Cluster", "RF", "Compression", "Dedup", "EC-X", "Max (GiB)",
             "Used (GiB)"],
            [[s.name, self.cluster(s), s.replication_factor,
              "On" if s.compression_enabled else "Off", _switch(s.deduplication),
              _switch(s.erasure_coding), gib(s.max_capacity_bytes), gib(s.used_bytes)]
             for s in env.storage_containers],
            "Storage containers and data-reduction settings", self.src(env.storage_containers)),
            Figure(cap, "Storage container capacity: used and free"
                   + (f" (first {shown} of {total} containers)." if total > shown else "."))
            if cap else None)
        return ch

    def workloads(self) -> Chapter:
        env = self.env
        ch = Chapter("workloads", "Workloads", "workloads_storage")
        if env.vms:
            resolve = diagrams._cluster_resolver(env)
            labels = diagrams._cluster_labels(env)
            per: dict[int, list[Any]] = {}
            for vm in env.vms:
                c = resolve(vm)
                if c is not None:
                    per.setdefault(id(c), []).append(vm)
            rows, clusters = [], []
            for c in env.clusters:
                vms = per.get(id(c), [])
                if not vms:
                    continue
                clusters.append(c)
                rows.append([
                    labels[id(c)], len(vms),
                    sum(1 for v in vms if power_label(v.power_state) == "On"),
                    sum(v.vcpu_total or 0 for v in vms),
                    gib(sum(v.memory_bytes or 0 for v in vms)),
                    gib(sum(v.total_disk_bytes or 0 for v in vms)),
                ])
            on = sum(1 for v in env.vms if power_label(v.power_state) == "On")
            lead = Paragraph(
                f"{plural(len(env.vms), 'user virtual machine')}, {fmt_count(on)} powered on, "
                f"with {fmt_count(sum(v.vcpu_total or 0 for v in env.vms))} vCPU and "
                f"{size_label(sum(v.memory_bytes or 0 for v in env.vms))} of memory "
                "allocated. Nutanix system VMs (CVMs and Prism Central) are listed "
                "separately. The Excel workbook lists every VM.")
            charts = [f for f in (
                _fig(diagrams.vm_power_state_chart(env), "VM power state distribution."),
                _fig(diagrams.vms_per_cluster_chart(env), "Virtual machines per cluster."),
            ) if f]
            ch.add("Virtual Machine Distribution", lead, make_table(
                ["Cluster", "VMs", "Powered On", "vCPU", "Memory (GiB)", "Provisioned (GiB)"],
                rows, "User virtual machines per cluster", self.src(clusters)),
                FigureRow(charts) if charts else None)
        ch.add("Nutanix System VMs", make_table(
            ["Name", "Type", "Power", "Cluster", "vCPU", "Memory (GiB)"],
            [[v.name, v.vm_type, power_label(v.power_state), self.cluster(v), v.vcpu_total,
              gib(v.memory_bytes)] for v in env.system_vms],
            "Nutanix system VMs (CVMs and Prism Central)", self.src(env.system_vms)))
        return ch

    def catalog(self) -> Chapter:
        env = self.env
        ch = Chapter("catalog", "Catalog: Templates, Images & OVAs", "workloads_storage")
        ch.add("VM Templates", make_table(
            ["Template", "Guest OS", "vCPU", "Memory (GiB)", "Active Version", "Versions"],
            [[t.name, t.guest_os, t.num_vcpus, gib(t.memory_bytes), t.active_version,
              t.version_count] for t in env.templates],
            "VM templates", self.src(env.templates)))
        ch.add("Images", make_table(
            ["Image", "Type", "Size (GiB)", "Architecture", "Clusters", "State"],
            [[i.name, i.image_type, gib(i.size_bytes), i.architecture,
              ", ".join(i.cluster_names), i.state] for i in env.images],
            "Images", self.src(env.images)))
        ch.add("OVAs", make_table(
            ["OVA", "Size (GiB)", "Disks", "Cluster"],
            [[o.name, gib(o.size_bytes), o.disk_count, self.cluster(o)] for o in env.ovas],
            "OVAs", self.src(env.ovas)))
        return ch

    def storage_services(self) -> Chapter:
        env = self.env
        ch = Chapter("nus", "Unified Storage Services (NUS)", "platform_services")
        ch.add("Files", make_table(
            ["File Server", "Cluster", "Version", "FSVMs", "Size (GiB)", "Shares"],
            [[f.name, self.cluster(f), f.version, f.num_fsvm, gib(f.size_bytes), f.share_count]
             for f in env.file_servers],
            "Nutanix Files servers", self.src(env.file_servers)),
            make_table(
            ["Share", "File Server", "Type", "Protocol", "Max Size (GiB)", "Used (GiB)"],
            [[s.name, s.file_server_name, s.share_type, s.protocol, gib(s.max_size_bytes),
              gib(s.used_bytes)] for s in env.file_shares],
            "File shares", self.src(env.file_shares)))
        ch.add("Objects", make_table(
            ["Object Store", "Cluster", "Workers", "Capacity (GiB)", "Buckets", "State"],
            [[o.name, self.cluster(o), o.num_worker_nodes, gib(o.total_capacity_bytes),
              o.bucket_count, o.state] for o in env.object_stores],
            "Nutanix Objects stores", self.src(env.object_stores)),
            make_table(
            ["Bucket", "Object Store", "Versioning", "WORM", "Objects", "Used (GiB)"],
            [[b.name, b.object_store_name, yes_no(b.versioning), yes_no(b.worm_enabled),
              b.object_count, gib(b.used_bytes)] for b in env.buckets],
            "Buckets", self.src(env.buckets)))
        ch.add("Volumes", make_table(
            ["Volume Group", "Cluster", "Disks", "Capacity (GiB)", "Shared"],
            [[v.name, self.cluster(v), v.disk_count, gib(v.total_capacity_bytes),
              yes_no(v.is_shared)] for v in env.volume_groups],
            "Volume groups", self.src(env.volume_groups)))
        return ch

    def networking(self) -> Chapter:
        env = self.env
        ch = Chapter("networking", "Networking", "security_networking")
        figs: list[Block] = []
        drawn = 0
        for c in env.clusters:
            if drawn >= MAX_CLUSTER_FIGURES:
                break
            d = diagrams.network_topology(env, c)
            if d:
                drawn += 1
                figs.append(Figure(d, f"Cluster {c.name}: virtual switch and VLAN layout."))
        ch.add("Subnets", make_table(
            ["Subnet", "Cluster", "Type", "VLAN", "vSwitch", "IP Prefix", "VPC"],
            [[s.name, self.cluster(s), s.subnet_type, s.vlan_id, s.virtual_switch, s.ip_prefix,
              s.vpc_name] for s in env.subnets],
            "Subnets", self.src(env.subnets), prune=("VPC", "vSwitch", "IP Prefix")), *figs)
        vpc_fig = diagrams.vpc_topology(env)
        ch.add("Virtual Networking (VPCs)", make_table(
            ["VPC", "Subnets", "External Subnets", "Routable Prefixes", "Routes",
             "Routing Policies"],
            [[v.name, ", ".join(v.subnet_names), ", ".join(v.external_subnets),
              ", ".join(v.externally_routable_prefixes), v.route_count,
              v.routing_policy_count] for v in env.vpcs],
            "Flow virtual networking VPCs", self.src(env.vpcs)),
            Figure(vpc_fig, "Flow Virtual Networking: VPCs with overlay and external subnets.")
            if vpc_fig and env.vpcs else None)
        return ch

    def data_protection(self) -> Chapter:
        env = self.env
        ch = Chapter("data_protection", "Data Protection & Disaster Recovery",
                     "data_protection")
        if not (env.protection_policies or env.recovery_plans):
            return ch
        ch.blocks.append(Paragraph(
            "Data protection is delivered through protection policies (snapshot and "
            "replication schedules) and recovery plans (orchestrated failover). The tables "
            "below document the configured strategy."
        ))
        rep = diagrams.replication_topology(env)
        if rep:
            ch.blocks.append(Figure(rep, "Replication topology: protection policy flows "
                                         "between availability zones."))
        ch.add("Protection Policies", make_table(
            ["Policy", "RPO", "Replication", "Local Retention", "Remote Retention",
             "Source AZ", "Target AZ"],
            [[p.name, rpo_label(p.rpo_seconds), p.replication_type, p.local_retention,
              p.remote_retention, p.source_az, p.target_az] for p in env.protection_policies],
            "Protection policies", self.src(env.protection_policies),
            prune=("Local Retention", "Remote Retention")))
        ch.add("Recovery Plans", make_table(
            ["Plan", "Stages", "Entities", "Primary", "Recovery"],
            [[r.name, r.stage_count, r.entities_count, r.primary_location, r.recovery_location]
             for r in env.recovery_plans],
            "Recovery plans", self.src(env.recovery_plans)))
        ch.add("Availability Zones", make_table(
            ["Availability Zone", "Type", "Region", "Connection"],
            [[a.name, a.az_type, a.region, a.connection_status]
             for a in env.availability_zones],
            "Availability zones", self.src(env.availability_zones)))
        return ch

    def security(self) -> Chapter:
        env = self.env
        ch = Chapter("security", "Security Posture (Flow)", "security_networking")
        ch.add("Security Policies", make_table(
            ["Policy", "Type", "State", "Inbound", "Outbound", "Secured Group"],
            [[p.name, p.policy_type, p.state, p.inbound_rule_count, p.outbound_rule_count,
              p.secured_group] for p in env.security_policies],
            "Flow network security policies", self.src(env.security_policies)))
        ch.add("Categories", make_table(
            ["Category", "Value", "Description"],
            [[c.name, c.value, c.description] for c in env.categories],
            "Categories", self.src(env.categories)))
        return ch

    def databases(self) -> Chapter:
        env = self.env
        ch = Chapter("ndb", "Database Services (NDB)", "platform_services")
        ch.add("Databases", make_table(
            ["Database", "Engine", "Type", "Status", "Time Machine", "SLA"],
            [[d.name, d.db_engine, d.db_type, d.status, d.time_machine_name, d.sla_name]
             for d in env.databases],
            "NDB-managed databases", self.src(env.databases)))
        ch.add("Database Servers", make_table(
            ["Server", "Engine", "Status", "Cluster", "Cores", "Memory (GB)"],
            [[s.name, s.db_engine, s.status, self.cluster(s), s.vm_cores, s.memory_gb]
             for s in env.database_servers],
            "Database servers", self.src(env.database_servers)))
        ch.add("Time Machines", make_table(
            ["Time Machine", "Database", "SLA", "Status", "Schedule", "Clones"],
            [[t.name, t.database_name, t.sla_name, t.status, t.schedule, t.clone_count]
             for t in env.time_machines],
            "Time machines", self.src(env.time_machines)))
        return ch

    def identity(self) -> Chapter:
        env = self.env
        ch = Chapter("identity", "Identity & Access Management", "security_networking")
        ch.add("Users", make_table(
            ["User", "Display Name", "Type", "Directory", "State"],
            [[u.username, u.display_name, u.user_type, u.directory, u.state]
             for u in env.users],
            "Users", self.src(env.users)))
        ch.add("Roles", make_table(
            ["Role", "Description", "System", "Permissions"],
            [[r.name, r.description, yes_no(r.is_system), r.permission_count]
             for r in env.roles],
            "Roles", self.src(env.roles)))
        ch.add("Projects", make_table(
            ["Project", "Description", "Users"],
            [[p.name, p.description, p.user_count] for p in env.projects],
            "Projects", self.src(env.projects)))
        return ch

    def automation(self) -> Chapter:
        env = self.env
        ch = Chapter("automation", "Automation & Reporting (NCM)", "platform_services")
        ch.add("Playbooks (X-Play)", make_table(
            ["Playbook", "Trigger", "Actions", "Enabled", "Executions"],
            [[p.name, p.trigger_type, p.action_count, yes_no(p.is_enabled), p.execution_count]
             for p in env.playbooks],
            "X-Play playbooks", self.src(env.playbooks), prune=("Executions",)))
        ch.add("NCM Reports", make_table(
            ["Report", "Type", "Schedule", "Last Run (UTC)"],
            [[r.name, r.report_type, r.schedule, r.last_run] for r in env.ncm_reports],
            "NCM reports", self.src(env.ncm_reports), prune=("Schedule",)))
        return ch

    def health(self) -> Chapter:
        env = self.env
        ch = Chapter("health", "Health & Alerts", "clusters_hosts")
        if not env.alerts:
            return ch
        rank = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}
        alerts = sorted(env.alerts, key=lambda a: (bool(a.resolved),
                                                   rank.get((a.severity or "").upper(), 3),
                                                   a.created_at or ""))
        open_ = [a for a in env.alerts if not a.resolved]
        by_sev = {s: sum(1 for a in open_ if (a.severity or "").upper() == s)
                  for s in ("CRITICAL", "WARNING", "INFO")}
        ch.blocks.append(Paragraph(
            f"The alerts API returned {plural(len(env.alerts), 'alert')}: "
            f"{fmt_count(len(open_))} open ({fmt_count(by_sev['CRITICAL'])} critical, "
            f"{fmt_count(by_sev['WARNING'])} warning, {fmt_count(by_sev['INFO'])} info) and "
            f"{fmt_count(len(env.alerts) - len(open_))} resolved. Open alerts are listed "
            "first, most severe first."
        ))
        chart = diagrams.alert_severity_chart(env)
        if chart:
            ch.blocks.append(Figure(chart, "Open alerts by severity." if open_
                                    else "Alerts by severity (none open)."))
        ch.add("Alerts", make_table(
            ["Severity", "Title", "Entity", "Cluster", "State", "Created (UTC)"],
            [[(a.severity or "").upper() or None, a.title, a.entity_name, self.cluster(a),
              "Resolved" if a.resolved else "Open", a.created_at] for a in alerts],
            "Health alerts", self.src(alerts)))
        return ch

    def lifecycle(self) -> Chapter:
        env = self.env
        ch = Chapter("lcm", "Platform Lifecycle (LCM)", "licensing_lcm")
        ents = env.lcm_entities
        if not ents:
            return ch
        pending = sum(1 for e in ents if e.update_available)
        unknown = sum(1 for e in ents if e.update_available is None)
        ch.blocks.append(Paragraph(
            "Life Cycle Manager (LCM) inventories the firmware and software running on "
            "this platform (AOS, the hypervisor, Prism Central services and the component "
            "firmware beneath them) and is the mechanism by which they are upgraded. The "
            "table below records each managed component, the version currently running, "
            "and whether LCM offers a newer one."
        ))
        currency = (f"Of the {fmt_count(len(ents))} component(s) inventoried, "
                    f"{fmt_count(pending)} {'has' if pending == 1 else 'have'} a newer version "
                    "available")
        if unknown:
            currency += (f"; for {fmt_count(unknown)} component(s) LCM did not report an "
                         "upgrade target, so their currency is recorded as unknown rather "
                         "than up to date")
        ch.blocks.append(Paragraph(f"{currency}."))
        ch.blocks.append(make_table(
            ["Component", "Class", "Cluster", "Current Version", "Update Available",
             "Available Versions"],
            [[e.name, e.entity_class, self.cluster(e), e.current_version,
              update_flag(e.update_available), versions(e.available_versions)]
             for e in ents],
            "LCM component inventory and available updates; upgrade targets beyond the "
            "three newest are summarised as a count (the Excel workbook lists them in full)",
            self.src(ents)) or Paragraph(""))
        return ch

    def appendix(self) -> Chapter:
        env = self.env
        ch = Chapter("appendix", "Collection Coverage & API Routing", "collection_notes",
                     appendix=True)
        rows, srcs = [], []
        for a in self.areas:
            label = clean(a.get("label") or a.get("id"))
            if env.is_combined and " · " in label:
                host, label = label.split(" · ", 1)
                srcs.append(host)
            elif env.is_combined:
                srcs.append(str(a.get("id", "")).split(": ", 1)[0])
            api = a.get("api")
            status = str(a.get("status"))
            detail = a.get("detail") or ("No notes" if status == "collected" else None)
            rows.append([label, _OUTCOME.get(status, status),
                         _API_LABEL.get(api, api) if api else None, detail])
        ch.blocks.append(Paragraph(
            "How each collection area finished and which API served it, as shown on the "
            "app's Results screen. v3 REST APIs are retired by Nutanix in Q4 2026; the "
            "routing table states what happens to each area after that sunset."
        ))
        ch.add("Collection Coverage", make_table(
            ["Area", "Outcome", "API", "Detail"], rows, "Collection outcome per area",
            srcs if env.is_combined else None))
        r_rows, r_src = [], []
        for r in self.routing:
            areas = str(r["areas"])
            if r.get("source"):
                areas = areas.removeprefix(f"{r['source']}: ")
                r_src.append(str(r["source"]))
            r_rows.append([areas, _API_LABEL.get(r["api"], r["api"]), r["reason"],
                           r["after_sunset"]["label"]])
        ch.add("API Routing and Compatibility", Paragraph(self.compat_sentence()),
               make_table(["Areas", "API", "Reason", "After Q4 2026 v3 sunset"], r_rows,
                          "API routing per collection area",
                          r_src if env.is_combined else None),
               Bullets([clean(w) for w in env.warnings], intro="Compatibility warnings:")
               if env.warnings else None)
        return ch


def _switch(value: Any) -> str:
    from ntnxvision_core.exporters.theme import on_off

    return on_off(value)


def _fig(d: Diagram | None, caption: str) -> Figure | None:
    return Figure(d, caption) if d else None


#: Chapter builders in document order.
_ORDER: tuple[str, ...] = (
    "executive_summary", "licensing", "assumptions", "prism_central", "logical_design",
    "physical_design", "workloads", "catalog", "storage_services", "networking",
    "data_protection", "security", "databases", "identity", "automation", "health",
    "lifecycle", "appendix",
)


def build_report(env: Environment, opts: Any, eyebrow: str) -> Report:
    """The full report for `env`, filtered to `opts.sections`, numbered."""
    b = _Builder(env, opts)
    chapters: list[Chapter] = []
    for name in _ORDER:
        build: Callable[[], Chapter | None] = getattr(b, name)
        ch = build()
        if ch is None or ch.empty or not opts.includes(ch.group):
            continue
        ch.blocks = [blk for blk in ch.blocks
                     if not (isinstance(blk, Paragraph) and not blk.text)]
        chapters.append(ch)
    _number(chapters)
    if env.is_combined:
        scope = f"{fmt_count(len(env.source_environments))} Prism Centrals (combined)"
    else:
        scope = env.prism_central.host or NA
    return Report(
        cover=b.cover(eyebrow),
        chapters=chapters,
        title=f"Nutanix Platform Architecture & Inventory Report: {scope}",
        subject=(f"Prepared for {opts.prepared_for}" if opts.prepared_for
                 else "Nutanix environment inventory and architecture documentation"),
        keywords="Nutanix; Prism Central; architecture; inventory; NTNXVision",
        running_title="Nutanix Platform Architecture & Inventory Report",
        scope=scope,
        prepared_for=opts.prepared_for,
        author=opts.author,
    )


def _number(chapters: list[Chapter]) -> None:
    """Assign chapter, section, table and figure numbers so skipped chapters leave no
    gaps; both renderers print exactly these."""
    n = 0
    appendix = 0
    for ch in chapters:
        if ch.appendix:
            ch.number = chr(ord("A") + appendix)
            appendix += 1
        else:
            n += 1
            ch.number = str(n)
        tables = figures = 0

        def visit(blocks: list[Block], chapter: Chapter = ch) -> None:
            nonlocal tables, figures
            for blk in blocks:
                if isinstance(blk, Table):
                    tables += 1
                    blk.label = f"Table {chapter.number}-{tables}."
                elif isinstance(blk, Figure):
                    figures += 1
                    blk.label = f"Figure {chapter.number}-{figures}."
                elif isinstance(blk, FigureRow):
                    visit(list(blk.figures))

        visit(ch.blocks)
        for i, s in enumerate(ch.sections, start=1):
            s.number = f"{ch.number}.{i}"
            visit(s.blocks)


__all__ = [
    "Block", "Bullets", "Chapter", "Cover", "Figure", "FigureRow", "FigureStrip",
    "Paragraph", "Report", "Section", "Status", "StripItem", "Table", "build_report",
    "cell_text", "make_table", "update_flag", "versions",
]
