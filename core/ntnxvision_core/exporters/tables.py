"""Column definitions shared by the Excel exporter and the Inventory screen.

Each object type has one `TableSpec`: how to list its objects from an `Environment` and
one `Col` per column. A column says where its value comes from (`get`), how Excel renders
it (`header` + `excel`), and how the Inventory grid renders it (`label` + `kind` +
`cell`). A column with ``header=None`` is grid-only (e.g. a VM's resolved protection
policy); one with ``label=None`` is Excel-only (e.g. the long tail of identifiers).
``grid=False`` keeps a labelled column out of the Inventory grid (and so out of its
Columns menu) while the detail pane and the reports still show it: the cluster's Data
Services IP reads better as a detail than as a grid column.

**Capacity lives in one place.** The cluster capacity and oversubscription figures (vCPU
Total, vCPU:pCore, Storage Total, Provisioned:Usable, each with N-1) are not Clusters
columns: the Excel workbook has them on the Capacity sheet (`CAPACITY`), the PDF and Word
reports in their "Capacity & Oversubscription" section, and the app in the cluster detail
pane (`inventory.capacity_fields`). `core/tests/test_grid_columns.py` checks that nothing
is lost.

Keeping both views on one definition is the point: the grid a user filters on screen and
the sheet they hand to a customer show the same columns, computed the same way. The Excel
workbook keeps its exact historical headers and order — `ExcelExporter` renders every
sheet below from these specs.

**Column order rule.** In every table the Cluster column comes first (the cluster *name*
column of the Clusters table is headed "Cluster" for the same reason). `TableSpec` enforces
it when the spec is built, so a column appended later still lands after Cluster; only the
VM tables opt out (`cluster_first=False`), where Name, Power and Cluster read better in
that order. In combined multi-PC tables the "Prism Central" attribution column sits right
after Cluster (see `sourcing.with_sources`). `core/tests/test_column_order.py` checks it.

`diff` names the field label Compare uses when that column changes between snapshots;
columns without it (counters that move constantly, timestamps) are not compared.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ntnxvision_core.capacity import (
    ClusterCapacity,
    coerce_ratio,
    compute_environment,
    target_label,
    targets_summary,
    valid_ratio,
)
from ntnxvision_core.exporters.theme import on_off, power_label, switch_label
from ntnxvision_core.models import Environment
from ntnxvision_core.numfmt import fmt_ratio

GIB = 1024**3


class Ctx:
    """Per-Environment lookups a few derived columns need (built once, cached)."""

    def __init__(self, env: Environment, vcpu_ratio: float | None = None) -> None:
        self.env = env
        #: Target vCPU:pCPU ratio for clusters that recorded none at collection
        #: (snapshots written before per-cluster targets); see `capacity.cluster_target`.
        self.vcpu_ratio = coerce_ratio(vcpu_ratio)
        #: The default target of this snapshot: the global setting recorded at
        #: collection, else the fallback above.
        self.default_ratio = valid_ratio(env.vcpu_ratio_default) or self.vcpu_ratio
        self._capacity: dict[int, ClusterCapacity] | None = None
        self.cluster_by_uuid = {c.uuid: c for c in env.clusters if c.uuid}
        # Prism Element mode inventories exactly one cluster, and its v2.0 payloads leave
        # the cluster off storage containers and networks; everything belongs to it.
        self.only_cluster = (env.clusters[0].name
                             if env.source_type == "prism_element" and len(env.clusters) == 1
                             else None)
        self.vms_per_cluster: dict[str, int] = {}
        for vm in env.vms:
            key = vm.cluster_uuid or vm.cluster_name or ""
            self.vms_per_cluster[key] = self.vms_per_cluster.get(key, 0) + 1
        self.hosts_per_cluster: dict[str, int] = {}
        self.model_per_cluster: dict[str, str] = {}
        for h in env.hosts:
            key = h.cluster_uuid or h.cluster_name or ""
            self.hosts_per_cluster[key] = self.hosts_per_cluster.get(key, 0) + 1
            if h.block_model and key not in self.model_per_cluster:
                self.model_per_cluster[key] = h.block_model
        # "Key:Value" → protection policy name, for resolving a VM's policy.
        self.policy_by_category: dict[str, str] = {}
        for p in env.protection_policies:
            for cat in p.categories:
                key, _, values = cat.partition(":")
                for value in values.split(","):
                    self.policy_by_category.setdefault(f"{key}:{value}", p.name or p.uuid)

    def cluster_name(self, obj: Any) -> str | None:
        """The object's cluster name: as reported, else resolved from its cluster UUID
        (v4 and v2.0 payloads often carry only the UUID), else the single PE cluster."""
        name = getattr(obj, "cluster_name", None)
        if name:
            return str(name)
        cluster = self.cluster_by_uuid.get(getattr(obj, "cluster_uuid", None) or "")
        if cluster is not None and cluster.name:
            return cluster.name
        return self.only_cluster

    def capacity(self, cluster: Any) -> ClusterCapacity | None:
        """The cluster's capacity figures (computed for every cluster on first use)."""
        if self._capacity is None:
            self._capacity = compute_environment(self.env, self.vcpu_ratio)
        return self._capacity.get(id(cluster))

    def targets_text(self) -> str:
        """The vCPU:pCPU target(s) behind the capacity figures, as one phrase for the
        reports ("a 4.0 : 1 vCPU:pCPU target", or the default plus custom ones)."""
        caps = [k for c in self.env.clusters if (k := self.capacity(c)) is not None]
        return targets_summary(caps, self.default_ratio)

    def vm_policy(self, vm: Any) -> str | None:
        if vm.protection_policy:
            return str(vm.protection_policy)
        for key, values in vm.categories.items():
            for value in values:
                name = self.policy_by_category.get(f"{key}:{value}")
                if name:
                    return name
        return None

    def vm_protection(self, vm: Any) -> str:
        policy = self.vm_policy(vm)
        if policy:
            return policy
        if (vm.protection_type or "").upper() in ("RULE_PROTECTED", "PD_PROTECTED"):
            return "Protected"
        return "None"


Getter = Callable[[Any, Ctx], Any]


@dataclass(frozen=True)
class Col:
    key: str
    header: str | None
    get: Getter
    excel: Callable[[Any], Any] | None = None
    label: str | None = None
    kind: str = "text"
    cell: Callable[[Any], Any] | None = None
    search: bool = False
    facet: bool = False
    diff: str | None = None
    sortable: bool = True
    #: False = a column of the detail pane and the reports, never a grid column.
    grid: bool = True


@dataclass(frozen=True)
class TableSpec:
    type: str
    sheet: str
    label: str
    singular: str
    unit_plural: str
    items: Callable[[Environment], list[Any]]
    cols: tuple[Col, ...]
    id: Callable[[Any], str]
    name: Callable[[Any], str]
    where: Callable[[Any], str | None] = lambda _o: None
    #: collection_notes keys that decide whether this type was collected at all.
    note_keys: tuple[str, ...] = ()
    #: Prepend the "Prism Central" attribution column in combined Excel exports.
    sources: bool = False
    group: str | None = None
    #: Move the Cluster column to the front (the house rule); False only for VM tables.
    cluster_first: bool = True

    def __post_init__(self) -> None:
        if self.cluster_first:
            object.__setattr__(self, "cols", cluster_first(self.cols))

    @property
    def cluster_col(self) -> Col | None:
        """The column naming each row's cluster (the name column on the Clusters table)."""
        for c in self.cols:
            if c.key == CLUSTER_KEY or (self.type == "clusters" and c.key == "name"):
                return c
        return None

    @property
    def excel_cols(self) -> list[Col]:
        return [c for c in self.cols if c.header is not None]

    @property
    def ui_cols(self) -> list[Col]:
        """The Inventory grid's columns: labelled and not excluded with ``grid=False``."""
        return [c for c in self.cols if c.label is not None and c.grid]


#: The key of the per-row cluster column in every spec.
CLUSTER_KEY = "cluster"


def cluster_first(cols: tuple[Col, ...]) -> tuple[Col, ...]:
    """`cols` with the Cluster column moved to the front, everything else in order."""
    first = [c for c in cols if c.key == CLUSTER_KEY]
    return (*first, *(c for c in cols if c.key != CLUSTER_KEY))


# ─── value helpers ─────────────────────────────────────────────────────


def gib(value: int | None) -> float | None:
    if not value:
        return None
    return round(value / GIB, 2)


def yes_no(value: Any) -> str:
    return "Yes" if value else "No"


def joined(values: list[Any] | None) -> str:
    return ", ".join(str(v) for v in values or [] if v is not None and v != "")


def update_flag(value: bool | None) -> str:
    """Yes / No / Unknown — None must not read as "current" (see the LCM sheet)."""
    if value is None:
        return "Unknown"
    return "Yes" if value else "No"


def _a(name: str) -> Getter:
    return lambda o, _c: getattr(o, name, None)


def _cluster(o: Any, c: Ctx) -> str | None:
    return c.cluster_name(o)


def power_cell(raw: Any) -> dict[str, Any] | None:
    if not raw:
        return None
    on = str(raw).upper() in ("ON", "POWERED_ON")
    return {"tone": "ok" if on else "idle", "label": "On" if on else "Off"}


def severity_cell(raw: Any) -> dict[str, Any] | None:
    if not raw:
        return None
    tone = {"CRITICAL": "error", "WARNING": "warn", "INFO": "live"}.get(str(raw), "idle")
    return {"tone": tone, "label": str(raw).title()}


def state_cell(ok_values: tuple[str, ...]) -> Callable[[Any], Any]:
    def cell(raw: Any) -> dict[str, Any] | None:
        if raw is None or raw == "":
            return None
        text = str(raw)
        return {"tone": "ok" if text.upper() in ok_values else "warn",
                "label": text.replace("_", " ").title()}
    return cell


def bool_cell(true_label: str, false_label: str, false_tone: str = "idle"
              ) -> Callable[[Any], Any]:
    def cell(raw: Any) -> dict[str, Any] | None:
        if raw is None:
            return None
        return {"tone": "ok" if raw else false_tone, "label": true_label if raw
                else false_label}
    return cell


def _vm_ips(v: Any, _c: Ctx) -> str:
    return ", ".join(ip for n in v.nics for ip in n.ip_addresses)


def _cluster_key(c: Any) -> str:
    return c.uuid or c.name or ""


def _cap(field: str) -> Getter:
    """A capacity figure of a cluster (see `capacity.py`)."""
    def get(c: Any, x: Ctx) -> Any:
        cap = x.capacity(c)
        return getattr(cap, field, None) if cap is not None else None
    return get


def _target(c: Any, x: Ctx) -> str | None:
    """The cluster's target ratio and where it came from: "6.0 : 1 (custom)"."""
    cap = x.capacity(c)
    return target_label(cap.target_ratio, cap.target_custom) if cap is not None else None


def _int_or_none(value: Any) -> int | None:
    return None if value is None else int(round(value))


def ratio_text(value: Any) -> str:
    """Excel cell for a ratio: "3.5 : 1", or N/A."""
    return fmt_ratio(value)


# ─── specs ─────────────────────────────────────────────────────────────

INFRA = "Infrastructure"
PROTECT = "Protection and security"
SERVICES = "Services"
GOVERN = "Governance"

CLUSTERS = TableSpec(
    "clusters", "Clusters", "Clusters", "Cluster", "clusters", lambda e: e.clusters,
    (
        Col("name", "Cluster", _a("name"), label="Cluster", kind="name", search=True),
        Col("uuid", "UUID", _a("uuid")),
        Col("aos", "AOS Version", _a("aos_version"), label="AOS", kind="mono", facet=True,
            diff="AOS"),
        Col("ncc", "NCC Version", _a("ncc_version"), label="NCC", kind="mono", diff="NCC"),
        Col("hypervisor", "Hypervisors", lambda c, _x: joined(c.hypervisor_types),
            label="Hypervisor", facet=True, search=True),
        Col("hosts", "Nodes", _a("num_nodes"), label="Hosts", kind="number", diff="nodes"),
        Col("vms", None, lambda c, x: x.vms_per_cluster.get(_cluster_key(c),
                                                           x.vms_per_cluster.get(c.name or "", 0)),
            label="VMs", kind="number"),
        Col("rf", "Redundancy Factor", _a("redundancy_factor"), label="RF", kind="number",
            diff="redundancy factor"),
        Col("external_ip", "External IP", _a("external_ip"), label="External IP", kind="mono",
            search=True),
        Col("data_services_ip", "Data Services IP", _a("data_services_ip"),
            label="Data Services IP", kind="mono", search=True, diff="Data Services IP",
            grid=False),
        Col("vcenter", "vCenter", _a("vcenter_address"), label="vCenter", kind="mono",
            search=True, diff="vCenter", grid=False),
        Col("encryption", "Encryption", _a("encryption_enabled"), excel=yes_no,
            label="Encryption", kind="status", cell=bool_cell("On", "Off"), facet=True,
            diff="encryption"),
        Col("model", None, lambda c, x: x.model_per_cluster.get(_cluster_key(c)),
            label="Model", search=True),
        Col("timezone", "Timezone", _a("timezone")),
        Col("api", "API", _a("api_source")),
        # Capacity and oversubscription figures live on the Capacity sheet (CAPACITY below),
        # the reports' capacity section and the detail pane, not here (see module docs).
    ),
    id=lambda c: c.uuid, name=lambda c: c.name or c.uuid, where=lambda c: c.source_pc,
    note_keys=("clusters",), sources=True, group=INFRA,
)

#: The Excel "Capacity" sheet: every input and result of the capacity math, per cluster.
#: The only workbook home of these figures. Not an Inventory type (the cluster detail pane
#: shows them). Provisioned figures count every VM, powered on or off; N-1 drops the
#: costliest node and is N/A below three nodes.
CAPACITY = TableSpec(
    "capacity", "Capacity", "Capacity", "Cluster", "clusters", lambda e: e.clusters,
    (
        Col("name", "Cluster", _a("name")),
        Col("nodes", "Nodes", _cap("node_count")),
        Col("rf", "RF", _cap("rf")),
        Col("cores", "Physical Cores", _cap("physical_cores")),
        Col("cvm", "CVM vCPUs", _cap("cvm_vcpus")),
        Col("usable", "Usable Cores", _cap("usable_cores")),
        Col("usable_n1", "Usable Cores N-1", _cap("usable_cores_n1")),
        Col("target", "Target vCPU:pCore", _target),
        Col("vcpu_total", "vCPU Total", lambda c, x: _int_or_none(_cap("vcpu_total")(c, x))),
        Col("vcpu_total_n1", "vCPU Total N-1",
            lambda c, x: _int_or_none(_cap("vcpu_total_n1")(c, x))),
        Col("vms", "VMs", _cap("vm_count")),
        Col("prov_vcpu", "Provisioned vCPU (all VMs)", _cap("provisioned_vcpus")),
        Col("prov_vcpu_on", "Provisioned vCPU (powered on)", _cap("provisioned_vcpus_on")),
        Col("cpu_ratio", "vCPU:pCore (all VMs)", _cap("cpu_ratio"), excel=ratio_text),
        Col("cpu_ratio_n1", "vCPU:pCore N-1", _cap("cpu_ratio_n1"), excel=ratio_text),
        Col("cpu_ratio_on", "vCPU:pCore (powered on)", _cap("cpu_ratio_on"), excel=ratio_text),
        Col("raw", "Raw Storage (GiB)", _cap("raw_bytes"), excel=gib),
        Col("storage_total", "Storage Total (GiB)", _cap("storage_total_bytes"), excel=gib),
        Col("storage_total_n1", "Storage Total N-1 (GiB)", _cap("storage_total_n1_bytes"),
            excel=gib),
        Col("prov_disk", "Provisioned Disk (GiB)", _cap("provisioned_disk_bytes"), excel=gib),
        Col("storage_ratio", "Storage Provisioned:Usable", _cap("storage_ratio"),
            excel=ratio_text),
        Col("storage_ratio_n1", "Storage Provisioned:Usable N-1", _cap("storage_ratio_n1"),
            excel=ratio_text),
        Col("notes", "Notes", lambda c, x: " ".join(_cap("notes")(c, x) or [])),
    ),
    id=lambda c: c.uuid, name=lambda c: c.name or c.uuid, where=lambda c: c.source_pc,
    note_keys=("clusters", "hosts"), sources=True, group=INFRA,
)

HOSTS = TableSpec(
    "hosts", "Hosts", "Hosts", "Host", "hosts", lambda e: e.hosts,
    (
        Col("name", "Name", _a("name"), label="Name", kind="name", search=True),
        Col("cluster", "Cluster", _cluster, label="Cluster", facet=True, search=True),
        Col("hypervisor", "Hypervisor", _a("hypervisor_type"), label="Hypervisor", facet=True),
        Col("hv_version", "Version", _a("hypervisor_version"), label="Hypervisor version",
            kind="mono", diff="hypervisor version"),
        Col("cpu_model", "CPU Model", _a("cpu_model"), label="CPU", search=True),
        Col("sockets", "Sockets", _a("num_cpu_sockets")),
        Col("cores", "Cores", _a("num_cpu_cores"), label="Cores", kind="number", diff="cores"),
        Col("threads", "Threads", _a("num_cpu_threads")),
        Col("memory", "Memory (GiB)", _a("memory_bytes"), excel=gib, label="Memory",
            kind="bytes", diff="memory"),
        Col("block_model", "Block Model", _a("block_model"), label="Model", facet=True),
        Col("serial", "Serial", _a("serial_number"), search=True),
        Col("cvm_ip", "CVM IP", _a("cvm_ip"), label="CVM IP", kind="mono", search=True),
        Col("hypervisor_ip", "Hypervisor IP", _a("hypervisor_ip"), search=True),
        Col("ipmi_ip", "IPMI IP", _a("ipmi_ip"), search=True),
        Col("gpus", "GPUs", lambda h, _x: joined(h.gpu_list)),
        Col("vms", "VMs", _a("num_vms"), label="VMs", kind="number"),
        Col("maintenance", None, _a("maintenance_state"), label="State", kind="status",
            cell=state_cell(("NORMAL",)), diff="maintenance state"),
        Col("api", "API", _a("api_source")),
    ),
    id=lambda h: h.uuid, name=lambda h: h.name or h.uuid, where=lambda h: h.cluster_name,
    note_keys=("hosts",), sources=True, group=INFRA,
)


def _vm_cols(include_type: bool) -> tuple[Col, ...]:
    cols = [
        Col("name", "Name", _a("name"), label="Name", kind="name", search=True),
        Col("power", "Power", _a("power_state"), excel=power_label, label="Power",
            kind="status", cell=power_cell, facet=True, diff="power"),
        Col("cluster", "Cluster", _cluster, label="Cluster", facet=True, search=True),
        Col("host", "Host", _a("host_name"), search=True),
    ]
    if include_type:
        cols.insert(1, Col("type", "Type", _a("vm_type"), label="Type", facet=True))
    cols += [
        Col("sockets", "vCPUs", _a("num_vcpus")),
        Col("cores_per", "Cores/vCPU", _a("num_cores_per_vcpu")),
        Col("vcpu", "Total vCPU", lambda v, _x: v.vcpu_total, label="vCPU", kind="number",
            diff="vCPU"),
        Col("memory", "Memory (GiB)", _a("memory_bytes"), excel=gib, label="Memory",
            kind="bytes", diff="memory"),
        Col("storage", "Provisioned Disk (GiB)", lambda v, _x: v.total_disk_bytes, excel=gib,
            label="Storage", kind="bytes", diff="provisioned storage"),
        Col("disks", "Disks", lambda v, _x: len(v.disks)),
        Col("nics", "NICs", lambda v, _x: len(v.nics)),
        Col("ip", "IP Addresses", _vm_ips, search=True),
        Col("os", "Guest OS", _a("guest_os"), label="OS", search=True, facet=True,
            diff="guest OS"),
        Col("gpus", "GPUs", lambda v, _x: joined(v.gpus)),
        Col("categories", "Categories", lambda v, _x: v.categories_display, search=True),
        Col("protection", None, lambda v, x: x.vm_protection(v), label="Protection",
            facet=True, diff="protection"),
        Col("ip_ui", None, lambda v, x: _vm_ips(v, x) or None, label="IP", kind="mono"),
        Col("uuid", "UUID", _a("uuid"), search=True),
        Col("api", "API", _a("api_source")),
    ]
    return tuple(cols)


VMS = TableSpec(
    "vms", "VMs", "Virtual machines", "VM", "VMs", lambda e: e.vms, _vm_cols(False),
    id=lambda v: v.uuid, name=lambda v: v.name, where=lambda v: v.cluster_name,
    note_keys=("vms",), sources=True, group=INFRA, cluster_first=False,
)
SYSTEM_VMS = TableSpec(
    "system_vms", "System VMs", "System VMs", "System VM", "system VMs",
    lambda e: e.system_vms, _vm_cols(True),
    id=lambda v: v.uuid, name=lambda v: v.name, where=lambda v: v.cluster_name,
    note_keys=("vms",), sources=True, group=INFRA, cluster_first=False,
)

STORAGE = TableSpec(
    "storage_containers", "Storage", "Storage containers", "Storage container",
    "storage containers", lambda e: e.storage_containers,
    (
        Col("name", "Name", _a("name"), label="Name", kind="name", search=True),
        Col("cluster", "Cluster", _cluster, label="Cluster", facet=True, search=True),
        Col("rf", "RF", _a("replication_factor"), label="RF", kind="number",
            diff="replication factor"),
        Col("compression", "Compression", _a("compression_enabled"),
            excel=lambda v: "On" if v else "Off", label="Compression", kind="status",
            cell=bool_cell("On", "Off"), facet=True, diff="compression"),
        Col("dedup", "Dedup", _a("deduplication"), excel=on_off,
            label="Dedup", facet=True, diff="dedup"),
        Col("ec", "Erasure Coding", _a("erasure_coding"), excel=on_off,
            diff="erasure coding"),
        Col("capacity", "Max Capacity (GiB)", _a("max_capacity_bytes"), excel=gib,
            label="Capacity", kind="bytes", diff="capacity"),
        Col("used", "Used (GiB)", _a("used_bytes"), excel=gib, label="Used", kind="bytes"),
        Col("free", "Free (GiB)", _a("free_bytes"), excel=gib, label="Free", kind="bytes"),
        Col("api", "API", _a("api_source")),
    ),
    id=lambda s: s.uuid, name=lambda s: s.name or s.uuid, where=lambda s: s.cluster_name,
    note_keys=("storage_containers",), sources=True, group=INFRA,
)

SUBNETS = TableSpec(
    "subnets", "Subnets", "Subnets", "Subnet", "subnets", lambda e: e.subnets,
    (
        Col("name", "Name", _a("name"), label="Name", kind="name", search=True),
        Col("cluster", "Cluster", _cluster, label="Cluster", facet=True, search=True),
        Col("type", "Type", _a("subnet_type"), label="Type", facet=True),
        Col("vlan", "VLAN", _a("vlan_id"), label="VLAN", kind="number", diff="VLAN"),
        Col("vswitch", "Virtual Switch", _a("virtual_switch"), label="Virtual switch"),
        Col("vpc", "VPC", _a("vpc_name"), label="VPC", facet=True),
        Col("prefix", "IP Prefix", _a("ip_prefix"), label="Prefix", kind="mono", search=True,
            diff="prefix"),
        Col("gateway", "Gateway", _a("gateway_ip"), label="Gateway", kind="mono", search=True,
            diff="gateway"),
        Col("dhcp", "DHCP", _a("dhcp_enabled"), excel=yes_no, label="DHCP", kind="status",
            cell=bool_cell("On", "Off"), facet=True, diff="DHCP"),
        Col("api", "API", _a("api_source")),
    ),
    id=lambda s: s.uuid, name=lambda s: s.name or s.uuid, where=lambda s: s.cluster_name,
    note_keys=("subnets",), sources=True, group=INFRA,
)

PORT_GROUPS = TableSpec(
    "port_groups", "Port Groups", "ESXi port groups", "Port group", "port groups",
    lambda e: e.port_groups,
    (
        Col("name", "Name", _a("name"), label="Name", kind="name", search=True),
        Col("cluster", "Cluster", _cluster, label="Cluster", facet=True, search=True),
        Col("vlan", "VLAN", _a("vlan_id"), label="VLAN", kind="number", diff="VLAN"),
        Col("switch", "Switch", _a("switch_type"), excel=switch_label, label="Switch",
            cell=lambda v: switch_label(v), facet=True, diff="switch type"),
        Col("vms", "VMs", _a("vm_count"), label="VMs", kind="number"),
        Col("network_type", "Network Type", _a("network_type")),
        Col("uuid", "UUID", _a("uuid")),
        Col("api", "API", _a("api_source")),
    ),
    id=lambda p: p.uuid, name=lambda p: p.name or p.uuid, where=lambda p: p.cluster_name,
    note_keys=("subnets",), sources=True, group=INFRA,
)

TEMPLATES = TableSpec(
    "templates", "Templates", "VM templates", "Template", "templates", lambda e: e.templates,
    (
        Col("name", "Name", _a("name"), label="Name", kind="name", search=True),
        Col("os", "Guest OS", _a("guest_os"), label="OS", search=True),
        Col("vcpu", "vCPU", _a("num_vcpus"), label="vCPU", kind="number", diff="vCPU"),
        Col("memory", "Memory (GiB)", _a("memory_bytes"), excel=gib, label="Memory",
            kind="bytes", diff="memory"),
        Col("active", "Active Version", _a("active_version"), label="Active version",
            kind="mono", diff="active version"),
        Col("versions", "Versions", _a("version_count"), label="Versions", kind="number"),
        Col("created_by", "Created By", _a("created_by")),
        Col("description", "Description", _a("description"), search=True),
    ),
    id=lambda t: t.uuid, name=lambda t: t.name or t.uuid, note_keys=("templates",),
)

IMAGES = TableSpec(
    "images_only", "Images", "Images", "Image", "images", lambda e: e.images,
    (
        Col("name", "Name", _a("name"), label="Name", kind="name", search=True),
        Col("type", "Type", _a("image_type"), label="Type", facet=True),
        Col("size", "Size (GiB)", _a("size_bytes"), excel=gib, label="Size", kind="bytes",
            diff="size"),
        Col("arch", "Architecture", _a("architecture")),
        Col("clusters", "Clusters", lambda i, _x: joined(i.cluster_names), label="Clusters",
            search=True, diff="placement"),
        Col("state", "State", _a("state")),
        Col("source", "Source", _a("source"), search=True),
    ),
    id=lambda i: i.uuid, name=lambda i: i.name or i.uuid, note_keys=("images",),
)

OVAS = TableSpec(
    "ovas", "OVAs", "OVAs", "OVA", "OVAs", lambda e: e.ovas,
    (
        Col("name", "Name", _a("name"), label="Name", kind="name", search=True),
        Col("size", "Size (GiB)", _a("size_bytes"), excel=gib, label="Size", kind="bytes"),
        Col("disks", "Disks", _a("disk_count"), label="Disks", kind="number"),
        Col("cluster", "Cluster", _cluster, label="Cluster", facet=True),
        Col("created", "Created", _a("created"), label="Created", kind="datetime"),
    ),
    id=lambda o: o.uuid, name=lambda o: o.name or o.uuid, note_keys=("ovas",),
)


class _CatalogItem:
    """An image, template or OVA as one row of the combined "Images and templates" list."""

    def __init__(self, kind: str, obj: Any) -> None:
        self.kind = kind
        self.obj = obj
        self.uuid = obj.uuid
        self.name = obj.name
        self.source_pc = getattr(obj, "source_pc", None)


def _catalog(env: Environment) -> list[Any]:
    return ([_CatalogItem("Image", i) for i in env.images]
            + [_CatalogItem("Template", t) for t in env.templates]
            + [_CatalogItem("OVA", o) for o in env.ovas])


def _catalog_size(item: Any, _c: Ctx) -> int | None:
    o = item.obj
    return getattr(o, "size_bytes", None) or getattr(o, "memory_bytes", None) \
        if item.kind != "Template" else None


CATALOG = TableSpec(
    "images", "", "Images and templates", "Catalog item", "items", _catalog,
    (
        Col("name", None, lambda i, _x: i.name, label="Name", kind="name", search=True),
        Col("kind", None, lambda i, _x: i.kind, label="Kind", facet=True),
        Col("detail", None, lambda i, _x: getattr(i.obj, "image_type", None)
            or getattr(i.obj, "guest_os", None) or (f"{i.obj.disk_count:,} disk(s)"
                                                    if getattr(i.obj, "disk_count", None)
                                                    else None),
            label="Type / OS", search=True),
        Col("size", None, _catalog_size, label="Size", kind="bytes"),
        Col("placement", None, lambda i, _x: joined(getattr(i.obj, "cluster_names", None))
            or getattr(i.obj, "cluster_name", None), label="Clusters"),
    ),
    id=lambda i: i.uuid, name=lambda i: i.name or i.uuid,
    note_keys=("images", "templates", "ovas"), group=INFRA,
)

PROTECTION_POLICIES = TableSpec(
    "protection_policies", "Protection Policies", "Protection policies", "Protection policy",
    "policies", lambda e: e.protection_policies,
    (
        Col("name", "Name", _a("name"), label="Name", kind="name", search=True),
        Col("rpo", "RPO (s)", _a("rpo_seconds"), label="RPO (s)", kind="number", diff="RPO"),
        Col("replication", "Replication", _a("replication_type"), label="Replication",
            facet=True, diff="replication"),
        Col("local", "Local Retention", _a("local_retention"), label="Local retention",
            kind="number", diff="local retention"),
        Col("remote", "Remote Retention", _a("remote_retention"), label="Remote retention",
            kind="number", diff="remote retention"),
        Col("source", "Source AZ", _a("source_az"), label="Source", search=True),
        Col("target", "Target AZ", _a("target_az"), label="Target", search=True,
            diff="target"),
        Col("categories", "Categories", lambda p, _x: joined(p.categories),
            label="Categories", search=True, diff="categories"),
        Col("api", "API", _a("api_source")),
    ),
    id=lambda p: p.uuid, name=lambda p: p.name or p.uuid, note_keys=("data_protection",),
    group=PROTECT,
)

RECOVERY_PLANS = TableSpec(
    "recovery_plans", "Recovery Plans", "Recovery plans", "Recovery plan", "plans",
    lambda e: e.recovery_plans,
    (
        Col("name", "Name", _a("name"), label="Name", kind="name", search=True),
        Col("stages", "Stages", _a("stage_count"), label="Stages", kind="number",
            diff="stages"),
        Col("entities", "Entities", _a("entities_count"), label="Entities", kind="number",
            diff="entities"),
        Col("primary", "Primary Location", _a("primary_location"), label="Primary"),
        Col("recovery", "Recovery Location", _a("recovery_location"), label="Recovery",
            diff="recovery location"),
        Col("network_maps", "Network Mappings", _a("network_mappings"),
            label="Network mappings", kind="number"),
        Col("api", "API", _a("api_source")),
    ),
    id=lambda r: r.uuid, name=lambda r: r.name or r.uuid, note_keys=("data_protection",),
    group=PROTECT,
)

AVAILABILITY_ZONES = TableSpec(
    "availability_zones", "Availability Zones", "Availability zones", "Availability zone",
    "zones", lambda e: e.availability_zones,
    (
        Col("name", "Name", _a("name"), label="Name", kind="name"),
        Col("type", "Type", _a("az_type"), label="Type"),
        Col("region", "Region", _a("region"), label="Region"),
        Col("url", "URL", _a("url"), label="URL", kind="mono"),
        Col("connection", "Connection", _a("connection_status"), label="Connection",
            kind="status", cell=state_cell(("CONNECTED", "REACHABLE"))),
        Col("api", "API", _a("api_source")),
    ),
    id=lambda a: a.uuid or a.name or "", name=lambda a: a.name or a.uuid or "",
    note_keys=("data_protection",),
)

REMOTE_SITES = TableSpec(
    "remote_sites", "Remote Sites", "Remote sites", "Remote site", "sites",
    lambda e: e.remote_sites,
    (
        Col("name", "Name", _a("name"), label="Name", kind="name"),
        Col("cluster", "Cluster", _cluster, label="Cluster"),
        Col("address", "Address", _a("address"), label="Address", kind="mono"),
        Col("status", "Status", _a("status"), label="Status"),
        Col("api", "API", _a("api_source")),
    ),
    id=lambda r: r.uuid or r.name or "", name=lambda r: r.name or r.uuid or "",
)

ALERTS = TableSpec(
    "alerts", "Alerts", "Alerts", "Alert", "alerts", lambda e: e.alerts,
    (
        Col("severity", "Severity", _a("severity"), label="Severity", kind="status",
            cell=severity_cell, facet=True),
        Col("name", "Title", _a("title"), label="Title", kind="name", search=True),
        Col("entity_type", "Entity Type", _a("entity_type"), label="Entity type", facet=True),
        Col("entity", "Entity", _a("entity_name"), label="Entity", search=True),
        Col("cluster", "Cluster", _cluster, label="Cluster", facet=True),
        Col("resolved", "Resolved", _a("resolved"), excel=yes_no, label="Resolved",
            kind="status", cell=bool_cell("Resolved", "Open", "warn"), facet=True),
        Col("acknowledged", "Acknowledged", _a("acknowledged"), excel=yes_no),
        Col("created", "Created", _a("created_at"), label="Created", kind="datetime"),
        Col("api", "API", _a("api_source")),
    ),
    id=lambda a: a.uuid or "", name=lambda a: a.title or a.uuid or "",
    where=lambda a: a.cluster_name, note_keys=("alerts",), group=GOVERN,
)

LCM = TableSpec(
    "lcm_entities", "LCM (Life Cycle Manager)", "LCM entities", "LCM entity", "components",
    lambda e: e.lcm_entities,
    (
        Col("name", "Component", _a("name"), label="Component", kind="name", search=True),
        Col("class", "Class", _a("entity_class"), label="Class", facet=True),
        Col("cluster", "Cluster", _cluster, label="Cluster", facet=True, search=True),
        Col("current", "Current Version", _a("current_version"), label="Version",
            kind="mono", search=True, diff="version"),
        Col("update", "Update Available", _a("update_available"), excel=update_flag,
            label="Update", kind="status", cell=lambda v: None if v is None else
            {"tone": "warn" if v else "ok", "label": "Available" if v else "Current"},
            facet=True),
        Col("available", "Available Versions", lambda e, _x: joined(e.available_versions),
            label="Available versions", kind="mono"),
        Col("updated", "Last Updated", _a("last_updated")),
        Col("api", "API", _a("api_source")),
    ),
    id=lambda e: e.uuid, name=lambda e: e.name or e.uuid, where=lambda e: e.cluster_name,
    note_keys=("lcm_entities",), sources=True, group=GOVERN,
)

SECURITY_POLICIES = TableSpec(
    "security_policies", "Flow Security Policies", "Flow policies", "Flow policy", "policies",
    lambda e: e.security_policies,
    (
        Col("name", "Name", _a("name"), label="Name", kind="name", search=True),
        Col("type", "Type", _a("policy_type"), label="Type", facet=True, diff="type"),
        Col("state", "State", _a("state"), label="State", kind="status",
            cell=state_cell(("ENFORCE", "APPLY")), facet=True, diff="state"),
        Col("inbound", "Inbound Rules", _a("inbound_rule_count"), label="Inbound",
            kind="number", diff="inbound rules"),
        Col("outbound", "Outbound Rules", _a("outbound_rule_count"), label="Outbound",
            kind="number", diff="outbound rules"),
        Col("secured", "Secured Group", _a("secured_group"), label="Secured group",
            search=True, diff="secured group"),
        Col("api", "API", _a("api_source")),
    ),
    id=lambda p: p.uuid, name=lambda p: p.name or p.uuid, note_keys=("flow_security",),
    group=PROTECT,
)

CATEGORIES = TableSpec(
    "categories", "Flow Categories", "Categories", "Category", "categories",
    lambda e: e.categories,
    (
        Col("name", "Name", _a("name"), label="Key", kind="name", search=True),
        Col("value", "Value", _a("value"), label="Value", search=True),
        Col("description", "Description", _a("description"), label="Description"),
        Col("api", "API", _a("api_source")),
    ),
    id=lambda c: f"{c.name}:{c.value or ''}", name=lambda c: f"{c.name}:{c.value or ''}",
    note_keys=("flow_security",),
)

SERVICE_GROUPS = TableSpec(
    "service_groups", "Service Groups", "Service groups", "Service group", "groups",
    lambda e: e.service_groups,
    (
        Col("name", "Name", _a("name"), label="Name", kind="name"),
        Col("system", "System", _a("is_system"), excel=yes_no),
        Col("tcp", "TCP Ports", _a("tcp_ports"), label="TCP", kind="mono"),
        Col("udp", "UDP Ports", _a("udp_ports"), label="UDP", kind="mono"),
        Col("icmp", "ICMP", _a("icmp"), excel=yes_no),
        Col("api", "API", _a("api_source")),
    ),
    id=lambda s: s.uuid, name=lambda s: s.name or s.uuid,
)

ADDRESS_GROUPS = TableSpec(
    "address_groups", "Address Groups", "Address groups", "Address group", "groups",
    lambda e: e.address_groups,
    (
        Col("name", "Name", _a("name"), label="Name", kind="name"),
        Col("system", "System", _a("is_system"), excel=yes_no),
        Col("ranges", "IP Ranges", lambda a, _x: joined(a.ip_ranges), label="Ranges",
            kind="mono"),
        Col("api", "API", _a("api_source")),
    ),
    id=lambda a: a.uuid, name=lambda a: a.name or a.uuid,
)

VPCS = TableSpec(
    "vpcs", "VPCs", "VPCs", "VPC", "VPCs", lambda e: e.vpcs,
    (
        Col("name", "Name", _a("name"), label="Name", kind="name", search=True),
        Col("subnets", "Subnets", lambda v, _x: joined(v.subnet_names), label="Subnets",
            search=True, diff="subnets"),
        Col("external", "External Subnets", lambda v, _x: joined(v.external_subnets),
            label="External subnets"),
        Col("prefixes", "Routable Prefixes", lambda v, _x: joined(v.externally_routable_prefixes),
            label="Routable prefixes", kind="mono", diff="routable prefixes"),
        Col("routes", "Routes", _a("route_count"), label="Routes", kind="number"),
        Col("policies", "Routing Policies", _a("routing_policy_count"),
            label="Routing policies", kind="number"),
        Col("dns", "DNS Servers", lambda v, _x: joined(v.dns_servers)),
        Col("api", "API", _a("api_source")),
    ),
    id=lambda v: v.uuid, name=lambda v: v.name or v.uuid, note_keys=("flow_networking",),
    group=PROTECT,
)

FLOATING_IPS = TableSpec(
    "floating_ips", "Floating IPs", "Floating IPs", "Floating IP", "addresses",
    lambda e: e.floating_ips,
    (
        Col("name", "Floating IP", _a("floating_ip"), label="Floating IP", kind="name"),
        Col("vpc", "VPC", _a("vpc_name"), label="VPC"),
        Col("external", "External Subnet", _a("external_subnet"), label="External subnet"),
        Col("private", "Private IP", _a("associated_private_ip"), label="Private IP",
            kind="mono"),
        Col("api", "API", _a("api_source")),
    ),
    id=lambda f: f.uuid or f.floating_ip or "", name=lambda f: f.floating_ip or f.uuid or "",
    note_keys=("flow_networking",),
)

FILE_SERVERS = TableSpec(
    "file_servers", "File Servers", "File servers", "File server", "file servers",
    lambda e: e.file_servers,
    (
        Col("name", "Name", _a("name"), label="Name", kind="name", search=True),
        Col("cluster", "Cluster", _cluster, label="Cluster", facet=True),
        Col("version", "Version", _a("version"), label="Version", kind="mono", diff="version"),
        Col("fsvms", "FSVMs", _a("num_fsvm"), label="FSVMs", kind="number", diff="FSVMs"),
        Col("size", "Size (GiB)", _a("size_bytes"), excel=gib, label="Size", kind="bytes",
            diff="size"),
        Col("used", "Used (GiB)", _a("used_bytes"), excel=gib, label="Used", kind="bytes"),
        Col("shares", "Shares", _a("share_count"), label="Shares", kind="number"),
        Col("api", "API", _a("api_source")),
    ),
    id=lambda f: f.uuid, name=lambda f: f.name or f.uuid, where=lambda f: f.cluster_name,
    note_keys=("files",), group=SERVICES,
)

FILE_SHARES = TableSpec(
    "file_shares", "File Shares", "File shares", "File share", "shares",
    lambda e: e.file_shares,
    (
        Col("name", "Name", _a("name"), label="Name", kind="name"),
        Col("server", "File Server", _a("file_server_name"), label="File server"),
        Col("type", "Type", _a("share_type"), label="Type"),
        Col("protocol", "Protocol", _a("protocol")),
        Col("max", "Max Size (GiB)", _a("max_size_bytes"), excel=gib, label="Max size",
            kind="bytes"),
        Col("used", "Used (GiB)", _a("used_bytes"), excel=gib),
        Col("api", "API", _a("api_source")),
    ),
    id=lambda s: s.uuid, name=lambda s: s.name or s.uuid, note_keys=("files",),
)

OBJECT_STORES = TableSpec(
    "object_stores", "Object Stores", "Object stores", "Object store", "object stores",
    lambda e: e.object_stores,
    (
        Col("name", "Name", _a("name"), label="Name", kind="name", search=True),
        Col("cluster", "Cluster", _cluster, label="Cluster"),
        Col("version", "Version", _a("deployment_version"), label="Version", kind="mono",
            diff="version"),
        Col("workers", "Workers", _a("num_worker_nodes"), label="Workers", kind="number"),
        Col("capacity", "Capacity (GiB)", _a("total_capacity_bytes"), excel=gib,
            label="Capacity", kind="bytes", diff="capacity"),
        Col("used", "Used (GiB)", _a("used_bytes"), excel=gib, label="Used", kind="bytes"),
        Col("buckets", "Buckets", _a("bucket_count"), label="Buckets", kind="number"),
        Col("state", "State", _a("state"), label="State", kind="status",
            cell=state_cell(("COMPLETE", "READY"))),
        Col("api", "API", _a("api_source")),
    ),
    id=lambda o: o.uuid, name=lambda o: o.name or o.uuid, where=lambda o: o.cluster_name,
    note_keys=("objects",), group=SERVICES,
)

BUCKETS = TableSpec(
    "buckets", "Buckets", "Buckets", "Bucket", "buckets", lambda e: e.buckets,
    (
        Col("name", "Name", _a("name"), label="Name", kind="name"),
        Col("store", "Object Store", _a("object_store_name"), label="Object store"),
        Col("versioning", "Versioning", _a("versioning"), excel=yes_no),
        Col("worm", "WORM", _a("worm_enabled"), excel=yes_no),
        Col("objects", "Objects", _a("object_count"), label="Objects", kind="number"),
        Col("used", "Used (GiB)", _a("used_bytes"), excel=gib, label="Used", kind="bytes"),
        Col("api", "API", _a("api_source")),
    ),
    id=lambda b: b.name, name=lambda b: b.name, note_keys=("objects",),
)

VOLUME_GROUPS = TableSpec(
    "volume_groups", "Volume Groups", "Volume groups", "Volume group", "volume groups",
    lambda e: e.volume_groups,
    (
        Col("name", "Name", _a("name"), label="Name", kind="name", search=True),
        Col("cluster", "Cluster", _cluster, label="Cluster", facet=True,
            search=True),
        Col("disks", "Disks", _a("disk_count"), label="Disks", kind="number", diff="disks"),
        Col("capacity", "Capacity (GiB)", _a("total_capacity_bytes"), excel=gib,
            label="Capacity", kind="bytes", diff="capacity"),
        Col("shared", "Shared", _a("is_shared"), excel=yes_no, label="Shared", kind="status",
            cell=bool_cell("Shared", "Not shared"), facet=True),
        Col("target", "iSCSI Target", _a("iscsi_target"), label="iSCSI target", kind="mono"),
        Col("attachments", "Attachments", _a("attachment_count"), label="Attachments",
            kind="number", diff="attachments"),
        Col("api", "API", _a("api_source")),
    ),
    id=lambda v: v.uuid, name=lambda v: v.name or v.uuid, where=lambda v: v.cluster_name,
    note_keys=("volume_groups",), group=SERVICES,
)

DATABASES = TableSpec(
    "databases", "Databases", "Databases", "Database", "databases", lambda e: e.databases,
    (
        Col("name", "Name", _a("name"), label="Name", kind="name", search=True),
        Col("engine", "Engine", _a("db_engine"), label="Engine", facet=True),
        Col("type", "Type", _a("db_type"), label="Type"),
        Col("status", "Status", _a("status"), label="Status", kind="status",
            cell=state_cell(("READY", "UP"))),
        Col("server", "DB Server", _a("db_server_name"), label="DB server", search=True),
        Col("tm", "Time Machine", _a("time_machine_name")),
        Col("sla", "SLA", _a("sla_name"), label="SLA", facet=True, diff="SLA"),
        Col("clone", "Clone", _a("clone"), excel=yes_no),
        Col("api", "API", _a("api_source")),
    ),
    id=lambda d: d.id, name=lambda d: d.name or d.id, note_keys=("ndb",), group=SERVICES,
)

DATABASE_SERVERS = TableSpec(
    "database_servers", "Database Servers", "Database servers", "Database server", "servers",
    lambda e: e.database_servers,
    (
        Col("name", "Name", _a("name"), label="Name", kind="name"),
        Col("engine", "Engine", _a("db_engine"), label="Engine"),
        Col("ips", "IPs", lambda s, _x: joined(s.ip_addresses), label="IP", kind="mono"),
        Col("status", "Status", _a("status"), label="Status"),
        Col("cluster", "Cluster", _cluster),
        Col("cores", "Cores", _a("vm_cores"), label="Cores", kind="number"),
        Col("memory", "Memory (GB)", _a("memory_gb")),
        Col("api", "API", _a("api_source")),
    ),
    id=lambda s: s.id, name=lambda s: s.name or s.id, note_keys=("ndb",),
)

TIME_MACHINES = TableSpec(
    "time_machines", "Time Machines", "Time machines", "Time machine", "time machines",
    lambda e: e.time_machines,
    (
        Col("name", "Name", _a("name"), label="Name", kind="name"),
        Col("database", "Database", _a("database_name"), label="Database"),
        Col("sla", "SLA", _a("sla_name"), label="SLA"),
        Col("status", "Status", _a("status"), label="Status"),
        Col("schedule", "Schedule", _a("schedule"), label="Schedule"),
        Col("clones", "Clones", _a("clone_count"), label="Clones", kind="number"),
        Col("api", "API", _a("api_source")),
    ),
    id=lambda t: t.id, name=lambda t: t.name or t.id, note_keys=("ndb",),
)

USERS = TableSpec(
    "users", "Users", "Users", "User", "users", lambda e: e.users,
    (
        Col("name", "Username", _a("username"), label="Username", kind="name", search=True),
        Col("display", "Display Name", _a("display_name"), label="Display name", search=True),
        Col("type", "Type", _a("user_type"), label="Type", facet=True, diff="type"),
        Col("directory", "Directory", _a("directory"), label="Directory", facet=True),
        Col("state", "State", _a("state"), label="State", kind="status",
            cell=state_cell(("ACTIVE", "COMPLETE")), diff="state"),
        Col("api", "API", _a("api_source")),
    ),
    id=lambda u: u.uuid or u.username or "", name=lambda u: u.username or u.uuid or "",
    note_keys=("iam",), group=GOVERN,
)

ROLES = TableSpec(
    "roles", "Roles", "Roles", "Role", "roles", lambda e: e.roles,
    (
        Col("name", "Name", _a("name"), label="Name", kind="name", search=True),
        Col("description", "Description", _a("description"), label="Description"),
        Col("system", "System", _a("is_system"), excel=yes_no, label="System",
            kind="status", cell=bool_cell("Built-in", "Custom")),
        Col("permissions", "Permissions", _a("permission_count"), label="Permissions",
            kind="number", diff="permissions"),
        Col("api", "API", _a("api_source")),
    ),
    id=lambda r: r.uuid or r.name or "", name=lambda r: r.name or r.uuid or "",
    note_keys=("iam",), group=GOVERN,
)

PROJECTS = TableSpec(
    "projects", "Projects", "Projects", "Project", "projects", lambda e: e.projects,
    (
        Col("name", "Name", _a("name"), label="Name", kind="name"),
        Col("description", "Description", _a("description"), label="Description"),
        Col("users", "Users", _a("user_count"), label="Users", kind="number"),
        Col("api", "API", _a("api_source")),
    ),
    id=lambda p: p.uuid or p.name or "", name=lambda p: p.name or p.uuid or "",
    note_keys=("iam",),
)

PLAYBOOKS = TableSpec(
    "playbooks", "Playbooks (X-Play)", "X-Play playbooks", "Playbook", "playbooks",
    lambda e: e.playbooks,
    (
        Col("name", "Name", _a("name"), label="Name", kind="name", search=True),
        Col("trigger", "Trigger", _a("trigger_type"), label="Trigger", facet=True),
        Col("actions", "Actions", _a("action_count"), label="Actions", kind="number"),
        Col("enabled", "Enabled", _a("is_enabled"), excel=yes_no, label="Enabled",
            kind="status", cell=bool_cell("Enabled", "Disabled"), diff="enabled"),
        Col("description", "Description", _a("description")),
        Col("api", "API", _a("api_source")),
    ),
    id=lambda p: p.uuid, name=lambda p: p.name or p.uuid, note_keys=("xplay",), group=GOVERN,
)

NCM_REPORTS = TableSpec(
    "ncm_reports", "NCM Reports", "NCM reports", "Report", "reports", lambda e: e.ncm_reports,
    (
        Col("name", "Name", _a("name"), label="Name", kind="name"),
        Col("type", "Type", _a("report_type"), label="Type"),
        Col("schedule", "Schedule", _a("schedule"), label="Schedule"),
        Col("api", "API", _a("api_source")),
    ),
    id=lambda r: r.uuid or r.name or "", name=lambda r: r.name or r.uuid or "",
    note_keys=("ncm_reports",),
)

LICENSES = TableSpec(
    "licenses", "Licensing", "Licenses", "License", "licenses", lambda e: e.licenses,
    (
        Col("name", "Product", _a("product"), label="Product", kind="name", search=True),
        Col("edition", "Edition", _a("edition"), label="Edition", facet=True, diff="edition"),
        Col("category", "Category", _a("category")),
        Col("quantity", "Quantity", _a("quantity"), label="Quantity", kind="number",
            diff="quantity"),
        Col("expiry", "Expiry", _a("expiry"), label="Expiry", kind="datetime", diff="expiry"),
        Col("compliance", "Compliance", _a("compliance"), label="Compliance", kind="status",
            cell=state_cell(("COMPLIANT",)), facet=True, diff="compliance"),
    ),
    id=lambda lic: f"{lic.product}:{lic.edition}:{lic.cluster_uuid or ''}",
    name=lambda lic: lic.product or "license", note_keys=("licenses",), group=GOVERN,
)

# ─── design data: host networking, external storage, PC VMs ─────────────

_BOND_LABELS = {"ACTIVE_BACKUP": "Active-backup", "BALANCE_SLB": "Balance-slb",
                "BALANCE_TCP": "Balance-tcp", "NONE": "None"}


def bond_label(value: Any) -> str | None:
    """"Active-backup" / "Balance-slb" / "Balance-tcp" for a bond mode enum."""
    if not value:
        return None
    text = str(value).upper()
    return _BOND_LABELS.get(text) or text.replace("_", "-").capitalize()


def speed_label(mbps: Any) -> str | None:
    """A port speed in Mb/s as "25 GbE" / "100 MbE"; None when unknown."""
    if not mbps:
        return None
    value = int(mbps)
    if value >= 1000 and value % 1000 == 0:
        return f"{value // 1000:,} GbE"
    if value >= 1000:
        return f"{value / 1000:,.1f} GbE"
    return f"{value:,} MbE"


def _vs_bridges(s: Any, _c: Ctx) -> str:
    return joined(sorted({h.bridge for h in s.hosts if h.bridge}))


def _vs_uplinks(s: Any, _c: Ctx) -> str:
    """The uplink set, when every host uses the same one ("eth0, eth1")."""
    sets = {tuple(h.uplinks) for h in s.hosts if h.uplinks}
    if len(sets) == 1:
        return ", ".join(next(iter(sets)))
    return "Varies by host" if sets else ""


def _vs_lacp(s: Any, _c: Ctx) -> str | None:
    states = sorted({h.lacp_status for h in s.hosts if h.lacp_status})
    if states:
        return ", ".join(x.title() for x in states)
    return None


def _nic_switch(n: Any, _c: Ctx) -> str | None:
    return n.switch_device_id or n.switch_vendor


def _hosting_cluster(v: Any, c: Ctx) -> str | None:
    if v.hosting_cluster_name:
        return str(v.hosting_cluster_name)
    cluster = c.cluster_by_uuid.get(v.hosting_cluster_uuid or "")
    return cluster.name if cluster is not None else v.hosting_cluster_uuid


VIRTUAL_SWITCHES = TableSpec(
    "virtual_switches", "Virtual Switches", "Virtual switches", "Virtual switch",
    "virtual switches", lambda e: e.virtual_switches,
    (
        Col("name", "Name", _a("name"), label="Name", kind="name", search=True),
        Col("cluster", "Cluster", _cluster, label="Cluster", facet=True, search=True),
        Col("default", "Default", _a("is_default"), excel=yes_no, label="Default",
            kind="status", cell=bool_cell("Default", "No")),
        Col("bond", "Bond Mode", _a("bond_mode"), excel=bond_label, label="Bond mode",
            cell=bond_label, facet=True, diff="bond mode"),
        Col("lacp", "LACP", _vs_lacp, label="LACP"),
        Col("mtu", "MTU", _a("mtu"), label="MTU", kind="number", diff="MTU"),
        Col("uplinks", "Uplinks", _vs_uplinks, label="Uplinks", diff="uplinks"),
        Col("bridges", "Bridges", _vs_bridges, label="Bridge"),
        Col("hosts", "Hosts", lambda s, _x: len(s.hosts), label="Hosts", kind="number"),
        Col("api", "API", _a("api_source")),
    ),
    id=lambda s: f"{s.uuid}:{s.cluster_uuid or ''}", name=lambda s: s.name or s.uuid,
    where=lambda s: s.cluster_name, note_keys=("host_networking",), sources=True,
    group=INFRA,
)

HOST_NICS = TableSpec(
    "host_nics", "Host NICs", "Host NICs", "Host NIC", "host NICs", lambda e: e.host_nics,
    (
        Col("name", "Name", _a("name"), label="Name", kind="name", search=True),
        Col("cluster", "Cluster", _cluster, label="Cluster", facet=True, search=True),
        Col("host", "Host", _a("host_name"), label="Host", search=True),
        Col("model", "Model", _a("model"), label="Model", search=True, facet=True),
        Col("speed", "Speed", lambda n, _x: speed_label(n.speed_mbps), label="Speed",
            facet=True, diff="speed"),
        Col("link", "Link", _a("link_up"),
            excel=lambda v: "N/A" if v is None else ("Up" if v else "Down"), label="Link",
            kind="status", cell=bool_cell("Up", "Down", "warn"), facet=True),
        Col("mtu", "MTU", _a("mtu"), label="MTU", kind="number", diff="MTU"),
        Col("vswitch", "Virtual Switch", _a("virtual_switch_name"), label="Virtual switch",
            facet=True, diff="virtual switch"),
        Col("driver", "Driver", _a("driver_version"), diff="driver"),
        Col("firmware", "Firmware", _a("firmware_version"), label="Firmware", kind="mono",
            diff="firmware"),
        Col("mac", "MAC Address", _a("mac_address"), search=True),
        Col("lldp", "LLDP Switch", _nic_switch, label="Switch", search=True),
        Col("switch_mac", "Switch MAC", _a("switch_mac"), search=True),
        Col("switch_port", "Switch Port", _a("switch_port_id"), label="Switch port"),
        Col("api", "API", _a("api_source")),
    ),
    id=lambda n: n.uuid, name=lambda n: n.name or n.uuid,
    where=lambda n: " · ".join(x for x in (n.cluster_name, n.host_name) if x) or None,
    note_keys=("host_networking",), sources=True, group=INFRA,
)

EXTERNAL_STORAGE = TableSpec(
    "external_storages", "External Storage", "External storage", "External array",
    "arrays", lambda e: e.external_storages,
    (
        Col("name", "Name", _a("name"), label="Name", kind="name", search=True),
        Col("cluster", "Cluster", _cluster, label="Cluster", facet=True, search=True),
        Col("product", "Product", lambda a, _x: a.product, label="Product", facet=True),
        Col("endpoint", "Endpoint", _a("endpoint"), label="Endpoint", kind="mono",
            search=True, diff="endpoint"),
        Col("protocol", "Protocol (Implied)", lambda a, _x: a.protocol, label="Protocol"),
        Col("capacity", "Capacity (GiB)", _a("total_capacity_bytes"), excel=gib,
            label="Capacity", kind="bytes", diff="capacity"),
        Col("free", "Free (GiB)", _a("free_capacity_bytes"), excel=gib, label="Free",
            kind="bytes"),
        Col("health", "Health", _a("health"), label="Health", kind="status",
            cell=state_cell(("HEALTHY",)), diff="health"),
        Col("containers", "Containers", lambda a, _x: joined(a.container_names),
            label="Containers", search=True),
        Col("api", "API", _a("api_source")),
    ),
    id=lambda a: a.uuid, name=lambda a: a.name or a.uuid, where=lambda a: a.cluster_name,
    note_keys=("storage_containers",), sources=True, group=INFRA,
)

PC_VMS = TableSpec(
    "pc_vms", "PC VMs", "Prism Central VMs", "Prism Central VM", "PC VMs",
    lambda e: e.pc_vms,
    (
        Col("name", "Name", _a("name"), label="Name", kind="name", search=True),
        Col("cluster", "Cluster", _hosting_cluster, label="Cluster", facet=True,
            search=True, diff="hosting cluster"),
        Col("vcpu", "vCPUs", _a("num_vcpus"), label="vCPU", kind="number", diff="vCPU"),
        Col("memory", "Memory (GiB)", _a("memory_bytes"), excel=gib, label="Memory",
            kind="bytes", diff="memory"),
        Col("disk", "Data Disk (GiB)", _a("data_disk_bytes"), excel=gib, label="Data disk",
            kind="bytes"),
        Col("ip", "IP Addresses", lambda v, _x: joined(v.ip_addresses), label="IP",
            kind="mono", search=True),
        Col("power", "Power", _a("power_state"), excel=power_label, label="Power",
            kind="status", cell=power_cell),
        Col("api", "API", _a("api_source")),
    ),
    id=lambda v: v.uuid, name=lambda v: v.name or v.uuid,
    where=lambda v: v.hosting_cluster_name, note_keys=("pc_config",), sources=True,
    group=GOVERN,
)

#: Every spec, keyed by type id.
SPECS: dict[str, TableSpec] = {s.type: s for s in (
    CLUSTERS, HOSTS, VMS, SYSTEM_VMS, STORAGE, SUBNETS, PORT_GROUPS, CATALOG, TEMPLATES,
    IMAGES, OVAS, VIRTUAL_SWITCHES, HOST_NICS, EXTERNAL_STORAGE, PC_VMS,
    PROTECTION_POLICIES, RECOVERY_PLANS, AVAILABILITY_ZONES, REMOTE_SITES, ALERTS, LCM,
    SECURITY_POLICIES, CATEGORIES, SERVICE_GROUPS, ADDRESS_GROUPS, VPCS, FLOATING_IPS,
    FILE_SERVERS, FILE_SHARES, OBJECT_STORES, BUCKETS, VOLUME_GROUPS, DATABASES,
    DATABASE_SERVERS, TIME_MACHINES, USERS, ROLES, PROJECTS, PLAYBOOKS, NCM_REPORTS, LICENSES,
)}

#: The Inventory screen's object-type list, in display order (groups as in the contract).
INVENTORY_TYPES: list[str] = [
    "clusters", "hosts", "vms", "system_vms", "storage_containers", "subnets", "port_groups",
    "virtual_switches", "host_nics", "external_storages", "pc_vms",
    "images",
    "protection_policies", "recovery_plans", "security_policies", "vpcs",
    "object_stores", "volume_groups", "file_servers", "databases",
    "users", "roles", "licenses", "lcm_entities", "alerts", "playbooks",
]

#: Object types Compare diffs, in report order.
COMPARE_TYPES: list[str] = [
    "clusters", "hosts", "storage_containers", "subnets", "port_groups", "vms", "images_only",
    "templates",
    "protection_policies", "recovery_plans", "security_policies", "vpcs", "file_servers",
    "object_stores", "volume_groups", "databases", "users", "roles", "lcm_entities",
    "licenses", "playbooks",
]


def table_rows(spec: TableSpec, env: Environment, ctx: Ctx | None = None) -> list[list[Any]]:
    """The Excel rows for a spec (values as the workbook has always shown them)."""
    ctx = ctx or Ctx(env)
    cols = spec.excel_cols
    rows: list[list[Any]] = []
    for obj in spec.items(env):
        row = []
        for col in cols:
            value = col.get(obj, ctx)
            row.append(col.excel(value) if col.excel else value)
        rows.append(row)
    return rows


__all__ = ["Col", "Ctx", "SPECS", "TableSpec", "table_rows"]
