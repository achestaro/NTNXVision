"""Network Segment Design (N1): model, facts, design summary and table rows.

Per availability zone, per cluster, per virtual switch: the VLAN subnets (VLAN id,
prefix, gateway, Nutanix IPAM, VMs attached) of AHV clusters, and the vSphere port
groups (VLAN, VMs) of ESXi clusters. This is the L2/L3 plan; the Host Network
Configuration (A4) draws the physical side (bonds, uplinks, LLDP) and is not repeated.

Only collected data: a switch with no subnet is kept ("No subnets on this switch"),
overlay subnets belong to the Virtual Networking Design (N2), and ESXi switches and
teaming are not reported by the Nutanix APIs (decision 8), so port groups carry no IP
data. Clusters whose plans are identical (switch, VLAN, prefix and IPAM per segment)
collapse into one card that names them all.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from ntnxvision_core.models import Environment

from . import fmt
from .facts import hypervisor_label
from .netcommon import azs, default_host, natural, src_of, vlan_text

#: The name a switch takes when the API names none for a subnet.
NO_SWITCH = "Virtual switch not reported"
PG_SWITCH = "vSphere port groups"


# ─── model ─────────────────────────────────────────────────────────────


@dataclass
class Segment:
    id: str  # subnet:<uuid> | pg:<uuid> | user:<uuid4>
    name: str
    #: subnet (Nutanix VLAN subnet) | portgroup (vSphere) | user (added in the editor)
    kind: str = "subnet"
    vlan: int | None = None
    prefix: str | None = None
    gateway: str | None = None
    ipam: bool | None = None
    vms: int | None = None
    external: bool = False
    #: STANDARD / DISTRIBUTED / OPAQUE, only when vCenter reported it.
    switch_type: str | None = None
    #: Free text of a user-added segment (never parsed).
    vlan_text: str | None = None
    prefix_text: str | None = None
    note: str | None = None
    data: dict[str, str] = field(default_factory=dict)


@dataclass
class SSwitch:
    id: str  # vs:<cluster uuid>:<name> | pg:<cluster uuid>
    name: str
    kind: str = "ahv"  # ahv | portgroups
    default: bool = False
    mtu: int | None = None
    segments: list[Segment] = field(default_factory=list)
    note: str | None = None
    data: dict[str, str] = field(default_factory=dict)


@dataclass
class SCluster:
    id: str  # cluster:<uuid> | user:<uuid4>
    name: str
    #: cluster | user (a gateway or firewall added in the editor) | note
    kind: str = "cluster"
    hypervisor: str | None = None
    nodes: int | None = None
    hosts_pc: bool = False
    #: Cluster names this card stands for when identical plans collapsed (2 or more).
    applies_to: list[str] = field(default_factory=list)
    switches: list[SSwitch] = field(default_factory=list)
    note: str | None = None
    data: dict[str, str] = field(default_factory=dict)


@dataclass
class SSite:
    id: str
    name: str
    kind: str = "az"  # az | pe
    clusters: list[SCluster] = field(default_factory=list)
    #: Clusters as collected (a card removed in the editor still counts here).
    cluster_count: int = 0
    data: dict[str, str] = field(default_factory=dict)


@dataclass
class SegmentsModel:
    sites: list[SSite] = field(default_factory=list)
    title: str = "Network segment design"
    edited: bool = False


def to_doc(m: SegmentsModel) -> dict[str, Any]:
    return asdict(m)


def from_doc(d: dict[str, Any]) -> SegmentsModel:
    def seg(s: dict[str, Any]) -> Segment:
        return Segment(**{k: v for k, v in s.items() if k in _SEG_FIELDS})

    def sw(v: dict[str, Any]) -> SSwitch:
        return SSwitch(**{**{k: x for k, x in v.items() if k in _SW_FIELDS},
                          "segments": [seg(s) for s in v.get("segments", [])]})

    def cl(c: dict[str, Any]) -> SCluster:
        return SCluster(**{**{k: x for k, x in c.items() if k in _CL_FIELDS},
                           "switches": [sw(v) for v in c.get("switches", [])]})

    return SegmentsModel(
        sites=[SSite(id=s["id"], name=s["name"], kind=s.get("kind", "az"),
                     clusters=[cl(c) for c in s.get("clusters", [])],
                     cluster_count=int(s.get("cluster_count", 0)),
                     data=dict(s.get("data", {}))) for s in d.get("sites", [])],
        title=d.get("title", "Network segment design"), edited=bool(d.get("edited", False)))


_SEG_FIELDS = set(Segment.__dataclass_fields__)
_SW_FIELDS = set(SSwitch.__dataclass_fields__)
_CL_FIELDS = set(SCluster.__dataclass_fields__)


# ─── facts ─────────────────────────────────────────────────────────────


def _is_vlan(s: Any) -> bool:
    """A VLAN-backed subnet (overlay subnets and VPC subnets belong to N2)."""
    return (s.subnet_type or "VLAN").upper() != "OVERLAY" and not s.vpc_name


def applies(env: Environment) -> bool:
    return bool(env.port_groups) or any(_is_vlan(s) and (s.cluster_uuid or s.cluster_name)
                                        for s in env.subnets)


def _on(obj: Any, c: Any) -> bool:
    """`obj` belongs to cluster `c`: by uuid, or by name when the payload has no uuid
    (callers already keep to one Prism Central, so a repeated name cannot merge)."""
    if obj.cluster_uuid:
        return bool(obj.cluster_uuid == c.uuid)
    return bool(obj.cluster_name) and obj.cluster_name == c.name


def _vm_counts(env: Environment) -> dict[str, int]:
    """subnet uuid -> VMs with at least one NIC on it."""
    per: dict[str, set[str]] = {}
    for v in env.vms:
        for n in v.nics:
            if n.subnet_uuid:
                per.setdefault(n.subnet_uuid, set()).add(v.uuid)
    return {k: len(v) for k, v in per.items()}


def _seg_key(s: Segment) -> tuple[Any, ...]:
    return (s.vlan is None, s.vlan or 0, natural(s.name), s.id)


def segments_model(env: Environment) -> SegmentsModel:
    default = default_host(env)
    vms = _vm_counts(env)

    def src(o: Any) -> str:
        return src_of(env, o, default)

    sites = []
    for az in azs(env):
        clusters = []
        for c in env.clusters:
            if src(c) != az.name:
                continue
            card = _cluster(env, c, az.name, src, vms)
            card.hosts_pc = c.uuid in az.hosting
            if card.switches:
                clusters.append(card)
        clusters.sort(key=lambda k: (not k.hosts_pc, natural(k.name), k.id))
        n = len(clusters)
        clusters = _collapse(clusters)
        if clusters:
            sites.append(SSite(id=az.id, name=az.name, kind=az.kind, clusters=clusters,
                               cluster_count=n, data={"name": az.name}))
    return SegmentsModel(sites=sites)


def _cluster(env: Environment, c: Any, host: str, src: Any, vms: dict[str, int]
             ) -> SCluster:
    name = c.name or c.uuid[:8]
    hyps = [x for x in dict.fromkeys(hypervisor_label(t) for t in c.hypervisor_types) if x]
    switches: list[SSwitch] = []
    vss = sorted((v for v in env.virtual_switches
                  if _on(v, c) and src(v) == host),
                 key=lambda v: (natural(v.name), v.uuid))
    subs = [s for s in env.subnets if _on(s, c) and src(s) == host and _is_vlan(s)]
    names: dict[str, Any] = {}
    for v in vss:
        names.setdefault(v.name or NO_SWITCH, v)
    for s in subs:
        names.setdefault(s.virtual_switch or NO_SWITCH, None)
    for sw_name, v in names.items():
        segs = [Segment(id=f"subnet:{s.uuid}", name=s.name or s.uuid[:8], kind="subnet",
                        vlan=s.vlan_id, prefix=s.ip_prefix, gateway=s.gateway_ip,
                        ipam=s.dhcp_enabled, vms=vms.get(s.uuid, 0),
                        external=bool(s.is_external),
                        data={"subnet_uuid": s.uuid, "cluster": name,
                              "prism_central": host})
                for s in subs if (s.virtual_switch or NO_SWITCH) == sw_name]
        segs.sort(key=_seg_key)
        switches.append(SSwitch(
            id=f"vs:{c.uuid}:{sw_name}", name=sw_name, kind="ahv",
            default=bool(v is not None and v.is_default), mtu=v.mtu if v is not None else None,
            segments=segs, data={"cluster_uuid": c.uuid, "virtual_switch": sw_name}))
    switches.sort(key=lambda w: (not w.default, w.name == NO_SWITCH, natural(w.name)))
    pgs = [p for p in env.port_groups if _on(p, c) and src(p) == host]
    if pgs:
        segs = [Segment(id=f"pg:{p.uuid}", name=p.name or p.uuid[:8], kind="portgroup",
                        vlan=p.vlan_id, vms=p.vm_count, switch_type=p.switch_type,
                        data={"port_group_uuid": p.uuid, "cluster": name,
                              "prism_central": host})
                for p in pgs]
        segs.sort(key=_seg_key)
        switches.append(SSwitch(id=f"pg:{c.uuid}", name=PG_SWITCH, kind="portgroups",
                                segments=segs, data={"cluster_uuid": c.uuid}))
    return SCluster(id=f"cluster:{c.uuid}", name=name, hypervisor=", ".join(hyps) or None,
                    nodes=c.num_nodes, switches=switches,
                    data={"cluster_uuid": c.uuid, "name": name, "prism_central": host})


def _plan(c: SCluster) -> tuple[Any, ...]:
    return tuple((w.name, w.kind, w.default, w.mtu,
                  tuple((s.kind, s.vlan, s.prefix, s.ipam) for s in w.segments))
                 for w in c.switches)


def _collapse(clusters: list[SCluster]) -> list[SCluster]:
    """Clusters with identical plans (never the one hosting Prism Central) become one
    card naming them all; segment VM counts are per cluster, so a collapsed card
    draws none (the table lists them)."""
    groups: dict[tuple[Any, ...], list[SCluster]] = {}
    for c in clusters:
        if not c.hosts_pc:
            groups.setdefault(_plan(c), []).append(c)
    out: list[SCluster] = []
    done: set[str] = set()
    for c in clusters:
        if c.id in done:
            continue
        same = groups.get(_plan(c), []) if not c.hosts_pc else []
        if len(same) > 1 and _plan(c):
            for o in same:
                done.add(o.id)
            c.applies_to = [o.name for o in same]
            c.nodes = sum(o.nodes or 0 for o in same) or None
            for w in c.switches:
                for s in w.segments:
                    s.vms = None
        out.append(c)
    return out


# ─── text shared by the layout, summary and table ──────────────────────


def card_line(c: SCluster) -> str:
    """"AHV, 8 nodes, 5 VLAN subnets" / "ESXi, 4 nodes, 3 port groups"."""
    subnets = sum(len(w.segments) for w in c.switches if w.kind == "ahv")
    pgs = sum(len(w.segments) for w in c.switches if w.kind == "portgroups")
    parts = [c.hypervisor or "Hypervisor N/A"]
    if c.nodes:
        parts.append(fmt.plural(c.nodes, "node") + (" in total" if c.applies_to else ""))
    if subnets or not pgs:
        parts.append(fmt.plural(subnets, "VLAN subnet") + (" each" if c.applies_to else ""))
    if pgs:
        parts.append(fmt.plural(pgs, "port group") + (" each" if c.applies_to else ""))
    return ", ".join(parts)


def applies_text(names: list[str]) -> str:
    if len(names) > 3:
        return f"Applies to {names[0]} to {names[-1]} ({len(names):,} clusters)"
    return f"Applies to {fmt.join_names(names)}"


def segment_detail(s: Segment) -> str:
    if s.kind == "user":
        return " · ".join(t for t in (s.prefix_text, s.note) if t) or "Added by the author"
    if s.kind == "portgroup":
        return "VM count only (no IP data)"
    from .netcommon import short_gw

    return " · ".join(p for p in (s.prefix or "IP range N/A", short_gw(s.prefix, s.gateway))
                      if p)


def segment_vlan(s: Segment) -> str:
    if s.kind == "user":
        return (s.vlan_text or "VLAN N/A")[:24]
    return vlan_text(s.vlan)


def site_line(s: SSite) -> str:
    cl = [c for c in s.clusters if c.kind == "cluster"]
    subnets = sum(len(w.segments) for c in cl for w in c.switches if w.kind == "ahv")
    pgs = sum(len(w.segments) for c in cl for w in c.switches if w.kind == "portgroups")
    what = "Prism Element" if s.kind == "pe" else "Availability zone"
    parts = [what, fmt.plural(s.cluster_count, "cluster"), fmt.plural(subnets, "VLAN subnet")]
    if pgs:
        parts.append(fmt.plural(pgs, "vSphere port group"))
    return ", ".join(parts)


def bullets(s: SSite) -> list[str]:
    """2 to 5 design-summary bullets for one AZ (from the generated model)."""
    cl = [c for c in s.clusters if c.kind == "cluster"]
    out: list[str] = []
    if not cl:
        return out
    subs = [(c, w, g) for c in cl for w in c.switches if w.kind == "ahv" for g in w.segments]
    pgs = [(c, w, g) for c in cl for w in c.switches if w.kind == "portgroups"
           for g in w.segments]
    ahv = {c.id for c, _, _ in subs} | {c.id for c in cl if any(w.kind == "ahv"
                                                               for w in c.switches)}
    # External subnets (the uplinks of Flow virtual networking) are counted apart.
    ext = sum(1 for _, _, g in subs if g.external)
    counted = fmt.plural(len(subs) - ext, "VLAN subnet") + \
        (f" and {ext:,} external" if ext else "")
    head = [f"{counted} on {fmt.plural(len(ahv), 'AHV cluster')}"] if ahv else []
    if pgs:
        esx = list(dict.fromkeys(c.name for c, _, _ in pgs))
        where = fmt.join_names(esx) if len(esx) <= 2 else fmt.plural(len(esx), "ESXi cluster")
        head.append(f"{fmt.plural(len(pgs), 'vSphere port group')} on {where}")
    out.append(f"{fmt.plural(s.cluster_count, 'cluster')}: {', '.join(head)}.")
    collapsed = [c for c in cl if c.applies_to]
    if collapsed:
        c = collapsed[0]
        out.append(f"{fmt.plural(len(c.applies_to), 'cluster')} share one segment plan "
                   f"({c.applies_to[0]} to {c.applies_to[-1]}), drawn once.")
    # Where the guest subnets sit.
    by_sw: dict[str, int] = Counter()
    mtus: dict[str, int | None] = {}
    for c in cl:
        for w in c.switches:
            if w.kind == "ahv":
                by_sw[w.name] += len(w.segments)
                mtus.setdefault(w.name, w.mtu)
    if by_sw:
        def mtu(n: str) -> str:
            return f" (MTU {mtus[n]:,})" if mtus.get(n) else ""
        used = [n for n in sorted(by_sw, key=natural) if by_sw[n]]
        empty = [n for n in sorted(by_sw, key=natural) if not by_sw[n]]
        if len(used) == 1:
            line = f"All VLAN subnets sit on {used[0]}{mtu(used[0])}"
        elif used:
            line = "VLAN subnets sit on " + fmt.join_names([f"{n}{mtu(n)}" for n in used])
        else:
            line = "No VLAN subnet on any virtual switch"
        if empty:
            line += f"; {fmt.join_names([f'{n}{mtu(n)}' for n in empty])} " + \
                ("carries none" if len(empty) == 1 else "carry none")
        out.append(line + ".")
    if subs:
        ipam = sum(1 for _, _, g in subs if g.ipam)
        none = sum(1 for _, _, g in subs if g.ipam is False)
        out.append(f"{ipam:,} of {fmt.plural(len(subs), 'subnet')} use Nutanix IPAM"
                   + (f"; {none:,} {'has' if none == 1 else 'have'} no IPAM." if none else "."))
    conflict = vlan_conflicts(s)
    if conflict:
        v, a, b = conflict[0]
        out.append(f"{vlan_text(v)} has {a[0]} on {a[1]} and {b[0]} on {b[1]}"
                   + (f" ({len(conflict) - 1:,} more VLAN ids differ)." if len(conflict) > 1
                      else "."))
    elif subs:
        out.append("No VLAN id carries two different prefixes in this "
                   + ("cluster." if s.kind == "pe" else "AZ."))
    if pgs:
        esx = list(dict.fromkeys(c.name for c, _, _ in pgs))
        out.append(f"{fmt.join_names(esx[:2])}{' and others' if len(esx) > 2 else ''} "
                   "(ESXi): switch and teaming are not reported by Nutanix APIs; see vCenter.")
    return out[:5]


def vlan_conflicts(s: SSite) -> list[tuple[int, tuple[str, str], tuple[str, str]]]:
    """VLAN ids carrying two different prefixes across this AZ's clusters."""
    seen: dict[int, tuple[str, str]] = {}
    out: list[tuple[int, tuple[str, str], tuple[str, str]]] = []
    for c in s.clusters:
        for w in c.switches:
            for g in w.segments:
                if g.kind != "subnet" or g.vlan is None or not g.prefix:
                    continue
                first = seen.setdefault(g.vlan, (g.prefix, c.name))
                if first[0] != g.prefix and not any(x[0] == g.vlan for x in out):
                    out.append((g.vlan, first, (g.prefix, c.name)))
    return sorted(out)


HEADERS = ["Cluster", "Virtual switch", "VLAN", "Subnet", "IP prefix", "Gateway", "IPAM",
           "VMs"]


def table_rows(env: Environment, s: SSite) -> list[list[object]]:
    """Every segment of the AZ as collected (collapsed cards expanded, port groups
    with no IP data), in the figure's order."""
    model = segments_model_uncollapsed(env, s.id)
    rows: list[list[object]] = []
    for c in model:
        for w in c.switches:
            for g in w.segments:
                rows.append([c.name, w.name if w.kind == "ahv" else "Port group",
                             str(g.vlan) if g.vlan else ("Untagged" if g.vlan == 0 else None),
                             g.name + (" (external)" if g.external else ""),
                             g.prefix, g.gateway,
                             None if g.kind == "portgroup" else
                             ("Nutanix IPAM" if g.ipam else "No IPAM" if g.ipam is False
                              else None), g.vms])
    return rows


def segments_model_uncollapsed(env: Environment, site_id: str) -> list[SCluster]:
    default = default_host(env)
    vms = _vm_counts(env)
    for az in azs(env):
        if az.id != site_id:
            continue
        out: list[SCluster] = []
        for c in env.clusters:
            if src_of(env, c, default) != az.name:
                continue
            card = _cluster(env, c, az.name, lambda o: src_of(env, o, default), vms)
            card.hosts_pc = c.uuid in az.hosting
            if card.switches:
                out.append(card)
        out.sort(key=lambda k: (not k.hosts_pc, natural(k.name), k.id))
        return out
    return []
