"""Excel exporter (openpyxl).

Produces an RVTools-style workbook: an Overview cover sheet plus one sheet per object type,
each with a frozen, styled header row, an auto-filter and print settings. Byte-valued
fields are rendered in GiB. Numeric quantity cells stay numbers with a `#,##0` /
`#,##0.00` format (comma grouping); identifier columns (VLAN, see `numfmt.PLAIN_HEADERS`)
are left unformatted. Timestamps are real Excel dates (UTC) formatted ``yyyy-mm-dd hh:mm``.
A value the API did not return reads "N/A". A sheet is omitted when its object type has
zero rows (feature not deployed or not licensed); the Overview's inventory list, Feature
Usage and Collection Notes still say so, so the workbook stays a place to check "is X in
use" without a noise sheet per absent feature.

Theme: the light Runway treatment shared with the PDF and Word reports. A very light
header fill with an iris rule beneath it, dark ink text, status values (alert severity,
licence compliance, LCM update, collection outcome) in their status colour, and an iris
band and tab on the Overview. No zebra striping.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.hyperlink import Hyperlink
from openpyxl.worksheet.worksheet import Worksheet

from ntnxvision_core.exporters import tables as T
from ntnxvision_core.exporters import theme as TH
from ntnxvision_core.exporters.sourcing import source_hosts as _source_hosts
from ntnxvision_core.exporters.sourcing import sources as _sources
from ntnxvision_core.exporters.tables import Ctx
from ntnxvision_core.models import Environment
from ntnxvision_core.numfmt import NA, PLAIN_HEADERS, excel_number_format, fmt_count

GIB = 1024**3
_FONT = TH.OFFICE_FONT

_HEADER_FILL = PatternFill("solid", fgColor=TH.HEADER_FILL)
_HEADER_FONT = Font(name=_FONT, size=10, bold=True, color=TH.INK)
_HEADER_BORDER = Border(bottom=Side(style="medium", color=TH.IRIS))
_BODY_FONT = Font(name=_FONT, size=10, color=TH.INK)
_NA_FONT = Font(name=_FONT, size=10, color=TH.INK_3)
_STATUS_FONT = {tone: Font(name=_FONT, size=10, color=hex_)
                for tone, hex_ in TH.STATUS_HEX.items()}
_RIGHT = Alignment(horizontal="right", vertical="center", indent=1)
_LEFT = Alignment(horizontal="left", vertical="center")
_TOP_LEFT = Alignment(horizontal="left", vertical="top", wrap_text=True)
_INDENT = Alignment(horizontal="left", vertical="center", indent=1)
_DATETIME = "yyyy-mm-dd hh:mm"
_DATE = "yyyy-mm-dd"

#: Every inventory sheet, in workbook order.
_SPECS = (
    # Phase 1: core infrastructure
    T.CLUSTERS, T.HOSTS, T.VMS, T.SYSTEM_VMS, T.STORAGE, T.SUBNETS,
    # PC catalog: templates / images / OVAs
    T.TEMPLATES, T.IMAGES, T.OVAS,
    # Phase 2: data protection / DR
    T.PROTECTION_POLICIES, T.RECOVERY_PLANS, T.AVAILABILITY_ZONES, T.REMOTE_SITES,
    # Phase 2: Health / Alerts, then LCM firmware / software currency
    T.ALERTS, T.LCM,
    # Phase 2: Flow
    T.SECURITY_POLICIES, T.CATEGORIES, T.SERVICE_GROUPS, T.ADDRESS_GROUPS, T.VPCS,
    T.FLOATING_IPS,
    # Phase 2: NUS
    T.FILE_SERVERS, T.FILE_SHARES, T.OBJECT_STORES, T.BUCKETS, T.VOLUME_GROUPS,
    # Phase 2: NDB
    T.DATABASES, T.DATABASE_SERVERS, T.TIME_MACHINES,
    # Phase 2: IAM / automation
    T.USERS, T.ROLES, T.PROJECTS, T.PLAYBOOKS, T.NCM_REPORTS,
    # Licensing
    T.LICENSES,
)


class ExcelExporter:
    #: Number of sheets written by the last export (for the "34 sheets" report detail).
    sheet_count: int = 0

    def export(self, env: Environment, path: str | Path, options: Any = None) -> Path:
        from ntnxvision_core.exporters import ExportOptions

        opts = ExportOptions.coerce(options)
        wb = Workbook()
        ctx = Ctx(env)
        overview = cast(Worksheet, wb.active)
        overview.title = "Overview"
        self._pc_config_sheet(wb, env)
        written: dict[str, str] = {}
        for spec in _SPECS:
            sheet = self._spec_sheet(wb, env, spec, ctx)
            if sheet:
                written[spec.type] = sheet
        self._feature_usage_sheet(wb, env)
        self._notes_sheet(wb, env)
        self._routing_sheet(wb, env)
        self._overview_sheet(overview, env, opts, written)
        self._properties(wb, env, opts)

        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        wb.save(out)
        self.sheet_count = len(wb.sheetnames)
        return out

    def _spec_sheet(self, wb: Workbook, env: Environment, spec: T.TableSpec,
                    ctx: Ctx) -> str | None:
        """One object-type sheet, from the column definitions the Inventory grid shares.

        The LCM sheet's `target_version` is deliberately not a column: despite the name it
        is the version LCM currently aims at (the running one unless an upgrade is
        staged), so it would only duplicate Current Version. It stays in the snapshot.
        """
        items = spec.items(env)
        headers = [c.header or "" for c in spec.excel_cols]
        rows = T.table_rows(spec, env, ctx)
        return self._table(wb, spec.sheet, headers, rows,
                           _sources(env, items) if spec.sources else None)

    # ─── sheets ────────────────────────────────────────────────────

    def _overview_sheet(self, ws: Worksheet, env: Environment, opts: Any,
                        written: dict[str, str]) -> None:
        """The workbook's cover: report facts, an inventory index linking every sheet, and
        the collection / API-sunset summary the app shows on Results."""
        from ntnxvision_core.exporters.content import run_areas
        from ntnxvision_core.runinfo import api_routing, compat_info, coverage, sunset_areas

        ws.sheet_view.showGridLines = False
        ws.sheet_properties.tabColor = TH.IRIS
        for col, width in zip("ABCD", (30, 16, 46, 30), strict=True):
            ws.column_dimensions[col].width = width
        band = PatternFill("solid", fgColor=TH.IRIS)
        for col in "ABCD":
            ws[f"{col}1"].fill = band
        ws.row_dimensions[1].height = 6

        is_pe = env.source_type == "prism_element"
        ws["A3"] = "INVENTORY WORKBOOK"
        ws["A3"].font = Font(name=_FONT, size=8.5, bold=True, color=TH.INK_3)
        ws["A4"] = "Nutanix Platform Architecture & Inventory Report"
        ws["A4"].font = Font(name=_FONT, size=18, color=TH.INK)
        ws.row_dimensions[4].height = 28
        if env.is_combined:
            subtitle = (f"{fmt_count(len(env.source_environments))} Prism Centrals combined: "
                        f"{', '.join(_source_hosts(env))}")
        elif is_pe:
            subtitle = f"Prism Element {env.prism_central.host or NA} · AOS " \
                       f"{env.prism_central.version or NA}"
        else:
            subtitle = f"Prism Central {env.prism_central.host or NA} · " \
                       f"{env.prism_central.version or NA}"
        ws["A5"] = subtitle
        ws["A5"].font = Font(name=_FONT, size=11, color=TH.INK_2)

        # A combined run has no single management plane: name every contributing PC
        # rather than reporting a synthetic host with an "unknown" version.
        if env.is_combined:
            source_rows: list[tuple[str, Any]] = [
                ("Prism Centrals", f"{fmt_count(len(env.source_environments))} (combined)"),
                ("Sources", ", ".join(_source_hosts(env))),
            ]
        elif is_pe:
            source_rows = [("Prism Element", env.prism_central.host or NA),
                           ("AOS Version", env.prism_central.version or NA)]
        else:
            source_rows = [("Prism Central", env.prism_central.host or NA),
                           ("PC Version", env.prism_central.version or NA)]
        info = compat_info(env)
        compat = ("Within the supported range" if info.get("supported")
                  else "Outside the tested range (collection proceeded)")
        areas = run_areas(env)
        cov = coverage(areas)
        details: list[tuple[str, Any]] = [
            *source_rows,
            ("Compatibility", f"{compat} ({info.get('range') or NA})"),
            ("Collection Mode", "Prism Element (v2.0 API)" if is_pe else
             "Combined Prism Central run" if env.is_combined else
             "Prism Central (v4 APIs, v3 where v4 is unavailable)"),
            ("Collected At (UTC)", TH.fmt_datetime(env.collected_at) or env.collected_at or NA),
            ("Report Date", TH.report_date()),
            ("Prepared For", opts.prepared_for or NA),
            ("Prepared By", opts.author or "NTNXVision"),
            ("Tool Version", f"NTNXVision {env.tool_version}"),
        ]
        row = self._section(ws, 7, "Report details")
        for label, value in details:
            ws.cell(row=row, column=1, value=label).font = Font(name=_FONT, size=10,
                                                                color=TH.INK_3)
            cell = ws.cell(row=row, column=2, value=value)
            cell.font = _NA_FONT if value == NA else _BODY_FONT
            cell.alignment = _LEFT
            ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=4)
            row += 1

        row = self._section(ws, row + 1, "Collection")
        summary = (f"{fmt_count(cov['collected'] + cov['with_notes'])} of "
                   f"{fmt_count(cov['total'])} areas collected"
                   + (f", {fmt_count(cov['with_notes'])} with notes" if cov["with_notes"] else "")
                   + (f", {fmt_count(cov['skipped'])} skipped (not deployed)"
                      if cov["skipped"] else "")
                   + (f", {fmt_count(cov['failed'])} failed" if cov["failed"] else ""))
        sunset = sunset_areas(api_routing(env, areas, limit=None))
        for label, value, tone in (
            ("Coverage", summary, "warn" if cov["failed"] else None),
            ("After the Q4 2026 v3 sunset",
             (f"{', '.join(sunset)}: stop collecting (see the API Routing sheet)"
              if sunset else "No area depends on a v3-only API"),
             "warn" if sunset else "ok"),
        ):
            lbl = ws.cell(row=row, column=1, value=label)
            lbl.alignment = _TOP_LEFT
            lbl.font = Font(name=_FONT, size=10, color=TH.INK_3)
            cell = ws.cell(row=row, column=2, value=value)
            cell.font = _STATUS_FONT[tone] if tone else _BODY_FONT
            cell.alignment = _TOP_LEFT
            ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=4)
            if len(str(value)) > 80:
                ws.row_dimensions[row].height = 15 * (len(str(value)) // 80 + 1)
            row += 1

        row = self._section(ws, row + 1, "Inventory")
        for ci, header in enumerate(("Object", "Count", "Sheet"), start=1):
            cell = ws.cell(row=row, column=ci, value=header)
            cell.font, cell.fill, cell.border = _HEADER_FONT, _HEADER_FILL, _HEADER_BORDER
            cell.alignment = _RIGHT if header == "Count" else _INDENT if ci == 3 else _LEFT
        row += 1
        notes = env.collection_notes or {}
        for spec in _SPECS:
            count = len(spec.items(env))
            sheet = written.get(spec.type)
            ws.cell(row=row, column=1, value=spec.label).font = _BODY_FONT
            c = ws.cell(row=row, column=2, value=count)
            c.font, c.number_format, c.alignment = _BODY_FONT, "#,##0", _RIGHT
            link = ws.cell(row=row, column=3)
            link.alignment = _INDENT
            if sheet:
                link.value = sheet
                link.hyperlink = Hyperlink(ref=link.coordinate, location=f"'{sheet}'!A1")
                link.font = Font(name=_FONT, size=10, color=TH.IRIS, underline="single")
            else:
                link.value = _absent(spec, notes, is_pe)
                link.font = _NA_FONT
            row += 1

        row += 1
        note = ws.cell(row=row, column=1, value=(
            "Timestamps are UTC. Capacities are binary GiB (1 GiB = 1,073,741,824 bytes). "
            "N/A marks a value the API did not return. Generated from a read-only "
            "inventory snapshot; no configuration was changed."))
        note.font = Font(name=_FONT, size=9, italic=True, color=TH.INK_3)
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)
        note.alignment = _TOP_LEFT
        ws.row_dimensions[row].height = 30
        ws.page_setup.orientation = "portrait"
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        if ws.sheet_properties.pageSetUpPr is not None:
            ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.print_area = f"A1:D{row}"

    @staticmethod
    def _section(ws: Worksheet, row: int, title: str) -> int:
        cell = ws.cell(row=row, column=1, value=title)
        cell.font = Font(name=_FONT, size=12, bold=True, color=TH.INK)
        for col in range(1, 5):
            ws.cell(row=row, column=col).border = Border(bottom=Side(style="thin",
                                                                     color=TH.RULE))
        return row + 1

    def _pc_config_sheet(self, wb: Workbook, env: Environment) -> None:
        if env.is_combined:
            self._source_pcs_sheet(wb, env)
            return
        if env.source_type == "prism_element":
            return  # no Prism Central in a direct Prism Element collection
        cfg = env.pc_config
        headers = ["Property", "Value"]
        rows: list[list[Any]] = [
            ["Version", cfg.version],
            ["Deployment", "Scale-out" if cfg.is_scale_out else "Single-node"
             if cfg.is_scale_out is not None else None],
            ["PC Nodes", cfg.node_count],
            ["Registered Clusters", cfg.registered_cluster_count],
            ["Total vCPU", cfg.num_vcpus],
            ["Total Memory (GiB)", _gib(cfg.memory_bytes)],
            ["NCC Version", cfg.ncc_version],
            ["PC VM(s)", ", ".join(cfg.pc_vm_names)],
            ["IP Address(es)", ", ".join(cfg.ip_addresses)],
        ]
        self._table(wb, "Prism Central", headers, rows)

    def _source_pcs_sheet(self, wb: Workbook, env: Environment) -> None:
        """One row per contributing Prism Central: the combined-run replacement for the
        single-PC property sheet, which would otherwise have to pick one PC's sizing and
        present it as the whole estate's.
        """
        headers = [
            "Prism Central", "Version", "Deployment", "PC Nodes", "Registered Clusters",
            "Total vCPU", "Total Memory (GiB)", "NCC Version", "IP Address(es)",
            "Clusters", "Hosts", "VMs",
        ]
        rows: list[list[Any]] = []
        for s in env.source_environments:
            cfg, counts = s.pc_config, s.summary
            rows.append([
                s.prism_central.host or "unknown",
                s.prism_central.version or cfg.version,
                "Scale-out" if cfg.is_scale_out else "Single-node"
                if cfg.is_scale_out is not None else None,
                cfg.node_count, cfg.registered_cluster_count, cfg.num_vcpus,
                _gib(cfg.memory_bytes), cfg.ncc_version, ", ".join(cfg.ip_addresses),
                counts.get("clusters"), counts.get("hosts"), counts.get("vms"),
            ])
        self._table(wb, "Prism Centrals", headers, rows)

    def _feature_usage_sheet(self, wb: Workbook, env: Environment) -> None:
        from ntnxvision_core.analysis import analyze_feature_usage

        rows = [fu.as_row() for fu in analyze_feature_usage(env)]
        self._table(wb, "Feature Usage", ["Product", "Feature", "In Use", "Detail"], rows)

    def _notes_sheet(self, wb: Workbook, env: Environment) -> None:
        """Per-area outcome, in the app's Results wording (same as the reports' appendix)."""
        from ntnxvision_core.exporters.content import _API_LABEL, _OUTCOME, run_areas

        rows: list[list[Any]] = []
        srcs: list[str] = []
        for a in run_areas(env):
            label = TH.clean(a.get("label") or a.get("id"))
            if env.is_combined:
                host, _, rest = label.partition(" · ")
                if rest:
                    label = rest
                srcs.append(host if rest else str(a.get("id", "")).split(": ", 1)[0])
            status = str(a.get("status"))
            api = a.get("api")
            rows.append([label, _OUTCOME.get(status, status),
                         _API_LABEL.get(api, api) if api else None,
                         a.get("detail") or ("No notes" if status == "collected" else None)])
        for w in env.warnings:
            rows.append(["Compatibility", "Warning", None, w])
            srcs.append("")
        self._table(wb, "Collection Notes", ["Area", "Outcome", "API", "Detail"], rows,
                    srcs if env.is_combined else None)

    def _routing_sheet(self, wb: Workbook, env: Environment) -> None:
        """Which API served each area and what happens to it after the Q4 2026 sunset."""
        from ntnxvision_core.exporters.content import _API_LABEL, run_areas
        from ntnxvision_core.runinfo import api_routing

        rows, srcs = [], []
        for r in api_routing(env, run_areas(env), limit=None):
            areas = str(r["areas"])
            if r.get("source"):
                areas = areas.removeprefix(f"{r['source']}: ")
                srcs.append(str(r["source"]))
            rows.append([areas, _API_LABEL.get(r["api"], r["api"]), r["reason"],
                         r["after_sunset"]["label"]])
        self._table(wb, "API Routing", ["Areas", "API", "Reason", "After Q4 2026 v3 sunset"],
                    rows, srcs if env.is_combined else None, wrap=("Areas",))

    def _properties(self, wb: Workbook, env: Environment, opts: Any) -> None:
        props = wb.properties
        scope = (f"{len(env.source_environments):,} Prism Centrals" if env.is_combined
                 else env.prism_central.host or "Nutanix environment")
        props.title = f"Nutanix Inventory Workbook: {scope}"
        props.subject = (f"Prepared for {opts.prepared_for}" if opts.prepared_for
                         else "Nutanix environment inventory")
        props.creator = opts.author or "NTNXVision"
        props.lastModifiedBy = opts.author or "NTNXVision"
        props.keywords = "Nutanix; Prism Central; inventory; NTNXVision"
        props.description = ("Generated by NTNXVision from a read-only inventory snapshot "
                             f"collected {TH.collected_label(env)}.")
        props.category = "Inventory"

    # ─── table helper ──────────────────────────────────────────────

    def _table(
        self, wb: Workbook, title: str, headers: list[str], rows: list[list[Any]],
        sources: list[str] | None = None, wrap: tuple[str, ...] = (),
    ) -> str | None:
        """Write one sheet; returns its name, or None when there are no rows.

        `sources` (combined runs only) prepends a "Prism Central" column naming the PC
        each row came from: object names are not unique across PCs, so without it two
        merged rows can be indistinguishable.
        """
        if not rows:
            return None
        if sources is not None:
            headers = ["Prism Central", *headers]
            rows = [[src, *row] for src, row in zip(sources, rows, strict=True)]
        ws: Worksheet = wb.create_sheet(_safe_title(title))
        ws.append(headers)
        numeric = [h not in PLAIN_HEADERS and _numeric_column(rows, i)
                   for i, h in enumerate(headers)]
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill, cell.font, cell.border = _HEADER_FILL, _HEADER_FONT, _HEADER_BORDER
            cell.alignment = _RIGHT if numeric[col_idx - 1] else _LEFT
        ws.row_dimensions[1].height = 20

        widths = [len(str(h)) + 3 for h in headers]  # + room for the filter button
        for r_idx, row in enumerate(rows, start=2):
            for c_idx, raw in enumerate(row, start=1):
                header = headers[c_idx - 1]
                value, number_format, font = _excel_value(raw, header)
                cell = ws.cell(row=r_idx, column=c_idx, value=value)
                cell.font = font
                if number_format:
                    cell.number_format = number_format
                if numeric[c_idx - 1]:
                    cell.alignment = _RIGHT
                elif header in wrap:
                    cell.alignment = _TOP_LEFT
                text_len = 16 if number_format == _DATETIME else len(_display(value))
                widths[c_idx - 1] = max(widths[c_idx - 1], text_len + 2)

        ws.freeze_panes = "A2"
        last_col = get_column_letter(len(headers))
        ws.auto_filter.ref = f"A1:{last_col}{max(len(rows) + 1, 1)}"
        for col_idx, width in enumerate(widths, start=1):
            cap = 70 if headers[col_idx - 1] in wrap else 50
            ws.column_dimensions[get_column_letter(col_idx)].width = max(min(width, cap), 8)
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        if ws.sheet_properties.pageSetUpPr is not None:
            ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.print_title_rows = "1:1"
        return ws.title


def _numeric_column(rows: list[list[Any]], i: int) -> bool:
    present = [r[i] for r in rows if i < len(r) and not TH.is_missing(r[i])]
    return bool(present) and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                 for v in present)


def _excel_value(raw: Any, header: str) -> tuple[Any, str | None, Font]:
    """The cell value, number format and font for a raw table value."""
    if TH.is_missing(raw):
        return NA, None, _NA_FONT
    if isinstance(raw, str):
        dt = TH.parse_datetime(raw)
        if dt is not None:
            midnight = (dt.hour, dt.minute, dt.second) == (0, 0, 0)
            return dt, _DATE if midnight else _DATETIME, _BODY_FONT
        text = TH.clean(raw)
        tone = TH.tone_for(header, text)
        return text, None, _STATUS_FONT[tone] if tone else _BODY_FONT
    if header not in PLAIN_HEADERS:
        return raw, excel_number_format(raw), _BODY_FONT
    return raw, None, _BODY_FONT


def _display(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}"
    return str(value)


def _absent(spec: T.TableSpec, notes: dict[str, str], is_pe: bool) -> str:
    """Why an object type has no sheet: not deployed, not collected, or simply none."""
    keys = spec.note_keys
    values = [v for k, v in notes.items()
              if any(k == key or k.endswith(f": {key}") for key in keys)]
    if keys and not values and notes:
        return "Not collected (Prism Element mode)" if is_pe else "Not collected"
    if values and all(v.startswith("skipped") for v in values):
        return "Not deployed"
    if values and all(v.startswith(("error", "failed")) for v in values):
        return "Collection failed"
    return "None found (sheet omitted)"


def _safe_title(title: str) -> str:
    """Excel sheet titles disallow []:*?/\\ and cap at 31 chars."""
    for ch in "[]:*?/\\":
        title = title.replace(ch, "-")
    return title[:31]


def _gib(value: int | None) -> float | None:
    if not value:
        return None
    return round(value / GIB, 2)
