"""Excel exporter (openpyxl).

Produces an RVTools-style workbook: an Overview sheet plus one sheet per object type,
each with a frozen, styled header row and an auto-filter. Byte-valued fields are rendered
in GiB for readability.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from ntnxvision_core.models import Environment

_HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
_HEADER_FONT = Font(color="FFFFFF", bold=True)
_TITLE_FONT = Font(size=14, bold=True, color="1F4E78")
GIB = 1024**3


class ExcelExporter:
    def export(self, env: Environment, path: str | Path) -> Path:
        wb = Workbook()
        self._overview_sheet(wb, env)
        self._clusters_sheet(wb, env)
        self._hosts_sheet(wb, env)
        self._vms_sheet(wb, env)
        self._storage_sheet(wb, env)
        self._subnets_sheet(wb, env)
        self._licenses_sheet(wb, env)
        self._notes_sheet(wb, env)

        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        wb.save(out)
        return out

    # ─── sheets ────────────────────────────────────────────────────

    def _overview_sheet(self, wb: Workbook, env: Environment) -> None:
        ws = wb.active
        ws.title = "Overview"
        ws["A1"] = "NTNXVision — Environment Inventory"
        ws["A1"].font = _TITLE_FONT
        rows = [
            ("Prism Central", env.prism_central.host or ""),
            ("PC Version", env.prism_central.version or "unknown"),
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
        self._table(wb, "Clusters", headers, rows)

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
        self._table(wb, "Hosts", headers, rows)

    def _vms_sheet(self, wb: Workbook, env: Environment) -> None:
        headers = [
            "Name", "Power", "Cluster", "Host", "vCPUs", "Cores/vCPU", "Total vCPU",
            "Memory (GiB)", "Provisioned Disk (GiB)", "Disks", "NICs", "IP Addresses",
            "Guest OS", "GPUs", "Categories", "UUID", "API",
        ]
        rows = []
        for v in env.vms:
            ips = ", ".join(ip for n in v.nics for ip in n.ip_addresses)
            cats = ", ".join(f"{k}:{val}" for k, val in v.categories.items())
            rows.append(
                [
                    v.name, v.power_state, v.cluster_name, v.host_name, v.num_vcpus,
                    v.num_cores_per_vcpu, v.vcpu_total, _gib(v.memory_bytes),
                    _gib(v.total_disk_bytes), len(v.disks), len(v.nics), ips,
                    v.guest_os, ", ".join(v.gpus), cats, v.uuid, v.api_source,
                ]
            )
        self._table(wb, "VMs", headers, rows)

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
        self._table(wb, "Storage", headers, rows)

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
        self._table(wb, "Subnets", headers, rows)

    def _licenses_sheet(self, wb: Workbook, env: Environment) -> None:
        headers = ["Product", "Edition", "Category", "Quantity", "Expiry", "Compliance"]
        rows = [
            [lic.product, lic.edition, lic.category, lic.quantity, lic.expiry, lic.compliance]
            for lic in env.licenses
        ]
        self._table(wb, "Licensing", headers, rows)

    def _notes_sheet(self, wb: Workbook, env: Environment) -> None:
        headers = ["Collector", "Status"]
        rows = [[k, v] for k, v in env.collection_notes.items()]
        self._table(wb, "Collection Notes", headers, rows)

    # ─── table helper ──────────────────────────────────────────────

    def _table(self, wb: Workbook, title: str, headers: list[str], rows: list[list[Any]]) -> None:
        ws: Worksheet = wb.create_sheet(title)
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


def _gib(value: int | None) -> float | None:
    if not value:
        return None
    return round(value / GIB, 2)
