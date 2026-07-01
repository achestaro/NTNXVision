"""Word Architecture Design Document (ADD) exporter (python-docx).

This is NTNXVision's differentiator: rather than a raw data dump, it produces a narrative
architecture design deliverable — cover page, executive summary, assumptions, logical and
physical design, data protection strategy, security posture, storage services, databases,
licensing, and appendices — styled with headings, a table of contents field, and captioned
tables.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from ntnxvision_core.models import Environment

GIB = 1024**3
_NAVY = RGBColor(0x1F, 0x4E, 0x78)


class WordExporter:
    def export(self, env: Environment, path: str | Path) -> Path:
        doc = Document()
        self._styles(doc)
        self._cover(doc, env)
        self._toc(doc)
        self._executive_summary(doc, env)
        self._assumptions(doc, env)
        self._logical_design(doc, env)
        self._physical_design(doc, env)
        self._storage_services(doc, env)
        self._networking(doc, env)
        self._data_protection(doc, env)
        self._security(doc, env)
        self._databases(doc, env)
        self._licensing(doc, env)
        self._appendix(doc, env)

        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(out))
        return out

    # ─── document scaffolding ──────────────────────────────────────

    def _styles(self, doc: Document) -> None:
        normal = doc.styles["Normal"]
        normal.font.name = "Calibri"
        normal.font.size = Pt(11)

    def _cover(self, doc: Document, env: Environment) -> None:
        for _ in range(6):
            doc.add_paragraph()
        title = doc.add_paragraph()
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = title.add_run("Nutanix Cloud Platform")
        run.font.size = Pt(30)
        run.font.bold = True
        run.font.color.rgb = _NAVY

        sub = doc.add_paragraph()
        sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
        srun = sub.add_run("Architecture Design Document")
        srun.font.size = Pt(20)
        srun.font.color.rgb = RGBColor(0x40, 0x40, 0x40)

        for _ in range(4):
            doc.add_paragraph()
        for label, value in [
            ("Prism Central", env.prism_central.host or "n/a"),
            ("PC Version", env.prism_central.version or "unknown"),
            ("Collected (UTC)", env.collected_at or "n/a"),
            ("Prepared by", "NTNXVision"),
        ]:
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            r = p.add_run(f"{label}:  {value}")
            r.font.size = Pt(12)
            r.font.color.rgb = RGBColor(0x60, 0x60, 0x60)
        doc.add_page_break()

    def _toc(self, doc: Document) -> None:
        self._heading(doc, "Table of Contents", level=1)
        p = doc.add_paragraph()
        run = p.add_run()
        fld_begin = OxmlElement("w:fldChar")
        fld_begin.set(qn("w:fldCharType"), "begin")
        instr = OxmlElement("w:instrText")
        instr.set(qn("xml:space"), "preserve")
        instr.text = 'TOC \\o "1-2" \\h \\z \\u'
        fld_sep = OxmlElement("w:fldChar")
        fld_sep.set(qn("w:fldCharType"), "separate")
        placeholder = OxmlElement("w:t")
        placeholder.text = "Right-click and choose 'Update Field' to build the contents."
        fld_end = OxmlElement("w:fldChar")
        fld_end.set(qn("w:fldCharType"), "end")
        for el in (fld_begin, instr, fld_sep, placeholder, fld_end):
            run._r.append(el)
        doc.add_page_break()

    # ─── sections ──────────────────────────────────────────────────

    def _executive_summary(self, doc: Document, env: Environment) -> None:
        self._heading(doc, "1. Executive Summary", level=1)
        c = env.summary()
        doc.add_paragraph(
            f"This document presents the architecture of the Nutanix Cloud Platform "
            f"environment managed by Prism Central {env.prism_central.host or ''} "
            f"(version {env.prism_central.version or 'unknown'}). The environment consists of "
            f"{c['clusters']} cluster(s) totalling {c['hosts']} node(s) and hosting "
            f"{c['vms']} virtual machine(s)."
        )
        doc.add_paragraph(
            "The design covers compute, storage, networking, data protection and disaster "
            "recovery, network security (Flow), unified storage services (Files, Objects, "
            "Volumes), database services (NDB), identity and access management, and licensing. "
            "Each section documents the as-built configuration discovered during inventory "
            "collection."
        )
        highlights = [
            f"{c['protection_policies']} protection policy(ies) and {c['recovery_plans']} "
            "recovery plan(s) providing data protection and DR.",
            f"{c['security_policies']} Flow security policy(ies) enforcing microsegmentation.",
            f"{c['file_servers']} file server(s) and {c['object_stores']} object store(s) "
            "delivering unified storage.",
            f"{c['databases']} database(s) under NDB lifecycle management.",
        ]
        for h in highlights:
            doc.add_paragraph(h, style="List Bullet")
        doc.add_page_break()

    def _assumptions(self, doc: Document, env: Environment) -> None:
        self._heading(doc, "2. Assumptions & Constraints", level=1)
        for item in [
            "Inventory was collected read-only via the Prism Central v3/v4 APIs; no "
            "configuration changes were made.",
            "Data reflects the environment state at the collection timestamp on the cover page.",
            "Feature availability depends on installed licenses and Prism Central version; "
            "areas not deployed are noted as such in the relevant sections.",
        ]:
            doc.add_paragraph(item, style="List Bullet")
        notes = [(k, v) for k, v in env.collection_notes.items() if not v.startswith("ok")]
        if notes:
            doc.add_paragraph("Collection notes (skipped / errored areas):")
            for name, status in notes:
                doc.add_paragraph(f"{name}: {status}", style="List Bullet")
        doc.add_page_break()

    def _logical_design(self, doc: Document, env: Environment) -> None:
        self._heading(doc, "3. Logical Design", level=1)
        doc.add_paragraph(
            "The environment is managed centrally through Prism Central, which registers and "
            "orchestrates the clusters below. Workloads are organised using categories and "
            "projects, protected by protection policies, and secured by Flow policies."
        )
        self._heading(doc, "3.1 Clusters Overview", level=2)
        self._table(
            doc, ["Cluster", "AOS", "Hypervisor", "Nodes", "RF", "Encryption"],
            [[c.name, c.aos_version, ", ".join(c.hypervisor_types), str(c.num_nodes or ""),
              str(c.redundancy_factor or ""), "Yes" if c.encryption_enabled else "No"]
             for c in env.clusters],
            "Table 3-1. Registered clusters.",
        )

    def _physical_design(self, doc: Document, env: Environment) -> None:
        self._heading(doc, "4. Physical Design", level=1)
        self._heading(doc, "4.1 Nodes / Hosts", level=2)
        self._table(
            doc, ["Host", "Cluster", "CPU Model", "Cores", "Memory (GiB)", "GPUs"],
            [[h.name, h.cluster_name, h.cpu_model, str(h.num_cpu_cores or ""),
              str(_gib(h.memory_bytes) or ""), ", ".join(h.gpu_list) or "—"]
             for h in env.hosts],
            "Table 4-1. Physical hosts.",
        )
        self._heading(doc, "4.2 Storage Containers", level=2)
        self._table(
            doc, ["Container", "Cluster", "RF", "Compression", "Dedup", "EC-X", "Max (GiB)"],
            [[s.name, s.cluster_name, str(s.replication_factor or ""),
              "On" if s.compression_enabled else "Off", s.deduplication or "None",
              s.erasure_coding or "Off", str(_gib(s.max_capacity_bytes) or "")]
             for s in env.storage_containers],
            "Table 4-2. Storage containers and data-reduction settings.",
        )

    def _storage_services(self, doc: Document, env: Environment) -> None:
        self._heading(doc, "5. Unified Storage Services (NUS)", level=1)
        self._heading(doc, "5.1 Files", level=2)
        self._table(
            doc, ["File Server", "Cluster", "Version", "FSVMs", "Shares"],
            [[f.name, f.cluster_name, f.version, str(f.num_fsvm or ""), str(f.share_count or "")]
             for f in env.file_servers],
            "Table 5-1. Nutanix Files servers.",
            empty="Nutanix Files is not deployed in this environment.",
        )
        self._heading(doc, "5.2 Objects", level=2)
        self._table(
            doc, ["Object Store", "Cluster", "Workers", "Capacity (GiB)", "Buckets"],
            [[o.name, o.cluster_name, str(o.num_worker_nodes or ""),
              str(_gib(o.total_capacity_bytes) or ""), str(o.bucket_count or "")]
             for o in env.object_stores],
            "Table 5-2. Nutanix Objects stores.",
            empty="Nutanix Objects is not deployed in this environment.",
        )
        self._heading(doc, "5.3 Volumes", level=2)
        self._table(
            doc, ["Volume Group", "Cluster", "Disks", "Capacity (GiB)", "Shared"],
            [[v.name, v.cluster_name, str(v.disk_count or ""),
              str(_gib(v.total_capacity_bytes) or ""), "Yes" if v.is_shared else "No"]
             for v in env.volume_groups],
            "Table 5-3. Volume groups.",
            empty="No volume groups are configured.",
        )

    def _networking(self, doc: Document, env: Environment) -> None:
        self._heading(doc, "6. Networking", level=1)
        self._heading(doc, "6.1 Subnets", level=2)
        self._table(
            doc, ["Subnet", "Cluster", "Type", "VLAN", "VPC"],
            [[s.name, s.cluster_name, s.subnet_type, str(s.vlan_id or ""), s.vpc_name or "—"]
             for s in env.subnets],
            "Table 6-1. Subnets.",
        )
        self._heading(doc, "6.2 Virtual Networking (VPCs)", level=2)
        self._table(
            doc, ["VPC", "External Subnets", "Routable Prefixes"],
            [[v.name, ", ".join(v.external_subnets), ", ".join(v.externally_routable_prefixes)]
             for v in env.vpcs],
            "Table 6-2. Flow virtual networking VPCs.",
            empty="Flow Virtual Networking is not enabled.",
        )

    def _data_protection(self, doc: Document, env: Environment) -> None:
        self._heading(doc, "7. Data Protection & Disaster Recovery", level=1)
        doc.add_paragraph(
            "Data protection is delivered through protection policies (snapshot and "
            "replication schedules) and recovery plans (orchestrated failover). The tables "
            "below document the configured strategy."
        )
        self._heading(doc, "7.1 Protection Policies", level=2)
        self._table(
            doc, ["Policy", "RPO (s)", "Replication", "Source AZ", "Target AZ"],
            [[p.name, str(p.rpo_seconds or ""), p.replication_type, p.source_az or "—",
              p.target_az or "—"] for p in env.protection_policies],
            "Table 7-1. Protection policies.",
            empty="No protection policies are configured.",
        )
        self._heading(doc, "7.2 Recovery Plans", level=2)
        self._table(
            doc, ["Plan", "Stages", "Entities", "Primary", "Recovery"],
            [[r.name, str(r.stage_count or ""), str(r.entities_count or ""),
              r.primary_location or "—", r.recovery_location or "—"]
             for r in env.recovery_plans],
            "Table 7-2. Recovery plans.",
            empty="No recovery plans are configured.",
        )

    def _security(self, doc: Document, env: Environment) -> None:
        self._heading(doc, "8. Security Posture (Flow)", level=1)
        self._table(
            doc, ["Policy", "Type", "State", "Inbound", "Outbound", "Secured Group"],
            [[p.name, p.policy_type, p.state, str(p.inbound_rule_count or ""),
              str(p.outbound_rule_count or ""), p.secured_group or "—"]
             for p in env.security_policies],
            "Table 8-1. Flow network security policies.",
            empty="Flow Network Security is not configured.",
        )

    def _databases(self, doc: Document, env: Environment) -> None:
        self._heading(doc, "9. Database Services (NDB)", level=1)
        self._table(
            doc, ["Database", "Engine", "Type", "Status", "SLA"],
            [[d.name, d.db_engine, d.db_type, d.status, d.sla_name or "—"]
             for d in env.databases],
            "Table 9-1. NDB-managed databases.",
            empty="Nutanix Database Service is not in use / not collected.",
        )

    def _licensing(self, doc: Document, env: Environment) -> None:
        self._heading(doc, "10. Licensing & Feature Usage", level=1)
        from ntnxvision_core.analysis import analyze_feature_usage

        self._table(
            doc, ["Product", "Feature", "In Use", "Detail"],
            [[fu.product, fu.feature, "Yes" if fu.in_use else "No", fu.detail]
             for fu in analyze_feature_usage(env)],
            "Table 10-1. Feature usage mapped to licensing product.",
        )

    def _appendix(self, doc: Document, env: Environment) -> None:
        self._heading(doc, "Appendix A. Virtual Machine Inventory", level=1)
        self._table(
            doc, ["VM", "Power", "Cluster", "vCPU", "Mem (GiB)", "Disk (GiB)"],
            [[v.name, v.power_state, v.cluster_name, str(v.vcpu_total or ""),
              str(_gib(v.memory_bytes) or ""), str(_gib(v.total_disk_bytes) or "")]
             for v in env.vms],
            "Table A-1. Full VM inventory.",
        )

    # ─── helpers ───────────────────────────────────────────────────

    def _heading(self, doc: Document, text: str, level: int) -> None:
        h = doc.add_heading(text, level=level)
        for run in h.runs:
            run.font.color.rgb = _NAVY

    def _table(self, doc: Document, headers: list[str], rows: list[list[Any]],
               caption: str, empty: str | None = None) -> None:
        if not rows:
            if empty:
                p = doc.add_paragraph(empty)
                p.runs[0].italic = True
            return
        table = doc.add_table(rows=1, cols=len(headers))
        table.style = "Light Grid Accent 1"
        for i, h in enumerate(headers):
            cell = table.rows[0].cells[i]
            cell.text = h
            for p in cell.paragraphs:
                for run in p.runs:
                    run.font.bold = True
        for row in rows:
            cells = table.add_row().cells
            for i, value in enumerate(row):
                cells[i].text = "" if value is None else str(value)
        cap = doc.add_paragraph(caption)
        cap.runs[0].italic = True
        cap.runs[0].font.size = Pt(9)
        cap.runs[0].font.color.rgb = RGBColor(0x70, 0x70, 0x70)


def _gib(value: int | None) -> float | None:
    if not value:
        return None
    return round(value / GIB, 1)
