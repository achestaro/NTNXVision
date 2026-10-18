"""Environment -> diagram model documents (sorted, stable ids, presentation-free).

Everything a layout needs is extracted here in a stable order, so layouts never iterate
the Environment directly and never depend on API response order. Only collected data is
used; anything a later collector adds (external storage arrays, PC size) is read when the
snapshot carries it and is simply absent otherwise.

Inferences, made visible rather than hidden:

* a site (availability zone) is one Prism Central: `source_environments` on a combined
  run, else the one PC; a direct Prism Element run has one site and no PC block;
* the cluster hosting the PC is the one running the ``PRISM_CENTRAL`` system VMs;
* an availability zone is another collected site when its management URL is that PC's
  uuid or address, or its name carries that PC's host or IP; else it is a remote AZ;
* replication type comes from the policy (or from its RPO: 0 Sync, up to 15 min
  NearSync, else Async).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from ntnxvision_core.models import Environment
from ntnxvision_core.versions import short_version

from .model import Card, ConceptualModel, ExternalArray, Link, PcBlock, Remote, Repl, Service, Site

_REPL = {"ASYNC": "Async", "NEARSYNC": "NearSync", "NEAR_SYNC": "NearSync", "SYNC": "Sync",
         "SYNCHRONOUS": "Sync", "METRO": "Metro"}
_HYP = {"AHV": "AHV", "ESX": "ESXi", "ESXI": "ESXi", "HYPERV": "Hyper-V", "HYPER-V": "Hyper-V"}
#: External storage providers (API enum) as product names.
_PRODUCTS = {"EVERPURE_FLASHARRAY": "Pure FlashArray", "PURE_FLASHARRAY": "Pure FlashArray",
             "FLASHARRAY": "Pure FlashArray", "DELL_POWERFLEX": "Dell PowerFlex",
             "DELL_POWERSTORE": "Dell PowerStore"}
_IP = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")


def hypervisor_label(value: str | None) -> str | None:
    if not value:
        return None
    return _HYP.get(value.strip().upper().replace(" ", ""), value.strip())


def product_label(value: str | None) -> str:
    """An external storage provider enum as a product name; unknown values title-cased."""
    if not value:
        return "External storage"
    key = value.strip().upper()
    return _PRODUCTS.get(key, key.replace("_", " ").title())


def repl_type(replication_type: str | None, rpo: int | None) -> str:
    if replication_type and replication_type.strip().upper() in _REPL:
        return _REPL[replication_type.strip().upper()]
    if rpo == 0:
        return "Sync"
    if rpo is not None and rpo <= 900:
        return "NearSync"
    return "Async"


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


def _external_arrays(env: Environment, cluster: Any, containers: list[Any]) -> list[ExternalArray]:
    """External storage arrays behind this cluster's containers, when the snapshot has
    them (a later collector adds `external_storages` and the container link)."""
    arrays = getattr(env, "external_storages", None) or []
    if not arrays:
        return []
    by_id = {str(getattr(a, "uuid", None) or getattr(a, "ext_id", "")): a for a in arrays}
    counts: Counter[str] = Counter()
    for ct in containers:
        ext = getattr(ct, "external_storage_id", None) or getattr(ct, "external_storage_uuid",
                                                                  None)
        if ext:
            counts[str(ext)] += 1
    for a in arrays:  # an array attached to the cluster with no container link yet
        if getattr(a, "cluster_uuid", None) == cluster.uuid:
            key = str(getattr(a, "uuid", None) or getattr(a, "ext_id", ""))
            counts.setdefault(key, 0)
    out = []
    for key in sorted(counts):
        a = by_id.get(key)
        out.append(ExternalArray(
            id=f"cluster:{cluster.uuid}:ext:{key}",
            product=product_label(getattr(a, "provider_type", None) if a else None),
            name=getattr(a, "name", None) if a else None, containers=counts[key]))
    return out


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

    sites: dict[str, Site] = {}
    for info in infos:
        pcvms = [v for v in mine(env.system_vms, info.host)
                 if (v.vm_type or "").upper() == "PRISM_CENTRAL"]
        pc_cluster_ids = {v.cluster_uuid for v in pcvms if v.cluster_uuid}
        pc_cluster_names = {v.cluster_name for v in pcvms if v.cluster_name}
        cards: list[Card] = []
        for c in sorted(mine(env.clusters, info.host), key=lambda c: ((c.name or "").lower(),
                                                                      c.uuid)):
            hosts = hosts_by_cluster.get(c.uuid, [])
            raw = sum(h.storage_capacity_bytes or 0 for h in hosts)
            name = c.name or c.uuid[:8]

            def on(objs: list[Any], cname: str = name, host: str = info.host) -> int:
                return sum(1 for o in objs if src(o) == host and o.cluster_name == cname)

            svc = []
            if n := on(env.file_servers):
                svc.append("Files" if n == 1 else f"Files ({n:,})")
            if n := on(env.object_stores):
                svc.append("Objects" if n == 1 else f"Objects ({n:,})")
            if n := on(env.database_servers):
                svc.append(f"NDB ({n:,})")
            if c.vcenter_address:
                svc.append("vCenter")
            hyps = [x for x in dict.fromkeys(hypervisor_label(t) for t in c.hypervisor_types)
                    if x]
            containers = ctr_by_cluster.get(c.uuid, [])
            cards.append(Card(
                id=f"cluster:{c.uuid}", name=name, hypervisor=", ".join(hyps) or None,
                nodes=c.num_nodes or (len(hosts) or None), rf=c.redundancy_factor,
                models=_models(hosts), aos=short_version(c.aos_version), raw_bytes=raw or None,
                containers=len(containers), services=svc,
                hosts_pc=c.uuid in pc_cluster_ids or name in pc_cluster_names,
                external=_external_arrays(env, c, containers),
                data={"cluster_uuid": c.uuid, "prism_central": info.host}))
        cards.sort(key=lambda k: (not k.hosts_pc, k.name.lower(), k.id))

        pc_block = None
        if not is_pe:
            services = []
            for label, objs in (("DR", env.protection_policies), ("Flow", env.security_policies),
                                ("VPCs", env.vpcs), ("X-Play", env.playbooks)):
                if n := len(mine(objs, info.host)):
                    services.append(Service(label, n))
            cfg = info.cfg
            size = getattr(cfg, "size", None)
            pc_block = PcBlock(
                id=f"{info.id}:pc", version=short_version(info.pc.version or cfg.version),
                vms=cfg.node_count, vcpus=cfg.num_vcpus,
                mem_gib=round(cfg.memory_bytes / 1024**3) if cfg.memory_bytes else None,
                size=str(size).title() if size else None, services=services)
        sites[info.id] = Site(id=info.id, name=info.host, kind="pe" if is_pe else "pc",
                              pc=pc_block, cards=cards,
                              data={"prism_central" if not is_pe else "prism_element":
                                    info.host})

    remotes: dict[str, Remote] = {}
    pair: dict[tuple[str, str], Link] = {}
    repl: dict[tuple[str, str], dict[str, Any]] = {}

    def target_of(info: _SiteInfo, name: str | None, url: str | None,
                  az: Any = None) -> str | None:
        """The site or remote id an AZ reference points at; None when it is local."""
        if not (name or url):
            return None
        if (name or "").lower().startswith("local") or info.matches(url) or (
                az is not None and (az.az_type or "").upper() == "LOCAL"):
            return None
        for other in infos:
            if other is not info and other.matches(url, name):
                return other.id
        if info.matches(name):
            return None
        label = name or _url_host(url) or "Availability zone"
        rid = f"az:{label.lower()}"
        if rid not in remotes:
            kind = "xi" if az is not None and (az.az_type or "").upper() == "XI" else "pc"
            remotes[rid] = Remote(id=rid, name=label, kind=kind,
                                  region=getattr(az, "region", None) if az else None,
                                  data={"availability_zone": label})
        return rid

    if not is_pe:
        for info in infos:
            az_names: dict[str, str | None] = {}
            for az in sorted(mine(env.availability_zones, info.host),
                             key=lambda a: ((a.name or "").lower(), a.url or "")):
                tid = target_of(info, az.name, az.url, az)
                if az.name:
                    az_names[az.name] = tid
                if tid is None:
                    continue
                key = _pair_key(info.id, tid)
                if key not in pair:
                    a, b = (info.id, tid) if tid.startswith("az:") else key
                    pair[key] = Link(id=f"link:pairing:{a}->{b}", kind="pairing", a=a, b=b,
                                     both=True)
            for p in mine(env.protection_policies, info.host):
                tgt = _resolve(p.target_az, az_names, info, target_of)
                srcz = _resolve(p.source_az, az_names, info, target_of)
                if tgt is not None and srcz is None:
                    a, b = info.id, tgt
                elif srcz is not None and tgt is None:
                    a, b = srcz, info.id
                else:
                    continue
                entry = repl.setdefault(_pair_key(a, b),
                                        {"dirs": set(), "repl": set(), "n": 0})
                entry["dirs"].add((a, b))
                entry["repl"].add((repl_type(p.replication_type, p.rpo_seconds),
                                   p.rpo_seconds))
                entry["n"] += 1

    site_order = _order_sites(list(sites), [*pair, *repl], {s: sites[s].name for s in sites})
    rank = {sid: i for i, sid in enumerate(site_order)}

    links: list[Link] = []
    for key in sorted(pair, key=lambda k: (k[0], k[1])):
        links.append(pair[key])
    for key in sorted(repl):
        e = repl[key]
        dirs = sorted(e["dirs"])
        if len(dirs) > 1:
            a, b = sorted(key, key=lambda i: (rank.get(i, 10**6), i))
            both = True
        else:
            a, b = dirs[0]
            both = False
        rs = sorted(e["repl"], key=lambda r: (r[1] if r[1] is not None else 10**12, r[0]))
        links.append(Link(id=f"link:replication:{a}->{b}", kind="replication", a=a, b=b,
                          both=both, repl=[Repl(t, s) for t, s in rs], policies=e["n"]))
    links.sort(key=lambda lk: (lk.kind, lk.id))

    return ConceptualModel(
        sites=[sites[s] for s in site_order],
        remotes=sorted(remotes.values(), key=lambda r: (r.name.lower(), r.id)),
        links=links)


def _pair_key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def _resolve(ref: str | None, az_names: dict[str, str | None], info: _SiteInfo,
             target_of: Callable[..., str | None]) -> str | None:
    """A policy's AZ reference (a resolved AZ name, a URL or an id) as a target id."""
    if not ref:
        return None
    if ref in az_names:
        return az_names[ref]
    return target_of(info, ref, ref if "//" in ref else None)


def _order_sites(ids: list[str], pairs: list[tuple[str, str]],
                 names: dict[str, str]) -> list[str]:
    """Sites by name, with linked sites kept together (BFS per connected component), so
    paired AZs sit side by side and land in the same part."""
    adj: dict[str, set[str]] = {i: set() for i in ids}
    for a, b in pairs:
        if a in adj and b in adj:
            adj[a].add(b)
            adj[b].add(a)
    key: Callable[[str], tuple[str, str]] = lambda i: (names[i].lower(), i)  # noqa: E731
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
