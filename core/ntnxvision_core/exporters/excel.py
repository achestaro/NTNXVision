"""Excel exporter (openpyxl).

Produces an RVTools-style workbook: an Overview sheet plus one sheet per object type,
each with a frozen, styled header row and an auto-filter. Byte-valued fields are rendered
in GiB for readability. A sheet is omitted entirely when its object type has zero rows
(feature not deployed / not licensed) — Feature Usage and Collection Notes still record
that status, so the workbook stays a place to check "is X in use" without a noise sheet
per absent feature.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from ntnxvision_core.exporters.sourcing import source_hosts as _source_hosts
from ntnxvision_core.exporters.sourcing import sources as _sources
from ntnxvision_core.models import Environment

_HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
_HEADER_FONT = Font(color="FFFFFF", bold=True)
_TITLE_FONT = Font(size=14, bold=True, color="1F4E78")
GIB = 1024**3


class ExcelExporter:
    def export(self, env: Environment, path: str | Path) -> Path:
        wb = Workbook()
        # Phase 1 — core infrastructure
        self._overview_sheet(wb, env)
        self._pc_config_sheet(wb, env)
        self._clusters_sheet(wb, env)
        self._hosts_sheet(wb, env)
        self._vms_sheet(wb, env)
        self._system_vms_sheet(wb, env)
        self._storage_sheet(wb, env)
        self._subnets_sheet(wb, env)
        # PC catalog — templates / images / OVAs
        self._catalog_sheets(wb, env)
        # Phase 2 — data protection / DR
        self._dr_sheets(wb, env)
        # Phase 2 — Health / Alerts
        self._alerts_sheet(wb, env)
        # LCM — firmware / software currency
        self._lcm_sheet(wb, env)
        # Phase 2 — Flow
        self._flow_sheets(wb, env)
        # Phase 2 — NUS
        self._nus_sheets(wb, env)
        # Phase 2 — NDB
        self._ndb_sheets(wb, env)
        # Phase 2 — IAM / automation
        self._iam_sheets(wb, env)
        # Licensing + analysis + diagnostics
        self._licenses_sheet(wb, env)
        self._feature_usage_sheet(wb, env)
        self._notes_sheet(wb, env)

        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        wb.save(out)
        return out

    # ─── sheets ────────────────────────────────────────────────────

    def _overview_sheet(self, wb: Workbook, env: Environment) -> None:
        ws = cast(Worksheet, wb.active)
        ws.title = "Overview"
        ws["A1"] = "NTNXVision — Environment Inventory"
        ws["A1"].font = _TITLE_FONT
        # A combined run has no single management plane: name every contributing PC
        # rather than reporting a synthetic host with an "unknown" version.
        if env.is_combined:
            pc_rows: list[tuple[str, Any]] = [
                ("Prism Centrals", f"{len(env.source_environments)} (combined)"),
                ("Sources", ", ".join(_source_hosts(env))),
            ]
        else:
            pc_rows = [
                ("Prism Central", env.prism_central.host or ""),
                ("PC Version", env.prism_central.version or "unknown"),
            ]
        rows: list[tuple[str, Any]] = [
            *pc_rows,
            ("Collected At (UTC)", env.collected_at or ""),
            ("Tool Version", env.tool_version),
            ("", ""),
        ]
        for label, value in env.summary().items():
            rows.append((label.replace("_", " ").title(), value))
        for i, (label, value) in enumerate(rows, start=3):
            ws.cell(row=i, column=1, value=label).font = Font(bold=True)
            ws.cell(row=i, column=2, value=value)
        ws.column_dimensions["A"].width = 26
        ws.column_dimensions["B"].width = 44

    def _clusters_sheet(self, wb: Workbook, env: Environment) -> None:
        headers = [
            "Name", "UUID", "AOS Version", "NCC Version", "Hypervisors",
            "Nodes", "Redundancy Factor", "External IP", "Encryption", "Timezone", "API",
        ]
        rows = [
            [
                c.name, c.uuid, c.aos_version, c.ncc_version, ", ".join(c.hypervisor_types),
                c.num_nodes, c.redundancy_factor, c.external_ip,
                "Yes" if c.encryption_enabled else "No", c.timezone, c.api_source,
            ]
            for c in env.clusters
        ]
        self._table(wb, "Clusters", headers, rows, _sources(env, env.clusters))

    def _hosts_sheet(self, wb: Workbook, env: Environment) -> None:
        headers = [
            "Name", "Cluster", "Hypervisor", "Version", "CPU Model", "Sockets",
            "Cores", "Threads", "Memory (GiB)", "Block Model", "Serial",
            "CVM IP", "Hypervisor IP", "IPMI IP", "GPUs", "VMs", "API",
        ]
        rows = [
            [
                h.name, h.cluster_name, h.hypervisor_type, h.hypervisor_version, h.cpu_model,
                h.num_cpu_sockets, h.num_cpu_cores, h.num_cpu_threads, _gib(h.memory_bytes),
                h.block_model, h.serial_number, h.cvm_ip, h.hypervisor_ip, h.ipmi_ip,
                ", ".join(h.gpu_list), h.num_vms, h.api_source,
            ]
            for h in env.hosts
        ]
        self._table(wb, "Hosts", headers, rows, _sources(env, env.hosts))

    def _vms_sheet(self, wb: Workbook, env: Environment) -> None:
        self._vm_table(wb, "VMs", env.vms, sources=_sources(env, env.vms))

    def _system_vms_sheet(self, wb: Workbook, env: Environment) -> None:
        self._vm_table(wb, "System VMs", env.system_vms, include_type=True,
                       sources=_sources(env, env.system_vms))

    def _vm_table(self, wb: Workbook, title: str, vms: list, include_type: bool = False,
                  sources: list[str] | None = None) -> None:
        headers = ["Name", "Power", "Cluster", "Host"]
        if include_type:
            headers.insert(1, "Type")
        headers += [
            "vCPUs", "Cores/vCPU", "Total vCPU", "Memory (GiB)", "Provisioned Disk (GiB)",
            "Disks", "NICs", "IP Addresses", "Guest OS", "GPUs", "Categories", "UUID", "API",
        ]
        rows = []
        for v in vms:
            ips = ", ".join(ip for n in v.nics for ip in n.ip_addresses)
            base = [v.name, v.power_state, v.cluster_name, v.host_name]
            if include_type:
                base.insert(1, v.vm_type)
            rows.append(
                base + [
                    v.num_vcpus, v.num_cores_per_vcpu, v.vcpu_total, _gib(v.memory_bytes),
                    _gib(v.total_disk_bytes), len(v.disks), len(v.nics), ips,
                    v.guest_os, ", ".join(v.gpus), v.categories_display, v.uuid, v.api_source,
                ]
            )
        self._table(wb, title, headers, rows, sources)

    def _pc_config_sheet(self, wb: Workbook, env: Environment) -> None:
        if env.is_combined:
            self._source_pcs_sheet(wb, env)
            return
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
        """One row per contributing Prism Central — the combined-run replacement for the
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

    def _catalog_sheets(self, wb: Workbook, env: Environment) -> None:
        self._table(
            wb, "Templates",
            ["Name", "Guest OS", "vCPU", "Memory (GiB)", "Active Version", "Versions",
             "Created By", "Description"],
            [[t.name, t.guest_os, t.num_vcpus, _gib(t.memory_bytes), t.active_version,
              t.version_count, t.created_by, t.description] for t in env.templates],
        )
        self._table(
            wb, "Images",
            ["Name", "Type", "Size (GiB)", "Architecture", "Clusters", "State", "Source"],
            [[i.name, i.image_type, _gib(i.size_bytes), i.architecture,
              ", ".join(i.cluster_names), i.state, i.source] for i in env.images],
        )
        self._table(
            wb, "OVAs",
            ["Name", "Size (GiB)", "Disks", "Cluster", "Created"],
            [[o.name, _gib(o.size_bytes), o.disk_count, o.cluster_name, o.created]
             for o in env.ovas],
        )

    def _storage_sheet(self, wb: Workbook, env: Environment) -> None:
        headers = [
            "Name", "Cluster", "RF", "Compression", "Dedup", "Erasure Coding",
            "Max Capacity (GiB)", "Used (GiB)", "Free (GiB)", "API",
        ]
        rows = [
            [
                s.name, s.cluster_name, s.replication_factor,
                "On" if s.compression_enabled else "Off", s.deduplication or "None",
                s.erasure_coding or "Off", _gib(s.max_capacity_bytes),
                _gib(s.used_bytes), _gib(s.free_bytes), s.api_source,
            ]
            for s in env.storage_containers
        ]
        self._table(wb, "Storage", headers, rows, _sources(env, env.storage_containers))

    def _subnets_sheet(self, wb: Workbook, env: Environment) -> None:
        headers = [
            "Name", "Cluster", "Type", "VLAN", "Virtual Switch", "VPC",
            "IP Prefix", "Gateway", "DHCP", "API",
        ]
        rows = [
            [
                s.name, s.cluster_name, s.subnet_type, s.vlan_id, s.virtual_switch,
                s.vpc_name, s.ip_prefix, s.gateway_ip,
                "Yes" if s.dhcp_enabled else "No", s.api_source,
            ]
            for s in env.subnets
        ]
        self._table(wb, "Subnets", headers, rows, _sources(env, env.subnets))

    # ─── Phase 2: Data Protection / DR ─────────────────────────────

    def _dr_sheets(self, wb: Workbook, env: Environment) -> None:
        self._table(
            wb, "Protection Policies",
            ["Name", "RPO (s)", "Replication", "Local Retention", "Remote Retention",
             "Source AZ", "Target AZ", "Categories", "API"],
            [[p.name, p.rpo_seconds, p.replication_type, p.local_retention, p.remote_retention,
              p.source_az, p.target_az, ", ".join(p.categories), p.api_source]
             for p in env.protection_policies],
        )
        self._table(
            wb, "Recovery Plans",
            ["Name", "Stages", "Entities", "Primary Location", "Recovery Location",
             "Network Mappings", "API"],
            [[r.name, r.stage_count, r.entities_count, r.primary_location, r.recovery_location,
              r.network_mappings, r.api_source] for r in env.recovery_plans],
        )
        self._table(
            wb, "Availability Zones",
            ["Name", "Type", "Region", "URL", "Connection", "API"],
            [[a.name, a.az_type, a.region, a.url, a.connection_status, a.api_source]
             for a in env.availability_zones],
        )
        self._table(
            wb, "Remote Sites",
            ["Name", "Cluster", "Address", "Status", "API"],
            [[r.name, r.cluster_name, r.address, r.status, r.api_source]
             for r in env.remote_sites],
        )

    # ─── Phase 2: Health / Alerts ───────────────────────────────────

    def _alerts_sheet(self, wb: Workbook, env: Environment) -> None:
        self._table(
            wb, "Alerts",
            ["Severity", "Title", "Entity Type", "Entity", "Cluster", "Resolved",
             "Acknowledged", "Created", "API"],
            [[a.severity, a.title, a.entity_type, a.entity_name, a.cluster_name,
              "Yes" if a.resolved else "No", "Yes" if a.acknowledged else "No",
              a.created_at, a.api_source] for a in env.alerts],
        )

    # ─── LCM — Life Cycle Manager ──────────────────────────────────

    def _lcm_sheet(self, wb: Workbook, env: Environment) -> None:
        """Firmware and software inventory as Life Cycle Manager sees it.

        This is the widest of the three formats, so it carries the full `available_versions`
        list — the PDF and the ADD trim it, and this sheet is where a reader goes to see
        every upgrade target LCM actually offers for a component.

        `target_version` is deliberately not a column. Despite the name it is not an
        upgrade target: it is the version LCM currently aims at, which is the running one
        unless an upgrade is staged — identical to Current Version on all 34 components of
        the lab PC. A column duplicating its neighbour reads as a bug in the report. The
        field is still collected and lives in the snapshot for anyone who wants it.
        """
        headers = [
            "Component", "Class", "Cluster", "Current Version",
            "Update Available", "Available Versions", "Last Updated", "API",
        ]
        rows = [
            [
                e.name, e.entity_class, e.cluster_name, e.current_version,
                _update_flag(e.update_available), ", ".join(e.available_versions),
                e.last_updated, e.api_source,
            ]
            for e in env.lcm_entities
        ]
        self._table(wb, "LCM (Life Cycle Manager)", headers, rows,
                    _sources(env, env.lcm_entities))

    # ─── Phase 2: Flow ─────────────────────────────────────────────

    def _flow_sheets(self, wb: Workbook, env: Environment) -> None:
        self._table(
            wb, "Flow Security Policies",
            ["Name", "Type", "State", "Inbound Rules", "Outbound Rules", "Secured Group", "API"],
            [[p.name, p.policy_type, p.state, p.inbound_rule_count, p.outbound_rule_count,
              p.secured_group, p.api_source] for p in env.security_policies],
        )
        self._table(
            wb, "Flow Categories",
            ["Name", "Value", "Description", "API"],
            [[c.name, c.value, c.description, c.api_source] for c in env.categories],
        )
        self._table(
            wb, "Service Groups",
            ["Name", "System", "TCP Ports", "UDP Ports", "ICMP", "API"],
            [[s.name, "Yes" if s.is_system else "No", s.tcp_ports, s.udp_ports,
              "Yes" if s.icmp else "No", s.api_source] for s in env.service_groups],
        )
        self._table(
            wb, "Address Groups",
            ["Name", "System", "IP Ranges", "API"],
            [[a.name, "Yes" if a.is_system else "No", ", ".join(a.ip_ranges), a.api_source]
             for a in env.address_groups],
        )
        self._table(
            wb, "VPCs",
            ["Name", "Subnets", "External Subnets", "Routable Prefixes", "Routes",
             "Routing Policies", "DNS Servers", "API"],
            [[v.name, ", ".join(v.subnet_names), ", ".join(v.external_subnets),
              ", ".join(v.externally_routable_prefixes), v.route_count,
              v.routing_policy_count, ", ".join(v.dns_servers), v.api_source]
             for v in env.vpcs],
        )
        self._table(
            wb, "Floating IPs",
            ["Floating IP", "VPC", "External Subnet", "Private IP", "API"],
            [[f.floating_ip, f.vpc_name, f.external_subnet, f.associated_private_ip,
              f.api_source] for f in env.floating_ips],
        )

    # ─── Phase 2: NUS (Files / Objects / Volumes) ──────────────────

    def _nus_sheets(self, wb: Workbook, env: Environment) -> None:
        self._table(
            wb, "File Servers",
            ["Name", "Cluster", "Version", "FSVMs", "Size (GiB)", "Used (GiB)", "Shares", "API"],
            [[f.name, f.cluster_name, f.version, f.num_fsvm, _gib(f.size_bytes),
              _gib(f.used_bytes), f.share_count, f.api_source] for f in env.file_servers],
        )
        self._table(
            wb, "File Shares",
            ["Name", "File Server", "Type", "Protocol", "Max Size (GiB)", "Used (GiB)", "API"],
            [[s.name, s.file_server_name, s.share_type, s.protocol, _gib(s.max_size_bytes),
              _gib(s.used_bytes), s.api_source] for s in env.file_shares],
        )
        self._table(
            wb, "Object Stores",
            ["Name", "Cluster", "Version", "Workers", "Capacity (GiB)", "Used (GiB)",
             "Buckets", "State", "API"],
            [[o.name, o.cluster_name, o.deployment_version, o.num_worker_nodes,
              _gib(o.total_capacity_bytes), _gib(o.used_bytes), o.bucket_count, o.state,
              o.api_source] for o in env.object_stores],
        )
        self._table(
            wb, "Buckets",
            ["Name", "Object Store", "Versioning", "WORM", "Objects", "Used (GiB)", "API"],
            [[b.name, b.object_store_name, "Yes" if b.versioning else "No",
              "Yes" if b.worm_enabled else "No", b.object_count, _gib(b.used_bytes),
              b.api_source] for b in env.buckets],
        )
        self._table(
            wb, "Volume Groups",
            ["Name", "Cluster", "Disks", "Capacity (GiB)", "Shared", "iSCSI Target",
             "Attachments", "API"],
            [[v.name, v.cluster_name, v.disk_count, _gib(v.total_capacity_bytes),
              "Yes" if v.is_shared else "No", v.iscsi_target, v.attachment_count, v.api_source]
             for v in env.volume_groups],
        )

    # ─── Phase 2: NDB ──────────────────────────────────────────────

    def _ndb_sheets(self, wb: Workbook, env: Environment) -> None:
        self._table(
            wb, "Databases",
            ["Name", "Engine", "Type", "Status", "DB Server", "Time Machine", "SLA",
             "Clone", "API"],
            [[d.name, d.db_engine, d.db_type, d.status, d.db_server_name, d.time_machine_name,
              d.sla_name, "Yes" if d.clone else "No", d.api_source] for d in env.databases],
        )
        self._table(
            wb, "Database Servers",
            ["Name", "Engine", "IPs", "Status", "Cluster", "Cores", "Memory (GB)", "API"],
            [[s.name, s.db_engine, ", ".join(s.ip_addresses), s.status, s.cluster_name,
              s.vm_cores, s.memory_gb, s.api_source] for s in env.database_servers],
        )
        self._table(
            wb, "Time Machines",
            ["Name", "Database", "SLA", "Status", "Schedule", "Clones", "API"],
            [[t.name, t.database_name, t.sla_name, t.status, t.schedule, t.clone_count,
              t.api_source] for t in env.time_machines],
        )

    # ─── Phase 2: IAM / automation ─────────────────────────────────

    def _iam_sheets(self, wb: Workbook, env: Environment) -> None:
        self._table(
            wb, "Users",
            ["Username", "Display Name", "Type", "Directory", "State", "API"],
            [[u.username, u.display_name, u.user_type, u.directory, u.state, u.api_source]
             for u in env.users],
        )
        self._table(
            wb, "Roles",
            ["Name", "Description", "System", "Permissions", "API"],
            [[r.name, r.description, "Yes" if r.is_system else "No", r.permission_count,
              r.api_source] for r in env.roles],
        )
        self._table(
            wb, "Projects",
            ["Name", "Description", "Users", "API"],
            [[p.name, p.description, p.user_count, p.api_source] for p in env.projects],
        )
        self._table(
            wb, "Playbooks (X-Play)",
            ["Name", "Trigger", "Actions", "Enabled", "Description", "API"],
            [[p.name, p.trigger_type, p.action_count, "Yes" if p.is_enabled else "No",
              p.description, p.api_source] for p in env.playbooks],
        )
        self._table(
            wb, "NCM Reports",
            ["Name", "Type", "Schedule", "API"],
            [[r.name, r.report_type, r.schedule, r.api_source] for r in env.ncm_reports],
        )

    def _feature_usage_sheet(self, wb: Workbook, env: Environment) -> None:
        from ntnxvision_core.analysis import analyze_feature_usage

        rows = [fu.as_row() for fu in analyze_feature_usage(env)]
        self._table(wb, "Feature Usage", ["Product", "Feature", "In Use", "Detail"], rows)

    def _licenses_sheet(self, wb: Workbook, env: Environment) -> None:
        headers = ["Product", "Edition", "Category", "Quantity", "Expiry", "Compliance"]
        rows = [
            [lic.product, lic.edition, lic.category, lic.quantity, lic.expiry, lic.compliance]
            for lic in env.licenses
        ]
        self._table(wb, "Licensing", headers, rows)

    def _notes_sheet(self, wb: Workbook, env: Environment) -> None:
        from ntnxvision_core.notes import clean_error, pretty_label

        headers = ["Area", "Outcome", "Detail"]
        rows: list[list[Any]] = []
        for key, status in env.collection_notes.items():
            if status == "ok":
                outcome, detail = "Collected", ""
            elif status.startswith("skipped"):
                outcome = "Not present"
                detail = status.split(":", 1)[1].strip() if ":" in status else status
            else:
                outcome, detail = "Error", clean_error(status)
            rows.append([pretty_label(key), outcome, detail])
        for w in env.warnings:
            rows.append(["Compatibility", "Warning", w])
        self._table(wb, "Collection Notes", headers, rows)

    # ─── table helper ──────────────────────────────────────────────

    def _table(
        self, wb: Workbook, title: str, headers: list[str], rows: list[list[Any]],
        sources: list[str] | None = None,
    ) -> None:
        """Write one sheet. `sources` (combined runs only) prepends a "Prism Central"
        column naming the PC each row came from — object names are not unique across
        PCs, so without it two merged rows can be indistinguishable.
        """
        if not rows:
            return
        if sources is not None:
            headers = ["Prism Central", *headers]
            rows = [[src, *row] for src, row in zip(sources, rows, strict=True)]
        ws: Worksheet = wb.create_sheet(_safe_title(title))
        ws.append(headers)
        for col_idx, _ in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = _HEADER_FILL
            cell.font = _HEADER_FONT
            cell.alignment = Alignment(vertical="center")
        for row in rows:
            ws.append(row)

        ws.freeze_panes = "A2"
        last_col = get_column_letter(len(headers))
        ws.auto_filter.ref = f"A1:{last_col}{max(len(rows) + 1, 1)}"
        self._autosize(ws, headers, rows)

    @staticmethod
    def _autosize(ws: Worksheet, headers: list[str], rows: list[list[Any]]) -> None:
        for col_idx, header in enumerate(headers, start=1):
            width = len(str(header))
            for row in rows:
                if col_idx - 1 < len(row) and row[col_idx - 1] is not None:
                    width = max(width, len(str(row[col_idx - 1])))
            ws.column_dimensions[get_column_letter(col_idx)].width = min(width + 2, 50)


def _safe_title(title: str) -> str:
    """Excel sheet titles disallow []:*?/\\ and cap at 31 chars."""
    for ch in "[]:*?/\\":
        title = title.replace(ch, "-")
    return title[:31]


def _update_flag(value: bool | None) -> str:
    """Yes / No / Unknown for an LCM update flag.

    None means LCM did not tell us whether a newer version exists; rendering that as
    "No" would assert the component is current, which is exactly the wrong answer to
    the one question this sheet is here to answer.
    """
    if value is None:
        return "Unknown"
    return "Yes" if value else "No"


def _gib(value: int | None) -> float | None:
    if not value:
        return None
    return round(value / GIB, 2)
