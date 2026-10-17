"""Cluster capacity and oversubscription: CPU and storage, at full capacity and at N-1.

All of the math lives here, computed from a collected `Environment`, so the Inventory
grid, the detail pane and the Excel / PDF / Word reports show the same figures.

Formulas (per cluster; *n* nodes, node *i* with physical cores *c_i*, CVM vCPUs *v_i*
and raw storage *r_i*; *T* is the cluster's target vCPU:pCPU ratio, see "Target ratio"
below):

* Usable cores = sum(c_i - v_i). Physical cores, never hyperthreads.
* vCPU Total = usable cores x T. This is the user's
  ``(cores - CVM vCPU) x node count x ratio``, summed per node so that a heterogeneous
  cluster is counted exactly; for a homogeneous one the two are identical.
* Storage Total = sum(r_i) x 0.95 / RF. The 0.95 keeps 5% back as the reserve Nutanix
  recommends; RF is the cluster's redundancy factor.
* N-1: the same, without the node whose loss costs the most (worst case): the node with
  the most usable cores for CPU and the one with the most raw storage for storage. They
  may be different nodes; each figure is the worst case for its own resource.
  Undefined (None, shown as N/A) for one- and two-node clusters: a single node has
  nothing to fail over to, and a two-node cluster that loses a node runs degraded on its
  witness rather than at a capacity worth planning against.
* CPU oversubscription = provisioned vCPUs / usable cores, shown "X.X : 1".
* Storage oversubscription = provisioned VM disk / Storage Total, shown "X.X : 1".

Provisioned figures count every VM on the cluster, powered on or off (allocations are
reserved whatever the power state); the powered-on vCPU figure is kept alongside. CVMs
are excluded because their vCPUs are already subtracted from the cores; other Nutanix
system VMs (Prism Central, file server VMs) are included because they consume cluster
capacity like any workload. Provisioned storage is the VMs' own virtual disks (CD-ROMs
excluded); volume groups are not counted.

Data sources:

* CVM vCPUs: each CVM's `num_vcpus` from the system-VM list (v3 groups ``is_cvm==1`` on
  Prism Central, the v1 VM list on Prism Element), matched to its node by host uuid, host
  name or CVM IP. A node whose CVM was not found takes the largest CVM allocation seen
  on that cluster (noted as estimated); a cluster with no CVM data at all gets no CPU
  figures, since guessing a CVM size would misstate every ratio.
* RF: `Cluster.redundancy_factor`. Storage containers can carry their own replication
  factor, but the cluster RF is the one capacity planning is done against; when it is
  missing the highest container RF on the cluster is used instead, and a container whose
  RF differs from the cluster's is called out in the notes.
* Raw storage per node: `Host.storage_capacity_bytes` (see the hosts collector).

Any missing input gives None (N/A), never a zero and never a division by zero.

Target ratio (*T*), per cluster:

* A collection records a target on every cluster it collects (`apply_targets`): the
  per-cluster override chosen in New collection (``custom``), else the global setting
  at the time (``default``). The snapshot keeps it (`Cluster.vcpu_ratio_target`,
  `Cluster.vcpu_ratio_custom`, `Environment.vcpu_ratio_default`), so a re-export, the
  Inventory detail pane and Compare use the target chosen at collection time.
* Snapshots written before per-cluster targets existed carry none: their clusters use
  the ratio the caller passes (the current setting), shown as ``default``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ntnxvision_core.models import Cluster, Environment, Host, VirtualMachine
from ntnxvision_core.numfmt import fmt_ratio

#: The vCPU:pCPU target ratio used for "vCPU Total" when none is configured.
DEFAULT_VCPU_RATIO = 4.0
#: Share of raw capacity treated as usable before replication (5% reserve).
STORAGE_RESERVE_FACTOR = 0.95
#: Bounds accepted for a configured target ratio.
MIN_VCPU_RATIO = 0.1
MAX_VCPU_RATIO = 64.0
#: Storage oversubscription from which a thin-provisioning note is added (the note shows
#: one decimal, so anything below 1.05 would read as "1.0 times").
THIN_NOTE_THRESHOLD = 1.05


def coerce_ratio(value: Any) -> float:
    """A configured target ratio, or the default when it is missing or out of range."""
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        return DEFAULT_VCPU_RATIO
    if not (MIN_VCPU_RATIO <= ratio <= MAX_VCPU_RATIO):
        return DEFAULT_VCPU_RATIO
    return round(ratio, 2)


def valid_ratio(value: Any) -> float | None:
    """`value` as a target ratio when it is a number in range, else None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        return None
    if not (MIN_VCPU_RATIO <= ratio <= MAX_VCPU_RATIO):
        return None
    return round(ratio, 2)


def cluster_target(cluster: Cluster, fallback: float) -> tuple[float, bool]:
    """(target ratio, is custom) for one cluster: the one recorded on it at collection,
    else `fallback` (snapshots written before per-cluster targets)."""
    recorded = valid_ratio(cluster.vcpu_ratio_target)
    if recorded is None:
        return fallback, False
    return recorded, bool(cluster.vcpu_ratio_custom)


def apply_targets(env: Environment, default: Any = None,
                  overrides: dict[str, Any] | None = None) -> None:
    """Record the vCPU:pCPU target of every cluster of a freshly collected `env`.

    `default` is the global setting (`DEFAULT_VCPU_RATIO` when unset or out of range);
    `overrides` maps cluster uuid to a per-cluster target. An override that is not a
    number in range is ignored (the cluster takes the default).
    """
    base = coerce_ratio(DEFAULT_VCPU_RATIO if default is None else default)
    env.vcpu_ratio_default = base
    custom = {str(k): r for k, v in (overrides or {}).items()
              if (r := valid_ratio(v)) is not None}
    for c in env.clusters:
        ratio = custom.get(c.uuid)
        c.vcpu_ratio_target = base if ratio is None else ratio
        c.vcpu_ratio_custom = ratio is not None


def target_label(ratio: float | None, custom: bool) -> str:
    """"6.0 : 1 (custom)" or "4.0 : 1 (default)": a cluster's target and where it came
    from, as the detail pane, the reports and Compare show it."""
    return f"{fmt_ratio(ratio)} ({'custom' if custom else 'default'})"


def targets_summary(caps: list[ClusterCapacity], default: float) -> str:
    """One phrase for the targets a report used.

    "a 4.0 : 1 vCPU:pCPU target" when every cluster shares it; else the default(s) and
    the custom ones by cluster: "a 4.0 : 1 vCPU:pCPU target by default and a custom
    target on 1 cluster: SEA-ROBO-01 6.5 : 1".
    """
    custom = [k for k in caps if k.target_custom]
    defaults = sorted({k.target_ratio for k in caps if not k.target_custom} or {default})
    if len(defaults) == 1:
        text = f"a {fmt_ratio(defaults[0])} vCPU:pCPU target"
    else:  # a combined run whose Prism Centrals were collected with different defaults
        text = " and ".join(fmt_ratio(r) for r in defaults) + " vCPU:pCPU targets"
    if not custom:
        return text
    names = ", ".join(f"{k.cluster_name or k.cluster_uuid} {fmt_ratio(k.target_ratio)}"
                      for k in custom[:5])
    more = f" and {len(custom) - 5:,} more" if len(custom) > 5 else ""
    which = ("a custom target on 1 cluster" if len(custom) == 1
             else f"custom targets on {len(custom):,} clusters")
    return f"{text} by default and {which}: {names}{more}"


@dataclass(frozen=True)
class NodeCapacity:
    uuid: str
    name: str | None
    cores: int | None
    cvm_vcpus: int | None
    #: True when `cvm_vcpus` is the cluster's largest known CVM, not this node's own.
    cvm_estimated: bool
    raw_bytes: int | None

    @property
    def usable_cores(self) -> int | None:
        if self.cores is None or self.cvm_vcpus is None:
            return None
        return max(0, self.cores - self.cvm_vcpus)


@dataclass
class ClusterCapacity:
    cluster_uuid: str
    cluster_name: str | None
    source_pc: str | None
    target_ratio: float
    #: True when `target_ratio` is the cluster's own override, not the global default.
    target_custom: bool = False
    nodes: list[NodeCapacity] = field(default_factory=list)
    #: Node count the cluster itself reports (may exceed the hosts collected).
    reported_nodes: int | None = None
    rf: int | None = None
    rf_source: str | None = None  # "cluster" | "containers"

    physical_cores: int | None = None
    cvm_vcpus: int | None = None
    usable_cores: int | None = None
    usable_cores_n1: int | None = None
    vcpu_total: float | None = None
    vcpu_total_n1: float | None = None
    provisioned_vcpus: int = 0
    provisioned_vcpus_on: int = 0
    cpu_ratio: float | None = None
    cpu_ratio_n1: float | None = None
    cpu_ratio_on: float | None = None

    raw_bytes: int | None = None
    storage_total_bytes: float | None = None
    storage_total_n1_bytes: float | None = None
    provisioned_disk_bytes: int = 0
    storage_ratio: float | None = None
    storage_ratio_n1: float | None = None

    vm_count: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def has_n1(self) -> bool:
        return self.node_count >= 3


# ─── public API ────────────────────────────────────────────────────────


def compute_environment(env: Environment, target_ratio: Any = None
                        ) -> dict[int, ClusterCapacity]:
    """Capacity for every cluster in `env`, keyed by ``id(cluster)``.

    Keyed by object identity because cluster names (and, in theory, uuids) repeat across
    the Prism Centrals of a combined environment. Each cluster uses the target recorded
    on it at collection; `target_ratio` is the fallback for clusters without one.
    """
    ratio = coerce_ratio(DEFAULT_VCPU_RATIO if target_ratio is None else target_ratio)
    resolve = _cluster_resolver(env)
    hosts: dict[int, list[Host]] = {}
    for h in env.hosts:
        c = resolve(h)
        if c is not None:
            hosts.setdefault(id(c), []).append(h)
    vms: dict[int, list[VirtualMachine]] = {}
    cvms: dict[int, list[VirtualMachine]] = {}
    for vm in [*env.vms, *env.system_vms]:
        c = resolve(vm)
        if c is None:
            continue
        bucket = cvms if vm.vm_type == "CVM" else vms
        bucket.setdefault(id(c), []).append(vm)
    containers: dict[int, list[Any]] = {}
    for ct in env.storage_containers:
        c = resolve(ct)
        if c is not None:
            containers.setdefault(id(c), []).append(ct)
    out: dict[int, ClusterCapacity] = {}
    for c in env.clusters:
        target, custom = cluster_target(c, ratio)
        out[id(c)] = compute_cluster(c, hosts.get(id(c), []), vms.get(id(c), []),
                                     cvms.get(id(c), []), containers.get(id(c), []), target,
                                     target_custom=custom)
    return out


def compute_cluster(cluster: Cluster, hosts: list[Host], vms: list[VirtualMachine],
                    cvms: list[VirtualMachine], containers: list[Any] | None = None,
                    target_ratio: float = DEFAULT_VCPU_RATIO, *,
                    target_custom: bool = False) -> ClusterCapacity:
    """Capacity of one cluster from its hosts, VMs (CVMs excluded) and CVMs."""
    out = ClusterCapacity(cluster_uuid=cluster.uuid, cluster_name=cluster.name,
                          source_pc=cluster.source_pc, target_ratio=target_ratio,
                          target_custom=target_custom, reported_nodes=cluster.num_nodes)
    out.nodes = _nodes(hosts, cvms)
    _rf(out, cluster, containers or [])
    _provisioned(out, vms)
    if not out.nodes:
        out.notes.append("No host data was collected for this cluster, so its capacity "
                         "cannot be calculated.")
        return out
    if cluster.num_nodes and cluster.num_nodes > len(out.nodes):
        out.notes.append(f"Only {len(out.nodes):,} of the cluster's {cluster.num_nodes:,} "
                         "nodes were collected; capacity covers the collected nodes.")
    _cpu(out)
    _storage(out)
    if not out.has_n1:
        out.notes.append(f"N-1 is not applicable to a {out.node_count:,}-node cluster.")
    return out


def ratio_or_none(numerator: float | None, denominator: float | None) -> float | None:
    """numerator / denominator, or None when either is missing or the divisor is not
    positive. The one place a capacity ratio is divided."""
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


# ─── steps ─────────────────────────────────────────────────────────────


def _nodes(hosts: list[Host], cvms: list[VirtualMachine]) -> list[NodeCapacity]:
    by_host_uuid: dict[str, int] = {}
    by_host_name: dict[str, int] = {}
    by_ip: dict[str, int] = {}
    for vm in cvms:
        vcpus = vm.vcpu_total
        if not vcpus:
            continue
        if vm.host_uuid:
            by_host_uuid[vm.host_uuid] = vcpus
        if vm.host_name:
            by_host_name[vm.host_name] = vcpus
        for nic in vm.nics:
            for ip in nic.ip_addresses:
                by_ip[ip] = vcpus
    known: list[int | None] = []
    for h in hosts:
        found = by_host_uuid.get(h.uuid)
        if found is None and h.name:
            found = by_host_name.get(h.name)
        if found is None and h.cvm_ip:
            found = by_ip.get(h.cvm_ip)
        known.append(found)
    fallback = max((k for k in known if k), default=None)
    nodes = []
    for h, cvm in zip(hosts, known, strict=True):
        nodes.append(NodeCapacity(
            uuid=h.uuid, name=h.name,
            cores=h.num_cpu_cores if h.num_cpu_cores and h.num_cpu_cores > 0 else None,
            cvm_vcpus=cvm if cvm is not None else fallback,
            cvm_estimated=cvm is None and fallback is not None,
            raw_bytes=h.storage_capacity_bytes if h.storage_capacity_bytes and
            h.storage_capacity_bytes > 0 else None,
        ))
    return nodes


def _rf(out: ClusterCapacity, cluster: Cluster, containers: list[Any]) -> None:
    ctr_rfs = {ct.name or ct.uuid: ct.replication_factor for ct in containers
               if ct.replication_factor and ct.replication_factor > 0}
    if cluster.redundancy_factor and cluster.redundancy_factor > 0:
        out.rf, out.rf_source = cluster.redundancy_factor, "cluster"
        differing = sorted(f"{name} (RF {rf})" for name, rf in ctr_rfs.items()
                           if rf != cluster.redundancy_factor
                           and name != "NutanixManagementShare")
        if differing:
            shown = ", ".join(differing[:3]) + (f" and {len(differing) - 3:,} more"
                                                if len(differing) > 3 else "")
            out.notes.append(f"Storage containers with a different replication factor: "
                             f"{shown}. Storage figures use the cluster RF {out.rf}.")
    elif ctr_rfs:
        out.rf, out.rf_source = max(ctr_rfs.values()), "containers"
        out.notes.append(f"The cluster reported no redundancy factor; storage figures use "
                         f"the highest container replication factor (RF {out.rf}).")
    else:
        out.notes.append("No redundancy factor was reported, so usable storage cannot be "
                         "calculated.")


def _provisioned(out: ClusterCapacity, vms: list[VirtualMachine]) -> None:
    out.vm_count = len(vms)
    out.provisioned_vcpus = sum(v.vcpu_total or 0 for v in vms)
    out.provisioned_vcpus_on = sum(v.vcpu_total or 0 for v in vms
                                   if (v.power_state or "").upper() in ("ON", "POWERED_ON"))
    out.provisioned_disk_bytes = sum(v.total_disk_bytes for v in vms)


def _cpu(out: ClusterCapacity) -> None:
    nodes = out.nodes
    if any(n.cores is None for n in nodes):
        out.notes.append("A node reported no physical core count, so CPU capacity cannot "
                         "be calculated.")
        return
    out.physical_cores = sum(n.cores or 0 for n in nodes)
    if any(n.cvm_vcpus is None for n in nodes):
        out.notes.append("No Controller VM vCPU allocation was reported for this cluster, "
                         "so CPU capacity cannot be calculated.")
        return
    estimated = [n for n in nodes if n.cvm_estimated]
    if estimated:
        out.notes.append(f"The CVM of {len(estimated):,} node(s) was not found; their CVM "
                         f"vCPUs are estimated as the cluster's largest CVM "
                         f"({max(n.cvm_vcpus or 0 for n in nodes):,} vCPU).")
    usable = [n.usable_cores or 0 for n in nodes]
    out.cvm_vcpus = sum(n.cvm_vcpus or 0 for n in nodes)
    out.usable_cores = sum(usable)
    if out.usable_cores <= 0:
        out.notes.append("CVM vCPUs equal or exceed the physical cores; no CPU capacity "
                         "remains for workloads.")
    out.vcpu_total = out.usable_cores * out.target_ratio
    out.cpu_ratio = ratio_or_none(out.provisioned_vcpus, out.usable_cores)
    out.cpu_ratio_on = ratio_or_none(out.provisioned_vcpus_on, out.usable_cores)
    if out.has_n1:
        out.usable_cores_n1 = out.usable_cores - max(usable)
        out.vcpu_total_n1 = out.usable_cores_n1 * out.target_ratio
        out.cpu_ratio_n1 = ratio_or_none(out.provisioned_vcpus, out.usable_cores_n1)
    if out.cpu_ratio is not None and out.cpu_ratio > out.target_ratio:
        out.notes.append(f"CPU oversubscription {out.cpu_ratio:,.1f} : 1 exceeds the "
                         f"{out.target_ratio:,.1f} : 1 target.")
    elif out.cpu_ratio_n1 is not None and out.cpu_ratio_n1 > out.target_ratio:
        out.notes.append(f"After losing a node, CPU oversubscription "
                         f"{out.cpu_ratio_n1:,.1f} : 1 would exceed the "
                         f"{out.target_ratio:,.1f} : 1 target.")


def _storage(out: ClusterCapacity) -> None:
    nodes = out.nodes
    if any(n.raw_bytes is None for n in nodes):
        out.notes.append("A node reported no raw storage capacity, so storage capacity "
                         "cannot be calculated.")
        return
    raw = [n.raw_bytes or 0 for n in nodes]
    out.raw_bytes = sum(raw)
    if not out.rf:
        return
    out.storage_total_bytes = out.raw_bytes * STORAGE_RESERVE_FACTOR / out.rf
    out.storage_ratio = ratio_or_none(out.provisioned_disk_bytes, out.storage_total_bytes)
    if out.has_n1:
        out.storage_total_n1_bytes = (out.raw_bytes - max(raw)) * STORAGE_RESERVE_FACTOR \
            / out.rf
        out.storage_ratio_n1 = ratio_or_none(out.provisioned_disk_bytes,
                                             out.storage_total_n1_bytes)
    if out.storage_ratio is not None and out.storage_ratio >= THIN_NOTE_THRESHOLD:
        out.notes.append(f"Provisioned VM disk is {out.storage_ratio:,.1f} times the usable "
                         "storage (thin provisioning).")


def _cluster_resolver(env: Environment) -> Any:
    """Map an object to its Cluster: by uuid, then by (name, source PC), then the single
    cluster of a Prism Element run (whose v2.0 payloads often omit the cluster)."""
    by_uuid = {c.uuid: c for c in env.clusters if c.uuid}
    by_name: dict[tuple[str | None, str | None], Cluster] = {}
    for c in env.clusters:
        by_name.setdefault((c.name, c.source_pc), c)
    only = env.clusters[0] if len(env.clusters) == 1 and not env.is_combined else None

    def resolve(obj: Any) -> Cluster | None:
        uuid = getattr(obj, "cluster_uuid", None)
        if uuid and uuid in by_uuid:
            return by_uuid[uuid]
        name = getattr(obj, "cluster_name", None)
        if name:
            return by_name.get((name, getattr(obj, "source_pc", None))) or (
                only if only is not None and only.name == name else None)
        return only

    return resolve


__all__ = [
    "DEFAULT_VCPU_RATIO", "STORAGE_RESERVE_FACTOR", "ClusterCapacity", "NodeCapacity",
    "apply_targets", "cluster_target", "coerce_ratio", "compute_cluster",
    "compute_environment", "ratio_or_none", "target_label", "targets_summary",
    "valid_ratio",
]
