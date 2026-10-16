"""Comparison PDF: what changed between two snapshots, as a short landscape report.

Rendered by the inventory PDF's own machinery (`pdf._Pdf` and `PdfExporter`'s block
renderers) from the shared `content` model, so it looks exactly like the inventory
report and the comparison workbook: Figtree / Montserrat Light / JetBrains Mono, the iris
cover band and heading marker, ruled tables with the iris header rule, comma grouping, N/A
in ink-3, and colour only on the change kind (Added ok, Removed error, Changed warn) as a
small square glyph beside an ink label.

Structure: a cover (snapshots, sources, dates), then

1. Summary: the headline figure strip the Compare screen shows, and a per-type count table;
2. Changes: one section per object type, with the grouped rows the screen shows (runs of
   same-stem added / removed objects collapse into one row, the same field change on
   several objects likewise), field-level Before and After for changed rows;

and, when an area was not collected by both runs, a note saying it was not compared. The
Cluster column leads every table that has one (the house rule); the Excel workbook remains
the full, ungrouped list.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fpdf.enums import PageMode
from fpdf.prefs import ViewerPreferences

from ntnxvision_core.exporters import theme as TH
from ntnxvision_core.exporters.content import (
    Block,
    Bullets,
    Chapter,
    Cover,
    FigureStrip,
    Paragraph,
    Report,
    Status,
    StripItem,
    Table,
    _number,
    make_table,
)
from ntnxvision_core.exporters.pdf import PdfExporter, _Pdf
from ntnxvision_core.exporters.tables import COMPARE_TYPES, SPECS
from ntnxvision_core.models import Environment
from ntnxvision_core.numfmt import NA, fmt_count

_KIND_LABEL = {"added": "Added", "removed": "Removed", "changed": "Changed"}
_KIND_TONE = {"added": "ok", "removed": "error", "changed": "warn"}


def _value(v: Any) -> Any:
    return None if v in (None, "", NA) else v


def _source(env: Environment) -> str:
    if env.is_combined:
        return f"{fmt_count(len(env.source_environments))} Prism Centrals (combined)"
    return env.prism_central.host or NA


def _type_table(type_id: str, rows: list[dict[str, Any]]) -> Table | None:
    """One object type's grouped changes. Cluster first wherever the location is a
    cluster; the Clusters type's object *is* the cluster."""
    spec = SPECS[type_id]
    kind = [Status(_KIND_LABEL[r["kind"]], _KIND_TONE[r["kind"]]) for r in rows]
    count = [r.get("count") or 1 for r in rows]
    body = [[r["object"], _value(r["field"]), _value(r["before"]), _value(r["after"]),
             _value(r["detail"])] for r in rows]
    caption = f"{spec.label}: what changed"
    if type_id == "clusters":
        headers = ["Cluster", "Change", "Field", "Before", "After", "Detail"]
        data = [[b[0], k, *b[1:]] for b, k in zip(body, kind, strict=True)]
    elif spec.cluster_col is not None and spec.cluster_col.key == "cluster":
        headers = ["Cluster", "Change", spec.singular, "Objects", "Field", "Before", "After",
                   "Detail"]
        data = [[_value(r["where"]), k, b[0], n, *b[1:]]
                for r, k, b, n in zip(rows, kind, body, count, strict=True)]
    else:
        headers = ["Change", spec.singular, "Objects", "Location", "Field", "Before", "After",
                   "Detail"]
        data = [[k, b[0], n, _value(r["where"]), *b[1:]]
                for r, k, b, n in zip(rows, kind, body, count, strict=True)]
    return make_table(headers, data, caption,
                      prune=("Field", "Before", "After", "Detail", "Location"))


def build_comparison_report(base: Environment, cur: Environment, base_name: str,
                            cur_name: str) -> Report:
    from ntnxvision_core.compare import _by_type, collected, compare, group_changes, raw_changes

    raw = raw_changes(base, cur)
    result = compare(base, cur)
    s = result["summary"]
    days = result["days"]

    summary = Chapter("summary", "Summary", "summary")
    summary.blocks.append(Paragraph(
        f"This report compares the snapshot {base_name} (collected "
        f"{TH.collected_label(base)}) with {cur_name} (collected {TH.collected_label(cur)})"
        + f", {days:,.1f} days apart. "
        f"{TH.plural(s['total'], 'object')} changed: {fmt_count(s['added'])} added, "
        f"{fmt_count(s['removed'])} removed and {fmt_count(s['changed'])} with changed "
        "fields."))
    strip = []
    for i, f in enumerate(result["figures"]):
        if i == 0:  # the screen's long qualifier overflows a 48 mm cell: compact it
            f = {**f, "qualifier": f"+{fmt_count(s['added'])} · −{fmt_count(s['removed'])}"
                                   f" · {fmt_count(s['changed'])} changed"}
        shown = f["value"] if isinstance(f["value"], str) else fmt_count(f["value"])
        unit = f" {f['unit']}" if f.get("unit") else ""
        strip.append(StripItem(f["label"], f"{shown}{unit}", TH.clean(f.get("qualifier")),
                               f.get("qualifier_tone")))
    summary.blocks.append(FigureStrip(strip))
    summary.add("Changes by Object Type", make_table(
        ["Object type", "Added", "Removed", "Changed", "Total"], _by_type(raw),
        "Objects added, removed and changed, per object type"),
        Paragraph("Counts are per object: an object with two changed fields counts once. "
                  "The Changes chapter groups runs of similar objects into one row; the "
                  "Excel export lists every object and field.", "note"))
    skipped = [SPECS[t].label for t in COMPARE_TYPES
               if not (collected(base, SPECS[t]) and collected(cur, SPECS[t]))]
    if skipped:
        summary.add("Not Compared", Bullets(
            skipped, intro="Areas one of the two runs did not collect, so changes there "
                           "cannot be told apart from collection scope:"))

    changes = Chapter("changes", "Changes", "changes")
    if not raw:
        changes.blocks.append(Paragraph("No changes between the two snapshots."))
    for type_id in COMPARE_TYPES:
        rows = group_changes([c for c in raw if c["type"] == type_id])
        if rows:
            blocks: list[Block | None] = [_type_table(type_id, rows)]
            changes.add(SPECS[type_id].label, *blocks)

    chapters = [summary, changes]
    _number(chapters)
    scope = f"{base_name} to {cur_name}"
    meta = [
        ("Baseline", f"{base_name}, collected {TH.collected_label(base)}"),
        ("Baseline source", _source(base)),
        ("Current", f"{cur_name}, collected {TH.collected_label(cur)}"),
        ("Current source", _source(cur)),
        ("Time between", f"{days:,.1f} days"),
        ("Changes", f"{fmt_count(s['total'])} objects: {fmt_count(s['added'])} added, "
                    f"{fmt_count(s['removed'])} removed, {fmt_count(s['changed'])} changed"),
        ("Report date", TH.report_date()),
        ("Generated by", f"NTNXVision {cur.tool_version}"),
    ]
    return Report(
        cover=Cover(eyebrow="Snapshot comparison", title="What Changed",
                    subtitle=scope, meta=meta,
                    note=("Confidential. Generated from read-only inventory snapshots; no "
                          "configuration was changed.")),
        chapters=chapters,
        title=f"Snapshot comparison: {scope}",
        subject="What changed between two NTNXVision inventory snapshots",
        keywords="Nutanix; inventory; comparison; NTNXVision",
        running_title="Snapshot Comparison",
        scope=scope,
    )


class ComparisonPdfExporter:
    #: Pages in the last export.
    page_count: int = 0

    def export(self, base: Environment, cur: Environment, base_name: str, cur_name: str,
               path: str | Path) -> Path:
        report = build_comparison_report(base, cur, base_name, cur_name)
        pdf = _Pdf(report)
        pdf.set_title(report.title)
        pdf.set_subject(report.subject)
        pdf.set_keywords(report.keywords)
        pdf.set_creator(f"NTNXVision {cur.tool_version}")
        pdf.set_author("NTNXVision")
        pdf.set_lang("en-US")
        pdf.page_mode = PageMode.USE_OUTLINES
        pdf.viewer_preferences = ViewerPreferences(display_doc_title=True)
        renderer = PdfExporter()
        renderer._cover(pdf, report)
        pdf.add_page()
        pdf.fresh_page = True
        for chapter in report.chapters:
            renderer._chapter(pdf, chapter)
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        pdf.output(str(out))
        self.page_count = len(pdf.pages)
        return out


__all__ = ["ComparisonPdfExporter", "build_comparison_report"]
