"""The collection-area catalogue: what the UI's "What to collect" list is made of.

One `Area` per collector (plus NDB, which has its own connection). The ids are the
contract between the renderer's checkboxes, the `areas` parameter of an inventory call,
the progress events and the run metadata stored in a snapshot — so they never change once
shipped, even if a label does.

Each area also records how to read its outcome back out of an `Environment`:

* ``fields`` — the Environment lists it fills, for the per-area object count.
* ``note_key`` — the `collection_notes` key its collector writes.
* ``v4_area`` — the negotiated v4 namespace that decides v4-or-legacy for it (None when
  the collector has no v4 path at all).
* ``legacy_only`` — sub-features that are v3-only regardless of the PC version (no v4
  equivalent yet), which the API-routing table lists as stopping at the v3 sunset.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ntnxvision_core.collectors import (
    AlertsCollector,
    ClusterCollector,
    DataProtectionCollector,
    FilesCollector,
    FlowNetworkingCollector,
    FlowSecurityCollector,
    HostCollector,
    IamCollector,
    ImageCollector,
    LcmCollector,
    LicenseCollector,
    NcmCollector,
    ObjectsCollector,
    OvaCollector,
    PrismCentralConfigCollector,
    StorageCollector,
    SubnetCollector,
    TemplateCollector,
    VmCollector,
    VolumesCollector,
    XPlayCollector,
)
from ntnxvision_core.collectors.base import Collector

INFRA = "Infrastructure"
PLATFORM = "Platform services"
OPS = "Operations and governance"
GROUPS = (INFRA, PLATFORM, OPS)

#: Typical full-run durations (seconds) the UI shows as an estimate before a run.
TYPICAL_SECONDS = {"prism_central": 240, "prism_element": 60}


@dataclass(frozen=True)
class Area:
    id: str
    label: str
    group: str
    collector: type[Collector] | None
    note_key: str
    fields: tuple[str, ...] = ()
    pe_supported: bool = False
    v4_area: str | None = None
    #: API used when the collector cannot use v4 here ("v3"; "v2" for PE; "era" for NDB).
    legacy_api: str = "v3"
    #: (label, reason) of parts that are v3-only whatever the PC version.
    legacy_only: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    default_on: bool = True


#: In *run* order — the order the engine executes collectors (phase 1, then phase 2,
#: with pc_config last because it sizes the PC from the system VMs).
AREAS: tuple[Area, ...] = (
    Area("clusters", "Clusters", INFRA, ClusterCollector, "clusters", ("clusters",),
         pe_supported=True, v4_area="clusters"),
    Area("hosts", "Hosts / nodes", INFRA, HostCollector, "hosts", ("hosts",),
         pe_supported=True, v4_area="hosts"),
    Area("storage_containers", "Storage containers", INFRA, StorageCollector,
         "storage_containers", ("storage_containers",), pe_supported=True,
         v4_area="storage_containers"),
    Area("subnets", "Subnets / networks", INFRA, SubnetCollector, "subnets",
         ("subnets", "port_groups"),
         pe_supported=True, v4_area=None),
    Area("vms", "Virtual machines", INFRA, VmCollector, "vms", ("vms", "system_vms"),
         pe_supported=True, v4_area="vms"),
    Area("licenses", "Licensing", OPS, LicenseCollector, "licenses", ("licenses",),
         v4_area="licensing"),
    Area("data_protection", "Data protection / DR", PLATFORM, DataProtectionCollector,
         "data_protection", ("protection_policies", "recovery_plans", "availability_zones"),
         v4_area="dataprotection",
         legacy_only=(("Recovery plans · availability zones", "No v4 equivalent yet"),)),
    Area("alerts", "Health alerts", OPS, AlertsCollector, "alerts", ("alerts",),
         v4_area="monitoring"),
    Area("flow_security", "Flow network security", PLATFORM, FlowSecurityCollector,
         "flow_security",
         ("security_policies", "categories", "service_groups", "address_groups"),
         v4_area="microseg"),
    Area("flow_networking", "Flow virtual networking", PLATFORM, FlowNetworkingCollector,
         "flow_networking", ("vpcs", "floating_ips"), v4_area=None,
         legacy_only=(("VPCs · floating IPs", "Collected over v3 in this release"),)),
    Area("files", "Nutanix Files", PLATFORM, FilesCollector, "files",
         ("file_servers", "file_shares"), v4_area=None,
         legacy_only=(("Nutanix Files", "Collected over v3 in this release"),)),
    Area("objects", "Nutanix Objects", PLATFORM, ObjectsCollector, "objects",
         ("object_stores", "buckets"), v4_area=None,
         legacy_only=(("Nutanix Objects", "Collected over v3 in this release"),)),
    Area("volume_groups", "Volume groups", PLATFORM, VolumesCollector, "volume_groups",
         ("volume_groups",), v4_area="volumes"),
    Area("iam", "IAM (users, roles, projects)", OPS, IamCollector, "iam",
         ("users", "roles", "projects"), v4_area="iam",
         legacy_only=(("IAM projects", "No v4 equivalent yet"),)),
    Area("xplay", "X-Play playbooks", OPS, XPlayCollector, "xplay", ("playbooks",),
         v4_area=None, legacy_only=(("X-Play playbooks", "No v4 equivalent yet"),)),
    Area("ncm_reports", "NCM reports", OPS, NcmCollector, "ncm_reports", ("ncm_reports",),
         v4_area="ncm_reports"),
    Area("lcm", "LCM firmware and software", OPS, LcmCollector, "lcm_entities",
         ("lcm_entities",), v4_area="lifecycle"),
    Area("images", "Images", INFRA, ImageCollector, "images", ("images",), v4_area="vms"),
    Area("templates", "VM templates", INFRA, TemplateCollector, "templates", ("templates",),
         v4_area="vms"),
    Area("ovas", "OVAs", INFRA, OvaCollector, "ovas", ("ovas",), v4_area="vms"),
    Area("pc_config", "Prism Central configuration", OPS, PrismCentralConfigCollector,
         "pc_config", (), v4_area="clusters"),
)

#: NDB runs only when an NDB endpoint is given; it is not a checkbox in the area list.
NDB_AREA = Area("ndb", "NDB databases", PLATFORM, None, "ndb",
                ("database_servers", "databases", "time_machines"), legacy_api="era")

#: vCenter enrichment of ESXi clusters runs only when a target has a vCenter configured;
#: like NDB it is not a checkbox. It adds no objects of its own (it fills ESXi gaps).
VCENTER_AREA = Area("vcenter", "vCenter enrichment (ESXi)", INFRA, None, "vcenter", (),
                    legacy_api="vcenter")

AREAS_BY_ID: dict[str, Area] = {a.id: a for a in (*AREAS, NDB_AREA, VCENTER_AREA)}

#: Prism Element (v2.0) mode labels, keyed by area id.
PE_LABELS = {
    "clusters": "Cluster",
    "hosts": "Hosts / nodes",
    "storage_containers": "Storage containers",
    "subnets": "Networks",
    "vms": "Virtual machines",
}


def select(area_ids: list[str] | None, mode: str = "prism_central") -> list[Area]:
    """The areas a run should execute, in run order.

    `None` or an empty list means every area available in the mode. Unknown ids are
    ignored rather than rejected — a renderer that is newer than the core must not be
    able to fail a run by naming an area this build does not know.
    """
    pool = [a for a in AREAS if mode != "prism_element" or a.pe_supported]
    if not area_ids:
        return pool
    wanted = set(area_ids)
    return [a for a in pool if a.id in wanted]


def catalogue() -> dict[str, object]:
    """The `list_areas` result: the checkbox list in display order, grouped."""
    ordered = sorted(AREAS, key=lambda a: GROUPS.index(a.group))
    return {
        "areas": [
            {
                "id": a.id,
                "label": a.label,
                "group": a.group,
                "pe_supported": a.pe_supported,
                "default_on": a.default_on,
            }
            for a in ordered
        ],
        "typical_seconds": dict(TYPICAL_SECONDS),
    }
