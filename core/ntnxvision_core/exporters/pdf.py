"""PDF exporter (fpdf2).

Produces a branded, paginated report with a cover page, a table of contents, and one
section per feature area. fpdf2 is pure-Python (no native GTK/Pango), so it bundles
cleanly with PyInstaller on both Windows and macOS.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fpdf import FPDF

from ntnxvision_core.models import Environment

GIB = 1024**3
_NAVY = (31, 78, 120)
_LIGHT = (235, 240, 247)
_GREY = (110, 120, 130)


class _Report(FPDF):
    title_text = "NTNXVision - Environment Report"

    def header(self) -> None:
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(*_GREY)
        self.cell(0, 8, self.title_text, align="L")
        self.cell(0, 8, "NTNXVision", align="R")
        self.ln(10)

    def footer(self) -> None:
        if self.page_no() == 1:
            return
        self.set_y(-12)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*_GREY)
        self.cell(0, 8, f"Page {self.page_no()}", align="C")


class PdfExporter:
    def export(self, env: Environment, path: str | Path) -> Path:
        pdf = _Report(orientation="L", unit="mm", format="A4")
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.set_title("NTNXVision Environment Report")

        self._cover(pdf, env)
        self._summary(pdf, env)
        self._infra_sections(pdf, env)
        self._dr_section(pdf, env)
        self._flow_section(pdf, env)
        self._nus_section(pdf, env)
        self._ndb_section(pdf, env)
        self._feature_usage(pdf, env)

        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        pdf.output(str(out))
        return out

    # ─── pages ─────────────────────────────────────────────────────

    def _cover(self, pdf: _Report, env: Environment) -> None:
        pdf.add_page()
        pdf.ln(50)
        pdf.set_font("Helvetica", "B", 34)
        pdf.set_text_color(*_NAVY)
        pdf.cell(0, 18, "NTNXVision", align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 16)
        pdf.set_text_color(60, 60, 60)
        pdf.cell(0, 10, "Nutanix Environment Report", align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(16)
        pdf.set_font("Helvetica", "", 12)
        pdf.set_text_color(*_GREY)
        for line in [
            f"Prism Central: {env.prism_central.host or 'n/a'}",
            f"Version: {env.prism_central.version or 'unknown'}",
            f"Collected (UTC): {env.collected_at or 'n/a'}",
        ]:
            pdf.cell(0, 8, _latin1(line), align="C", new_x="LMARGIN", new_y="NEXT")

    def _summary(self, pdf: _Report, env: Environment) -> None:
        pdf.add_page()
        self._h1(pdf, "Executive Summary")
        pdf.set_font("Helvetica", "", 11)
        pdf.set_text_color(40, 40, 40)
        counts = env.summary()
        line = (
            f"This environment comprises {counts['clusters']} cluster(s), "
            f"{counts['hosts']} host(s), and {counts['vms']} virtual machine(s). "
            f"It includes {counts['protection_policies']} protection policy(ies) and "
            f"{counts['recovery_plans']} recovery plan(s) for data protection, "
            f"{counts['security_policies']} Flow security policy(ies), "
            f"{counts['file_servers']} file server(s), {counts['object_stores']} object store(s), "
            f"and {counts['databases']} managed database(s)."
        )
        pdf.multi_cell(0, 6, line)
        pdf.ln(4)
        # Key metrics grid as a simple table.
        metrics = [(k.replace("_", " ").title(), str(v)) for k, v in counts.items() if v]
        self._kv_table(pdf, metrics)

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
        self._section_table(
            pdf, "Virtual Machines", ["Name", "Power", "Cluster", "vCPU", "Mem GiB", "Disk GiB"],
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

    def _dr_section(self, pdf: _Report, env: Environment) -> None:
        self._section_table(
            pdf, "Protection Policies",
            ["Name", "RPO (s)", "Replication", "Source AZ", "Target AZ"],
            [[p.name, p.rpo_seconds, p.replication_type, p.source_az, p.target_az]
             for p in env.protection_policies],
            [70, 30, 45, 60, 60],
        )
        self._section_table(
            pdf, "Recovery Plans", ["Name", "Stages", "Entities", "Primary", "Recovery"],
            [[r.name, r.stage_count, r.entities_count, r.primary_location, r.recovery_location]
             for r in env.recovery_plans],
            [70, 25, 30, 65, 65],
        )

    def _flow_section(self, pdf: _Report, env: Environment) -> None:
        self._section_table(
            pdf, "Flow Security Policies", ["Name", "Type", "State", "In", "Out", "Secured Group"],
            [[p.name, p.policy_type, p.state, p.inbound_rule_count, p.outbound_rule_count,
              p.secured_group] for p in env.security_policies],
            [55, 40, 30, 15, 15, 90],
        )
        self._section_table(
            pdf, "VPCs", ["Name", "External Subnets", "Routable Prefixes"],
            [[v.name, ", ".join(v.external_subnets), ", ".join(v.externally_routable_prefixes)]
             for v in env.vpcs],
            [60, 90, 100],
        )

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

    # ─── helpers ───────────────────────────────────────────────────

    def _h1(self, pdf: _Report, text: str) -> None:
        pdf.set_font("Helvetica", "B", 18)
        pdf.set_text_color(*_NAVY)
        pdf.cell(0, 12, _latin1(text), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

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

    def _table(self, pdf: _Report, headers: list[str], rows: list[list[Any]],
               widths: list[float]) -> None:
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_fill_color(*_NAVY)
        pdf.set_text_color(255, 255, 255)
        for h, w in zip(headers, widths, strict=False):
            pdf.cell(w, 8, _latin1(str(h)), border=0, fill=True, align="L")
        pdf.ln()
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(30, 30, 30)
        fill = False
        for row in rows:
            if pdf.get_y() > pdf.h - 20:
                pdf.add_page()
            pdf.set_fill_color(*_LIGHT)
            for value, w in zip(row, widths, strict=False):
                pdf.cell(w, 6, _truncate(value, w), border=0, fill=fill, align="L")
            pdf.ln()
            fill = not fill

    def _kv_table(self, pdf: _Report, pairs: list[tuple[str, str]]) -> None:
        pdf.set_font("Helvetica", "", 10)
        for label, value in pairs:
            pdf.set_text_color(*_GREY)
            pdf.cell(70, 7, _latin1(label), border="B")
            pdf.set_text_color(30, 30, 30)
            pdf.cell(40, 7, _latin1(value), border="B", new_x="LMARGIN", new_y="NEXT")


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
