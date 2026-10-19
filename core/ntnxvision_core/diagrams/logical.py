"""Logical Cluster Design (A2): model, facts, design summary and table rows.

One diagram per availability zone (one Prism Central, or the Prism Element of a direct
run): its clusters grouped by POD when the clusters carry a ``POD`` category, each with
its nodes (hardware groups, blocks, racks), Controller VM sizing, fault domain, RF,
management role, data services and user storage containers, plus the external arrays
those containers live on.

Only collected data is drawn. Racks have no name in any Nutanix API, so they read
"rack <id>"; a block's form factor is not reported, so blocks read as nodes per block;
block and node serials are never shown.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from ntnxvision_core.models import Environment
from ntnxvision_core.versions import short_version

from . import fmt
from .facts import _sites, hypervisor_label, pc_size_label, short_name

GIB = 1024**3
#: The category key that groups clusters into PODs.
POD_KEY = "POD"
FD_LABELS = {"NODE": "node", "BLOCK": "block", "RACK": "rack", "DISK": "disk"}


@dataclass
class NodeGroup:
    """Nodes of one cluster sharing model, CPU and memory."""

    count: int
    model: str | None = None
    cpu: str | None = None
    sockets: int | None = None
    cores: int | None = None
    mem_gib: int | None = None
    #: The largest memory size when the group's nodes differ only in memory.
    mem_max_gib: int | None = None
    #: "" (hyperconverged), "compute-only" or "storage-only".
    role: str = ""


@dataclass
class Container:
    id: str
    name: str
    rf: int | None = None
    compression: bool | None = None
    dedup: bool | None = None
    ecx: bool | None = None
    max_bytes: int | None = None
    #: The external array id (`Array.id`) the container lives on; None for DSF.
    array: str | None = None


@dataclass
class LCluster:
    id: str
    name: str
    hypervisor: str | None = None
    aos: str | None = None
    nodes: int | None = None
    rf: int | None = None
    fault_domain: str | None = None  # node / block / rack / disk
    tolerates: int | None = None
    desired: int | None = None
    groups: list[NodeGroup] = field(default_factory=list)
    blocks: int = 0
    #: Nodes per block (multi-node blocks only): the most in one block, and whether
    #: every block holds that many.
    per_block: int | None = None
    blocks_full: bool = True
    racks: list[int] = field(default_factory=list)
    cvm_vcpus: list[int] = field(default_factory=list)  # distinct, ascending
    cvm_mem_gib: list[int] = field(default_factory=list)
    hosts_pc: bool = False
    pc_vms: int = 0
    pc_size: str | None = None
    services: list[str] = field(default_factory=list)
    containers: list[Container] = field(default_factory=list)
    data: dict[str, str] = field(default_factory=dict)


@dataclass
class Array:
    id: str
    product: str
    name: str | None = None
    endpoint: str | None = None
    protocol: str | None = None
    total_bytes: int | None = None
    free_bytes: int | None = None
    health: str | None = None
    cluster: str | None = None  # LCluster id
    cluster_name: str | None = None
    containers: list[str] = field(default_factory=list)
    data: dict[str, str] = field(default_factory=dict)


@dataclass
class Pod:
    id: str
    #: The POD value, or None for clusters outside any POD.
    name: str | None
    clusters: list[LCluster] = field(default_factory=list)


@dataclass
class LSite:
    id: str
    name: str
    kind: str = "pc"  # pc | pe
    pods: list[Pod] = field(default_factory=list)
    arrays: list[Array] = field(default_factory=list)
    pc_vms: int = 0
    data: dict[str, str] = field(default_factory=dict)

    @property
    def clusters(self) -> list[LCluster]:
        return [c for p in self.pods for c in p.clusters]

    @property
    def has_pods(self) -> bool:
        return any(p.name for p in self.pods)


@dataclass
class LogicalModel:
    sites: list[LSite] = field(default_factory=list)
    title: str = "Logical cluster design"


# ─── documents ─────────────────────────────────────────────────────────


def to_doc(m: LogicalModel) -> dict[str, Any]:
    return asdict(m)


def from_doc(d: dict[str, Any]) -> LogicalModel:
    def cluster(c: dict[str, Any]) -> LCluster:
        return LCluster(**{**c, "groups": [NodeGroup(**g) for g in c.get("groups", [])],
                           "containers": [Container(**x) for x in c.get("containers", [])]})

    return LogicalModel(
        sites=[LSite(id=s["id"], name=s["name"], kind=s.get("kind", "pc"),
                     pods=[Pod(id=p["id"], name=p.get("name"),
                               clusters=[cluster(c) for c in p.get("clusters", [])])
                           for p in s.get("pods", [])],
                     arrays=[Array(**a) for a in s.get("arrays", [])],
                     pc_vms=s.get("pc_vms", 0), data=dict(s.get("data", {})))
               for s in d.get("sites", [])],
        title=d.get("title", "Logical cluster design"))


# ─── facts ─────────────────────────────────────────────────────────────

_CPU_NOISE = re.compile(r"\((?:R|TM)\)|\bCPU\b|\bProcessor\b|@.*$", re.IGNORECASE)


def cpu_label(model: str | None) -> str | None:
    """"Intel(R) Xeon(R) Gold 6438Y+ CPU @ 2.0GHz" -> "Xeon Gold 6438Y+"."""
    if not model:
        return None
    text = _CPU_NOISE.sub("", model)
    text = re.sub(r"^\s*(Intel|AMD)\s+", "", text.strip(), flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip() or None


def _role(host_type: str | None) -> str:
    t = (host_type or "").upper()
    if "COMPUTE" in t:
        return "compute-only"
    if "STORAGE" in t:
        return "storage-only"
    return ""


def _bool_switch(value: str | None) -> bool | None:
    if value is None:
        return None
    return value.strip().upper() not in ("", "NONE", "OFF", "DISABLED", "FALSE")


def _pod_of(c: Any) -> str | None:
    for key, values in sorted(c.categories.items()):
        if key.strip().upper() == POD_KEY and values:
            return sorted(values)[0]
    return None


def logical_model(env: Environment) -> LogicalModel:
    """The Logical Cluster Design model for `env`: one site per availability zone."""
    infos = _sites(env)
    is_pe = env.source_type == "prism_element"
    default = infos[0].host

    def src(obj: Any) -> str:
        return (getattr(obj, "source_pc", None) or default) if env.is_combined else default

    def by_cluster(objs: list[Any]) -> dict[tuple[str, str], list[Any]]:
        out: dict[tuple[str, str], list[Any]] = {}
        for o in objs:
            out.setdefault((src(o), o.cluster_uuid or ""), []).append(o)
        return out

    hosts = by_cluster(env.hosts)
    cvms = by_cluster(env.cvms)
    blocks = by_cluster(env.blocks)
    ctrs = by_cluster(env.storage_containers)
    arrays_by = by_cluster(env.external_storages)

    sites: list[LSite] = []
    for info in infos:
        cfg = info.cfg
        hosting = {getattr(cfg, "hosting_cluster_uuid", None)} - {None}
        pcvms = [v for v in env.pc_vms if src(v) == info.host]
        if not hosting:
            hosting = {v.hosting_cluster_uuid for v in pcvms if v.hosting_cluster_uuid}
        if not hosting:
            hosting = {v.cluster_uuid for v in env.system_vms if src(v) == info.host
                       and (v.vm_type or "").upper() == "PRISM_CENTRAL" and v.cluster_uuid}
        n_pcvms = 0 if is_pe else (cfg.node_count or len(pcvms) or 0)
        clusters: list[LCluster] = []
        arrays: list[Array] = []
        pod_of: dict[str, str | None] = {}
        for c in env.clusters:
            if src(c) != info.host:
                continue
            key = (info.host, c.uuid)
            cl = _cluster(env, c, info.host, src, hosts.get(key, []), cvms.get(key, []),
                          blocks.get(key, []), ctrs.get(key, []))
            if c.uuid in hosting and not is_pe:
                cl.hosts_pc = True
                cl.pc_vms = n_pcvms
                cl.pc_size = pc_size_label(getattr(cfg, "size", None))
            for x in sorted(arrays_by.get(key, []), key=lambda a: (a.name or "", a.uuid)):
                aid = f"{cl.id}:ext:{x.uuid}"
                on = [ct for ct in cl.containers if ct.array == x.uuid]
                for ct in on:
                    ct.array = aid
                arrays.append(Array(
                    id=aid, product=x.product, name=x.name, endpoint=x.endpoint,
                    protocol=x.protocol, total_bytes=x.total_capacity_bytes,
                    free_bytes=x.free_capacity_bytes,
                    health=x.health.lower() if x.health else None, cluster=cl.id,
                    cluster_name=cl.name,
                    containers=sorted({ct.name for ct in on} | set(x.container_names)),
                    data={"product": x.product, **({"name": x.name} if x.name else {})}))
            # A container on an array that was not collected is still external; it just
            # has no array box to point at.
            for ct in cl.containers:
                if ct.array and not ct.array.startswith(f"{cl.id}:ext:"):
                    ct.array = f"{cl.id}:ext:{ct.array}"
            clusters.append(cl)
            pod_of[cl.id] = _pod_of(c)
        clusters.sort(key=lambda k: (not k.hosts_pc, k.name.lower(), k.id))
        pods: dict[str | None, list[LCluster]] = {}
        for cl in clusters:
            pods.setdefault(pod_of[cl.id], []).append(cl)
        pod_list = [Pod(id=f"{info.id}:pod:{name}" if name else f"{info.id}:pod:-",
                        name=name, clusters=members)
                    for name, members in sorted(pods.items(),
                                                key=lambda kv: (kv[0] is None,
                                                                not any(c.hosts_pc
                                                                        for c in kv[1]),
                                                                (kv[0] or "").lower()))]
        sites.append(LSite(id=info.id, name=info.host, kind="pe" if is_pe else "pc",
                           pods=pod_list, arrays=arrays, pc_vms=n_pcvms,
                           data={"name": info.host}))
    sites.sort(key=lambda s: (s.name.lower(), s.id))
    return LogicalModel(sites=sites)


def _cluster(env: Environment, c: Any, host: str, src: Any, hosts: list[Any],
             cvms: list[Any], blocks: list[Any], ctrs: list[Any]) -> LCluster:
    name = c.name or c.uuid[:8]
    # Nodes that differ only in memory share a group (memory then reads as a range).
    groups: dict[tuple[str, str, int, int, str], list[int]] = {}
    for h in hosts:
        groups.setdefault((h.block_model or "", cpu_label(h.cpu_model) or "",
                           h.num_cpu_sockets or 0, h.num_cpu_cores or 0, _role(h.host_type)),
                          []).append(round((h.memory_bytes or 0) / GIB))
    node_groups = [NodeGroup(count=len(mems), model=m or None, cpu=cpu or None,
                             sockets=s or None, cores=co or None,
                             mem_gib=min(mems) or None,
                             mem_max_gib=max(mems) if max(mems) != min(mems) else None,
                             role=role)
                   for (m, cpu, s, co, role), mems in
                   sorted(groups.items(), key=lambda kv: (kv[0][4] != "", -len(kv[1]), kv[0]))]
    per = [len(b.nodes) for b in blocks if b.nodes]
    per_block = max(per) if per else None
    racks = sorted({b.rack_id for b in blocks if b.rack_id is not None}
                   | {h.rack_id for h in hosts if h.rack_id is not None})
    svc = []

    def on(objs: list[Any]) -> list[Any]:
        return [o for o in objs if src(o) == host and o.cluster_name == name]

    for fs in sorted(on(env.file_servers), key=lambda f: (f.name or "", f.uuid)):
        svc.append(f"Files, {fmt.plural(fs.num_fsvm, 'FSVM')}" if fs.num_fsvm else "Files")
    for ob in sorted(on(env.object_stores), key=lambda o: (o.name or "", o.uuid)):
        svc.append(f"Objects, {fmt.plural(ob.num_worker_nodes, 'worker')}"
                   if ob.num_worker_nodes else "Objects")
    if n := len(on(env.database_servers)):
        svc.append(f"NDB, {fmt.plural(n, 'DB server')}")
    svc = list(dict.fromkeys(svc))
    containers = [Container(
        id=f"cluster:{c.uuid}:ctr:{ct.uuid}", name=ct.name or ct.uuid[:8],
        rf=ct.replication_factor, compression=ct.compression_enabled,
        dedup=_bool_switch(ct.deduplication), ecx=_bool_switch(ct.erasure_coding),
        max_bytes=ct.max_capacity_bytes, array=ct.external_storage_uuid)
        for ct in ctrs if not ct.is_internal and not _is_system(ct.name)]
    containers.sort(key=lambda x: (x.array is not None, x.name.lower(), x.id))
    hyps = [x for x in dict.fromkeys(hypervisor_label(t) for t in c.hypervisor_types) if x]
    return LCluster(
        id=f"cluster:{c.uuid}", name=name, hypervisor=", ".join(hyps) or None,
        aos=short_version(c.aos_version), nodes=c.num_nodes or (len(hosts) or None),
        rf=c.redundancy_factor, fault_domain=FD_LABELS.get((c.fault_domain or "").upper()),
        tolerates=c.fault_tolerance, desired=c.desired_fault_tolerance, groups=node_groups,
        blocks=len(blocks), per_block=per_block if per_block and per_block > 1 else None,
        blocks_full=len(set(per)) <= 1,
        racks=racks,
        cvm_vcpus=sorted({v.num_vcpus for v in cvms if v.num_vcpus}),
        cvm_mem_gib=sorted({round(v.memory_bytes / GIB) for v in cvms if v.memory_bytes}),
        services=svc, containers=containers,
        data={"cluster_uuid": c.uuid, "name": name, "prism_central": host})


#: Nutanix-created containers that are not user storage (older snapshots lack
#: `is_internal`).
_SYSTEM_CTRS = ("NutanixManagementShare", "SelfServiceContainer", "NutanixMetadata")


def _is_system(name: str | None) -> bool:
    return bool(name) and any(name.startswith(p) for p in _SYSTEM_CTRS)  # type: ignore[union-attr]


# ─── display text shared by the layout, summary and table ──────────────


def span(values: list[int], unit: str) -> str:
    if not values:
        return "N/A"
    if len(values) == 1:
        return f"{values[0]:,} {unit}"
    return f"{values[0]:,} to {values[-1]:,} {unit}"


def cvm_text(c: LCluster) -> str | None:
    if not (c.cvm_vcpus or c.cvm_mem_gib):
        return None
    each = " on each node" if len(c.cvm_vcpus) <= 1 and len(c.cvm_mem_gib) <= 1 else ""
    parts = [p for p in (span(c.cvm_vcpus, "vCPU") if c.cvm_vcpus else None,
                         span(c.cvm_mem_gib, "GiB") if c.cvm_mem_gib else None) if p]
    return f"CVM: {', '.join(parts)}{each}"


def fault_text(c: LCluster) -> str | None:
    if not c.fault_domain and c.tolerates is None:
        return None
    parts = [f"Fault domain: {c.fault_domain}" if c.fault_domain else "Fault domain N/A"]
    if c.tolerates is not None:
        parts.append(f"tolerates {fmt.plural(c.tolerates, 'failure')}")
        if c.desired is not None and c.desired > c.tolerates:
            parts[-1] += f" (target {c.desired:,})"
    return ", ".join(parts)


def headline(c: LCluster) -> str:
    parts = [c.hypervisor or "Hypervisor N/A"]
    if c.aos:
        parts.append(f"AOS {c.aos}")
    if c.nodes is not None:
        parts.append(fmt.plural(c.nodes, "node"))
    if c.rf:
        parts.append(f"RF{c.rf}" + (" (disk)" if c.fault_domain == "disk" else ""))
    return ", ".join(parts)


def group_lines(g: NodeGroup) -> tuple[str, str | None]:
    first = f"{g.count:,} x {g.model or 'model N/A'}" + (f" ({g.role})" if g.role else "")
    spec = []
    if g.cpu:
        spec.append(f"{g.sockets:,} x {g.cpu}" if g.sockets else g.cpu)
    if g.cores:
        spec.append(f"{g.cores:,} cores")
    if g.mem_gib:
        spec.append(f"{g.mem_gib:,} to {g.mem_max_gib:,} GiB" if g.mem_max_gib
                    else f"{g.mem_gib:,} GiB")
    return first, ", ".join(spec) or None


def blocks_text(c: LCluster) -> str | None:
    parts = []
    if c.per_block and c.blocks:
        parts.append(f"{fmt.plural(c.blocks, 'block')} of {c.per_block:,} nodes"
                     if c.blocks_full else
                     f"{fmt.plural(c.blocks, 'block')}, up to {c.per_block:,} nodes each")
    if c.racks:
        racks = [str(r) for r in c.racks]
        parts.append(("rack " if len(racks) == 1 else "racks ") + fmt.join_names(racks))
    if not parts:
        return None
    return ", ".join(parts)


def chip_name(ct: Container, cluster: str) -> str:
    """A container's name inside its cluster's card: a leading copy of the cluster's
    own name is dropped ("prod-a-ctr-images" on PROD-A reads "ctr-images"); the table
    and the shape data keep the full name."""
    prefix = cluster.lower() + "-"
    if ct.name.lower().startswith(prefix) and len(ct.name) > len(prefix):
        return ct.name[len(prefix):]
    return ct.name


def container_flags(ct: Container, cluster: str | None = None) -> str:
    flags = [f for f, on in (("compression", ct.compression), ("dedup", ct.dedup),
                             ("EC-X", ct.ecx)) if on]
    parts = [chip_name(ct, cluster) if cluster else ct.name]
    if ct.array:
        parts.append("external")
    else:
        if ct.rf:
            parts.append(f"RF{ct.rf}")
        if flags:
            parts.append(", ".join(flags))
    return " · ".join(parts)


def pc_role_text(c: LCluster) -> str | None:
    if not c.hosts_pc:
        return None
    text = "Runs Prism Central"
    if c.pc_vms:
        text += f": {fmt.plural(c.pc_vms, 'PCVM')}"
        if c.pc_size:
            text += f", {c.pc_size}"
    return text


def array_title(a: Array) -> str:
    return f"{a.product} {a.name}" if a.name else a.product


def array_lines(a: Array) -> list[str]:
    out = []
    ep = []
    if a.endpoint:
        ep.append(f"Endpoint {a.endpoint}")
    if a.protocol:
        ep.append(f"{a.protocol} (implied by the provider)")
    if ep:
        out.append(" · ".join(ep))
    cap = []
    if a.total_bytes:
        cap.append(fmt.tib(a.total_bytes))
        if a.free_bytes is not None:
            cap[-1] += f", {round(100 * a.free_bytes / a.total_bytes):,}% free"
    if a.health:
        cap.append(f"health: {a.health}")
    if cap:
        out.append(" · ".join(cap))
    att = f"Attached to {a.cluster_name}" if a.cluster_name else ""
    if a.containers:
        ctr = f"{fmt.plural(len(a.containers), 'container')} ({', '.join(a.containers)})"
        att = f"{att} · {ctr}" if att else ctr
    if att:
        out.append(att)
    return out


def site_line(s: LSite) -> str:
    cl = s.clusters
    nodes = sum(c.nodes or 0 for c in cl)
    what = "Availability zone" if s.kind == "pc" else "Prism Element"
    pods = [p for p in s.pods if p.name]
    text = f"{what}, {fmt.plural(len(cl), 'cluster')}"
    if pods:
        text += f" in {fmt.plural(len(pods), 'POD')}"
    text += f", {fmt.plural(nodes, 'node')}"
    if s.kind == "pc" and s.pc_vms > 1:
        text += f", Prism Central scale-out ({s.pc_vms:,} PCVMs)"
    return text


# ─── design summary and tables ─────────────────────────────────────────

MAX_BULLETS = 5


def logical_bullets(s: LSite) -> list[str]:
    cl = s.clusters
    if not cl:
        return []
    nodes = sum(c.nodes or 0 for c in cl)
    out: list[str] = []
    hyp = Counter(c.hypervisor or "N/A" for c in cl)
    aos = sorted({c.aos for c in cl if c.aos})
    sw = fmt.join_names(sorted(hyp)) + (f" and AOS {fmt.join_names(aos)}" if aos else "")
    pods = [p for p in s.pods if p.name]
    if pods:
        out.append(f"{fmt.plural(len(cl), 'cluster')} in {fmt.plural(len(pods), 'POD')} "
                   f"(from the {POD_KEY} category), {fmt.plural(nodes, 'node')} on {sw}.")
    else:
        out.append(f"{fmt.plural(len(cl), 'cluster')}, {fmt.plural(nodes, 'node')} on {sw}.")
    for c in cl:
        if c.hosts_pc:
            what = f"Prism Central ({fmt.plural(c.pc_vms, 'PCVM')})" if c.pc_vms else \
                "Prism Central"
            extra = f" and {fmt.join_names(c.services)}" if c.services else ""
            out.append(f"{c.name} hosts {what}{extra}.")
            break
    by_fd: dict[tuple[int | None, str | None], list[str]] = {}
    for c in cl:
        by_fd.setdefault((c.rf, c.fault_domain), []).append(c.name)
    phrases = []
    for (rf, fd), names in sorted(by_fd.items(), key=lambda kv: (-len(kv[1]), str(kv[0]))):
        what = (f"RF{rf}" if rf else "RF N/A") + (f" with a {fd} fault domain" if fd else "")
        who = fmt.join_names(names) if len(names) <= 2 else \
            f"{', '.join(names[:2])} and {len(names) - 2:,} more"
        phrases.append(f"{what} on {who}" if len(by_fd) > 1 else f"{what} on every cluster")
    if phrases:
        out.append("; ".join(phrases[:3]) + ".")
    for a in s.arrays[:1]:
        ctr = fmt.join_names(a.containers) if a.containers else "its containers"
        proto = f" over {a.protocol}" if a.protocol else ""
        more = f" ({len(s.arrays) - 1:,} more arrays in the table)" if len(s.arrays) > 1 else ""
        out.append(f"{a.cluster_name or 'A cluster'} keeps {ctr} on {array_title(a)}"
                   f"{proto}{more}.")
    compute = [(c, sum(g.count for g in c.groups if g.role == "compute-only")) for c in cl]
    compute = [(c, n) for c, n in compute if n]
    if compute:
        c, n = compute[0]
        out.append(f"{c.name} has {fmt.plural(n, 'compute-only node')}.")
    sizes: dict[str, list[str]] = {}
    for c in cl:
        if t := cvm_text(c):
            sizes.setdefault(t.removeprefix("CVM: ").removesuffix(" on each node"),
                             []).append(c.name)
    if len(sizes) == 1:
        out.append(f"Every CVM has {next(iter(sizes)).replace(', ', ' and ')}.")
    elif sizes:
        parts = [f"{k} on {fmt.join_names(v) if len(v) <= 2 else f'{len(v):,} clusters'}"
                 for k, v in sorted(sizes.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:3]]
        out.append("CVMs: " + "; ".join(parts) + ".")
    return out[:MAX_BULLETS]


CONTAINER_HEADERS = ["Cluster", "Container", "RF", "Compression", "Dedup", "EC-X",
                     "Backing storage", "Max capacity (TiB)"]


def container_rows(s: LSite) -> list[list[object]]:
    arrays = {a.id: a for a in s.arrays}

    def yn(v: bool | None) -> str | None:
        return None if v is None else ("Yes" if v else "No")

    rows: list[list[object]] = []
    for c in s.clusters:
        for ct in c.containers:
            a = arrays.get(ct.array or "")
            backing = array_title(a) if a else ("External array" if ct.array else "DSF")
            rows.append([c.name, ct.name, None if ct.array else ct.rf, yn(ct.compression),
                         yn(ct.dedup), yn(ct.ecx), backing,
                         round(ct.max_bytes / fmt.TIB, 1) if ct.max_bytes else None])
    return rows


def short(s: LSite) -> str:
    return short_name(s.name)
