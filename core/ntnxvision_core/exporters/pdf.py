"""PDF exporter (fpdf2).

Produces a branded, enterprise-styled report: a full-bleed cover, a section per feature
area with accent-underlined headings, zebra tables, and a ruled header/footer. fpdf2 is
pure-Python (no native GTK/Pango), so it bundles cleanly with PyInstaller on Win + macOS.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from fpdf import FPDF

from ntnxvision_core.exporters import diagrams
from ntnxvision_core.exporters.diagrams import Diagram
from ntnxvision_core.models import Environment

GIB = 1024**3
# Enterprise palette.
_NAVY = (24, 58, 92)
_ACCENT = (0, 122, 204)
_HEADER = (31, 78, 120)
_ZEBRA = (241, 245, 250)
_GREY = (120, 130, 142)
_INK = (40, 46, 54)


class _Report(FPDF):
    title_text = "Nutanix Environment Report"

    def header(self) -> None:
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(*_NAVY)
        self.cell(0, 6, "NTNXVision", align="L")
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*_GREY)
        self.cell(0, 6, self.title_text, align="R")
        self.ln(7)
        # Thin rule under the header.
        self.set_draw_color(*_ACCENT)
        self.set_line_width(0.4)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(4)

    def footer(self) -> None:
        if self.page_no() == 1:
            return
        self.set_y(-12)
        self.set_draw_color(220, 226, 232)
        self.set_line_width(0.2)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(1)
        self.set_font("Helvetica", "", 7.5)
        self.set_text_color(*_GREY)
        self.cell(0, 6, "NTNXVision - Nutanix Environment Report", align="L")
        self.cell(0, 6, f"Page {self.page_no() - 1}", align="R")


class PdfExporter:
    def export(self, env: Environment, path: str | Path) -> Path:
        pdf = _Report(orientation="L", unit="mm", format="A4")
        pdf.set_auto_page_break(auto=True, margin=16)
        pdf.set_title("NTNXVision Environment Report")

        self._cover(pdf, env)
        self._summary(pdf, env)
        self._topology(pdf, env)
        self._pc_config(pdf, env)
        self._infra_sections(pdf, env)
        self._system_vms(pdf, env)
        self._catalog(pdf, env)
        self._dr_section(pdf, env)
        self._flow_section(pdf, env)
        self._nus_section(pdf, env)
        self._ndb_section(pdf, env)
        self._feature_usage(pdf, env)

        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        pdf.output(str(out))
        return out

    # ─── cover & summary ───────────────────────────────────────────

    def _cover(self, pdf: _Report, env: Environment) -> None:
        pdf.add_page()
        # Full-bleed navy band across the top third.
        pdf.set_fill_color(*_NAVY)
        pdf.rect(0, 0, pdf.w, 78, style="F")
        pdf.set_fill_color(*_ACCENT)
        pdf.rect(0, 78, pdf.w, 2.5, style="F")

        pdf.set_y(28)
        pdf.set_font("Helvetica", "B", 40)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(0, 20, "NTNXVision", align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 15)
        pdf.set_text_color(205, 220, 235)
        pdf.cell(0, 10, "Nutanix Cloud Platform - Environment Report", align="C",
                 new_x="LMARGIN", new_y="NEXT")

        pdf.set_y(110)
        pdf.set_font("Helvetica", "", 12)
        pdf.set_text_color(*_INK)
        for label, value in [
            ("Prism Central", env.prism_central.host or "n/a"),
            ("Version", env.prism_central.version or "unknown"),
            ("Clusters", str(len(env.clusters))),
            ("Collected (UTC)", (env.collected_at or "n/a")[:19].replace("T", "  ")),
        ]:
            pdf.set_x(pdf.w / 2 - 55)
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(*_NAVY)
            pdf.cell(45, 9, label, align="L")
            pdf.set_font("Helvetica", "", 11)
            pdf.set_text_color(*_INK)
            pdf.cell(65, 9, _latin1(value), align="L", new_x="LMARGIN", new_y="NEXT")

    def _summary(self, pdf: _Report, env: Environment) -> None:
        pdf.add_page()
        self._h1(pdf, "Executive Summary")
        pdf.set_font("Helvetica", "", 11)
        pdf.set_text_color(*_INK)
        c = env.summary()
        line = (
            f"This environment is managed by Prism Central {env.prism_central.host or ''} "
            f"(version {env.prism_central.version or 'unknown'}) and comprises "
            f"{c['clusters']} cluster(s), {c['hosts']} host(s), and {c['vms']} user virtual "
            f"machine(s) ({c['system_vms']} Nutanix system VM(s)). It includes "
            f"{c['protection_policies']} protection policy(ies) and {c['recovery_plans']} "
            f"recovery plan(s), {c['security_policies']} Flow security policy(ies), "
            f"{c['storage_containers']} storage container(s), {c['templates']} template(s), "
            f"and {c['images']} image(s)."
        )
        pdf.multi_cell(0, 6, _latin1(line))
        pdf.ln(4)
        metrics = [(k.replace("_", " ").title(), str(v)) for k, v in c.items() if v]
        self._metric_grid(pdf, metrics)

    def _topology(self, pdf: _Report, env: Environment) -> None:
        self._diagram_page(pdf, "Environment Topology", [
            ("Management topology - Prism Central and registered clusters.",
             diagrams.management_topology(env)),
        ])

    def _pc_config(self, pdf: _Report, env: Environment) -> None:
        cfg = env.pc_config
        rows = [
            ["Version", cfg.version or "-"],
            ["Deployment", "Scale-out (3-node)" if cfg.is_scale_out else "Single-node"
             if cfg.is_scale_out is not None else "-"],
            ["PC nodes", str(cfg.node_count or "-")],
            ["Registered clusters", str(cfg.registered_cluster_count or "-")],
            ["Total vCPU", str(cfg.num_vcpus or "-")],
            ["Total memory (GiB)", str(_gib(cfg.memory_bytes) or "-")],
            ["PC VM(s)", ", ".join(cfg.pc_vm_names) or "-"],
            ["IP address(es)", ", ".join(cfg.ip_addresses) or "-"],
        ]
        pdf.add_page()
        self._h1(pdf, "Prism Central - Management & Sizing")
        self._kv_table(pdf, [(k, v) for k, v in rows])

    def _infra_sections(self, pdf: _Report, env: Environment) -> None:
        self._section_table(
            pdf, "Clusters", ["Name", "AOS", "Hyp.", "Nodes", "RF", "Encryption"],
            [[c.name, c.aos_version, ",".join(c.hypervisor_types), c.num_nodes,
              c.redundancy_factor, "Yes" if c.encryption_enabled else "No"]
             for c in env.clusters],
            [55, 30, 35, 25, 20, 35],
        )
        self._section_table(
            pdf, "Hosts", ["Name", "Cluster", "Hypervisor", "Cores", "Mem GiB", "VMs"],
            [[h.name, h.cluster_name, h.hypervisor_type, h.num_cpu_cores,
              _gib(h.memory_bytes), h.num_vms] for h in env.hosts],
            [55, 45, 45, 25, 30, 20],
        )
        self._diagram_page(pdf, "Cluster Compute Topology", [
            (f"Cluster {c.name} - node composition and capacity.",
             diagrams.cluster_compute(env, c)) for c in env.clusters[:8]
        ])
        self._section_table(
            pdf, "Virtual Machines (User Workloads)",
            ["Name", "Power", "Cluster", "vCPU", "Mem GiB", "Disk GiB"],
            [[v.name, v.power_state, v.cluster_name, v.vcpu_total, _gib(v.memory_bytes),
              _gib(v.total_disk_bytes)] for v in env.vms],
            [70, 25, 55, 20, 30, 30],
        )
        self._section_table(
            pdf, "Storage Containers",
            ["Name", "Cluster", "RF", "Compression", "Max GiB", "Used GiB"],
            [[s.name, s.cluster_name, s.replication_factor,
              "On" if s.compression_enabled else "Off", _gib(s.max_capacity_bytes),
              _gib(s.used_bytes)] for s in env.storage_containers],
            [55, 45, 20, 40, 35, 35],
        )

    def _system_vms(self, pdf: _Report, env: Environment) -> None:
        self._section_table(
            pdf, "Nutanix System VMs (CVMs / Prism Central)",
            ["Name", "Type", "Power", "Cluster", "vCPU", "Mem GiB"],
            [[v.name, v.vm_type, v.power_state, v.cluster_name, v.vcpu_total,
              _gib(v.memory_bytes)] for v in env.system_vms],
            [70, 40, 25, 55, 20, 30],
        )

    def _catalog(self, pdf: _Report, env: Environment) -> None:
        self._section_table(
            pdf, "VM Templates", ["Name", "Guest OS", "vCPU", "Mem GiB", "Version", "Versions"],
            [[t.name, t.guest_os, t.num_vcpus, _gib(t.memory_bytes), t.active_version,
              t.version_count] for t in env.templates],
            [70, 55, 20, 30, 40, 25],
        )
        self._section_table(
            pdf, "Images", ["Name", "Type", "Size GiB", "Arch", "Clusters", "State"],
            [[i.name, i.image_type, _gib(i.size_bytes), i.architecture,
              ", ".join(i.cluster_names), i.state] for i in env.images],
            [70, 35, 30, 30, 55, 30],
        )
        self._section_table(
            pdf, "OVAs", ["Name", "Size GiB", "Disks", "Cluster"],
            [[o.name, _gib(o.size_bytes), o.disk_count, o.cluster_name] for o in env.ovas],
            [80, 35, 25, 60],
        )

    def _dr_section(self, pdf: _Report, env: Environment) -> None:
        self._section_table(
            pdf, "Protection Policies",
            ["Name", "RPO (s)", "Replication", "Source AZ", "Target AZ"],
            [[p.name, p.rpo_seconds, p.replication_type, p.source_az, p.target_az]
             for p in env.protection_policies],
            [65, 28, 42, 65, 65],
        )
        self._section_table(
            pdf, "Recovery Plans", ["Name", "Stages", "Entities", "Primary", "Recovery"],
            [[r.name, r.stage_count, r.entities_count, r.primary_location, r.recovery_location]
             for r in env.recovery_plans],
            [70, 25, 30, 65, 65],
        )
        self._diagram_page(pdf, "Replication Topology", [
            ("Protection policy flows between availability zones.",
             diagrams.replication_topology(env)),
        ])

    def _flow_section(self, pdf: _Report, env: Environment) -> None:
        self._section_table(
            pdf, "Flow Security Policies", ["Name", "Type", "State", "In", "Out", "Secured Group"],
            [[p.name, p.policy_type, p.state, p.inbound_rule_count, p.outbound_rule_count,
              p.secured_group] for p in env.security_policies],
            [55, 40, 30, 15, 15, 90],
        )
        self._section_table(
            pdf, "VPCs", ["Name", "Subnets", "External Subnets", "Routes", "Policies"],
            [[v.name, ", ".join(v.subnet_names), ", ".join(v.external_subnets),
              v.route_count, v.routing_policy_count]
             for v in env.vpcs],
            [55, 80, 75, 20, 20],
        )
        self._diagram_page(pdf, "Network Topology", [
            (f"Cluster {c.name} - virtual switch and VLAN layout.",
             diagrams.network_topology(env, c)) for c in env.clusters[:8]
        ] + [
            ("Flow Virtual Networking - VPCs with overlay and external subnets.",
             diagrams.vpc_topology(env)),
        ])

    def _nus_section(self, pdf: _Report, env: Environment) -> None:
        self._section_table(
            pdf, "File Servers", ["Name", "Cluster", "Version", "FSVMs", "Size GiB", "Shares"],
            [[f.name, f.cluster_name, f.version, f.num_fsvm, _gib(f.size_bytes), f.share_count]
             for f in env.file_servers],
            [55, 45, 35, 25, 35, 25],
        )
        self._section_table(
            pdf, "Object Stores",
            ["Name", "Cluster", "Workers", "Capacity GiB", "Buckets", "State"],
            [[o.name, o.cluster_name, o.num_worker_nodes, _gib(o.total_capacity_bytes),
              o.bucket_count, o.state] for o in env.object_stores],
            [55, 45, 25, 40, 25, 30],
        )
        self._section_table(
            pdf, "Volume Groups", ["Name", "Cluster", "Disks", "Capacity GiB", "Shared"],
            [[v.name, v.cluster_name, v.disk_count, _gib(v.total_capacity_bytes),
              "Yes" if v.is_shared else "No"] for v in env.volume_groups],
            [70, 55, 25, 40, 30],
        )

    def _ndb_section(self, pdf: _Report, env: Environment) -> None:
        self._section_table(
            pdf, "Databases (NDB)", ["Name", "Engine", "Type", "Status", "Time Machine", "SLA"],
            [[d.name, d.db_engine, d.db_type, d.status, d.time_machine_name, d.sla_name]
             for d in env.databases],
            [55, 40, 40, 35, 55, 40],
        )

    def _feature_usage(self, pdf: _Report, env: Environment) -> None:
        from ntnxvision_core.analysis import analyze_feature_usage

        rows = [[fu.product, fu.feature, "Yes" if fu.in_use else "No", fu.detail]
                for fu in analyze_feature_usage(env)]
        self._section_table(
            pdf, "Feature Usage & Licensing", ["Product", "Feature", "In Use", "Detail"],
            rows, [55, 80, 25, 80], force=True,
        )

    # ─── styling helpers ───────────────────────────────────────────

    def _h1(self, pdf: _Report, text: str) -> None:
        pdf.set_font("Helvetica", "B", 17)
        pdf.set_text_color(*_NAVY)
        pdf.cell(0, 11, _latin1(text), new_x="LMARGIN", new_y="NEXT")
        # Accent underline.
        y = pdf.get_y() + 0.5
        pdf.set_draw_color(*_ACCENT)
        pdf.set_line_width(0.8)
        pdf.line(pdf.l_margin, y, pdf.l_margin + 60, y)
        pdf.ln(4)

    def _diagram_page(self, pdf: _Report, title: str,
                      items: list[tuple[str, Diagram | None]]) -> None:
        """One section of stacked topology diagrams; skipped entirely if none rendered."""
        drawn = [(caption, d) for caption, d in items if d is not None]
        if not drawn:
            return
        pdf.add_page()
        self._h1(pdf, title)
        for caption, diag in drawn:
            # Landscape pages afford a bit more room than the logical size.
            w_mm = min(pdf.epw, diag.width_in * 25.4 * 1.15)
            h_mm = w_mm * diag.height / diag.width
            max_h = pdf.h - 70  # keep one diagram within a single page
            if h_mm > max_h:
                w_mm, h_mm = w_mm * max_h / h_mm, max_h
            if pdf.get_y() + h_mm + 10 > pdf.h - 18:
                pdf.add_page()
            pdf.image(io.BytesIO(diag.png), x=pdf.l_margin, w=w_mm)
            pdf.ln(1)
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(*_GREY)
            pdf.cell(0, 5, _latin1(caption), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(4)

    def _section_table(
        self, pdf: _Report, title: str, headers: list[str],
        rows: list[list[Any]], widths: list[float], force: bool = False,
    ) -> None:
        if not rows and not force:
            return
        pdf.add_page()
        self._h1(pdf, title)
        if not rows:
            pdf.set_font("Helvetica", "I", 10)
            pdf.set_text_color(*_GREY)
            pdf.cell(0, 8, "None configured / not detected.", new_x="LMARGIN", new_y="NEXT")
            return
        self._table(pdf, headers, rows, widths)
        pdf.ln(2)
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(*_GREY)
        pdf.cell(0, 5, f"{len(rows)} item(s).", new_x="LMARGIN", new_y="NEXT")

    def _table(self, pdf: _Report, headers: list[str], rows: list[list[Any]],
               widths: list[float]) -> None:
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_fill_color(*_HEADER)
        pdf.set_text_color(255, 255, 255)
        pdf.set_draw_color(*_HEADER)
        for h, w in zip(headers, widths, strict=False):
            pdf.cell(w, 8, _latin1(str(h)), border=0, fill=True, align="L")
        pdf.ln()
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*_INK)
        fill = False
        for row in rows:
            if pdf.get_y() > pdf.h - 22:
                pdf.add_page()
                # Re-draw header on the continuation page.
                pdf.set_font("Helvetica", "B", 9)
                pdf.set_fill_color(*_HEADER)
                pdf.set_text_color(255, 255, 255)
                for h, w in zip(headers, widths, strict=False):
                    pdf.cell(w, 8, _latin1(str(h)), border=0, fill=True, align="L")
                pdf.ln()
                pdf.set_font("Helvetica", "", 8)
                pdf.set_text_color(*_INK)
            pdf.set_fill_color(*_ZEBRA)
            for value, w in zip(row, widths, strict=False):
                pdf.cell(w, 6, _truncate(value, w), border=0, fill=fill, align="L")
            pdf.ln()
            fill = not fill

    def _metric_grid(self, pdf: _Report, pairs: list[tuple[str, str]]) -> None:
        # Fixed 5-column card grid of key metrics, positioned absolutely per cell.
        cols = 5
        gutter = 4
        col_w = (pdf.w - pdf.l_margin - pdf.r_margin - gutter * (cols - 1)) / cols
        card_h = 16
        row_gap = 4
        x0 = pdf.l_margin
        y0 = pdf.get_y() + 2
        for i, (label, value) in enumerate(pairs):
            col = i % cols
            row = i // cols
            x = x0 + col * (col_w + gutter)
            y = y0 + row * (card_h + row_gap)
            pdf.set_fill_color(*_ZEBRA)
            pdf.set_draw_color(210, 220, 230)
            pdf.rect(x, y, col_w, card_h, style="DF")
            pdf.set_xy(x + 3, y + 2.5)
            pdf.set_font("Helvetica", "B", 16)
            pdf.set_text_color(*_NAVY)
            pdf.cell(col_w - 6, 8, _latin1(value), align="L")
            pdf.set_xy(x + 3, y + 10)
            pdf.set_font("Helvetica", "", 7.5)
            pdf.set_text_color(*_GREY)
            pdf.cell(col_w - 6, 4, _latin1(label), align="L")
        # Advance cursor below the grid.
        rows = (len(pairs) + cols - 1) // cols
        pdf.set_y(y0 + rows * (card_h + row_gap))

    def _kv_table(self, pdf: _Report, pairs: list[tuple[str, str]]) -> None:
        pdf.set_font("Helvetica", "", 10)
        for label, value in pairs:
            pdf.set_fill_color(*_ZEBRA)
            pdf.set_text_color(*_NAVY)
            pdf.set_font("Helvetica", "B", 10)
            pdf.cell(70, 8, _latin1(label), border=0, fill=True)
            pdf.set_text_color(*_INK)
            pdf.set_font("Helvetica", "", 10)
            pdf.cell(120, 8, _truncate(value, 120), border=0, fill=True,
                     new_x="LMARGIN", new_y="NEXT")


def _truncate(value: Any, width: float) -> str:
    text = _latin1("" if value is None else str(value))
    max_chars = max(4, int(width / 1.8))
    return text if len(text) <= max_chars else text[: max_chars - 1] + "."


def _latin1(text: str) -> str:
    """fpdf2's built-in fonts are Latin-1 only; drop characters outside that range."""
    return text.encode("latin-1", "replace").decode("latin-1")


def _gib(value: int | None) -> float | None:
    if not value:
        return None
    return round(value / GIB, 1)
