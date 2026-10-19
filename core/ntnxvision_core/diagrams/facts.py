"""Environment -> diagram model documents (sorted, stable ids, presentation-free).

Everything a layout needs is extracted here in a stable order, so layouts never iterate
the Environment directly and never depend on API response order. Only collected data is
used; design data later collectors add (external arrays, PC deployment, registrations,
per-direction replication) is read when the snapshot has it and is absent otherwise.

Inferences, made visible rather than hidden:

* a site (availability zone) is one Prism Central: `source_environments` on a combined
  run, else the one PC; a direct Prism Element run has one site and no PC block;
* the cluster hosting the PC: the PC's own record (`hosting_cluster_uuid`), else the PC
  VMs' placement, else the cluster running the ``PRISM_CENTRAL`` system VMs;
* an availability zone (or a remote PC registration) is another collected site when its
  reference is that PC's uuid or address, or its name carries that PC's host or IP; else
  it is a remote AZ;
* replication follows each policy's replication rules (source site to target site, per
  direction); the type comes from the RPO (`replication_type_for_rpo`) when the policy
  does not state it.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any
from urllib.parse import urlparse

from ntnxvision_core.models import Environment
from ntnxvision_core.models.environment import replication_label, replication_type_for_rpo
from ntnxvision_core.versions import pc_version, short_version

from .model import (
    Card,
    ConceptualModel,
    ExternalArray,
    Link,
    PcBlock,
    Remote,
    Repl,
    Service,
    Site,
)

_HYP = {"AHV": "AHV", "ESX": "ESXi", "ESXI": "ESXi", "HYPERV": "Hyper-V", "HYPER-V": "Hyper-V"}
_IP = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
#: PC deployment sizes (v4 vocabulary) as display names.
PC_SIZE_LABELS = {"STARTER": "Starter", "SMALL": "Small", "LARGE": "Large",
                  "EXTRALARGE": "X-Large"}
#: Connection states that mean a pairing is up (None: not reported, assumed up).
_UP = {"CONNECTED", "REACHABLE", "COMPLETE", "SUCCEEDED", "OK"}


def hypervisor_label(value: str | None) -> str | None:
    if not value:
        return None
    return _HYP.get(value.strip().upper().replace(" ", ""), value.strip())


def pc_size_label(value: str | None) -> str | None:
    if not value:
        return None
    key = str(value).strip().upper()
    return PC_SIZE_LABELS.get(key, key.replace("_", " ").capitalize())


def short_name(host: str) -> str:
    """``pc01.demo.local`` -> ``pc01``; addresses stay whole."""
    if _IP.fullmatch(host or ""):
        return host
    return (host or "").split(".")[0] or host


def _url_host(url: str | None) -> str:
    if not url:
        return ""
    if "//" in url:
        return (urlparse(url).hostname or "").lower()
    return url.strip().lower()


def _connected(status: str | None) -> bool:
    return status is None or status.strip().upper() in _UP


class _SiteInfo:
    def __init__(self, host: str, uuid: str | None, ips: list[str], pc: Any, cfg: Any) -> None:
        self.host = host
        self.id = f"site:{host}"
        self.uuid = (uuid or "").lower()
        self.ips = [ip for ip in ips if ip]
        self.pc = pc
        self.cfg = cfg

    def matches(self, *texts: str | None) -> bool:
        keys = {self.host.lower(), short_name(self.host).lower()} | set(self.ips)
        if self.uuid:
            keys.add(self.uuid)
        for t in texts:
            if not t:
                continue
            t = t.strip().lower()
            if t in keys or _url_host(t) in keys:
                return True
            if any(ip in self.ips for ip in _IP.findall(t)):
                return True
            if self.host and self.host.lower() in t:
                return True
        return False


def _sites(env: Environment) -> list[_SiteInfo]:
    if env.is_combined:
        return [_SiteInfo(s.prism_central.host or "unknown", s.prism_central.uuid,
                          list(s.pc_config.ip_addresses), s.prism_central, s.pc_config)
                for s in env.source_environments]
    host = env.prism_central.host or ("Prism Element" if env.source_type == "prism_element"
                                      else "Prism Central")
    return [_SiteInfo(host, env.prism_central.uuid, list(env.pc_config.ip_addresses),
                      env.prism_central, env.pc_config)]


def _models(hosts: list[Any]) -> list[str]:
    c = Counter(h.block_model for h in hosts if h.block_model)
    return [m for m, _ in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))]


class _Remotes:
    """Remote AZs by exact name; ids ``az:<lowercased name>``, with a stable hash
    suffix when two names differ only by case."""

    def __init__(self) -> None:
        self.by_name: dict[str, Remote] = {}
        self.ids: set[str] = set()

    def get(self, name: str, kind: str, region: str | None) -> str:
        if name in self.by_name:
            r = self.by_name[name]
            if region and not r.region:
                r.region = region
            if kind == "xi":
                r.kind = "xi"
            return r.id
        rid = f"az:{name.lower()}"
        if rid in self.ids:
            rid += "~" + hashlib.sha256(name.encode()).hexdigest()[:6]
        self.ids.add(rid)
        self.by_name[name] = Remote(id=rid, name=name, kind=kind, region=region,
                                    data={"availability_zone": name})
        return rid


def conceptual_model(env: Environment) -> ConceptualModel:
    """The Solution Conceptual Overview model for `env`."""
    infos = _sites(env)
    is_pe = env.source_type == "prism_element"
    default = infos[0].host

    def src(obj: Any) -> str:
        if env.is_combined:
            return getattr(obj, "source_pc", None) or default
        return default

    def mine(objs: list[Any], host: str) -> list[Any]:
        return [o for o in objs if src(o) == host]

    hosts_by_cluster: dict[str, list[Any]] = {}
    for h in env.hosts:
        hosts_by_cluster.setdefault(h.cluster_uuid or "", []).append(h)
    ctr_by_cluster: dict[str, list[Any]] = {}
    for ct in env.storage_containers:
        ctr_by_cluster.setdefault(ct.cluster_uuid or "", []).append(ct)
    arrays_by_cluster: dict[str, list[Any]] = {}
    for arr in env.external_storages:
        arrays_by_cluster.setdefault(arr.cluster_uuid or "", []).append(arr)

    sites: dict[str, Site] = {}
    for info in infos:
        hosting = _hosting_clusters(env, info, mine)
        cards = [_card(env, c, info.host, src, hosting, hosts_by_cluster, ctr_by_cluster,
                       arrays_by_cluster) for c in mine(env.clusters, info.host)]
        cards.sort(key=lambda k: (not k.hosts_pc, k.name.lower(), k.id))
        pc_block = None if is_pe else _pc_block(env, info, mine)
        sites[info.id] = Site(id=info.id, name=info.host, kind="pe" if is_pe else "pc",
                              pc=pc_block, cards=cards, cluster_count=len(cards),
                              node_count=sum(c.nodes or 0 for c in cards),
                              data={"prism_central" if not is_pe else "prism_element":
                                    info.host, "name": info.host})

    remotes = _Remotes()
    pair: dict[tuple[str, str], Link] = {}
    flows: dict[tuple[str, str], dict[str, Any]] = {}

    def target_of(info: _SiteInfo, name: str | None, ref: str | None,
                  kind: str = "pc", region: str | None = None) -> str | None:
        """The site or remote id an AZ reference points at; None when it is local."""
        if not (name or ref):
            return None
        if (name or "").lower().startswith("local") or info.matches(ref):
            return None
        for other in infos:
            if other is not info and other.matches(ref, name):
                return other.id
        if info.matches(name):
            return None
        return remotes.get(name or _url_host(ref) or "Availability zone", kind, region)

    def add_pair(info: _SiteInfo, tid: str, status: str | None) -> None:
        key = _pair_key(info.id, tid)
        if key not in pair:
            a, b = (info.id, tid) if tid.startswith("az:") else key
            pair[key] = Link(id=f"link:pairing:{a}->{b}", kind="pairing", a=a, b=b)
        if not _connected(status):
            pair[key].connected = False
            pending = (status or "").strip().upper() == "PENDING"
            if not pair[key].state or not pending:
                pair[key].state = "pending" if pending else "not connected"

    if not is_pe:
        for info in infos:
            az_names: dict[str, str | None] = {}
            for az in sorted(mine(env.availability_zones, info.host),
                             key=lambda a: ((a.name or "").lower(), a.url or "")):
                if (az.az_type or "").upper() == "LOCAL":
                    continue
                tid = target_of(info, az.name, az.url,
                                "xi" if (az.az_type or "").upper() == "XI" else "pc",
                                az.region)
                for ident in (az.name, az.url, az.uuid):
                    if ident:
                        az_names[ident] = tid
                if tid is not None:
                    add_pair(info, tid, az.connection_status)
            for reg in mine(env.pc_registrations, info.host):
                if not reg.is_remote_pc:
                    continue
                tid = az_names.get(reg.remote_uuid) or target_of(info, reg.name,
                                                                 reg.remote_uuid)
                if tid is not None:
                    add_pair(info, tid, reg.connectivity_status)

            def resolve(name: str | None, ref: str | None,
                        info: _SiteInfo = info, az_names: dict[str, str | None] = az_names
                        ) -> str:
                for ident in (ref, name):
                    if ident and ident in az_names:
                        return az_names[ident] or info.id
                return target_of(info, name, ref) or info.id

            for p in mine(env.protection_policies, info.host):
                for a, b, rep in _policy_flows(p, resolve):
                    entry = flows.setdefault(_pair_key(a, b), {"dirs": {}, "n": set()})
                    entry["dirs"].setdefault((a, b), set()).add((rep.type, rep.rpo_seconds))
                    entry["n"].add(p.uuid)

    site_order = _order_sites(list(sites), [*pair, *flows], {s: sites[s].name for s in sites})
    rank = {sid: i for i, sid in enumerate(site_order)}

    links: list[Link] = [pair[k] for k in sorted(pair)]
    for key in sorted(flows):
        e = flows[key]
        x, y = key
        if x.startswith("az:") or (not y.startswith("az:")
                                   and rank.get(y, 10**6) < rank.get(x, 10**6)):
            x, y = y, x
        fwd = sorted((Repl(t, r) for t, r in e["dirs"].get((x, y), ())), key=_rk)
        bwd = sorted((Repl(t, r) for t, r in e["dirs"].get((y, x), ())), key=_rk)
        links.append(Link(id=f"link:replication:{x}->{y}", kind="replication", a=x, b=y,
                          forward=fwd, backward=bwd, policies=len(e["n"])))
    links.sort(key=lambda lk: (lk.kind, lk.id))
    return ConceptualModel(
        sites=[sites[s] for s in site_order],
        remotes=sorted(remotes.by_name.values(), key=lambda r: (r.name.lower(), r.id)),
        links=links)


def _rk(r: Repl) -> tuple[int, str]:
    return (r.rpo_seconds if r.rpo_seconds is not None else 10**12, r.type)


def _repl(replication_type: str | None, rpo: int | None) -> Repl:
    t = replication_label(replication_type or replication_type_for_rpo(rpo)) or "Async"
    return Repl(t, rpo)


def _policy_flows(p: Any, resolve: Any) -> list[tuple[str, str, Repl]]:
    """(source id, target id, Repl) per replication direction of policy `p`."""
    out = []
    if p.replications and p.locations:
        locs = {loc.label: loc for loc in p.locations}
        for r in p.replications:
            s, t = locs.get(r.source_label), locs.get(r.target_label)
            if s is None or t is None:
                continue
            a, b = resolve(s.az_name, s.az_ref), resolve(t.az_name, t.az_ref)
            if a != b:  # a policy inside one AZ (Metro between its clusters) is not drawn
                out.append((a, b, _repl(r.replication_type, r.rpo_seconds)))
        return out
    a, b = resolve(p.source_az, None), resolve(p.target_az, None)
    if p.target_az and a != b:
        out.append((a, b, _repl(p.replication_type, p.rpo_seconds)))
    return out


def _hosting_clusters(env: Environment, info: _SiteInfo, mine: Any) -> tuple[set[str],
                                                                             set[str]]:
    """(uuids, names) of the cluster(s) hosting this site's PC VMs."""
    cfg = info.cfg
    hosting = getattr(cfg, "hosting_cluster_uuid", None)
    uuids = {hosting} if hosting else {v.hosting_cluster_uuid
                                       for v in mine(env.pc_vms, info.host)
                                       if v.hosting_cluster_uuid}
    names: set[str] = set()
    if not uuids:
        pcvms = [v for v in mine(env.system_vms, info.host)
                 if (v.vm_type or "").upper() == "PRISM_CENTRAL"]
        uuids = {v.cluster_uuid for v in pcvms if v.cluster_uuid}
        names = {v.cluster_name for v in pcvms if v.cluster_name}
    return uuids, names


def _card(env: Environment, c: Any, host: str, src: Any, hosting: tuple[set[str], set[str]],
          hosts_by_cluster: dict[str, list[Any]], ctr_by_cluster: dict[str, list[Any]],
          arrays_by_cluster: dict[str, list[Any]]) -> Card:
    hosts = hosts_by_cluster.get(c.uuid, [])
    raw = sum(h.storage_capacity_bytes or 0 for h in hosts)
    name = c.name or c.uuid[:8]

    def on(objs: list[Any]) -> int:
        return sum(1 for o in objs if src(o) == host and o.cluster_name == name)

    svc = []
    if n := on(env.file_servers):
        svc.append("Files" if n == 1 else f"Files ({n:,})")
    if n := on(env.object_stores):
        svc.append("Objects" if n == 1 else f"Objects ({n:,})")
    if n := on(env.database_servers):
        svc.append(f"NDB ({n:,})")
    if c.vcenter_address:
        svc.append("vCenter")
    hyps = [x for x in dict.fromkeys(hypervisor_label(t) for t in c.hypervisor_types) if x]
    containers = ctr_by_cluster.get(c.uuid, [])
    dsf = [ct for ct in containers if not ct.is_internal and not ct.external_storage_uuid]
    external = []
    for x in sorted(arrays_by_cluster.get(c.uuid, []), key=lambda a: (a.name or "", a.uuid)):
        n_ctr = sum(1 for ct in containers if ct.external_storage_uuid == x.uuid)
        external.append(ExternalArray(id=f"cluster:{c.uuid}:ext:{x.uuid}", product=x.product,
                                      name=x.name, containers=n_ctr))
    uuids, names = hosting
    return Card(
        id=f"cluster:{c.uuid}", name=name, hypervisor=", ".join(hyps) or None,
        nodes=c.num_nodes or (len(hosts) or None), rf=c.redundancy_factor,
        models=_models(hosts), aos=short_version(c.aos_version), raw_bytes=raw or None,
        containers=len(dsf), services=svc, hosts_pc=c.uuid in uuids or name in names,
        compute_only=c.is_compute_only, external=external,
        data={"cluster_uuid": c.uuid, "prism_central": host, "name": name})


def _pc_block(env: Environment, info: _SiteInfo, mine: Any) -> PcBlock:
    cfg = info.cfg
    services = []
    enabled = set(getattr(cfg, "enabled_products", []) or [])
    for label, objs, product in (
            ("DR", env.protection_policies, "NUTANIX_DISASTER_RECOVERY"),
            ("Flow", env.security_policies, "FLOW_NETWORK_SECURITY"),
            ("VPCs", env.vpcs, "FLOW_VIRTUAL_NETWORKING"),
            ("X-Play", env.playbooks, None)):
        n = len(mine(objs, info.host))
        if n or (product and product in enabled):
            services.append(Service(label, n))
    gib = 1024**3
    return PcBlock(
        id=f"{info.id}:pc", version=pc_version(info.pc.version or cfg.version),
        vms=cfg.node_count, vm_vcpus=getattr(cfg, "vm_vcpus", None),
        vm_mem_gib=round(cfg.vm_memory_bytes / gib) if getattr(cfg, "vm_memory_bytes", None)
        else None,
        vcpus=cfg.num_vcpus, mem_gib=round(cfg.memory_bytes / gib) if cfg.memory_bytes else None,
        size=pc_size_label(getattr(cfg, "size", None)), services=services)


def _pair_key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def _order_sites(ids: list[str], pairs: list[tuple[str, str]],
                 names: dict[str, str]) -> list[str]:
    """Sites by name, with linked sites kept together (BFS per connected component), so
    paired AZs sit side by side and land in the same part."""
    adj: dict[str, set[str]] = {i: set() for i in ids}
    for a, b in pairs:
        if a in adj and b in adj:
            adj[a].add(b)
            adj[b].add(a)

    def key(i: str) -> tuple[str, str]:
        return (names[i].lower(), i)

    seen: set[str] = set()
    out: list[str] = []
    for start in sorted(ids, key=key):
        if start in seen:
            continue
        queue = [start]
        seen.add(start)
        while queue:
            cur = queue.pop(0)
            out.append(cur)
            for nxt in sorted(adj[cur] - seen, key=key):
                seen.add(nxt)
                queue.append(nxt)
    return out
