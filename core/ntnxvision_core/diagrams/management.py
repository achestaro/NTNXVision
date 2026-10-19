"""Management plane design (A3): model, facts, design summary and table rows.

One column per Prism Central: its deployment (single VM or scale-out, size, per-PCVM
sizing, addresses), the cluster(s) hosting its VMs, the Prism Element clusters registered
with it (with the vCenter Server of ESXi clusters), and the AZ pairings between Prism
Centrals, in this inventory or outside it.

Pairing is resolved exactly as the conceptual overview resolves it (`facts.conceptual_model`):
the same sites, remote AZs, ids and connection states. Only collected values are used;
a value the snapshot lacks is left out of the figure and reads "N/A" in the tables.

Inferences, made visible:

* a cluster "hosts PC" when the PC record or the PC VMs name it (as in the overview);
* a cluster is a dedicated management cluster ("MGMT") only when it hosts the PC and
  runs no user VM at all: user VMs cannot be told apart as management VMs reliably.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any

from ntnxvision_core.models import Environment
from ntnxvision_core.versions import short_version

from . import fmt
from .facts import _sites, conceptual_model, hypervisor_label, pc_size_label, pc_version, short_name
from .model import dumps, envelope, open_envelope

GIB = 1024**3

#: PC products (v4 enum) as the short labels the PC block lists.
PRODUCT_SHORT = {
    "NUTANIX_DISASTER_RECOVERY": "DR",
    "FLOW_NETWORK_SECURITY": "Flow Security",
    "FLOW_VIRTUAL_NETWORKING": "Flow Networking",
    "FLOW_CONTROLLER": "Flow Controller",
    "INTELLIGENT_OPERATIONS": "Intelligent Ops",
    "NUTANIX_CLOUD_MANAGER": "NCM",
    "SELF_SERVICE": "Self-Service",
    "NUTANIX_MARKETPLACE": "Marketplace",
}
_PRODUCT_ORDER = list(PRODUCT_SHORT)


@dataclass
class MgmtCluster:
    """A Prism Element cluster registered with the PC."""

    id: str
    name: str
    hypervisor: str | None = None
    pe_ip: str | None = None
    dsip: str | None = None
    aos: str | None = None
    vcenter: str | None = None
    hosts_pc: bool = False
    #: Hosts the PC and runs no user VM: a dedicated management cluster.
    dedicated: bool = False
    user_vms: int | None = None
    #: CONNECTED / DISCONNECTED / PENDING as registered, None when not reported.
    connectivity: str | None = None
    data: dict[str, str] = field(default_factory=dict)

    @property
    def connected(self) -> bool:
        return self.connectivity is None or self.connectivity.upper() == "CONNECTED"


@dataclass
class MgmtPc:
    id: str
    version: str | None = None
    size: str | None = None
    vms: int = 1
    vm_vcpus: int | None = None
    vm_mem_gib: int | None = None
    #: Totals across the PC VMs (older snapshots without per-VM sizing).
    vcpus: int | None = None
    mem_gib: int | None = None
    virtual_ip: str | None = None
    #: PC VM addresses (without the virtual IP).
    ips: list[str] = field(default_factory=list)
    #: Names of the hosting cluster(s) (drawn or not).
    hosting: list[str] = field(default_factory=list)
    products: list[str] = field(default_factory=list)
    ncc: str | None = None
    lcm: str | None = None


@dataclass
class MgmtSite:
    id: str
    name: str
    pc: MgmtPc | None = None
    clusters: list[MgmtCluster] = field(default_factory=list)
    #: Registered Prism Element clusters (registrations, else the collected clusters).
    registered: int = 0
    data: dict[str, str] = field(default_factory=dict)


@dataclass
class MgmtRemote:
    id: str
    name: str
    kind: str = "pc"  # pc | xi
    region: str | None = None
    data: dict[str, str] = field(default_factory=dict)


@dataclass
class MgmtLink:
    """An AZ pairing between two sites, or a site and a remote AZ (`b`)."""

    id: str
    a: str
    b: str
    connected: bool = True
    state: str = ""

    @property
    def state_text(self) -> str:
        return "connected" if self.connected else (self.state or "not connected")


@dataclass
class ManagementModel:
    sites: list[MgmtSite] = field(default_factory=list)
    remotes: list[MgmtRemote] = field(default_factory=list)
    links: list[MgmtLink] = field(default_factory=list)
    title: str = "Management plane design"

    def site(self, ident: str) -> MgmtSite | None:
        return next((s for s in self.sites if s.id == ident), None)

    def remote(self, ident: str) -> MgmtRemote | None:
        return next((r for r in self.remotes if r.id == ident), None)

    def name_of(self, ident: str) -> str:
        s = self.site(ident)
        if s:
            return s.name
        r = self.remote(ident)
        return r.name if r else ident

    def partners(self, ident: str) -> list[MgmtLink]:
        return [lk for lk in self.links if ident in (lk.a, lk.b)]


# ─── documents ─────────────────────────────────────────────────────────


def to_doc(m: ManagementModel) -> dict[str, Any]:
    return asdict(m)


def from_doc(d: dict[str, Any]) -> ManagementModel:
    def site(s: dict[str, Any]) -> MgmtSite:
        pc = s.get("pc")
        return MgmtSite(id=s["id"], name=s["name"], pc=MgmtPc(**pc) if pc else None,
                        clusters=[MgmtCluster(**c) for c in s.get("clusters", [])],
                        registered=s.get("registered", 0), data=dict(s.get("data", {})))

    return ManagementModel(
        sites=[site(s) for s in d.get("sites", [])],
        remotes=[MgmtRemote(**r) for r in d.get("remotes", [])],
        links=[MgmtLink(**lk) for lk in d.get("links", [])],
        title=d.get("title", "Management plane design"))


def model_json(m: ManagementModel) -> bytes:
    return dumps(envelope("management", to_doc(m)))


def model_from_json(data: bytes) -> ManagementModel:
    import json

    type_id, payload = open_envelope(json.loads(data))
    if type_id != "management":
        raise ValueError(f"Not a management plane model: {type_id!r}")
    return from_doc(payload)


def model_hash(m: ManagementModel) -> str:
    return hashlib.sha256(model_json(m)).hexdigest()


# ─── facts ─────────────────────────────────────────────────────────────


def _version(value: str | None) -> str | None:
    v = short_version(value)
    if not v:
        return None
    for p in ("ncc-", "LM.", "lcm-"):
        if v.startswith(p):
            v = v[len(p):]
    return v or None


def management_model(env: Environment) -> ManagementModel:
    """The management plane model for `env` (one site per Prism Central)."""
    concept = conceptual_model(env)
    infos = {i.id: i for i in _sites(env)}
    default = next(iter(infos.values())).host if infos else ""

    def src(obj: Any) -> str:
        return (getattr(obj, "source_pc", None) or default) if env.is_combined else default

    user_vms: dict[str, int] = {}
    for vm in env.vms:
        if vm.cluster_uuid:
            user_vms[vm.cluster_uuid] = user_vms.get(vm.cluster_uuid, 0) + 1
    vms_known = bool(env.vms) or "vms" in env.collection_notes

    sites: list[MgmtSite] = []
    for cs in concept.sites:
        info = infos.get(cs.id)
        if info is None:  # pragma: no cover - conceptual sites come from _sites
            continue
        host = info.host
        regs = {r.remote_uuid: r for r in env.pc_registrations
                if src(r) == host and not r.is_remote_pc}
        hosting_ids = {c.id.removeprefix("cluster:") for c in cs.cards if c.hosts_pc}
        clusters = []
        for c in env.clusters:
            if src(c) != host:
                continue
            name = c.name or c.uuid[:8]
            hyps = [h for h in dict.fromkeys(hypervisor_label(t) for t in c.hypervisor_types)
                    if h]
            hosts_pc = c.uuid in hosting_ids
            n_user = user_vms.get(c.uuid, 0) if vms_known else None
            reg = regs.get(c.uuid)
            clusters.append(MgmtCluster(
                id=f"cluster:{c.uuid}", name=name, hypervisor=", ".join(hyps) or None,
                pe_ip=c.external_ip, dsip=c.data_services_ip, aos=short_version(c.aos_version),
                vcenter=c.vcenter_address, hosts_pc=hosts_pc,
                dedicated=hosts_pc and n_user == 0, user_vms=n_user,
                connectivity=(reg.connectivity_status.upper()
                              if reg and reg.connectivity_status else None),
                data={"cluster_uuid": c.uuid, "name": name, "prism_central": host}))
        clusters.sort(key=lambda k: (not k.hosts_pc, k.name.lower(), k.id))
        pc = None
        if cs.pc is not None:
            pc = _pc(env, cs.pc.id, info, src, clusters)
        registered = len(regs) or len(clusters)
        sites.append(MgmtSite(id=cs.id, name=cs.name, pc=pc, clusters=clusters,
                              registered=registered,
                              data={"prism_central": host, "name": host}))
    remotes = [MgmtRemote(id=r.id, name=r.name, kind=r.kind, region=r.region,
                          data=dict(r.data)) for r in concept.remotes]
    links = [MgmtLink(id=lk.id, a=lk.a, b=lk.b, connected=lk.connected, state=lk.state)
             for lk in concept.links if lk.kind == "pairing"]
    used = {x for lk in links for x in (lk.a, lk.b)}
    remotes = [r for r in remotes if r.id in used]
    return ManagementModel(sites=sites, remotes=remotes, links=links)


def _pc(env: Environment, pc_id: str, info: Any, src: Any,
        clusters: list[MgmtCluster]) -> MgmtPc:
    host, cfg = info.host, info.cfg
    vms = [v for v in env.pc_vms if src(v) == host]
    n = cfg.node_count or len(vms) or 1
    vm_vcpus = cfg.vm_vcpus or (vms[0].num_vcpus if vms and len(
        {v.num_vcpus for v in vms}) == 1 else None)
    vm_mem = cfg.vm_memory_bytes or (vms[0].memory_bytes if vms and len(
        {v.memory_bytes for v in vms}) == 1 else None)
    ips = sorted({ip for v in vms for ip in v.ip_addresses}) or \
        [ip for ip in cfg.ip_addresses if ip != cfg.virtual_ip]
    hosting = [c.name for c in clusters if c.hosts_pc]
    if not hosting:
        hosting = sorted({v.hosting_cluster_name for v in vms if v.hosting_cluster_name}) or \
            ([cfg.hosting_cluster_name] if cfg.hosting_cluster_name else [])
    enabled = set(cfg.enabled_products)
    products = [PRODUCT_SHORT[p] for p in _PRODUCT_ORDER if p in enabled] + \
        sorted(p.replace("_", " ").title() for p in enabled if p not in PRODUCT_SHORT)
    lcm = None
    for e in env.lcm_entities:
        if src(e) == host and (e.name or "").lower() == "lcm framework" and e.current_version:
            lcm = _version(e.current_version)
            break
    return MgmtPc(
        id=pc_id, version=pc_version(info.pc.version or cfg.version),
        size=pc_size_label(cfg.size), vms=n, vm_vcpus=vm_vcpus,
        vm_mem_gib=round(vm_mem / GIB) if vm_mem else None,
        vcpus=cfg.num_vcpus, mem_gib=round(cfg.memory_bytes / GIB) if cfg.memory_bytes else None,
        virtual_ip=cfg.virtual_ip if n > 1 else None, ips=ips, hosting=hosting,
        products=products, ncc=_version(cfg.ncc_version), lcm=lcm)


# ─── text shared by the figure, the summary and the tables ─────────────


def deployment(pc: MgmtPc | None) -> str:
    if pc is None:
        return "N/A"
    shape = "Single VM" if pc.vms <= 1 else "Scale-out"
    if pc.vms > 1:
        return f"{shape}, {pc.size}: {pc.vms:,} PCVMs" if pc.size else \
            f"{shape}: {pc.vms:,} PCVMs"
    return f"{shape}, {pc.size}" if pc.size else shape


def per_pcvm(pc: MgmtPc) -> str | None:
    """"10 vCPU, 44 GiB" for one PC VM; totals on older snapshots ("30 vCPU, 132 GiB
    total")."""
    if pc.vm_vcpus or pc.vm_mem_gib:
        parts = [f"{pc.vm_vcpus:,} vCPU" if pc.vm_vcpus else None,
                 f"{pc.vm_mem_gib:,} GiB" if pc.vm_mem_gib else None]
        return ", ".join(p for p in parts if p)
    if pc.vcpus or pc.mem_gib:
        parts = [f"{pc.vcpus:,} vCPU" if pc.vcpus else None,
                 f"{pc.mem_gib:,} GiB" if pc.mem_gib else None]
        return ", ".join(p for p in parts if p) + (" total" if pc.vms > 1 else "")
    return None


def addresses(pc: MgmtPc) -> str | None:
    ips = list(pc.ips)
    out = ", ".join(ips)
    if pc.virtual_ip:
        out = f"{out}, VIP {pc.virtual_ip}" if out else f"VIP {pc.virtual_ip}"
    return out or None


def cluster_detail(c: MgmtCluster) -> str:
    parts = [f"PE {c.pe_ip}" if c.pe_ip else None, f"DSIP {c.dsip}" if c.dsip else None,
             f"AOS {c.aos}" if c.aos else None]
    return " · ".join(p for p in parts if p) or "Addresses N/A"


def _short(model: ManagementModel, ident: str) -> str:
    s = model.site(ident)
    return short_name(s.name) if s else model.name_of(ident)


def bullets(model: ManagementModel) -> list[str]:
    out: list[str] = []
    pcs = [s for s in model.sites if s.pc]
    if not pcs:
        return out
    if len(pcs) == 1:
        s = pcs[0]
        assert s.pc is not None
        out.append(f"1 Prism Central ({short_name(s.name)}): {deployment(s.pc)}"
                   + (f", {s.pc.version}" if s.pc.version else "") + ".")
    else:
        groups: dict[str, list[str]] = {}
        for s in pcs:
            groups.setdefault(deployment(s.pc), []).append(short_name(s.name))
        parts = []
        for dep, names in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            parts.append(f"{dep} ({fmt.join_names(names)})" if len(names) <= 3
                         else f"{dep} ({len(names):,})")
        out.append(f"{len(pcs):,} Prism Centrals: {'; '.join(parts)}.")

    hosted = []
    for s in pcs:
        assert s.pc is not None
        if s.pc.hosting:
            hosted.append((short_name(s.name), fmt.join_names(s.pc.hosting)))
    if hosted:
        if len(hosted) == 1:
            out.append(f"{hosted[0][0]} runs on {hosted[0][1]}.")
        elif len(hosted) <= 3:
            out.append("PC VMs run on " + "; ".join(f"{h} for {n}" for n, h in hosted) + ".")
        else:
            out.append(f"Each of the {len(hosted):,} Prism Centrals runs on a cluster of its "
                       "own availability zone.")
    dedicated = [c.name for s in model.sites for c in s.clusters if c.dedicated]
    hosts = [c for s in model.sites for c in s.clusters if c.hosts_pc]
    if dedicated:
        out.append(f"Dedicated management cluster{'s' if len(dedicated) > 1 else ''} "
                   f"(PC VMs, no user VMs): {fmt.join_names(dedicated[:4])}"
                   + (f" and {len(dedicated) - 4:,} more" if len(dedicated) > 4 else "") + ".")
    elif hosts and all(c.user_vms is not None for c in hosts):
        out.append("No dedicated management cluster: every cluster hosting Prism Central "
                   "also runs user VMs.")

    out.extend(_pairing_bullets(model))

    vc = [c for s in model.sites for c in s.clusters if c.vcenter]
    if vc:
        if len(vc) <= 2:
            out.append(" ".join(f"{c.name} ({c.hypervisor or 'ESXi'}) is managed by vCenter "
                                f"{c.vcenter}." for c in vc))
        else:
            out.append(f"{len(vc):,} ESXi clusters are managed by vCenter "
                       f"({len({c.vcenter for c in vc}):,} vCenter Servers).")
    total = sum(s.registered for s in model.sites)
    down_c = [c.name for s in model.sites for c in s.clusters if not c.connected]
    out.append(f"{fmt.plural(total, 'Prism Element cluster')} registered"
               + (f"; not connected: {fmt.join_names(down_c[:3])}." if down_c
                  else (", all connected." if any(c.connectivity for s in model.sites
                                                  for c in s.clusters) else ".")))
    return out[:6]


def _pairing_bullets(model: ManagementModel) -> list[str]:
    """Pairings between zones of this inventory ("pc01 and pc02 are paired"), then
    pairings with zones outside it, grouped by the zones that share them ("pc01, pc02
    and pc03 pair with pc-dr and Xi-US-East, outside this inventory"). States other than
    connected are named."""
    if not model.links:
        return []
    parts = []
    for lk in model.links:
        if model.remote(lk.b) is None:
            state = "" if lk.connected else f" ({lk.state_text})"
            parts.append(f"{_short(model, lk.a)} and {_short(model, lk.b)} are paired{state}")
    by_sources: dict[tuple[tuple[str, ...], str], list[str]] = {}
    for r in model.remotes:
        lks = [lk for lk in model.links if r.id in (lk.a, lk.b)]
        srcs = tuple(_short(model, lk.a if lk.b == r.id else lk.b) for lk in lks)
        states = sorted({lk.state_text for lk in lks if not lk.connected})
        if srcs:
            by_sources.setdefault((srcs, " or ".join(states)), []).append(r.name)
    for (srcs, state), remotes in by_sources.items():
        who = fmt.join_names(list(srcs)) if len(srcs) <= 3 else \
            f"{len(srcs):,} Prism Centrals"
        verb = "pairs" if len(srcs) == 1 else "pair"
        parts.append(f"{who} {verb} with {fmt.join_names(remotes)}, outside this inventory"
                     + (f" ({state})" if state else ""))
    down = [lk for lk in model.links if not lk.connected]
    if len(parts) > 3:
        text = (f"{len(model.links):,} AZ pairings, "
                + (f"{len(down):,} not connected" if down else "all connected") + ".")
        return [text]
    return ["; ".join(parts) + "."]


PC_HEADERS = ["Prism Central", "Version", "Deployment", "PCVM IP addresses", "Per PCVM",
              "Hosting cluster", "Clusters", "Paired with"]


def pc_rows(model: ManagementModel) -> list[list[object]]:
    rows: list[list[object]] = []
    for s in model.sites:
        if s.pc is None:
            continue
        partners = []
        for lk in model.partners(s.id):
            other = lk.b if lk.a == s.id else lk.a
            partners.append(model.name_of(other) + (" (remote)" if model.remote(other) else "")
                            + ("" if lk.connected else f" ({lk.state_text})"))
        rows.append([s.name, s.pc.version, deployment(s.pc), addresses(s.pc), per_pcvm(s.pc),
                     ", ".join(s.pc.hosting) or None, s.registered, ", ".join(partners) or None])
    return rows


CLUSTER_HEADERS = ["Cluster", "PE IP", "DSIP", "AOS", "Hypervisor", "vCenter", "Connectivity",
                   "In figure"]


def cluster_rows(model: ManagementModel, drawn: dict[str, str]
                 ) -> tuple[list[list[object]], list[str]]:
    """Registered clusters, and each row's Prism Central (for attribution). `drawn` maps
    cluster ids to the figure that draws them."""
    rows: list[list[object]] = []
    srcs: list[str] = []
    for s in model.sites:
        for c in s.clusters:
            rows.append([c.name, c.pe_ip, c.dsip, c.aos, c.hypervisor, c.vcenter,
                         (c.connectivity or "").capitalize() or None,
                         drawn.get(c.id, "Summary card")])
            srcs.append(s.name)
    return rows, srcs
