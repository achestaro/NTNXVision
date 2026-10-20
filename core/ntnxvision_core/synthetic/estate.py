"""Deterministic synthetic Nutanix estates.

The generator builds one canonical, API-neutral description of an estate — plain dicts
with snake_case keys — that `render` then turns into v4 / v3 / v2.0 / Era payloads. Keeping
the estate API-neutral is what makes the payloads consistent with each other: an AHV VM
has the same uuid in v3 ``vms/list`` and in v4 ``vmm/v4.0/ahv/config/vms``, a disk names a
storage container that exists, a NIC names a subnet that exists, a protection policy's
category filter matches categories that VMs actually carry.

Determinism: every id is a uuid5 of a stable key (host + object name), and every random
attribute comes from a `random.Random` seeded with that same key. Nothing depends on
generation order, so adding drift for a later ``epoch`` (new VMs, a removed policy, an AOS
upgrade) leaves every other object byte-for-byte identical — which is what lets Compare
show a clean, believable change list between two synthetic collections.
"""

from __future__ import annotations

import hashlib
import random
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from ntnxvision_core.compat import parse_version
from ntnxvision_core.versions import pc_version

_NS = uuid.UUID("6f1c2d4e-9a3b-4c5d-8e7f-0a1b2c3d4e5f")
GIB = 1024**3
TIB = 1024**4
MIB = 1024**2

#: Reference "now" for synthetic timestamps — fixed so payloads are reproducible.
BASE_TIME = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def uid(*parts: object) -> str:
    return str(uuid.uuid5(_NS, "/".join(str(p) for p in parts)))


def build_string(version: str, *parts: object) -> str:
    """A realistic full build string with a fake, deterministic 40-hex commit id.

    Real Prism Central and AOS endpoints answer some version fields with
    ``el8.5-release-<branch>-<version>-stable-<commit>``; the reports must never show
    it. The commit here is a hash of the synthetic seed, never a real build's.
    """
    commit = hashlib.sha1("/".join(["synthetic-build", version, *map(str, parts)])
                          .encode()).hexdigest()
    return f"el8.5-release-fraser-{version}-stable-{commit}"


def rng(*parts: object) -> random.Random:
    return random.Random("/".join(str(p) for p in parts))


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ─── profiles ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ClusterSpec:
    name: str
    hypervisor: str  # "AHV" | "ESXi"
    nodes: int
    vms: int
    aos: str
    model: str = "NX-8155-G9"
    #: A second node model for the upper half of the nodes: a heterogeneous cluster
    #: (an expansion with a newer generation), which the capacity math must sum per node.
    mixed_model: str | None = None


@dataclass(frozen=True)
class NodeModel:
    """Hardware of one node model: what the capacity figures are computed from."""

    cpu_model: str
    sockets: int
    cores_per_socket: int
    #: The Controller VM's vCPU allocation on this model.
    cvm_vcpus: int
    #: Physical data disks: (count, size in bytes, tier). Decimal TB, as disks are sold.
    disks: tuple[tuple[int, int, str], ...]


_TB = 10**12

#: Node hardware per block model (realistic, not vendor-exact).
NODE_MODELS: dict[str, NodeModel] = {
    "NX-8155-G9": NodeModel("Intel(R) Xeon(R) Gold 6438Y+", 2, 32, 12,
                            ((10, int(3.84 * _TB), "SSD"),)),
    "NX-8170-G9": NodeModel("Intel(R) Xeon(R) Platinum 8468", 2, 48, 16,
                            ((12, int(7.68 * _TB), "NVMe"),)),
    "NX-3170-G8": NodeModel("Intel(R) Xeon(R) Gold 6342", 2, 24, 12,
                            ((6, int(3.84 * _TB), "SSD"),)),
    "NX-1175S-G8": NodeModel("Intel(R) Xeon(R) Gold 6338N", 1, 32, 8,
                             ((4, int(1.92 * _TB), "SSD"),)),
    # OEM and third-party platforms (realistic model strings, not vendor-exact sizing).
    "HPE DX380 Gen11": NodeModel("Intel(R) Xeon(R) Gold 6430", 2, 32, 12,
                                 ((8, int(3.84 * _TB), "SSD"),)),
    "HPE DX360 Gen10 Plus": NodeModel("Intel(R) Xeon(R) Gold 6338", 2, 32, 12,
                                      ((6, int(1.92 * _TB), "SSD"),)),
    "XC760xd-24": NodeModel("Intel(R) Xeon(R) Gold 6448Y", 2, 32, 12,
                            ((12, int(3.84 * _TB), "NVMe"),)),
    "XC660-10": NodeModel("Intel(R) Xeon(R) Gold 5418Y", 2, 24, 12,
                          ((6, int(3.84 * _TB), "SSD"),)),
    "HX665 V3": NodeModel("AMD EPYC 9354", 2, 32, 12, ((10, int(3.84 * _TB), "NVMe"),)),
    "HX630 V3": NodeModel("Intel(R) Xeon(R) Gold 6426Y", 2, 16, 8,
                          ((4, int(1.92 * _TB), "SSD"),)),
    "UCSC-C240-M7SX": NodeModel("Intel(R) Xeon(R) Platinum 8462Y+", 2, 32, 12,
                                ((12, int(7.68 * _TB), "NVMe"),)),
    "UCSC-C220-M6S": NodeModel("Intel(R) Xeon(R) Gold 6338", 2, 32, 12,
                               ((8, int(3.84 * _TB), "SSD"),)),
}


@dataclass
class Features:
    """What a synthetic Prism Central has deployed, and how its APIs misbehave."""

    #: v4 namespaces (`v4_client.NAMESPACES` keys) that 404 on this PC.
    v4_absent: set[str] = field(default_factory=set)
    #: v4 namespaces that answer 503 (deployed service not running).
    v4_unavailable: set[str] = field(default_factory=set)
    files: bool = True
    objects: bool = True
    fvn: bool = True
    xplay: bool = True
    ncm: bool = True
    #: The v4 alerts endpoint answers 429 once (and enforces 2 requests/s).
    alerts_rate_limited: bool = False
    #: ESXi VMs whose individual v4 record makes its page answer HTTP 500.
    esxi_bad_vms: int = 0
    #: The groups API serves ``mh_vm`` rows (every VM, CVMs included), so a collector can
    #: recover the VMs v4 fails on. Off by default: the demo PCs keep today's skip note
    #: (their groups API rejects ``mh_vm``, which exercises the fallback's own fallback).
    groups_mh_vm: bool = False
    #: LCM reports its own framework as out of date (an LCM "with notes" outcome).
    lcm_framework_stale: bool = False
    #: LCM operations already running when the PC is first asked: (cluster index, -1 for
    #: Prism Central itself; "inventory" | "upgrade"). See `synthetic.lcm`.
    lcm_running: tuple[tuple[int, str], ...] = ()
    #: How an LCM inventory started through the API ends: succeeded / failed /
    #: forbidden / status_forbidden / stuck.
    lcm_outcome: str = "succeeded"
    #: Prism Central proxies Prism Element APIs (``proxyClusterUuid``), as real PCs do.
    #: Off: the proxy answers 403, which leaves only the groups API as an ESXi fallback.
    pe_proxy: bool = True
    #: The vCenter Server the ESXi clusters are registered to (v4 managementServer).
    vcenter_address: str | None = None
    # ─ design data (see `_design`) ─
    #: Prism Central VMs: 1 = single VM, 3 = scale-out (the VIP is then `pc_ip`).
    pc_vm_count: int = 1
    #: Deployment size (v4 vocabulary): SMALL / LARGE / EXTRALARGE.
    pc_size: str = "SMALL"
    #: Highest v4 minor version served per namespace prefix (others: v4.0 only).
    #: clustermgmt 4.3 serves the external storage list; 4.2 the container link.
    v4_minor: dict[str, int] = field(default_factory=lambda: {
        "clustermgmt": 2, "networking": 3, "prism": 3, "datapolicies": 2,
        "dataprotection": 3})
    #: External arrays: (cluster index, provider enum) per array.
    external_storage: tuple[tuple[int, str], ...] = ()
    #: Hosts advertise LLDP neighbours (on the first two AHV clusters only).
    lldp: bool = True
    #: Nodes per block (chassis): 1 for current one-node hardware, 4 for 2U4N blocks.
    nodes_per_block: int = 1
    #: Other demo Prism Centrals this one is paired with (an AZ and a DOMAIN_MANAGER
    #: registration each, keyed on the peer's own PC uuid).
    az_peers: tuple[str, ...] = ()
    #: Extra protection policies: (name, RPO seconds, source cluster, target PC host or
    #: "" for this PC, target cluster). RPO 0 is a synchronous (Metro) policy.
    extra_policies: tuple[tuple[str, int, str, str, str], ...] = ()
    #: Index of the cluster hosting the PC VM(s).
    pc_cluster: int = 0
    #: Remote availability zones outside the demo estate, and the policies replicating
    #: to (or from) them. None keeps the default DR set (pc-dr and a Xi AZ, policies to
    #: pc-dr); a tuple replaces it. Paired demo PCs use `az_peers` / `extra_policies`.
    azs: tuple[AzSpec, ...] | None = None
    policies: tuple[PolicySpec, ...] | None = None


@dataclass(frozen=True)
class AzSpec:
    """A remote availability zone outside the demo estate: a PC (`url` is its address)
    or Nutanix Cloud (Xi)."""

    name: str
    url: str
    type: str = "PHYSICAL"  # PHYSICAL | XI
    region: str | None = None
    status: str = "CONNECTED"


@dataclass(frozen=True)
class PolicySpec:
    """A protection policy replicating to the AZ named `target`, or with `inbound` from
    it to this PC."""

    name: str
    rpo: int
    target: str
    inbound: bool = False
    categories: tuple[tuple[str, str], ...] = (("DRTier", "Gold"),)
    local_retention: int = 24
    remote_retention: int = 48


PROFILES: dict[str, list[ClusterSpec]] = {
    "tiny": [ClusterSpec("LAB-01", "AHV", 3, 40, "7.0", "NX-1175S-G8")],
    "medium": [
        ClusterSpec("PHX-PROD-01", "AHV", 8, 342, "6.10.1"),
        ClusterSpec("PHX-PROD-02", "AHV", 12, 612, "7.0", mixed_model="NX-8170-G9"),
        ClusterSpec("PHX-DR-01", "AHV", 6, 212, "7.0", "NX-3170-G8"),
        ClusterSpec("AMS-EDGE-02", "ESXi", 2, 48, "6.10", "NX-1175S-G8"),
    ],
    "small": [
        ClusterSpec("SEA-PROD-01", "AHV", 10, 820, "7.3", "NX-8170-G9"),
        ClusterSpec("SEA-PROD-02", "AHV", 10, 610, "7.3"),
        ClusterSpec("SEA-VDI-01", "AHV", 7, 160, "7.3", "NX-3170-G8"),
        # A single-node remote office cluster: no N-1 capacity.
        ClusterSpec("SEA-ROBO-01", "AHV", 1, 18, "7.3", "NX-1175S-G8"),
    ],
    "legacy": [
        ClusterSpec("DAL-PROD-01", "AHV", 6, 420, "6.8", "NX-8170-G9"),
        ClusterSpec("DAL-EDGE-01", "AHV", 3, 90, "6.5", "NX-1175S-G8"),
    ],
    # One Prism Central's share of the 20 000-VM estate (three of these, see profiles.py).
    "large": [
        ClusterSpec("PROD-A", "AHV", 16, 1800, "7.0"),
        ClusterSpec("PROD-B", "AHV", 16, 1800, "7.0"),
        ClusterSpec("PROD-C", "AHV", 14, 1500, "6.10.1", mixed_model="NX-8170-G9"),
        ClusterSpec("VDI-A", "AHV", 12, 1000, "7.0", "NX-3170-G8"),
        ClusterSpec("EDGE-A", "ESXi", 6, 567, "6.10", "NX-1175S-G8"),
    ],
}

# ─── scenario estates (architecture diagram coverage) ──────────────────
# Small, medium, large-enterprise and very-large estates with mixed hypervisors and
# hardware vendors. VM counts stay low: these exist for the topology, not the workload.

PROFILES.update({
    "small-branch": [
        ClusterSpec("BRANCH-PRD-01", "AHV", 4, 24, "7.0.1", "NX-3170-G8"),
        ClusterSpec("BRANCH-VDI-01", "AHV", 3, 12, "7.0.1", "HPE DX360 Gen10 Plus"),
    ],
    "med-east": [
        ClusterSpec("PROD-E01", "AHV", 8, 30, "7.0.1"),
        ClusterSpec("PROD-E02", "AHV", 6, 24, "7.0.1", "XC760xd-24"),
        ClusterSpec("VDI-E01", "ESXi", 4, 16, "6.10.1", "HPE DX380 Gen11"),
    ],
    "med-west": [
        ClusterSpec("PROD-W01", "AHV", 8, 30, "7.0.1", "NX-8170-G9"),
        ClusterSpec("DR-W01", "AHV", 4, 12, "7.0.1", "HX665 V3"),
        ClusterSpec("ESX-W01", "ESXi", 3, 12, "6.10.1", "UCSC-C220-M6S"),
    ],
    "ent-nyc": [
        ClusterSpec("NYC-PROD-01", "AHV", 16, 40, "7.0.1", mixed_model="NX-8170-G9"),
        ClusterSpec("NYC-PROD-02", "AHV", 12, 30, "7.0.1", "XC760xd-24"),
        ClusterSpec("NYC-DB-01", "AHV", 6, 12, "7.0.1", "UCSC-C240-M7SX"),
        ClusterSpec("NYC-VDI-01", "ESXi", 10, 24, "6.10.1", "HPE DX380 Gen11"),
        ClusterSpec("NYC-MGMT-01", "AHV", 3, 6, "7.0.1", "NX-1175S-G8"),
    ],
    "ent-chi": [
        ClusterSpec("CHI-PROD-01", "AHV", 12, 30, "7.0.1", "NX-8170-G9"),
        ClusterSpec("CHI-DR-01", "AHV", 8, 12, "7.0.1", "HX665 V3"),
        ClusterSpec("CHI-ESX-01", "ESXi", 6, 18, "6.10.1", "UCSC-C220-M6S"),
        ClusterSpec("CHI-ROBO-01", "AHV", 1, 4, "7.0.1", "HX630 V3"),
    ],
    "ent-lon": [
        ClusterSpec("LON-PROD-01", "AHV", 10, 30, "7.0.1", "XC660-10"),
        ClusterSpec("LON-PROD-02", "AHV", 8, 24, "6.10.1"),
        ClusterSpec("LON-VDI-01", "ESXi", 6, 18, "6.10.1", "HPE DX360 Gen10 Plus"),
        ClusterSpec("LON-EDGE-01", "AHV", 3, 6, "7.0.1", "NX-1175S-G8"),
    ],
    "ent-sin": [
        ClusterSpec("SIN-PROD-01", "AHV", 8, 24, "7.0.1", "HX665 V3"),
        ClusterSpec("SIN-PROD-02", "AHV", 6, 18, "7.0.1", "UCSC-C240-M7SX"),
        ClusterSpec("SIN-EDGE-01", "AHV", 2, 4, "7.0.1", "NX-1175S-G8"),
    ],
})

#: Very-large estate: one profile per site, 13 clusters each with long names.
XL_SITES = ("AMER-NEWYORK-DC1", "AMER-CHICAGO-DC2", "AMER-DALLAS-DC3", "AMER-SEATTLE-DC4",
            "EMEA-FRANKFURT-DC1", "EMEA-LONDON-DC2", "APAC-SINGAPORE-DC1", "APAC-SYDNEY-DC2")
_XL_ROLES = ("PROD-GENERAL-PURPOSE", "PROD-DATABASE-TIER1", "VDI-KNOWLEDGE-WORKERS",
             "DEV-TEST-CONTINUOUS-INTEGRATION", "EDGE-RETAIL-STORES", "PROD-ANALYTICS",
             "BACKUP-TARGET")
_XL_MODELS = ("NX-8155-G9", "XC760xd-24", "HPE DX380 Gen11", "HX665 V3", "UCSC-C240-M7SX",
              "NX-8170-G9", "XC660-10", "HPE DX360 Gen10 Plus", "UCSC-C220-M6S", "HX630 V3")
_XL_NODES = (16, 12, 8, 24, 6, 4, 3, 10, 1, 8, 12, 5, 2)
for _si, _site in enumerate(XL_SITES):
    PROFILES[f"xl-{_si + 1}"] = [
        ClusterSpec(f"{_site}-{_XL_ROLES[_ci % len(_XL_ROLES)]}-{_ci + 1:02d}",
                    "ESXi" if (_ci + _si) % 4 == 3 else "AHV",
                    _XL_NODES[(_ci + _si) % len(_XL_NODES)], 4,
                    "7.0.1" if _ci % 3 else "6.10.1",
                    _XL_MODELS[(_ci * 3 + _si) % len(_XL_MODELS)])
        for _ci in range(13)
    ]

#: Workload mix: (prefix, weight, sockets, cores/socket, memory GiB, disk GiB list, OS).
_WORKLOADS: list[tuple[str, int, int, int, int, list[int], str]] = [
    ("web", 18, 2, 1, 8, [60], "Ubuntu 22.04"),
    ("app", 16, 4, 1, 16, [80, 200], "Rocky Linux 9"),
    ("db", 6, 8, 1, 64, [100, 500], "Red Hat Enterprise Linux 9"),
    ("app-sql", 4, 8, 1, 32, [100, 400], "Windows Server 2022"),
    ("k8s-worker", 14, 16, 1, 64, [120, 400], "Rocky Linux 9"),
    ("vdi", 14, 2, 1, 8, [80], "Windows 11 Enterprise"),
    ("test-runner", 8, 8, 1, 16, [200], "Ubuntu 24.04"),
    ("file-gw", 3, 4, 1, 16, [60, 1024], "Windows Server 2019"),
    ("mon", 4, 2, 2, 8, [100], "Debian 12"),
    ("legacy-app", 3, 1, 2, 4, [250], "CentOS 7"),
    ("jump", 2, 2, 1, 4, [40], "Windows Server 2019"),
    ("ml", 3, 12, 1, 96, [200, 800], "Ubuntu 22.04"),
    ("backup-proxy", 2, 4, 1, 8, [60], "Ubuntu 22.04"),
]

_ENV_FOR = {"web": "Prod", "app": "Prod", "db": "Prod", "app-sql": "Prod", "k8s-worker": "Prod",
            "vdi": "Dev", "test-runner": "Dev", "file-gw": "Prod", "mon": "Ops",
            "legacy-app": "Prod", "jump": "Ops", "ml": "Dev", "backup-proxy": "Prod"}
_APP_FOR = {"web": "Web", "app": "Billing", "db": "Postgres", "app-sql": "SQL",
            "k8s-worker": "Kubernetes", "vdi": "VDI", "test-runner": "CI", "file-gw": "Files",
            "mon": "Monitoring", "legacy-app": "Legacy", "jump": "Access", "ml": "ML",
            "backup-proxy": "Backup"}
_TIER_FOR = {"Prod": "Gold", "Ops": "Silver"}

_GUEST_ID = {
    "Ubuntu 22.04": "linux:64:Ubuntu-22.04", "Ubuntu 24.04": "linux:64:Ubuntu-24.04",
    "Rocky Linux 9": "linux:64:Rocky-9", "Red Hat Enterprise Linux 9": "linux:64:RHEL-9",
    "Windows Server 2022": "windows:64:WindowsServer2022Standard",
    "Windows Server 2019": "windows:64:WindowsServer2019Standard",
    "Windows 11 Enterprise": "windows:64:Windows11Enterprise",
    "Debian 12": "linux:64:Debian-12", "CentOS 7": "linux:64:CentOS-7",
}



# ─── estate containers ─────────────────────────────────────────────────


@dataclass
class PcEstate:
    host: str
    version: str
    seed: str
    epoch: int
    features: Features
    pc_uuid: str = ""
    pc_ip: str = ""
    pc_build: str = ""
    ncc: str = "ncc-5.1.0"
    clusters: list[dict[str, Any]] = field(default_factory=list)
    hosts: list[dict[str, Any]] = field(default_factory=list)
    cvms: list[dict[str, Any]] = field(default_factory=list)
    containers: list[dict[str, Any]] = field(default_factory=list)
    vswitches: list[dict[str, Any]] = field(default_factory=list)
    subnets: list[dict[str, Any]] = field(default_factory=list)
    #: vSphere port groups of the ESXi clusters (not Nutanix subnets).
    port_groups: list[dict[str, Any]] = field(default_factory=list)
    vms: list[dict[str, Any]] = field(default_factory=list)
    images: list[dict[str, Any]] = field(default_factory=list)
    templates: list[dict[str, Any]] = field(default_factory=list)
    ovas: list[dict[str, Any]] = field(default_factory=list)
    categories: list[dict[str, Any]] = field(default_factory=list)
    valueless_category_keys: list[str] = field(default_factory=list)
    protection_policies: list[dict[str, Any]] = field(default_factory=list)
    recovery_plans: list[dict[str, Any]] = field(default_factory=list)
    availability_zones: list[dict[str, Any]] = field(default_factory=list)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    security_policies: list[dict[str, Any]] = field(default_factory=list)
    service_groups: list[dict[str, Any]] = field(default_factory=list)
    address_groups: list[dict[str, Any]] = field(default_factory=list)
    vpcs: list[dict[str, Any]] = field(default_factory=list)
    floating_ips: list[dict[str, Any]] = field(default_factory=list)
    file_servers: list[dict[str, Any]] = field(default_factory=list)
    file_shares: list[dict[str, Any]] = field(default_factory=list)
    object_stores: list[dict[str, Any]] = field(default_factory=list)
    volume_groups: list[dict[str, Any]] = field(default_factory=list)
    users: list[dict[str, Any]] = field(default_factory=list)
    roles: list[dict[str, Any]] = field(default_factory=list)
    projects: list[dict[str, Any]] = field(default_factory=list)
    directories: list[dict[str, Any]] = field(default_factory=list)
    playbooks: list[dict[str, Any]] = field(default_factory=list)
    report_configs: list[dict[str, Any]] = field(default_factory=list)
    lcm_entities: list[dict[str, Any]] = field(default_factory=list)
    licenses: list[dict[str, Any]] = field(default_factory=list)
    # ─ design data (`_design`) ─
    blocks: list[dict[str, Any]] = field(default_factory=list)
    host_nics: list[dict[str, Any]] = field(default_factory=list)
    uplink_bonds: list[dict[str, Any]] = field(default_factory=list)
    external_storages: list[dict[str, Any]] = field(default_factory=list)
    pc_vms: list[dict[str, Any]] = field(default_factory=list)
    products: list[dict[str, Any]] = field(default_factory=list)
    registrations: list[dict[str, Any]] = field(default_factory=list)

    @property
    def v4_ga(self) -> bool:
        parsed, scheme = parse_version(self.version)
        if parsed is None:
            return False
        return parsed >= ((2024, 3) if scheme == "calendar" else (7, 0))

    def vm_count(self) -> int:
        return len(self.vms)


@dataclass
class PeEstate:
    host: str
    version: str
    seed: str
    epoch: int
    cluster: dict[str, Any] = field(default_factory=dict)
    hosts: list[dict[str, Any]] = field(default_factory=list)
    containers: list[dict[str, Any]] = field(default_factory=list)
    subnets: list[dict[str, Any]] = field(default_factory=list)
    vms: list[dict[str, Any]] = field(default_factory=list)
    cvms: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class NdbEstate:
    host: str
    version: str
    seed: str
    clusters: list[dict[str, Any]] = field(default_factory=list)
    dbservers: list[dict[str, Any]] = field(default_factory=list)
    databases: list[dict[str, Any]] = field(default_factory=list)
    time_machines: list[dict[str, Any]] = field(default_factory=list)


# ─── Prism Central estate ──────────────────────────────────────────────


def build_pc_estate(
    host: str,
    profile: str = "medium",
    version: str = "pc.2024.3",
    *,
    seed: str | None = None,
    epoch: int = 0,
    features: Features | None = None,
    pc_ip: str | None = None,
) -> PcEstate:
    """Build a synthetic Prism Central estate for `profile`.

    `epoch` adds drift on top of the same base estate: epoch 1 is "a week later" (new
    Kubernetes workers, retired legacy VMs, an AOS upgrade, a new host, a removed
    protection policy …), epoch 2 a week after that, and so on.
    """
    specs = PROFILES[profile]
    seed = seed or host
    est = PcEstate(host=host, version=version, seed=seed, epoch=epoch,
                   features=features or Features())
    est.pc_uuid = uid(seed, "pc")
    octet = 10 + (sum(map(ord, seed)) % 200)
    est.pc_ip = pc_ip or f"10.{octet}.0.50"
    est.pc_build = build_string(version, seed, "pc")

    _clusters_and_hosts(est, specs)
    _networking(est)
    _storage(est)
    _catalog(est)
    _categories(est)
    _vms(est, specs)
    _system_vms(est)
    _data_protection(est)
    _flow(est)
    _nus(est)
    _iam(est)
    _automation(est)
    _lcm(est)
    _licenses(est)
    _alerts(est)
    _design(est)
    return est


def _clusters_and_hosts(est: PcEstate, specs: list[ClusterSpec]) -> None:
    seed, epoch = est.seed, est.epoch
    for ci, spec in enumerate(specs):
        cuuid = uid(seed, "cluster", spec.name)
        aos = spec.aos
        # Drift: the first cluster is upgraded to AOS 7.0 a week later.
        if ci == 0 and epoch >= 1 and aos.startswith("6.10"):
            aos = "7.0"
        nodes = spec.nodes + (2 if ci == 1 and epoch >= 1 else 0) + (2 if ci == 1 and epoch >= 2
                                                                    else 0)
        r = rng(seed, "cluster", spec.name)
        cluster = {
            "uuid": cuuid,
            "name": spec.name,
            "aos": aos,
            "ncc": "ncc-5.1.0" if aos.startswith("7") else "ncc-4.6.6",
            "hypervisor": spec.hypervisor,
            "nodes": nodes,
            "rf": 3 if nodes >= 8 else 2,
            "external_ip": f"10.{20 + ci}.0.10",
            # Every third cluster runs without a Data Services IP (no Volumes / Files
            # clients), so the reports and grid exercise the N/A path.
            "data_services_ip": None if ci % 3 == 2 else f"10.{20 + ci}.0.11",
            "timezone": ["America/Phoenix", "Europe/Amsterdam", "UTC"][ci % 3],
            "encryption": "ENABLED" if r.random() < 0.5 else "DISABLED",
            "is_lts": aos in ("6.10", "6.10.1", "7.0"),
            "arch": "X86_64",
            "model": spec.model,
            "index": ci,
        }
        est.clusters.append(cluster)
        for n in range(1, nodes + 1):
            hname = f"{spec.name.lower()}-n{n}"
            hr = rng(seed, "host", hname)
            serial = f"{spec.name[:3].upper()}{ci:01d}{n:02d}{hr.randint(1000, 9999)}"
            # Nodes past the original half of a mixed cluster are the newer model; the
            # expansion nodes a later epoch adds are, too.
            block_model = (spec.mixed_model if spec.mixed_model and n > spec.nodes // 2
                           else spec.model)
            hw = NODE_MODELS[block_model]
            sockets = hw.sockets
            cores = hw.sockets * hw.cores_per_socket
            disks = [{"size": size, "tier": tier}
                     for count, size, tier in hw.disks for _ in range(count)]
            hv_version = (
                "Nutanix 20230302.101026" if spec.hypervisor == "AHV"
                else "VMware ESXi 8.0.2 build-22380479"
            )
            est.hosts.append({
                "uuid": uid(seed, "host", hname),
                "name": hname,
                "cluster_uuid": cuuid,
                "cluster_name": spec.name,
                "hypervisor": spec.hypervisor,
                "hv_version": hv_version,
                "cpu_model": hw.cpu_model,
                "sockets": sockets,
                "cores": cores,
                "threads": cores * 2,
                "freq_hz": 2_600_000_000,
                "memory": hr.choice([512, 768, 1024]) * GIB,
                "disks": disks,
                "storage_raw": sum(d["size"] for d in disks),
                "block_serial": f"{serial[:-2]}XX",
                "block_model": block_model,
                "serial": serial,
                "ipmi_ip": f"10.{20 + ci}.9.{n}",
                "cvm_ip": f"10.{20 + ci}.0.{30 + n}",
                "hv_ip": f"10.{20 + ci}.0.{60 + n}",
                "maintenance": "normal",
                "gpus": ["NVIDIA L4"] if spec.name.upper().startswith("VDI") else [],
                "index": n,
            })
            est.cvms.append({
                "uuid": uid(seed, "cvm", hname),
                "name": f"NTNX-{serial}-A-CVM",
                "cluster_name": spec.name,
                "host_name": hname,
                "memory": 48 * GIB,
                "vcpus": hw.cvm_vcpus,
                "ip": f"10.{20 + ci}.0.{30 + n}",
                "hypervisor": spec.hypervisor,
            })


def _networking(est: PcEstate) -> None:
    seed = est.seed
    est.vswitches = [{"uuid": uid(seed, "vs", "vs0"), "name": "vs0"},
                     {"uuid": uid(seed, "vs", "vs1"), "name": "vs1-storage"}]
    for c in est.clusters:
        ci = c["index"]
        base = c["name"].lower()
        if c["hypervisor"] == "ESXi":
            # ESXi clusters have vSphere port groups, not Nutanix subnets: Prism
            # Central's subnet APIs list nothing for them (as on the lab PC). Their
            # Prism Element lists the port groups (v2.0 networks), vCenter their type.
            for k, (pg_name, vlan, switch) in enumerate([
                    ("servers_vlan", 300 + ci, "DISTRIBUTED"),
                    ("VM Network", 0, "STANDARD"),
                    ("vMotion", 445, "DISTRIBUTED")]):
                est.port_groups.append({
                    "uuid": uid(seed, "portgroup", c["name"], pg_name),
                    "name": pg_name,
                    "cluster_uuid": c["uuid"],
                    "cluster_name": c["name"],
                    "vlan": vlan,
                    "switch": switch,
                    "prefix": f"10.{40 + ci}.{k}.0",
                    "vc_id": (f"dvportgroup-{100 + ci * 10 + k}" if switch == "DISTRIBUTED"
                              else f"network-{100 + ci * 10 + k}"),
                })
            continue
        names = [("prod", 100), ("dev", 110), ("mgmt", 10), ("dmz", 200), ("vdi", 120)]
        for k, (suffix, vlan) in enumerate(names):
            name = f"{base}-{suffix}"
            est.subnets.append({
                "uuid": uid(seed, "subnet", name),
                "name": name,
                "cluster_uuid": c["uuid"],
                "cluster_name": c["name"],
                "type": "VLAN",
                "vlan": vlan + ci,
                "vswitch_uuid": est.vswitches[0]["uuid"],
                "prefix": f"10.{20 + ci}.{k + 1}.0",
                "prefix_len": 24 if suffix != "vdi" else 22,
                "gateway": f"10.{20 + ci}.{k + 1}.1",
                "dhcp": suffix in ("vdi", "dev"),
                "vpc": None,
                "port": k + 1,
            })
    if est.features.fvn:
        # The VPCs' uplink: an external VLAN subnet on the first AHV cluster, routed
        # (no NAT), so the virtual networking design can draw its VLAN and range.
        host = next((c for c in est.clusters if c["hypervisor"] != "ESXi"), None)
        if host is not None:
            est.subnets.append({
                "uuid": uid(seed, "subnet", "ext-transit"),
                "name": "ext-transit",
                "cluster_uuid": host["uuid"],
                "cluster_name": host["name"],
                "type": "VLAN",
                "vlan": 900,
                "vswitch_uuid": est.vswitches[0]["uuid"],
                "prefix": "203.0.113.0",
                "prefix_len": 24,
                "gateway": "203.0.113.1",
                "dhcp": False,
                "vpc": None,
                "port": 0,
                "external": True,
                "nat": False,
            })
        for i, vpc_name in enumerate(["vpc-prod", "vpc-dev", "vpc-pci"]):
            vuuid = uid(seed, "vpc", vpc_name)
            est.vpcs.append({
                "uuid": vuuid,
                "name": vpc_name,
                "external_subnet": "ext-transit",
                "prefixes": [(f"172.{16 + i}.0.0", 16)],
                "dns": ["10.20.1.5", "10.20.1.6"],
                "routes": 2 + i,
                "policies": i,
            })
            for j in range(2):
                name = f"{vpc_name}-overlay-{j + 1}"
                est.subnets.append({
                    "uuid": uid(seed, "subnet", name),
                    "name": name,
                    "cluster_uuid": None,
                    "cluster_name": None,
                    "type": "OVERLAY",
                    "vlan": None,
                    "vswitch_uuid": None,
                    "prefix": f"172.{16 + i}.{j}.0",
                    "prefix_len": 24,
                    "gateway": f"172.{16 + i}.{j}.1",
                    "dhcp": True,
                    "vpc": vpc_name,
                    "port": 0,
                })
            est.floating_ips.append({
                "uuid": uid(seed, "fip", vpc_name),
                "ip": f"203.0.113.{10 + i}",
                "vpc": vpc_name,
                "external_subnet": "ext-transit",
                "private_ip": f"172.{16 + i}.0.10",
            })


def _storage(est: PcEstate) -> None:
    seed, epoch = est.seed, est.epoch
    for c in est.clusters:
        names = ["default-container", "ctr-prod", "ctr-images", "NutanixManagementShare"]
        if c["index"] == 1 and epoch >= 1:
            names.append("ctr-k8s-02")
        for name in names:
            full = f"{c['name'].lower()}-{name}" if name != "NutanixManagementShare" else name
            r = rng(seed, "container", c["name"], name)
            # Every container's ceiling is the cluster's shared storage pool.
            raw = sum(h["storage_raw"] for h in est.hosts if h["cluster_uuid"] == c["uuid"])
            usable = int(raw * 0.95) // (c["rf"])
            if name == "NutanixManagementShare":
                usable = min(4 * TIB, usable)
            # The containers share the pool, so each uses a slice of it.
            used = int(usable * r.uniform(0.08, 0.2))
            est.containers.append({
                "uuid": uid(seed, "container", c["name"], name),
                "name": full,
                "cluster_uuid": c["uuid"],
                "cluster_name": c["name"],
                "rf": c["rf"],
                "compression": name != "ctr-images",
                "compression_delay": 0 if name != "ctr-prod" else 3600,
                "dedup": "POST_PROCESS" if name == "ctr-images" else "NONE",
                "ec": "on" if name == "ctr-prod" and c["nodes"] >= 6 else "off",
                "raw": raw,
                "max": usable,
                "used": used,
                "free": usable - used,
            })


def _catalog(est: PcEstate) -> None:
    seed = est.seed
    names = [
        ("ubuntu-22.04-server.qcow2", "DISK_IMAGE", 3), ("rocky-9.4.qcow2", "DISK_IMAGE", 2),
        ("windows-server-2022.iso", "ISO_IMAGE", 6), ("windows-11-ent.iso", "ISO_IMAGE", 7),
        ("rhel-9.4-x86_64.iso", "ISO_IMAGE", 11), ("virtio-win-0.1.248.iso", "ISO_IMAGE", 1),
        ("ngt-4.1.iso", "ISO_IMAGE", 1), ("debian-12-genericcloud.qcow2", "DISK_IMAGE", 2),
        ("vdi-gold-w11.qcow2", "DISK_IMAGE", 60), ("k8s-node-1.30.qcow2", "DISK_IMAGE", 20),
        ("centos-7-legacy.qcow2", "DISK_IMAGE", 8), ("move-5.3.qcow2", "DISK_IMAGE", 9),
    ]
    count = min(len(names), 6 + len(est.clusters) * 2)
    for name, kind, size in names[:count]:
        clusters = [c["uuid"] for c in est.clusters
                    if c["hypervisor"] == "AHV"][: 1 + (len(name) % 3)]
        est.images.append({
            "uuid": uid(seed, "image", name),
            "name": name,
            "type": kind,
            "size": size * GIB,
            "arch": "X86_64",
            "source": f"https://repo.example.com/images/{name}",
            "clusters": clusters,
            "state": "COMPLETE",
        })
    for i, (name, os_name, sockets, mem) in enumerate([
        ("tpl-ubuntu-22.04", "Ubuntu 22.04", 2, 8), ("tpl-rocky-9", "Rocky Linux 9", 4, 16),
        ("tpl-win2022", "Windows Server 2022", 4, 16), ("tpl-k8s-node", "Rocky Linux 9", 16, 64),
        ("tpl-vdi-w11", "Windows 11 Enterprise", 2, 8), ("tpl-sql-2022", "Windows Server 2022",
                                                         8, 32),
    ][: 3 + len(est.clusters)]):
        est.templates.append({
            "uuid": uid(seed, "template", name),
            "name": name,
            "description": f"Golden image for {os_name}",
            "guest_os": _GUEST_ID.get(os_name, os_name),
            "sockets": sockets,
            "memory": mem * GIB,
            "versions": 1 + i % 3,
            "created_by": "admin",
        })
    for i, name in enumerate(["appliance-fw-4.2", "monitoring-stack-2.1", "lb-virtual-9"][
            : max(1, len(est.clusters) - 1)]):
        c = est.clusters[i % len(est.clusters)]
        est.ovas.append({
            "uuid": uid(seed, "ova", name),
            "name": name,
            "size": (4 + i * 3) * GIB,
            "disks": 1 + i % 2,
            "cluster_uuid": c["uuid"],
            "created": iso(BASE_TIME - timedelta(days=90 + i * 17)),
        })


def _categories(est: PcEstate) -> None:
    seed = est.seed
    pairs = [("Env", v) for v in ("Prod", "Dev", "Ops", "Lab")]
    pairs += [("App", v) for v in sorted(set(_APP_FOR.values()))]
    pairs += [("DRTier", v) for v in ("Gold", "Silver", "Bronze")]
    pairs += [("AppType", v) for v in ("Kubernetes", "Default")]
    pairs += [("Quarantine", "Strict")]
    for key, value in pairs:
        est.categories.append({
            "uuid": uid(seed, "category", key, value),
            "key": key,
            "value": value,
            "description": f"{key} = {value}",
        })
    est.valueless_category_keys = ["CalmApplication", "OSType", "CalmProject"]


def _workload_plan(spec: ClusterSpec, seed: str) -> list[tuple[str, int]]:
    """How many VMs of each workload a cluster runs, summing to `spec.vms`."""
    if spec.name.upper().startswith("VDI"):
        mix = [w for w in _WORKLOADS if w[0] in ("vdi", "jump", "mon")]
    elif spec.hypervisor == "ESXi":
        mix = [w for w in _WORKLOADS if w[0] in ("web", "app", "jump", "legacy-app", "mon")]
    else:
        mix = list(_WORKLOADS)
    total_w = sum(w[1] for w in mix)
    plan = [(w[0], max(1, spec.vms * w[1] // total_w)) for w in mix]
    diff = spec.vms - sum(n for _, n in plan)
    name, n = plan[0]
    plan[0] = (name, max(1, n + diff))
    return plan


def _vms(est: PcEstate, specs: list[ClusterSpec]) -> None:
    seed, epoch = est.seed, est.epoch
    by_prefix = {w[0]: w for w in _WORKLOADS}
    counters: dict[str, int] = {}
    hosts_by_cluster: dict[str, list[dict[str, Any]]] = {}
    for h in est.hosts:
        hosts_by_cluster.setdefault(h["cluster_uuid"], []).append(h)
    subnets_by_cluster: dict[str, list[dict[str, Any]]] = {}
    for s in est.subnets:
        if s["cluster_uuid"] and not s.get("external"):  # no guest VM on an uplink
            subnets_by_cluster.setdefault(s["cluster_uuid"], []).append(s)
    servers_pg = {p["cluster_uuid"]: p for p in est.port_groups if p["name"] == "servers_vlan"}
    containers_by_cluster: dict[str, list[dict[str, Any]]] = {}
    for ct in est.containers:
        containers_by_cluster.setdefault(ct["cluster_uuid"], []).append(ct)
    ahv_images = [i for i in est.images if i["type"] == "DISK_IMAGE"]

    def make(prefix: str, n: int, cluster: dict[str, Any], spec_name: str) -> dict[str, Any]:
        w = by_prefix[prefix]
        name = f"{prefix}-{n:02d}"
        key = (seed, "vm", name)
        r = rng(*key)
        # A fixed number of draws, mapped onto the choices afterwards: randrange() uses
        # rejection sampling, so its consumption depends on the range size — adding a
        # host to a cluster would otherwise reshuffle every later attribute of every VM.
        d_host, d_subnet, d_tier, d_power, d_image, d_pick = (r.random() for _ in range(6))
        mac = [int(r.random() * 256) for _ in range(3)]
        hosts = hosts_by_cluster[cluster["uuid"]]
        host = hosts[int(d_host * len(hosts))]
        subnets = subnets_by_cluster.get(cluster["uuid"], [])
        subnet = subnets[int(d_subnet * min(len(subnets), 3))] if subnets else None
        ctrs = [c for c in containers_by_cluster[cluster["uuid"]]
                if "Management" not in c["name"]]
        ctr = ctrs[1 % len(ctrs)] if prefix not in ("vdi",) else ctrs[0]
        env_name = _ENV_FOR[prefix]
        cats: dict[str, list[str]] = {"Env": [env_name], "App": [_APP_FOR[prefix]]}
        tier = _TIER_FOR.get(env_name)
        if tier and d_tier < 0.87:
            cats["DRTier"] = [tier]
        if prefix == "k8s-worker":
            cats["AppType"] = ["Kubernetes"]
        power = "ON" if d_power < 0.91 else "OFF"
        memory = w[4] * GIB
        if name == "app-sql-01" and epoch >= 1:
            memory = 64 * GIB
        disks = []
        for di, size in enumerate(w[5]):
            disks.append({
                "uuid": uid(*key, "disk", di),
                "size": size * GIB,
                "container_uuid": ctr["uuid"],
                "container_name": ctr["name"],
                "bus": "SCSI",
                "index": di,
                "image": (ahv_images[int(d_pick * len(ahv_images))]["uuid"]
                          if di == 0 and ahv_images and d_image < 0.6 else None),
                "datastore": None,
            })
        esxi = cluster["hypervisor"] == "ESXi"
        if esxi and n % 4 == 0:
            # A scratch disk on the host's local VMFS datastore: no Nutanix storage
            # container holds it, so v4 leaves it out; Prism Element lists it with a
            # size only and vCenter names its datastore.
            disks.append({
                "uuid": uid(*key, "disk", "local"), "size": 40 * GIB,
                "container_uuid": None, "container_name": None, "bus": "SCSI",
                "index": len(disks), "image": None,
                "datastore": f"{host['name']}-local-ds",
            })
        pg = servers_pg.get(cluster["uuid"]) if esxi else None
        ip = None
        if subnet is not None and power == "ON":
            ip = f"{subnet['prefix'].rsplit('.', 1)[0]}.{10 + (n % 240)}"
        elif pg is not None and power == "ON":
            ip = f"{pg['prefix'].rsplit('.', 1)[0]}.{10 + (n % 240)}"
        nic = {
            "uuid": uid(*key, "nic", 0),
            "mac": ("00:50:56:" if esxi else "50:6b:8d:") + ":".join(f"{b:02x}" for b in mac),
            "subnet_uuid": subnet["uuid"] if subnet else None,
            "port_group": pg["name"] if pg else None,
            "port_group_uuid": pg["uuid"] if pg else None,
            "ip": ip,
            "connected": power == "ON",
        }
        protected = "DRTier" in cats
        return {
            "uuid": uid(*key),
            "name": name,
            "cluster_uuid": cluster["uuid"],
            "cluster_name": cluster["name"],
            "host_uuid": host["uuid"],
            "host_name": host["name"],
            "power": power,
            "sockets": w[2],
            "cores": w[3],
            "memory": memory,
            "guest_os": w[6],
            "guest_os_id": _GUEST_ID.get(w[6], w[6]),
            "description": f"{_APP_FOR[prefix]} workload ({env_name})",
            "categories": cats,
            "protection_type": "RULE_PROTECTED" if protected else "UNPROTECTED",
            "disks": disks,
            "cdroms": 1 if prefix in ("vdi", "app-sql") else 0,
            "nics": [nic],
            "boot": "UEFI" if w[6].startswith("Windows") else "LEGACY",
            "secure_boot": w[6].startswith("Windows 11"),
            "created": iso(BASE_TIME - timedelta(days=5 + int(d_pick * 895))),
            "gpus": ["NVIDIA L4"] if spec_name.upper().startswith("VDI") and prefix == "vdi"
            and d_image < 0.2 else [],
            "hypervisor": cluster["hypervisor"],
            "esxi_bad": False,
        }

    for ci, spec in enumerate(specs):
        cluster = est.clusters[ci]
        for prefix, count in _workload_plan(spec, seed):
            for _ in range(count):
                counters[prefix] = counters.get(prefix, 0) + 1
                n = counters[prefix]
                # Drift: the legacy apps on the first cluster are retired a week later.
                if epoch >= 1 and prefix == "legacy-app" and ci == 0 and n <= 6:
                    continue
                est.vms.append(make(prefix, n, cluster, spec.name))
    # Drift: eight new Kubernetes workers per epoch on the second cluster (or the first).
    target = est.clusters[1] if len(est.clusters) > 1 else est.clusters[0]
    if target["hypervisor"] == "AHV":
        for _ in range(8 * epoch * (4 if len(est.vms) > 1000 else 1)):
            counters["k8s-worker"] = counters.get("k8s-worker", 0) + 1
            est.vms.append(make("k8s-worker", counters["k8s-worker"], target, target["name"]))
    # Mark the ESXi records the v4 endpoint fails to serialize.
    bad = est.features.esxi_bad_vms
    if bad:
        for vm in [v for v in est.vms if v["hypervisor"] == "ESXi"][3: 3 + bad]:
            vm["esxi_bad"] = True


#: Per-PC-VM sizing for each deployment size: (vCPU, memory GiB, data disk GiB).
PC_SIZING: dict[str, tuple[int, int, int]] = {
    "SMALL": (6, 28, 500), "LARGE": (10, 44, 2500), "EXTRALARGE": (14, 60, 2500),
}


def pc_vm_ips(est: PcEstate) -> list[str]:
    """The PC VMs' addresses: `pc_ip` for a single VM; for scale-out `pc_ip` is the VIP
    and the VMs take the next addresses."""
    count = max(1, est.features.pc_vm_count)
    if count == 1:
        return [est.pc_ip]
    base, last = est.pc_ip.rsplit(".", 1)
    return [f"{base}.{int(last) + i}" for i in range(1, count + 1)]


def pc_host_cluster(est: PcEstate) -> dict[str, Any]:
    """The cluster running the PC VM(s) (`Features.pc_cluster`)."""
    return est.clusters[min(max(est.features.pc_cluster, 0), len(est.clusters) - 1)]


def _system_vms(est: PcEstate) -> None:
    """The Prism Central VM(s), which collectors must reclassify as PRISM_CENTRAL."""
    c = pc_host_cluster(est)
    hosts = [h for h in est.hosts if h["cluster_uuid"] == c["uuid"]]
    ctr = next(ct for ct in est.containers if ct["cluster_uuid"] == c["uuid"])
    subnet = next((s for s in est.subnets if s["cluster_uuid"] == c["uuid"]), None)
    vcpus, mem_gib, _disk = PC_SIZING.get(est.features.pc_size, PC_SIZING["SMALL"])
    for i, ip in enumerate(pc_vm_ips(est)):
        # The first PC VM keeps the ids a single-VM estate always had.
        key: tuple[object, ...] = () if i == 0 else (i,)
        host = hosts[i % len(hosts)]
        est.vms.append({
            "uuid": uid(est.seed, "pcvm", *key),
            "name": f"NTNX-{ip.replace('.', '-')}-PCVM-1696000000",
            "cluster_uuid": c["uuid"],
            "cluster_name": c["name"],
            "host_uuid": host["uuid"],
            "host_name": host["name"],
            "power": "ON",
            "sockets": vcpus, "cores": 1, "memory": mem_gib * GIB,
            "guest_os": "Prism Central", "guest_os_id": "linux:64:PrismCentral",
            "description": "Prism Central VM",
            "categories": {},
            "protection_type": "UNPROTECTED",
            "disks": [{"uuid": uid(est.seed, "pcvm", *key, "disk", d), "size": size * GIB,
                       "container_uuid": ctr["uuid"], "container_name": ctr["name"],
                       "bus": "SCSI", "index": d, "image": None}
                      for d, size in enumerate([30, 500, 100])],
            "cdroms": 0,
            "nics": [{"uuid": uid(est.seed, "pcvm", *key, "nic"),
                      "mac": f"50:6b:8d:00:00:{0x50 + i:02x}",
                      "subnet_uuid": subnet["uuid"] if subnet else None, "port_group": None,
                      "ip": ip, "connected": True}],
            "boot": "LEGACY", "secure_boot": False,
            "created": iso(BASE_TIME - timedelta(days=700)),
            "gpus": [], "hypervisor": "AHV", "esxi_bad": False,
        })


def _data_protection(est: PcEstate) -> None:
    seed, epoch = est.seed, est.epoch
    local = {"uuid": est.pc_uuid, "name": f"Local ({est.host})", "type": "PHYSICAL",
             "url": est.pc_uuid}
    if est.features.azs is not None or est.features.policies is not None:
        _custom_data_protection(est, local)
        return
    remote_host = "pc-dr.example.com"
    remote = {"uuid": uid(seed, "az", remote_host), "name": "pc-dr",
              "type": "PHYSICAL", "url": f"https://{remote_host}:9440"}
    est.availability_zones = [
        {**remote, "region": "us-west", "status": "CONNECTED"},
        {"uuid": uid(seed, "az", "xi"), "name": "Xi-US-East", "type": "XI",
         "url": "https://xi.nutanix.com", "region": "us-east", "status": "CONNECTED"},
    ]
    policies = [
        ("Gold-15min", 900 if epoch >= 1 else 3600, {"DRTier": ["Gold"]}, 24, 48),
        ("Silver-1h", 3600, {"DRTier": ["Silver"]}, 12, 24),
        ("Bronze-daily", 86400, {"DRTier": ["Bronze"]}, 7, 14),
        ("SQL-NearSync", 60, {"App": ["SQL"]}, 24, 24),
        ("K8s-Nightly", 86400, {"AppType": ["Kubernetes"]}, 3, 7),
    ]
    count = 2 + min(len(est.clusters), 3)
    for name, rpo, cats, loc, rem in policies[:count]:
        if name == "Bronze-daily" and epoch >= 1:
            continue
        est.protection_policies.append({
            "uuid": uid(seed, "pp", name), "name": name, "rpo": rpo, "categories": cats,
            "local_retention": loc, "remote_retention": rem,
            "source": local, "target": remote,
        })
    for i, name in enumerate(["RP-Billing", "RP-Web-Tier", "RP-SQL", "RP-Kubernetes",
                              "RP-Monitoring", "RP-All-Prod"][: 2 + len(est.clusters)]):
        est.recovery_plans.append({
            "uuid": uid(seed, "rp", name), "name": name, "stages": 1 + i % 3,
            "entities": 4 + i * 3, "network_maps": 1 + i % 2,
            "primary": local["url"], "recovery": remote["url"],
        })


def _custom_data_protection(est: PcEstate, local: dict[str, Any]) -> None:
    """Data protection from `Features.azs` / `Features.policies` (scenario estates)."""
    seed = est.seed
    by_name: dict[str, dict[str, Any]] = {}
    for az in est.features.azs or ():
        entry = {"uuid": uid(seed, "az", az.name), "name": az.name, "type": az.type,
                 "url": az.url, "region": az.region, "status": az.status}
        est.availability_zones.append(entry)
        by_name[az.name] = entry
    for p in est.features.policies or ():
        remote = by_name.get(p.target)
        if remote is None:
            continue
        cats: dict[str, list[str]] = {}
        for k, v in p.categories:
            cats.setdefault(k, []).append(v)
        est.protection_policies.append({
            "uuid": uid(seed, "pp", p.name), "name": p.name, "rpo": p.rpo,
            "categories": cats, "local_retention": p.local_retention,
            "remote_retention": p.remote_retention,
            "source": remote if p.inbound else local,
            "target": local if p.inbound else remote,
        })
    for i, pol in enumerate(est.protection_policies[:3]):
        name = f"RP-{pol['name']}"
        est.recovery_plans.append({
            "uuid": uid(seed, "rp", name), "name": name, "stages": 1 + i % 2,
            "entities": 6 + i * 4, "network_maps": 1,
            "primary": pol["source"]["url"], "recovery": pol["target"]["url"],
            # A synchronous (Metro) plan names its witness.
            **({"witness": uid(seed, "witness", name)} if pol["rpo"] == 0 else {}),
        })


def _flow(est: PcEstate) -> None:
    seed, epoch = est.seed, est.epoch
    cat_id = {(c["key"], c["value"]): c["uuid"] for c in est.categories}
    defs = [
        ("app-billing", "APPLICATION", "ENFORCE", [("App", "Billing")], 3, 2),
        ("app-web", "APPLICATION", "MONITOR", [("App", "Web")], 2, 1),
        ("app-sql", "APPLICATION", "ENFORCE", [("App", "SQL")], 4, 1),
        ("k8s-cluster", "APPLICATION", "ENFORCE", [("AppType", "Kubernetes")], 5, 3),
        ("isolate-dev-prod", "ISOLATION", "ENFORCE", [("Env", "Dev"), ("Env", "Prod")], 0, 0),
        ("quarantine-strict", "QUARANTINE", "ENFORCE", [("Quarantine", "Strict")], 0, 0),
        ("app-monitoring", "APPLICATION", "MONITOR", [("App", "Monitoring")], 1, 4),
        ("app-vdi", "APPLICATION", "SAVE", [("App", "VDI")], 2, 2),
    ]
    count = 3 + len(est.clusters)
    if len(est.vms) > 3000:
        # A big estate has a policy per application tier per environment.
        defs = defs + [(f"{d[0]}-{env}", d[1], d[2], d[3], d[4], d[5])
                       for env in ("prod", "dr") for d in defs if d[1] == "APPLICATION"]
        count = len(defs)
    for name, ptype, state, groups, inbound, outbound in defs[:count]:
        est.security_policies.append({
            "uuid": uid(seed, "nsp", name), "name": name, "type": ptype, "state": state,
            "groups": groups, "group_ids": [cat_id[g] for g in groups if g in cat_id],
            "inbound": inbound, "outbound": outbound,
        })
    if epoch >= 1:
        est.security_policies.append({
            "uuid": uid(seed, "nsp", "isolate-pci"), "name": "isolate-pci", "type": "ISOLATION",
            "state": "ENFORCE", "groups": [("Env", "Prod"), ("Env", "Lab")],
            "group_ids": [cat_id[("Env", "Prod")], cat_id[("Env", "Lab")]],
            "inbound": 0, "outbound": 0,
        })
    est.service_groups = [
        {"uuid": uid(seed, "sg", n), "name": n, "system": sysdef, "tcp": tcp, "udp": udp,
         "icmp": icmp}
        for n, sysdef, tcp, udp, icmp in [
            ("HTTP", True, [(80, 80)], [], False), ("HTTPS", True, [(443, 443)], [], False),
            ("SSH", True, [(22, 22)], [], False), ("DNS", True, [(53, 53)], [(53, 53)], False),
            ("app-ports", False, [(8000, 8080), (9443, 9443)], [], True),
            ("sql", False, [(1433, 1434)], [(1434, 1434)], False),
        ]
    ]
    est.address_groups = [
        {"uuid": uid(seed, "ag", n), "name": n, "system": sysdef, "v4": v4, "ranges": ranges}
        for n, sysdef, v4, ranges in [
            ("corp-networks", False, [("10.0.0.0", 8)], []),
            ("monitoring-hosts", False, [("10.20.1.70", 32), ("10.20.1.71", 32)], []),
            ("partner-range", False, [], [("198.51.100.10", "198.51.100.40")]),
            ("Any-IPv4", True, [("0.0.0.0", 0)], []),
        ]
    ]
    _rule_peers(est, cat_id)


def _rule_peers(est: PcEstate, cat_id: dict[tuple[str, str], str]) -> None:
    """Who each application policy's allow rules name (the v4 detail and v3 allow
    lists): any source first, then the corporate address group, then a category; out
    to the SQL tier (sql service group), then the monitoring hosts over HTTPS."""
    ag = {a["name"]: a["uuid"] for a in est.address_groups}
    sg = {g["name"]: g["uuid"] for g in est.service_groups}
    for p in est.security_policies:
        own = p["groups"][0] if p["groups"] else None
        other = ("App", "Web") if own != ("App", "Web") else ("App", "Billing")
        peer_cat = [cat_id[other]] if other in cat_id else []
        sql = ("App", "SQL") if own != ("App", "SQL") else ("App", "Billing")
        p["in_peers"] = [
            {"kind": "ALL"} if i == 0 else
            {"kind": "AG", "ids": [ag["corp-networks"]], "names": ["corp-networks"]} if i == 1
            else {"kind": "CAT", "ids": peer_cat, "labels": [f"{other[0]}:{other[1]}"],
                  "services": [sg["HTTPS"]], "service_names": ["HTTPS"]}
            for i in range(p["inbound"])]
        p["out_peers"] = [
            {"kind": "CAT", "ids": [cat_id[sql]] if sql in cat_id else [],
             "labels": [f"{sql[0]}:{sql[1]}"], "services": [sg["sql"]],
             "service_names": ["sql"]} if j == 0 else
            {"kind": "AG", "ids": [ag["monitoring-hosts"]], "names": ["monitoring-hosts"]}
            for j in range(p["outbound"])]


def _nus(est: PcEstate) -> None:
    seed = est.seed
    feats = est.features
    if feats.files:
        for i, name in enumerate(["fs-corp", "fs-home"][: max(1, len(est.clusters) - 1)]):
            c = est.clusters[i % len(est.clusters)]
            est.file_servers.append({
                "uuid": uid(seed, "fs", name), "name": name, "cluster_name": c["name"],
                "version": "5.0.0.1", "fsvms": 3, "size_gib": 20480 * (i + 1),
                "used": int(20480 * (i + 1) * GIB * 0.41),
                "internal": f"{c['name'].lower()}-mgmt", "external": f"{c['name'].lower()}-prod",
            })
            for j, share in enumerate(["projects", "finance", "profiles", "scratch"]):
                est.file_shares.append({
                    "uuid": uid(seed, "share", name, share), "name": f"{share}",
                    "file_server": name, "protocol": "SMB" if j % 3 else "NFS",
                    "max_gib": 1024 * (j + 1),
                })
    if feats.objects:
        c = est.clusters[0]
        est.object_stores.append({
            "uuid": uid(seed, "oss", "objects-01"), "name": "objects-01",
            "cluster_name": c["name"], "version": "4.4", "workers": 3,
            "capacity_gib": 102400, "used": int(102400 * GIB * 0.18), "state": "COMPLETE",
        })
    for c in est.clusters:
        for k in range(3 + c["nodes"] // 3):
            name = f"vg-{c['name'].lower()}-{k + 1:02d}"
            r = rng(seed, "vg", name)
            disks = [r.choice([100, 200, 500]) * 1024 for _ in range(1 + k % 3)]
            est.volume_groups.append({
                "uuid": uid(seed, "vg", name), "name": name, "cluster_uuid": c["uuid"],
                "cluster_name": c["name"], "disks_mib": disks, "shared": k % 4 == 0,
                "target": f"iqn.2010-06.com.nutanix:{name}", "attachments": 1 + (k % 4 == 0),
                "flash": k % 5 == 0,
            })


def _iam(est: PcEstate) -> None:
    seed = est.seed
    est.directories = [
        {"uuid": uid(seed, "dir", "corp-ad"), "name": "corp-ad", "kind": "ldap"},
        {"uuid": uid(seed, "dir", "okta"), "name": "Okta", "kind": "saml"},
    ]
    users: list[tuple[str, str, str, str | None]] = [
        ("admin", "Administrator", "LOCAL", None),
        ("svc-inventory", "Inventory service", "SERVICE_ACCOUNT", None),
        ("svc-backup", "Backup service", "SERVICE_ACCOUNT", None),
    ]
    first = ["alex", "sam", "jordan", "taylor", "morgan", "casey", "riley", "jamie", "drew",
             "kai", "noor", "lee", "ana", "omar", "mia", "ivan", "zoe", "li", "sara", "tom"]
    n_users = 12 + 3 * len(est.clusters)
    for i in range(n_users):
        u = first[i % len(first)] + (str(i // len(first)) if i >= len(first) else "")
        if i % 3 == 0:
            users.append((f"{u}@okta.example.com", u.title(), "SAML", "Okta"))
        else:
            users.append((f"{u}@corp.example.com", u.title(), "LDAP", "corp-ad"))
    for name, display, kind, directory in users:
        est.users.append({"uuid": uid(seed, "user", name), "username": name,
                          "display": display, "type": kind, "directory": directory,
                          "state": "ACTIVE"})
    for name, perms, system in [
        ("Super Admin", 61, True), ("Prism Admin", 54, True), ("Prism Viewer", 22, True),
        ("Project Admin", 31, True), ("Consumer", 18, True), ("Operator", 25, True),
        ("Backup Admin", 12, False), ("Inventory Reader", 9, False), ("VDI Operator", 14, False),
    ]:
        est.roles.append({"uuid": uid(seed, "role", name), "name": name, "perms": perms,
                          "system": system, "description": f"{name} role"})
    for i, name in enumerate(["default", "platform-team", "data-science", "vdi-ops",
                              "ci-runners"][: 2 + len(est.clusters)]):
        est.projects.append({"uuid": uid(seed, "project", name), "name": name,
                             "description": f"{name} project", "users": 1 + i * 3})


def _automation(est: PcEstate) -> None:
    seed = est.seed
    if est.features.xplay:
        for name, trigger, actions, enabled in [
            ("Restart hung VM", "alert_trigger", 2, True),
            ("Snapshot before patch", "manual_trigger", 1, True),
            ("Notify on host down", "alert_trigger", 3, True),
            ("Weekly capacity email", "time_trigger", 1, True),
            ("Scale k8s workers", "webhook_trigger", 4, False),
            ("Quarantine on ransomware", "event_trigger", 2, True),
        ]:
            est.playbooks.append({"uuid": uid(seed, "pb", name), "name": name,
                                  "trigger": trigger, "actions": actions, "enabled": enabled,
                                  "description": f"Playbook: {name}"})
    if est.features.ncm:
        for i in range(10 + 3 * len(est.clusters)):
            name = ["Capacity runway", "VM inventory", "Cluster efficiency",
                    "Zombie VMs", "Alert summary", "License usage"][i % 6]
            name = f"{name} {i // 6 + 1}" if i >= 6 else name
            est.report_configs.append({
                "uuid": uid(seed, "report", name), "name": name,
                "last_run": iso(BASE_TIME - timedelta(days=i % 7, hours=i)),
            })


def _lcm(est: PcEstate) -> None:
    seed, epoch = est.seed, est.epoch
    rows: list[tuple[str, str, str, str | None, list[tuple[str, int]]]] = [
        # LCM names the PC entity version with its prefix ("pc.7.5.1.1"), as a real PC does.
        ("Prism Central", "PC Core Cluster", pc_version(est.version) or est.version, est.pc_uuid,
         [("pc.2024.3.0.2", 3), ("pc.7.3", 4)] if est.version.startswith("pc.2024") else []),
        ("LCM framework", "PC Service", "LM.2025.9.2", est.pc_uuid, []),
        ("Calm", "PC Service", "4.0.0", est.pc_uuid, [("4.1.0", 2)]),
    ]
    for c in est.clusters:
        bios_from = "PB60.002"
        bios = "PB60.004" if epoch >= 1 and c["index"] == 0 else bios_from
        rows += [
            ("AOS", "Core Cluster", c["aos"], c["uuid"],
             [("7.0.1", 3), ("7.3", 4)] if c["aos"] < "7.3" else []),
            ("AHV hypervisor" if c["hypervisor"] == "AHV" else "ESXi", "Hypervisor",
             "20230302.101026" if c["hypervisor"] == "AHV" else "8.0.2",
             c["uuid"], [("20230302.102001", 2)] if c["hypervisor"] == "AHV" else []),
            ("NCC", "Core Cluster", c["ncc"].removeprefix("ncc-"), c["uuid"], [("5.1.1", 2)]),
            ("Foundation", "Core Cluster", "5.7", c["uuid"], [("5.8", 2)]),
            ("BIOS", "Firmware", bios, c["uuid"], [("PB60.004", 2)]),
            ("BMC", "Firmware", "7.13", c["uuid"], [("7.13", 1)]),
        ]
    for name, cls, current, cluster_uuid, available in rows:
        est.lcm_entities.append({
            "uuid": uid(seed, "lcm", name, cluster_uuid or ""), "model": name,
            "class": cls, "type": "SOFTWARE" if cls != "Firmware" else "FIRMWARE",
            "version": current, "cluster_uuid": cluster_uuid, "available": available,
            "updated": iso(BASE_TIME - timedelta(days=14)),
        })


def _licenses(est: PcEstate) -> None:
    rows = [("NCI", "Pro", 1200, 400), ("NCM", "Starter", 600, 400),
            ("NUS", "Pro", 200, 400), ("NCI-VDI", "Ultimate", 300, -20),
            ("NDB", "Platform", 64, 200), ("Flow", "Security", 1200, 400)]
    for i, (cat, kind, qty, days) in enumerate(rows[: 3 + len(est.clusters)]):
        est.licenses.append({
            "uuid": uid(est.seed, "lic", cat), "category": cat, "type": kind, "quantity": qty,
            "expiry": (BASE_TIME + timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z"),
            "index": i,
        })


def _alerts(est: PcEstate) -> None:
    seed = est.seed
    catalog = [
        ("CRITICAL", "Host {h} is down", "host"),
        ("WARNING", "Storage container {c} space usage above 75%", "storage_container"),
        ("WARNING", "CVM memory usage high on {h}", "host"),
        ("INFO", "NCC check passed with warnings on {cl}", "cluster"),
        ("WARNING", "Protection policy {p} RPO not met", "protection_rule"),
        ("CRITICAL", "Disk failure detected on {h}", "disk"),
        ("INFO", "VM {v} has no NGT installed", "vm"),
    ]
    count = min(60 + 20 * len(est.clusters), 260)
    for i in range(count):
        sev, tmpl, etype = catalog[i % len(catalog)]
        c = est.clusters[i % len(est.clusters)]
        hosts = [h for h in est.hosts if h["cluster_uuid"] == c["uuid"]]
        h = hosts[i % len(hosts)]
        ctr = next(ct for ct in est.containers if ct["cluster_uuid"] == c["uuid"])
        vm = est.vms[(i * 7) % len(est.vms)]
        pp = est.protection_policies[0]["name"] if est.protection_policies else "Gold"
        title = tmpl.format(h=h["name"], c=ctr["name"], cl=c["name"], p=pp, v=vm["name"])
        entity = {"host": h["name"], "storage_container": ctr["name"], "cluster": c["name"],
                  "protection_rule": pp, "disk": f"{h['name']}-disk-{i % 6}",
                  "vm": vm["name"]}[etype]
        resolved = i % 3 == 0
        est.alerts.append({
            "uuid": uid(seed, "alert", i), "severity": sev, "title": title,
            "message": title + ".", "type": etype, "entity": entity,
            "cluster_uuid": c["uuid"], "resolved": resolved, "acknowledged": i % 2 == 0,
            "auto_resolved": resolved and i % 2 == 1,
            "created": iso(BASE_TIME - timedelta(hours=i * 5)),
            "classifications": ["Hardware"] if etype in ("host", "disk") else ["Storage"]
            if etype == "storage_container" else ["Cluster"],
        })


# ─── Prism Element (direct v2.0) estate ────────────────────────────────


# ─── design data (architecture diagrams) ───────────────────────────────

#: NIC hardware per host network profile: (model, PCI id, driver, firmware, Gb/s).
_NIC_MODELS = {
    "fast": ("Mellanox Technologies MT2894 Family [ConnectX-6 Lx]", "15b3:101f",
             "mlx5_core:23.10", "26.36.1010", 25),
    "standard": ("Intel Corporation Ethernet Controller X710 for 10GbE SFP+", "8086:1572",
                 "i40e:2.23.17", "9.40 0x8000f19e", 10),
}
_SWITCH_MODEL = "Arista Networks EOS version 4.31.2F running on an Arista DCS-7050SX3-48YC8"
_PROVIDERS = {"EVERPURE_FLASHARRAY": ("fa", 120), "DELL_POWERFLEX": ("pflex", 240),
              "DELL_POWERSTORE": ("pstore", 80)}


def _design(est: PcEstate) -> None:
    """Blocks, host networking, external storage, PC deployment, products, registrations
    and cluster design fields: everything the logical / management / network / DR
    diagrams draw from. Added last and keyed on the estate's own objects only, so every
    other object stays byte-identical."""
    _cluster_design(est)
    _blocks(est)
    _host_network(est)
    _external_storage(est)
    # DR first: its AZ pairings become DOMAIN_MANAGER registrations.
    _dr_design(est)
    _pc_deployment(est)


def _dr_design(est: PcEstate) -> None:
    """Which cluster each policy protects and replicates to (v4 replication locations),
    and each recovery plan's clusters. The remote AZ's clusters are outside the estate:
    their ids are stable but they resolve to no collected cluster."""
    ahv = [c for c in est.clusters if c["hypervisor"] == "AHV"] or est.clusters
    remote = [uid(est.seed, "remote-cluster", n) for n in ("DR-01", "DR-02")]
    for i, p in enumerate(est.protection_policies):
        p["source_cluster"] = ahv[i % len(ahv)]["uuid"]
        p["target_cluster"] = remote[i % len(remote)]
        p["bidirectional"] = p["name"] == "Silver-1h"
    f = est.features
    # Paired demo PCs: each side lists the other as an AZ, by the peer's PC uuid.
    for peer in f.az_peers:
        ref = uid(peer, "pc")
        est.availability_zones.append({"uuid": ref, "name": peer, "type": "PHYSICAL",
                                       "url": ref, "region": "us-west",
                                       "status": "CONNECTED"})
    by_name = {c["name"]: c for c in est.clusters}
    local = {"uuid": est.pc_uuid, "name": f"Local ({est.host})", "type": "PHYSICAL",
             "url": est.pc_uuid}
    for name, rpo, source, peer, target in f.extra_policies:
        src = by_name.get(source)
        if src is None or (not peer and target not in by_name):
            continue
        est.protection_policies.append({
            "uuid": uid(est.seed, "pp", name), "name": name, "rpo": rpo,
            "categories": {"DRTier": ["Platinum" if rpo == 0 else "Gold"]},
            "local_retention": None if rpo == 0 else 24,
            "remote_retention": None if rpo == 0 else 24,
            "source": local,
            "target": {"uuid": uid(peer, "pc"), "url": uid(peer, "pc")} if peer else local,
            "source_cluster": src["uuid"],
            # The peer's cluster uuid is what that PC's own estate gives it.
            "target_cluster": uid(peer, "cluster", target) if peer
            else by_name[target]["uuid"],
            "bidirectional": False,
        })
    for i, rp in enumerate(est.recovery_plans):
        rp["primary_cluster"] = ahv[i % len(ahv)]["uuid"]
        rp["recovery_cluster"] = remote[i % len(remote)]
    # A synchronous (Metro) policy to a paired PC gets a recovery plan with a witness.
    for name, rpo, source, peer, target in f.extra_policies:
        if rpo != 0 or not peer or source not in by_name:
            continue
        plan = f"RP-{name}"
        est.recovery_plans.append({
            "uuid": uid(est.seed, "rp", plan), "name": plan, "stages": 2, "entities": 8,
            "network_maps": 1, "primary": est.pc_uuid, "recovery": uid(peer, "pc"),
            "primary_cluster": by_name[source]["uuid"],
            "recovery_cluster": uid(peer, "cluster", target),
            "witness": uid(est.seed, "witness", plan),
        })


def _cluster_design(est: PcEstate) -> None:
    pods = len(est.clusters) >= 4
    for c in est.clusters:
        n = c["nodes"]
        c["cluster_type"] = "HYPER_CONVERGED"
        c["functions"] = ["AOS"] + (["ONE_NODE"] if n == 1 else ["TWO_NODE"] if n == 2 else [])
        blocks = -(-n // max(1, est.features.nodes_per_block))
        c["fault_domain"] = ("DISK" if n == 1 else "RACK" if n >= 12 else "BLOCK"
                             if n >= 8 or (est.features.nodes_per_block > 1 and blocks >= 3)
                             else "NODE")
        c["fault_tolerance"] = c["rf"] - 1
        c["nearsync"] = n >= 3
        # A POD category on estates big enough to have PODs: clusters in pairs.
        c["categories"] = ({"POD": [f"{c['name'].split('-')[0]}-{1 + c['index'] // 2}"]}
                           if pods else {})


def _blocks(est: PcEstate) -> None:
    """Blocks of `Features.nodes_per_block` nodes (1: current G8 / G9 hardware, one node
    per chassis; 4: 2U4N blocks, slots 1 to 4). On rack-aware clusters the blocks are
    spread round-robin over three racks."""
    per = max(1, est.features.nodes_per_block)
    for c in est.clusters:
        hosts = [h for h in est.hosts if h["cluster_uuid"] == c["uuid"]]
        rack_aware = c["fault_domain"] == "RACK"
        for b, start in enumerate(range(0, len(hosts), per)):
            members = hosts[start:start + per]
            first = members[0]
            rack = 1 + b % 3 if rack_aware else None
            block = {
                "uuid": uid(est.seed, "block", first["block_serial"], first["name"]),
                "cluster_uuid": c["uuid"], "cluster_name": c["name"],
                "model_name": first["block_model"], "serial": first["block_serial"],
                "rack_id": rack,
                "rack_uuid": uid(est.seed, "rack", c["name"], rack) if rack else None,
                "nodes": [{"uuid": h["uuid"], "position": slot}
                          for slot, h in enumerate(members, start=1)],
            }
            est.blocks.append(block)
            for slot, h in enumerate(members, start=1):
                h["block_uuid"] = block["uuid"]
                h["block_position"] = slot
                # The nodes of one chassis share its serial.
                h["block_serial"] = first["block_serial"]


def _host_network(est: PcEstate) -> None:
    """AHV: vs0 (active-backup, MTU 1,500) everywhere; vs1-storage (balance-tcp with LACP,
    MTU 9,000) on clusters of 8 nodes or more; vs2-guest (balance-slb) on mid-size
    clusters. ESXi: NIC names and link speed only, as the APIs report them."""
    seed = est.seed
    vs0, vs1 = est.vswitches[0], est.vswitches[1]
    vs0.update(bond="ACTIVE_BACKUP", mtu=1500, bridge="br0", clusters=[])
    vs1.update(bond="BALANCE_TCP", mtu=9000, bridge="br1", clusters=[])
    vs2 = {"uuid": uid(seed, "vs", "vs2"), "name": "vs2-guest", "bond": "BALANCE_SLB",
           "mtu": 1500, "bridge": "br2", "clusters": []}
    ahv_index = 0
    for c in est.clusters:
        hosts = [h for h in est.hosts if h["cluster_uuid"] == c["uuid"]]
        if c["hypervisor"] == "ESXi":
            for h in hosts:
                for k, gbps in enumerate((10, 10, 25, 25)):
                    est.host_nics.append({
                        "uuid": uid(seed, "hostnic", h["name"], k), "name": f"vmnic{k}",
                        "host_uuid": h["uuid"], "cluster_uuid": None, "esxi": True,
                        "speed_kbps": gbps * 1_000_000, "mac": _mac(seed, h["name"], k)})
            continue
        fast = c["nodes"] >= 8
        single = c["nodes"] == 1
        model = _NIC_MODELS["fast" if fast else "standard"]
        nic_count = 2 if single else 4
        second = None if single else vs1 if fast else vs2
        lldp = est.features.lldp and ahv_index < 2
        ports = ahv_index == 0
        ahv_index += 1
        members = {vs0["uuid"]: (vs0, ["eth0", "eth1"])}
        if second is not None:
            members[second["uuid"]] = (second, ["eth2", "eth3"])
        for sw, uplinks in members.values():
            sw["clusters"].append({"uuid": c["uuid"], "hosts": [
                {"uuid": h["uuid"], "bridge": sw["bridge"], "uplinks": uplinks,
                 "ip": h["hv_ip"] if sw is vs0 else None} for h in hosts]})
        for h in hosts:
            for k in range(nic_count):
                nic_sw = vs0 if k < 2 else second
                switch = "A" if k % 2 == 0 else "B"
                est.host_nics.append({
                    "uuid": uid(seed, "hostnic", h["name"], k), "name": f"eth{k}",
                    "host_uuid": h["uuid"], "cluster_uuid": c["uuid"], "esxi": False,
                    "model": model[0], "pci": model[1], "driver": model[2],
                    "firmware": model[3], "speed_kbps": model[4] * 1_000_000,
                    "capacity_mbps": model[4] * 1000,
                    "mtu": nic_sw["mtu"] if nic_sw else 1500,
                    "vswitch_uuid": nic_sw["uuid"] if nic_sw else None,
                    "mac": _mac(seed, h["name"], k),
                    "lldp": lldp,
                    "switch_vendor": _SWITCH_MODEL if lldp else None,
                    # Documentation-range MACs (RFC 7042) for the two leaf switches.
                    "switch_mac": f"00:00:5e:00:53:{0x10 * (1 + c['index']) + (k % 2):02x}"
                    if lldp else None,
                    "switch_port": f"Ethernet{h['index']}/{1 + k // 2}"
                    if lldp and ports else None,
                    "switch_device": f"leaf-{c['name'].lower()}-{switch.lower()}"
                    if lldp and ports else None,
                })
            for sw, uplinks in members.values():
                refs = [uid(seed, "hostnic", h["name"], int(u[3:])) for u in uplinks]
                est.uplink_bonds.append({
                    "uuid": uid(seed, "bond", h["name"], sw["name"]),
                    "name": f"{sw['bridge']}-up", "type": sw["bond"],
                    "lacp": "NEGOTIATED" if sw["bond"] == "BALANCE_TCP" else "NIL",
                    "host_uuid": h["uuid"], "cluster_uuid": c["uuid"],
                    "vswitch_uuid": sw["uuid"], "vswitch_name": sw["name"], "nics": refs})
    if vs2["clusters"]:
        est.vswitches.append(vs2)


def _mac(seed: str, host: str, k: int) -> str:
    """A locally administered MAC (02:...), stable per host NIC."""
    digest = uid(seed, "mac", host, k).replace("-", "")
    return "02:" + ":".join(digest[i:i + 2] for i in range(0, 10, 2))


def _external_storage(est: PcEstate) -> None:
    for i, (index, provider) in enumerate(est.features.external_storage):
        if index >= len(est.clusters):
            continue
        c = est.clusters[index]
        prefix, tib = _PROVIDERS.get(provider, ("ext", 100))
        name = f"{prefix}-{c['name'].lower()}"
        array = {
            "uuid": uid(est.seed, "extstorage", name), "name": name, "provider": provider,
            "cluster_uuid": c["uuid"], "cluster_name": c["name"],
            "endpoint": f"198.51.100.{40 + i}", "total": tib * 1024**4,
            "free": int(tib * 1024**4 * 0.58), "health": "HEALTHY",
            "pod": f"pod-{c['name'].lower()}" if prefix == "fa" else None,
            "realm": "nutanix" if prefix == "fa" else None,
            "pd": "pd-nutanix" if prefix == "pflex" else None,
            "pool": "sp-ssd-01" if prefix == "pflex" else None,
        }
        est.external_storages.append(array)
        ctr_name = f"{c['name'].lower()}-ext-{prefix}01"
        # With the cluster's other containers, not at the end of the list.
        last = max(i for i, ct in enumerate(est.containers)
                   if ct["cluster_uuid"] == c["uuid"])
        est.containers.insert(last + 1, {
            "uuid": uid(est.seed, "container", c["name"], ctr_name), "name": ctr_name,
            "cluster_uuid": c["uuid"], "cluster_name": c["name"], "rf": None,
            "compression": False, "compression_delay": 0, "dedup": "NONE", "ec": "off",
            "raw": array["total"], "max": array["total"],
            "used": array["total"] - array["free"], "free": array["free"],
            "external_storage_uuid": array["uuid"],
        })


def _pc_deployment(est: PcEstate) -> None:
    f = est.features
    vcpus, mem_gib, disk_gib = PC_SIZING.get(f.pc_size, PC_SIZING["SMALL"])
    pcvms = [v for v in est.vms if "PCVM" in v["name"]]
    for vm in pcvms:
        est.pc_vms.append({"uuid": vm["uuid"], "name": vm["name"],
                           "cluster_uuid": vm["cluster_uuid"],
                           "container_uuid": vm["disks"][0]["container_uuid"],
                           "vcpus": vcpus, "memory": mem_gib * GIB, "disk": disk_gib * GIB,
                           "ip": vm["nics"][0]["ip"]})
    enabled = {
        "FLOW_NETWORK_SECURITY": bool(est.security_policies),
        "FLOW_VIRTUAL_NETWORKING": f.fvn,
        "NUTANIX_DISASTER_RECOVERY": bool(est.protection_policies),
        "SELF_SERVICE": False,
        "NUTANIX_MARKETPLACE": True,
        "INTELLIGENT_OPERATIONS": f.ncm,
        "NUTANIX_CLOUD_MANAGER": f.ncm,
        "FLOW_CONTROLLER": f.fvn,
    }
    est.products = [{"uuid": uid(est.seed, "product", name), "name": name, "enabled": on}
                    for name, on in enabled.items()]
    est.registrations = [
        {"uuid": c["uuid"], "name": c["name"], "type": "AOS", "version": c["aos"],
         "address": c["external_ip"], "status": "CONNECTED"} for c in est.clusters]
    for az in est.availability_zones:
        if az["type"] == "PHYSICAL":
            est.registrations.append({
                "uuid": az["uuid"], "name": az["name"], "type": "DOMAIN_MANAGER",
                "version": est.version, "address": None, "status": az["status"]})


def build_pe_estate(
    host: str, profile: str = "pe", version: str = "7.0", *, seed: str | None = None,
    epoch: int = 0, vms: int = 212, nodes: int = 6, cluster_name: str = "phx-dr-01",
) -> PeEstate:
    """A single cluster as its own Prism Element sees it (no PC-scoped services)."""
    seed = seed or host
    spec = ClusterSpec(cluster_name.upper(), "AHV", nodes, vms, version, "NX-3170-G8")
    est = PcEstate(host=host, version="pc.2024.3", seed=seed, epoch=epoch,
                   features=Features(files=False, objects=False, fvn=False))
    _clusters_and_hosts(est, [spec])
    _networking(est)
    _storage(est)
    _catalog(est)
    _categories(est)
    _vms(est, [spec])
    cluster = dict(est.clusters[0])
    cluster["name"] = cluster_name
    return PeEstate(host=host, version=version, seed=seed, epoch=epoch, cluster=cluster,
                    hosts=est.hosts, containers=est.containers, subnets=est.subnets,
                    vms=est.vms, cvms=est.cvms)


# ─── NDB estate ────────────────────────────────────────────────────────


def build_ndb_estate(host: str, version: str = "2.5.6", *, seed: str | None = None) -> NdbEstate:
    seed = seed or host
    est = NdbEstate(host=host, version=version, seed=seed)
    est.clusters = [{"id": uid(seed, "ndbcl", n), "name": n, "status": "UP"}
                    for n in ("PHX-PROD-01", "PHX-PROD-02")]
    engines = [("postgres_database", "pg"), ("oracle_database", "ora"),
               ("sqlserver_database", "mssql"), ("mysql_database", "mysql")]
    for i in range(12):
        engine, short = engines[i % len(engines)]
        cl = est.clusters[i % 2]
        srv = f"{short}-srv-{i + 1:02d}"
        est.dbservers.append({
            "id": uid(seed, "dbsrv", srv), "name": srv, "type": engine,
            "ipAddresses": [f"10.21.4.{40 + i}"], "status": "UP", "nxClusterId": cl["id"],
            "properties": [{"name": "vm_cpu_count", "value": str(4 + 2 * (i % 3))},
                           {"name": "vm_memory_gb", "value": str(16 * (1 + i % 3))},
                           {"name": "application_type", "value": engine}],
        })
        db = f"{short}-db-{i + 1:02d}"
        sla = ["GOLD", "SILVER", "BRONZE"][i % 3]
        tm = f"{db}_TM"
        est.databases.append({
            "id": uid(seed, "db", db), "name": db, "type": engine,
            "databaseClusterType": "HA" if i % 5 == 0 else None, "status": "READY",
            "dbserverLogicalCluster": {"name": srv},
            "timeMachine": {"name": tm, "sla": {"name": sla}},
            "clone": i % 6 == 5, "dateCreated": iso(BASE_TIME - timedelta(days=30 * (i + 1))),
        })
        est.time_machines.append({
            "id": uid(seed, "tm", tm), "name": tm, "databaseName": db, "clustered": False,
            "sla": {"name": sla}, "status": "READY",
            "schedule": {"snapshotTimeOfDay": {"hours": 1 + i % 4, "minutes": 0}},
            "clones": i % 3,
        })
    return est
