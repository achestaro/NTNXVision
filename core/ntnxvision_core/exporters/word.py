"""Word Architecture Design Document (ADD) exporter (python-docx).

This is NTNXVision's differentiator: rather than a raw data dump, it produces a narrative
architecture design deliverable — cover page, executive summary, assumptions, logical and
physical design, data protection strategy, security posture, storage services, databases,
licensing, and appendices — styled with headings, a table of contents field, and captioned
tables.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from docx import Document
from docx.document import Document as DocumentObject
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from ntnxvision_core.exporters import diagrams
from ntnxvision_core.exporters.diagrams import Diagram
from ntnxvision_core.models import Environment
from ntnxvision_core.notes import clean_error as _clean_error
from ntnxvision_core.notes import pretty_label as _pretty

GIB = 1024**3
_NAVY = RGBColor(0x18, 0x3A, 0x5C)
_ACCENT = RGBColor(0x00, 0x7A, 0xCC)
_HEADER_BG = "1F4E78"
_ZEBRA_BG = "F1F5FA"


class WordExporter:
    def export(self, env: Environment, path: str | Path) -> Path:
        doc = Document()
        self._styles(doc)
        self._cover(doc, env)
        self._toc(doc)
        self._executive_summary(doc, env)
        self._licensing(doc, env)
        self._assumptions(doc, env)
        self._pc_management(doc, env)
        self._logical_design(doc, env)
        self._physical_design(doc, env)
        self._catalog(doc, env)
        self._storage_services(doc, env)
        self._networking(doc, env)
        self._data_protection(doc, env)
        self._security(doc, env)
        self._databases(doc, env)
        self._appendix(doc, env)

        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(out))
        return out

    # ─── document scaffolding ──────────────────────────────────────

    def _styles(self, doc: DocumentObject) -> None:
        normal = doc.styles["Normal"]
        normal.font.name = "Calibri"
        normal.font.size = Pt(11)

    def _cover(self, doc: DocumentObject, env: Environment) -> None:
        """Report cover — embeds the shared diagrams.cover_page() raster so the
        Word and PDF covers are pixel-identical rather than two hand-drawn
        approximations.
        """
        diag = diagrams.cover_page(env)
        for _ in range(3):
            doc.add_paragraph()
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run()
        run.add_picture(io.BytesIO(diag.png), width=Inches(min(6.3, diag.width_in)))
        doc.add_page_break()

    def _toc(self, doc: DocumentObject) -> None:
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

    def _executive_summary(self, doc: DocumentObject, env: Environment) -> None:
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
        self._figure(
            doc, diagrams.executive_dashboard(env),
            "Figure 1-1. Executive summary dashboard — clusters, hosts, VMs, storage "
            "capacity, projects, and protection policies at a glance.",
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

    def _assumptions(self, doc: DocumentObject, env: Environment) -> None:
        self._heading(doc, "3. Assumptions & Constraints", level=1)
        for item in [
            "Inventory was collected read-only via the Prism Central v3/v4 APIs; no "
            "configuration changes were made.",
            "Data reflects the environment state at the collection timestamp on the cover page.",
            "Feature availability depends on installed licenses and Prism Central version; "
            "areas not deployed are noted as such in the relevant sections.",
        ]:
            doc.add_paragraph(item, style="List Bullet")
        for w in env.warnings:
            p = doc.add_paragraph(w, style="List Bullet")
            p.runs[0].italic = True

        skipped = {k: v for k, v in env.collection_notes.items() if v.startswith("skipped")}
        errored = {k: v for k, v in env.collection_notes.items() if v.startswith("error")}
        if skipped:
            doc.add_paragraph("Areas not present / not deployed in this environment:")
            for name, status in skipped.items():
                reason = status.split(":", 1)[1].strip() if ":" in status else status
                doc.add_paragraph(f"{_pretty(name)} — {reason}", style="List Bullet")
        if errored:
            doc.add_paragraph("Areas that could not be fully collected:")
            for name, status in errored.items():
                doc.add_paragraph(f"{_pretty(name)} — {_clean_error(status)}", style="List Bullet")
        doc.add_page_break()

    def _pc_management(self, doc: DocumentObject, env: Environment) -> None:
        cfg = env.pc_config
        self._heading(doc, "4. Prism Central — Management & Sizing", level=1)
        doc.add_paragraph(
            "Prism Central is the management plane for this environment. The table below "
            "records its version, sizing, and the number of clusters it manages."
        )
        deployment = (
            "Scale-out (multi-node)" if cfg.is_scale_out
            else "Single-node" if cfg.is_scale_out is not None else "Unknown"
        )
        self._table(
            doc, ["Property", "Value"],
            [
                ["Version", cfg.version or "—"],
                ["Deployment", deployment],
                ["PC nodes", str(cfg.node_count or "—")],
                ["Registered clusters", str(cfg.registered_cluster_count or "—")],
                ["Total vCPU", str(cfg.num_vcpus or "—")],
                ["Total memory (GiB)", str(_gib(cfg.memory_bytes) or "—")],
                ["PC VM(s)", ", ".join(cfg.pc_vm_names) or "—"],
                ["IP address(es)", ", ".join(cfg.ip_addresses) or "—"],
            ],
            "Table 4-1. Prism Central management-plane configuration.",
        )

    def _catalog(self, doc: DocumentObject, env: Environment) -> None:
        self._heading(doc, "7. Catalog — Templates, Images & OVAs", level=1)
        self._heading(doc, "7.1 VM Templates", level=2)
        self._table(
            doc, ["Template", "Guest OS", "vCPU", "Memory (GiB)", "Active Version"],
            [[t.name, t.guest_os, str(t.num_vcpus or ""), str(_gib(t.memory_bytes) or ""),
              t.active_version or "—"] for t in env.templates],
            "Table 7-1. VM templates.",
            empty="No VM templates are defined in Prism Central.",
        )
        self._heading(doc, "7.2 Images", level=2)
        self._table(
            doc, ["Image", "Type", "Size (GiB)", "Architecture", "Clusters"],
            [[i.name, i.image_type, str(_gib(i.size_bytes) or ""), i.architecture or "—",
              ", ".join(i.cluster_names) or "—"] for i in env.images],
            "Table 7-2. Images.",
            empty="No images are present in the Prism Central catalog.",
        )
        self._heading(doc, "7.3 OVAs", level=2)
        self._table(
            doc, ["OVA", "Size (GiB)", "Disks", "Cluster"],
            [[o.name, str(_gib(o.size_bytes) or ""), str(o.disk_count or ""),
              o.cluster_name or "—"] for o in env.ovas],
            "Table 7-3. OVAs.",
            empty="No OVAs are present in the Prism Central catalog.",
        )

    def _logical_design(self, doc: DocumentObject, env: Environment) -> None:
        self._heading(doc, "5. Logical Design", level=1)
        doc.add_paragraph(
            "The environment is managed centrally through Prism Central, which registers and "
            "orchestrates the clusters below. Workloads are organised using categories and "
            "projects, protected by protection policies, and secured by Flow policies."
        )
        self._figure(
            doc, diagrams.management_topology(env),
            "Figure 5-1. Management topology — Prism Central and its registered clusters.",
        )
        self._heading(doc, "5.1 Clusters Overview", level=2)
        self._table(
            doc, ["Cluster", "AOS", "Hypervisor", "Nodes", "RF", "Encryption"],
            [[c.name, c.aos_version, ", ".join(c.hypervisor_types), str(c.num_nodes or ""),
              str(c.redundancy_factor or ""), "Yes" if c.encryption_enabled else "No"]
             for c in env.clusters],
            "Table 5-1. Registered clusters.",
        )

    def _physical_design(self, doc: DocumentObject, env: Environment) -> None:
        self._heading(doc, "6. Physical Design", level=1)
        self._heading(doc, "6.1 Nodes / Hosts", level=2)
        self._table(
            doc, ["Host", "Cluster", "CPU Model", "Cores", "Memory (GiB)", "GPUs"],
            [[h.name, h.cluster_name, h.cpu_model, str(h.num_cpu_cores or ""),
              str(_gib(h.memory_bytes) or ""), ", ".join(h.gpu_list) or "—"]
             for h in env.hosts],
            "Table 6-1. Physical hosts.",
        )
        fig = 0
        for cluster in env.clusters[:8]:
            d = diagrams.cluster_compute(env, cluster)
            if d:
                fig += 1
                self._figure(doc, d, f"Figure 6-{fig}. Cluster {cluster.name} — "
                                     "node composition and capacity.")
        self._heading(doc, "6.2 Storage Containers", level=2)
        self._table(
            doc, ["Container", "Cluster", "RF", "Compression", "Dedup", "EC-X", "Max (GiB)"],
            [[s.name, s.cluster_name, str(s.replication_factor or ""),
              "On" if s.compression_enabled else "Off", s.deduplication or "None",
              s.erasure_coding or "Off", str(_gib(s.max_capacity_bytes) or "")]
             for s in env.storage_containers],
            "Table 6-2. Storage containers and data-reduction settings.",
        )
        self._figure(
            doc, diagrams.storage_capacity_chart(env),
            f"Figure 6-{fig + 1}. Storage container capacity — used vs. free per container.",
        )
        self._heading(doc, "6.3 Virtual Machine Distribution", level=2)
        self._figure(
            doc, diagrams.vm_power_state_chart(env),
            f"Figure 6-{fig + 2}. VM power state distribution.",
        )
        self._figure(
            doc, diagrams.vms_per_cluster_chart(env),
            f"Figure 6-{fig + 3}. Virtual machines per cluster.",
        )

    def _storage_services(self, doc: DocumentObject, env: Environment) -> None:
        self._heading(doc, "8. Unified Storage Services (NUS)", level=1)
        self._heading(doc, "8.1 Files", level=2)
        self._table(
            doc, ["File Server", "Cluster", "Version", "FSVMs", "Shares"],
            [[f.name, f.cluster_name, f.version, str(f.num_fsvm or ""), str(f.share_count or "")]
             for f in env.file_servers],
            "Table 8-1. Nutanix Files servers.",
            empty="Nutanix Files is not deployed in this environment.",
        )
        self._heading(doc, "8.2 Objects", level=2)
        self._table(
            doc, ["Object Store", "Cluster", "Workers", "Capacity (GiB)", "Buckets"],
            [[o.name, o.cluster_name, str(o.num_worker_nodes or ""),
              str(_gib(o.total_capacity_bytes) or ""), str(o.bucket_count or "")]
             for o in env.object_stores],
            "Table 8-2. Nutanix Objects stores.",
            empty="Nutanix Objects is not deployed in this environment.",
        )
        self._heading(doc, "8.3 Volumes", level=2)
        self._table(
            doc, ["Volume Group", "Cluster", "Disks", "Capacity (GiB)", "Shared"],
            [[v.name, v.cluster_name, str(v.disk_count or ""),
              str(_gib(v.total_capacity_bytes) or ""), "Yes" if v.is_shared else "No"]
             for v in env.volume_groups],
            "Table 8-3. Volume groups.",
            empty="No volume groups are configured.",
        )

    def _networking(self, doc: DocumentObject, env: Environment) -> None:
        self._heading(doc, "9. Networking", level=1)
        self._heading(doc, "9.1 Subnets", level=2)
        self._table(
            doc, ["Subnet", "Cluster", "Type", "VLAN", "vSwitch", "VPC"],
            [[s.name, s.cluster_name, s.subnet_type, str(s.vlan_id or ""),
              s.virtual_switch or "—", s.vpc_name or "—"]
             for s in env.subnets],
            "Table 9-1. Subnets.",
        )
        fig = 0
        for cluster in env.clusters[:8]:
            d = diagrams.network_topology(env, cluster)
            if d:
                fig += 1
                self._figure(doc, d, f"Figure 9-{fig}. Cluster {cluster.name} — "
                                     "virtual switch and VLAN layout.")
        self._heading(doc, "9.2 Virtual Networking (VPCs)", level=2)
        self._table(
            doc, ["VPC", "Subnets", "External Subnets", "Routable Prefixes", "Routes",
                  "Routing Policies"],
            [[v.name, ", ".join(v.subnet_names) or "—", ", ".join(v.external_subnets),
              ", ".join(v.externally_routable_prefixes),
              str(v.route_count) if v.route_count is not None else "—",
              str(v.routing_policy_count) if v.routing_policy_count is not None else "—"]
             for v in env.vpcs],
            "Table 9-2. Flow virtual networking VPCs.",
            empty="Flow Virtual Networking is not enabled.",
        )
        self._figure(
            doc, diagrams.vpc_topology(env),
            f"Figure 9-{fig + 1}. Flow Virtual Networking — VPCs with overlay and "
            "external subnets.",
        )

    def _data_protection(self, doc: DocumentObject, env: Environment) -> None:
        self._heading(doc, "10. Data Protection & Disaster Recovery", level=1)
        doc.add_paragraph(
            "Data protection is delivered through protection policies (snapshot and "
            "replication schedules) and recovery plans (orchestrated failover). The tables "
            "below document the configured strategy."
        )
        self._figure(
            doc, diagrams.replication_topology(env),
            "Figure 10-1. Replication topology — protection policy flows between "
            "availability zones.",
        )
        self._heading(doc, "10.1 Protection Policies", level=2)
        self._table(
            doc, ["Policy", "RPO (s)", "Replication", "Source AZ", "Target AZ"],
            [[p.name, str(p.rpo_seconds or ""), p.replication_type, p.source_az or "—",
              p.target_az or "—"] for p in env.protection_policies],
            "Table 10-1. Protection policies.",
            empty="No protection policies are configured.",
        )
        self._heading(doc, "10.2 Recovery Plans", level=2)
        self._table(
            doc, ["Plan", "Stages", "Entities", "Primary", "Recovery"],
            [[r.name, str(r.stage_count or ""), str(r.entities_count or ""),
              r.primary_location or "—", r.recovery_location or "—"]
             for r in env.recovery_plans],
            "Table 10-2. Recovery plans.",
            empty="No recovery plans are configured.",
        )

    def _security(self, doc: DocumentObject, env: Environment) -> None:
        self._heading(doc, "11. Security Posture (Flow)", level=1)
        self._table(
            doc, ["Policy", "Type", "State", "Inbound", "Outbound", "Secured Group"],
            [[p.name, p.policy_type, p.state, str(p.inbound_rule_count or ""),
              str(p.outbound_rule_count or ""), p.secured_group or "—"]
             for p in env.security_policies],
            "Table 11-1. Flow network security policies.",
            empty="Flow Network Security is not configured.",
        )

    def _databases(self, doc: DocumentObject, env: Environment) -> None:
        self._heading(doc, "12. Database Services (NDB)", level=1)
        self._table(
            doc, ["Database", "Engine", "Type", "Status", "SLA"],
            [[d.name, d.db_engine, d.db_type, d.status, d.sla_name or "—"]
             for d in env.databases],
            "Table 12-1. NDB-managed databases.",
            empty="Nutanix Database Service is not in use / not collected.",
        )

    def _licensing(self, doc: DocumentObject, env: Environment) -> None:
        self._heading(doc, "2. Licensing & Feature Usage", level=1)
        from ntnxvision_core.analysis import analyze_feature_usage

        self._table(
            doc, ["Product", "Feature", "In Use", "Detail"],
            [[fu.product, fu.feature, "Yes" if fu.in_use else "No", fu.detail]
             for fu in analyze_feature_usage(env)],
            "Table 2-1. Feature usage mapped to licensing product.",
        )

    def _appendix(self, doc: DocumentObject, env: Environment) -> None:
        self._heading(doc, "Appendix A. Full Virtual Machine Inventory", level=1)
        self._table(
            doc, ["VM", "Power", "Cluster", "vCPU", "Mem (GiB)", "Disk (GiB)"],
            [[v.name, v.power_state, v.cluster_name, str(v.vcpu_total or ""),
              str(_gib(v.memory_bytes) or ""), str(_gib(v.total_disk_bytes) or "")]
             for v in env.vms],
            "Table A-1. Full VM inventory.",
        )

    # ─── helpers ───────────────────────────────────────────────────

    def _figure(self, doc: DocumentObject, diag: Diagram | None, caption: str) -> None:
        """Embed a rendered topology diagram, centered, with an italic caption."""
        if diag is None:
            return
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run()
        run.add_picture(io.BytesIO(diag.png), width=Inches(min(6.3, diag.width_in)))
        cap = doc.add_paragraph(caption)
        cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
        cap.runs[0].italic = True
        cap.runs[0].font.size = Pt(9)
        cap.runs[0].font.color.rgb = RGBColor(0x70, 0x70, 0x70)

    def _heading(self, doc: DocumentObject, text: str, level: int) -> None:
        h = doc.add_heading(text, level=level)
        for run in h.runs:
            run.font.color.rgb = _NAVY if level == 1 else _ACCENT

    def _table(self, doc: DocumentObject, headers: list[str], rows: list[list[Any]],
               caption: str, empty: str | None = None) -> None:
        if not rows:
            if empty:
                p = doc.add_paragraph(empty)
                p.runs[0].italic = True
            return
        table = doc.add_table(rows=1, cols=len(headers))
        table.style = "Table Grid"
        # Branded header row: navy fill, white bold text.
        for i, h in enumerate(headers):
            cell = table.rows[0].cells[i]
            _shade_cell(cell, _HEADER_BG)
            cell.text = ""
            run = cell.paragraphs[0].add_run(h)
            run.font.bold = True
            run.font.size = Pt(9)
            run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        for r_idx, row in enumerate(rows):
            cells = table.add_row().cells
            for i, value in enumerate(row):
                cells[i].text = ""
                run = cells[i].paragraphs[0].add_run("" if value is None else str(value))
                run.font.size = Pt(9)
                if r_idx % 2 == 1:  # zebra striping
                    _shade_cell(cells[i], _ZEBRA_BG)
        cap = doc.add_paragraph(caption)
        cap.runs[0].italic = True
        cap.runs[0].font.size = Pt(9)
        cap.runs[0].font.color.rgb = RGBColor(0x70, 0x70, 0x70)


def _shade_cell(cell: Any, hex_color: str) -> None:
    """Set a table cell's background fill (docx has no direct API for this)."""
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tc_pr.append(shd)


def _gib(value: int | None) -> float | None:
    if not value:
        return None
    return round(value / GIB, 1)
