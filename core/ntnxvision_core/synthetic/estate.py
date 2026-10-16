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

import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ntnxvision_core.compat import parse_version

_NS = uuid.UUID("6f1c2d4e-9a3b-4c5d-8e7f-0a1b2c3d4e5f")
GIB = 1024**3
TIB = 1024**4
MIB = 1024**2

#: Reference "now" for synthetic timestamps — fixed so payloads are reproducible.
BASE_TIME = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


def uid(*parts: object) -> str:
    return str(uuid.uuid5(_NS, "/".join(str(p) for p in parts)))


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
    #: LCM reports its own framework as out of date (an LCM "with notes" outcome).
    lcm_framework_stale: bool = False


PROFILES: dict[str, list[ClusterSpec]] = {
    "tiny": [ClusterSpec("LAB-01", "AHV", 3, 40, "7.0", "NX-1175S-G8")],
    "medium": [
        ClusterSpec("PHX-PROD-01", "AHV", 8, 342, "6.10.1"),
        ClusterSpec("PHX-PROD-02", "AHV", 12, 612, "7.0"),
        ClusterSpec("PHX-DR-01", "AHV", 6, 212, "7.0", "NX-3170-G8"),
        ClusterSpec("AMS-EDGE-02", "ESXi", 2, 48, "6.10", "NX-1175S-G8"),
    ],
    "small": [
        ClusterSpec("SEA-PROD-01", "AHV", 10, 820, "7.3"),
        ClusterSpec("SEA-PROD-02", "AHV", 10, 610, "7.3"),
        ClusterSpec("SEA-VDI-01", "AHV", 7, 160, "7.3", "NX-3170-G8"),
    ],
    "legacy": [
        ClusterSpec("DAL-PROD-01", "AHV", 6, 420, "6.8"),
        ClusterSpec("DAL-EDGE-01", "AHV", 3, 90, "6.5", "NX-1175S-G8"),
    ],
    # One Prism Central's share of the 20 000-VM estate (three of these, see profiles.py).
    "large": [
        ClusterSpec("PROD-A", "AHV", 16, 1800, "7.0"),
        ClusterSpec("PROD-B", "AHV", 16, 1800, "7.0"),
        ClusterSpec("PROD-C", "AHV", 14, 1500, "6.10.1"),
        ClusterSpec("VDI-A", "AHV", 12, 1000, "7.0", "NX-3170-G8"),
        ClusterSpec("EDGE-A", "ESXi", 6, 567, "6.10", "NX-1175S-G8"),
    ],
}

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

_CPU_MODELS = ["Intel(R) Xeon(R) Gold 6438Y+", "Intel(R) Xeon(R) Gold 6342",
               "AMD EPYC 9354 32-Core Processor"]


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
    est.pc_build = f"el8.5-release-fraser-{version.removeprefix('pc.')}-stable-pc-0"

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
            sockets = 2
            cores = hr.choice([16, 24, 32]) * sockets
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
                "cpu_model": hr.choice(_CPU_MODELS),
                "sockets": sockets,
                "cores": cores,
                "threads": cores * 2,
                "freq_hz": 2_600_000_000,
                "memory": hr.choice([512, 768, 1024]) * GIB,
                "block_serial": f"{serial[:-2]}XX",
                "block_model": spec.model,
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
                "vcpus": 12,
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
            # ESXi clusters have port groups, not Nutanix subnets; one VLAN for reach.
            names = [("servers_vlan", 300)]
        else:
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
            raw = c["nodes"] * r.choice([8, 12, 16]) * TIB
            usable = raw // (c["rf"])
            if name == "NutanixManagementShare":
                usable = 4 * TIB
            used = int(usable * r.uniform(0.15, 0.62))
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
        if s["cluster_uuid"]:
            subnets_by_cluster.setdefault(s["cluster_uuid"], []).append(s)
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
            })
        ip = None
        if subnet is not None and power == "ON":
            ip = f"{subnet['prefix'].rsplit('.', 1)[0]}.{10 + (n % 240)}"
        nic = {
            "uuid": uid(*key, "nic", 0),
            "mac": "50:6b:8d:" + ":".join(f"{b:02x}" for b in mac),
            "subnet_uuid": subnet["uuid"] if subnet else None,
            "port_group": "servers_vlan" if cluster["hypervisor"] == "ESXi" else None,
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


def _system_vms(est: PcEstate) -> None:
    """The Prism Central VM itself, which collectors must reclassify as PRISM_CENTRAL."""
    c = est.clusters[0]
    host = next(h for h in est.hosts if h["cluster_uuid"] == c["uuid"])
    name = f"NTNX-{est.pc_ip.replace('.', '-')}-PCVM-1696000000"
    ctr = next(ct for ct in est.containers if ct["cluster_uuid"] == c["uuid"])
    subnet = next((s for s in est.subnets if s["cluster_uuid"] == c["uuid"]), None)
    est.vms.append({
        "uuid": uid(est.seed, "pcvm"),
        "name": name,
        "cluster_uuid": c["uuid"],
        "cluster_name": c["name"],
        "host_uuid": host["uuid"],
        "host_name": host["name"],
        "power": "ON",
        "sockets": 6, "cores": 1, "memory": 28 * GIB,
        "guest_os": "Prism Central", "guest_os_id": "linux:64:PrismCentral",
        "description": "Prism Central VM",
        "categories": {},
        "protection_type": "UNPROTECTED",
        "disks": [{"uuid": uid(est.seed, "pcvm", "disk", i), "size": s * GIB,
                   "container_uuid": ctr["uuid"], "container_name": ctr["name"],
                   "bus": "SCSI", "index": i, "image": None}
                  for i, s in enumerate([30, 500, 100])],
        "cdroms": 0,
        "nics": [{"uuid": uid(est.seed, "pcvm", "nic"), "mac": "50:6b:8d:00:00:50",
                  "subnet_uuid": subnet["uuid"] if subnet else None, "port_group": None,
                  "ip": est.pc_ip, "connected": True}],
        "boot": "LEGACY", "secure_boot": False,
        "created": iso(BASE_TIME - timedelta(days=700)),
        "gpus": [], "hypervisor": "AHV", "esxi_bad": False,
    })


def _data_protection(est: PcEstate) -> None:
    seed, epoch = est.seed, est.epoch
    local = {"uuid": est.pc_uuid, "name": f"Local ({est.host})", "type": "PHYSICAL",
             "url": est.pc_uuid}
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
        ("Prism Central", "PC Core Cluster", est.version, est.pc_uuid,
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
                    vms=est.vms)


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
