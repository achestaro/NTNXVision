"""Flow Virtual Networking Design (N2): model, facts, design summary and table rows.

Per availability zone: the external subnets (VLAN and range when the subnet was
collected, the floating IPs drawn from each), the VPCs (distributed routers: NAT or
routed prefixes, static routes, routing policies, DNS, floating IPs) and their overlay
subnets (prefix, gateway, IPAM, VMs).

Everything is scoped by Prism Central: a VPC's overlay subnets and floating IPs are
matched by (source PC, VPC name), so two PCs that both have a "vpc-prod" never share
subnets (the legacy figure merged them).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from ntnxvision_core.models import Environment

from . import fmt
from .netcommon import azs, default_host, ip_key, ip_range, natural, src_of


@dataclass
class ExtNet:
    id: str  # ext:<pc>:<name> | user:<uuid4>
    name: str
    kind: str = "external"  # external | user
    vlan: int | None = None
    prefix: str | None = None
    #: NAT on the external subnet (None: not reported).
    nat: bool | None = None
    #: Whether the subnet itself was collected (else only its name, from the VPCs).
    collected: bool = False
    fips: list[str] = field(default_factory=list)
    note: str | None = None
    data: dict[str, str] = field(default_factory=dict)


@dataclass
class Overlay:
    id: str
    name: str
    kind: str = "overlay"  # overlay | user
    prefix: str | None = None
    gateway: str | None = None
    ipam: bool | None = None
    vms: int | None = None
    note: str | None = None
    data: dict[str, str] = field(default_factory=dict)


@dataclass
class VVpc:
    id: str
    name: str
    kind: str = "vpc"  # vpc | note
    #: External subnet ids (`ExtNet.id`) this VPC uplinks to.
    externals: list[str] = field(default_factory=list)
    routed: list[str] = field(default_factory=list)
    routes: int | None = None
    policies: int | None = None
    dns: list[str] = field(default_factory=list)
    fips: int = 0
    overlays: list[Overlay] = field(default_factory=list)
    note: str | None = None
    data: dict[str, str] = field(default_factory=dict)


@dataclass
class VSite:
    id: str
    name: str
    kind: str = "az"
    externals: list[ExtNet] = field(default_factory=list)
    vpcs: list[VVpc] = field(default_factory=list)
    vpc_count: int = 0
    data: dict[str, str] = field(default_factory=dict)


@dataclass
class VpcModel:
    sites: list[VSite] = field(default_factory=list)
    title: str = "Flow virtual networking design"
    edited: bool = False


def to_doc(m: VpcModel) -> dict[str, Any]:
    return asdict(m)


def _pick(cls: Any, d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if k in cls.__dataclass_fields__}


def from_doc(d: dict[str, Any]) -> VpcModel:
    def vpc(v: dict[str, Any]) -> VVpc:
        return VVpc(**{**_pick(VVpc, v),
                       "overlays": [Overlay(**_pick(Overlay, o)) for o in v.get("overlays", [])]})

    return VpcModel(
        sites=[VSite(id=s["id"], name=s["name"], kind=s.get("kind", "az"),
                     externals=[ExtNet(**_pick(ExtNet, e)) for e in s.get("externals", [])],
                     vpcs=[vpc(v) for v in s.get("vpcs", [])],
                     vpc_count=int(s.get("vpc_count", 0)), data=dict(s.get("data", {})))
               for s in d.get("sites", [])],
        title=d.get("title", "Flow virtual networking design"),
        edited=bool(d.get("edited", False)))


# ─── facts ─────────────────────────────────────────────────────────────


def applies(env: Environment) -> bool:
    return bool(env.vpcs)


def vpc_model(env: Environment) -> VpcModel:
    default = default_host(env)

    def src(o: Any) -> str:
        return src_of(env, o, default)

    vms: dict[str, set[str]] = {}
    for vm in env.vms:
        for nic in vm.nics:
            if nic.subnet_uuid:
                vms.setdefault(nic.subnet_uuid, set()).add(vm.uuid)
    sites = []
    for az in azs(env):
        vpcs = sorted((v for v in env.vpcs if src(v) == az.name),
                      key=lambda v: (natural(v.name), v.uuid))
        if not vpcs:
            continue
        subnets = [s for s in env.subnets if src(s) == az.name]
        by_name: dict[str, Any] = {}
        for s in sorted(subnets, key=lambda s: s.uuid):
            if s.name and s.is_external:
                by_name.setdefault(s.name, s)
        for s in sorted(subnets, key=lambda s: s.uuid):  # a named subnet not flagged
            if s.name:
                by_name.setdefault(s.name, s)
        fips = [f for f in env.floating_ips if src(f) == az.name]
        ext_names = sorted({e for v in vpcs for e in v.external_subnets}
                           | {s.name for s in subnets if s.is_external and s.name
                              and not s.vpc_name}, key=natural)
        externals = []
        for name in ext_names:
            sub = by_name.get(name)
            ips = sorted((f.floating_ip for f in fips
                          if f.external_subnet == name and f.floating_ip), key=ip_key)
            externals.append(ExtNet(
                id=f"ext:{az.name}:{name}", name=name, vlan=sub.vlan_id if sub else None,
                prefix=sub.ip_prefix if sub else None, nat=sub.nat_enabled if sub else None,
                collected=sub is not None, fips=ips,
                data={"name": name, "prism_central": az.name,
                      **({"subnet_uuid": sub.uuid} if sub else {})}))
        out = []
        for v in vpcs:
            name = v.name or v.uuid[:8]
            ovs = sorted((s for s in subnets if s.vpc_name == v.name and not s.is_external),
                         key=lambda s: (natural(s.name), s.uuid))
            out.append(VVpc(
                id=f"vpc:{v.uuid}", name=name,
                externals=[f"ext:{az.name}:{e}" for e in sorted(set(v.external_subnets),
                                                                 key=natural)],
                routed=sorted(v.externally_routable_prefixes, key=ip_key),
                routes=v.route_count, policies=v.routing_policy_count,
                dns=sorted(v.dns_servers, key=ip_key),
                fips=sum(1 for f in fips if f.vpc_name == v.name),
                overlays=[Overlay(id=f"subnet:{s.uuid}", name=s.name or s.uuid[:8],
                                  prefix=s.ip_prefix, gateway=s.gateway_ip,
                                  ipam=s.dhcp_enabled, vms=len(vms.get(s.uuid, ())),
                                  data={"subnet_uuid": s.uuid, "vpc": name,
                                        "prism_central": az.name}) for s in ovs],
                data={"vpc_uuid": v.uuid, "name": name, "prism_central": az.name}))
        sites.append(VSite(id=az.id, name=az.name, kind=az.kind, externals=externals,
                           vpcs=out, vpc_count=len(out), data={"name": az.name}))
    return VpcModel(sites=sites)


# ─── text ──────────────────────────────────────────────────────────────


def mode(v: VVpc, externals: dict[str, ExtNet]) -> str:
    """"nat" or "routed": NAT on an external subnet the VPC uses when reported, else
    routed when the VPC advertises routable prefixes."""
    nats = [externals[e].nat for e in v.externals if e in externals]
    if any(n is True for n in nats):
        return "nat"
    if nats and all(n is False for n in nats):
        return "routed"
    return "routed" if v.routed else "nat"


def mode_text(v: VVpc, externals: dict[str, ExtNet]) -> str:
    if not v.externals:
        return "Not connected to an external subnet"
    if mode(v, externals) == "nat":
        return "NAT to the external subnet"
    if v.routed:
        return "No NAT, routable " + ", ".join(v.routed)
    return "No NAT, no routable prefixes reported"


def routing_text(v: VVpc) -> str:
    def part(n: int | None, one: str, many: str) -> str:
        if n is None:
            return f"{many} N/A"
        return f"no {many}" if n == 0 else fmt.plural(n, one, many)
    return (f"{part(v.routes, 'static route', 'static routes')}, "
            f"{part(v.policies, 'routing policy', 'routing policies')}")


def ext_detail(e: ExtNet) -> str:
    if e.kind == "user":
        return e.note or "Added by the author, not collected"
    if not e.collected or (e.vlan is None and not e.prefix):
        return "VLAN and IP range not collected for this subnet"
    parts = [f"VLAN {e.vlan}" if e.vlan is not None else "VLAN N/A", e.prefix or "range N/A"]
    if e.nat is not None:
        parts.append("NAT" if e.nat else "no NAT")
    return " · ".join(parts)


def fips_text(e: ExtNet) -> str | None:
    if not e.fips:
        return None
    return f"{fmt.plural(len(e.fips), 'floating IP')}: {ip_range(e.fips)}"


def site_line(s: VSite) -> str:
    vpcs = [v for v in s.vpcs if v.kind == "vpc"]
    ov = sum(len(v.overlays) for v in vpcs)
    fips = sum(len(e.fips) for e in s.externals)
    return (f"Availability zone, {fmt.plural(s.vpc_count, 'VPC')}, "
            f"{fmt.plural(ov, 'overlay subnet')}, {fmt.plural(fips, 'floating IP')}")


def bullets(s: VSite) -> list[str]:
    vpcs = [v for v in s.vpcs if v.kind == "vpc"]
    if not vpcs:
        return []
    exts = {e.id: e for e in s.externals}
    out: list[str] = []
    for e in s.externals:
        users = [v for v in vpcs if e.id in v.externals]
        if not users:
            continue
        modes = Counter(mode(v, exts) for v in users)
        if len(modes) == 1:
            how = ("all routed without NAT" if "routed" in modes else "all behind NAT") \
                if len(users) > 1 else ("routed without NAT" if "routed" in modes
                                        else "behind NAT")
        else:
            how = f"{modes['routed']:,} routed, {modes['nat']:,} behind NAT"
        pre = sorted({p for v in users for p in v.routed}, key=ip_key)
        span = f" ({pre[0]} to {pre[-1]})" if len(pre) > 1 else f" ({pre[0]})" if pre else ""
        who = (f"{fmt.plural(len(users), 'VPC')} share" if len(users) > 1
               else f"{users[0].name} uses")
        out.append(f"{who} external subnet {e.name}, {how}{span if 'routed' in modes else ''}.")
        if len(out) >= 2:
            break
    unlinked = [v.name for v in vpcs if not v.externals]
    if unlinked:
        out.append(f"{fmt.join_names(unlinked[:3])} "
                   f"{'has' if len(unlinked) == 1 else 'have'} no external subnet.")
    ovs = [o for v in vpcs for o in v.overlays]
    if ovs:
        ipam = sum(1 for o in ovs if o.ipam)
        what = ("all with Nutanix IPAM" if ipam == len(ovs) else "none with Nutanix IPAM"
                if ipam == 0 else f"{ipam:,} with Nutanix IPAM")
        out.append(f"{fmt.plural(len(ovs), 'overlay subnet')}, {what}.")
    fips = sorted((ip for e in s.externals for ip in e.fips), key=ip_key)
    if fips:
        per = Counter(v.fips for v in vpcs)
        each = (f", {per.most_common(1)[0][0]:,} per VPC" if len(per) == 1 else "")
        out.append(f"{fmt.plural(len(fips), 'floating IP')}{each} ({ip_range(fips)}).")
    busy = max(vpcs, key=lambda v: ((v.routes or 0) + (v.policies or 0), natural(v.name)))
    if (busy.routes or 0) + (busy.policies or 0) and len(vpcs) > 1:
        out.append(f"{busy.name} carries the most routing: {routing_text(busy)}.")
    dns = {tuple(v.dns) for v in vpcs}
    if len(dns) == 1 and next(iter(dns)) and len(vpcs) > 1:
        out.append(f"Every VPC uses DNS {fmt.join_names(list(next(iter(dns))))}.")
    return out[:5]


VPC_HEADERS = ["VPC", "Overlay subnets", "External subnets", "Routable prefixes", "Routes",
               "Routing policies", "Floating IPs", "DNS"]
OVERLAY_HEADERS = ["VPC", "Subnet", "IP prefix", "Gateway", "IPAM", "VMs"]


def vpc_rows(s: VSite) -> list[list[object]]:
    exts = {e.id: e.name for e in s.externals}
    return [[v.name, ", ".join(o.name for o in v.overlays),
             ", ".join(exts.get(e, e) for e in v.externals), ", ".join(v.routed),
             v.routes, v.policies, v.fips, ", ".join(v.dns)]
            for v in s.vpcs if v.kind == "vpc"]


def overlay_rows(s: VSite) -> list[list[object]]:
    return [[v.name, o.name, o.prefix, o.gateway,
             "Nutanix IPAM" if o.ipam else "No IPAM" if o.ipam is False else None, o.vms]
            for v in s.vpcs if v.kind == "vpc" for o in v.overlays]
