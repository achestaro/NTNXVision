"""Disaster recovery design (A5): model, facts, design summary and policy table rows.

The model draws protection at cluster level: every replication rule of every protection
policy becomes a flow from each cluster of its source location to each cluster of its
target location, per direction, with the type derived from the RPO (P2's rule,
`replication_type_for_rpo`: 0 Sync, up to 15 min NearSync, else Async). A location
without cluster data (v3 policies, older snapshots) is drawn at availability-zone level.
A location in an availability zone outside this inventory ends on that zone (its
clusters are counted, and named only when the API gave a name, never a uuid).

Ids come from object identity: ``site:<pc host>``, ``cluster:<uuid>``, ``az:<name>``,
``flow:<a>-><b>``, ``plans:<a>|<b>``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from ntnxvision_core.models import Environment
from ntnxvision_core.models.environment import replication_label, replication_type_for_rpo

from . import fmt
from .facts import (
    _order_sites,
    _Remotes,
    _SiteInfo,
    _sites,
    _url_host,
    hypervisor_label,
    short_name,
)
from .model import Repl

TITLE = "Disaster recovery design"
_UUID = re.compile(r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$", re.I)


# ─── model ─────────────────────────────────────────────────────────────


@dataclass
class DrCluster:
    id: str
    name: str
    hypervisor: str | None = None
    nodes: int | None = None
    rf: int | None = None
    nearsync: bool | None = None
    #: True: in a protection policy; False: in none; None: not known (a policy of this
    #: zone names no clusters, so a cluster may be protected through categories).
    protected: bool | None = None
    data: dict[str, str] = field(default_factory=dict)


@dataclass
class DrSite:
    id: str
    name: str
    clusters: list[DrCluster] = field(default_factory=list)
    data: dict[str, str] = field(default_factory=dict)


@dataclass
class DrRemote:
    """An availability zone outside this inventory that policies replicate with."""

    id: str
    name: str
    kind: str = "pc"  # pc | xi
    region: str | None = None
    #: Distinct clusters the policies name there, and those of them with a known name.
    clusters: int = 0
    cluster_names: list[str] = field(default_factory=list)
    data: dict[str, str] = field(default_factory=dict)


@dataclass
class DrFlow:
    """Replication between two endpoints (a cluster id, else a site or remote id):
    `forward` a to b, `backward` b to a, per (type, RPO)."""

    id: str
    a: str
    b: str
    site_a: str
    site_b: str
    forward: list[Repl] = field(default_factory=list)
    backward: list[Repl] = field(default_factory=list)
    policies: list[str] = field(default_factory=list)

    @property
    def both(self) -> bool:
        return bool(self.forward) and bool(self.backward)

    @property
    def repl(self) -> list[Repl]:
        seen = {(r.type, r.rpo_seconds): r for r in [*self.forward, *self.backward]}
        return sorted(seen.values(), key=_rk)


@dataclass
class DrPlans:
    """Recovery plans between two zones (`a` == `b`: inside one zone)."""

    id: str
    a: str
    b: str
    count: int = 0
    witness: bool = False


@dataclass
class DrModel:
    sites: list[DrSite] = field(default_factory=list)
    remotes: list[DrRemote] = field(default_factory=list)
    flows: list[DrFlow] = field(default_factory=list)
    plans: list[DrPlans] = field(default_factory=list)
    policies: int = 0
    recovery_plans: int = 0
    title: str = TITLE

    def site(self, ident: str) -> DrSite | None:
        return next((s for s in self.sites if s.id == ident), None)

    def remote(self, ident: str) -> DrRemote | None:
        return next((r for r in self.remotes if r.id == ident), None)

    def cluster(self, ident: str) -> tuple[DrSite, DrCluster] | None:
        for s in self.sites:
            for c in s.clusters:
                if c.id == ident:
                    return s, c
        return None

    def name_of(self, ident: str) -> str:
        """Display name of an endpoint (cluster, site short name, remote name)."""
        found = self.cluster(ident)
        if found:
            return found[1].name
        s = self.site(ident)
        if s:
            return short_name(s.name)
        r = self.remote(ident)
        return r.name if r else ident

    def plans_between(self, x: str, y: str) -> DrPlans | None:
        return next((p for p in self.plans if {p.a, p.b} == {x, y}), None)


def to_doc(m: DrModel) -> dict[str, Any]:
    return asdict(m)


def from_doc(d: dict[str, Any]) -> DrModel:
    return DrModel(
        sites=[DrSite(id=s["id"], name=s["name"],
                      clusters=[DrCluster(**c) for c in s.get("clusters", [])],
                      data=dict(s.get("data", {}))) for s in d.get("sites", [])],
        remotes=[DrRemote(**r) for r in d.get("remotes", [])],
        flows=[DrFlow(**{**f, "forward": [Repl(**r) for r in f.get("forward", [])],
                         "backward": [Repl(**r) for r in f.get("backward", [])]})
               for f in d.get("flows", [])],
        plans=[DrPlans(**p) for p in d.get("plans", [])],
        policies=int(d.get("policies", 0)), recovery_plans=int(d.get("recovery_plans", 0)),
        title=d.get("title", TITLE))


# ─── facts ─────────────────────────────────────────────────────────────


def _rk(r: Repl) -> tuple[int, str]:
    return (r.rpo_seconds if r.rpo_seconds is not None else 10**12, r.type)


def _repl(replication_type: str | None, rpo: int | None) -> Repl:
    t = replication_label(replication_type or replication_type_for_rpo(rpo)) or "Async"
    return Repl(t, rpo)


def has_targets(env: Environment) -> bool:
    """A protection policy replicates somewhere (the diagram has something to draw)."""
    for p in env.protection_policies:
        if p.replications and p.locations:
            labels = {loc.label for loc in p.locations}
            if any(r.source_label in labels and r.target_label in labels
                   and r.source_label != r.target_label for r in p.replications):
                return True
        elif p.target_az:
            return True
    return False


def applies(env: Environment) -> bool:
    return env.source_type != "prism_element" and bool(env.protection_policies) \
        and has_targets(env)


class _Resolver:
    """Availability-zone references of one site's objects -> site or remote ids (the
    conceptual overview's rules: "Local (pc)" and the site's own uuid or address are the
    site itself; another collected PC by uuid, address or name; else a remote AZ)."""

    def __init__(self, env: Environment, info: _SiteInfo, infos: list[_SiteInfo],
                 remotes: _Remotes, mine: Any) -> None:
        self.info, self.infos, self.remotes = info, infos, remotes
        self.az: dict[str, tuple[str, str | None, str, str | None]] = {}
        for az in sorted(mine(env.availability_zones, info.host),
                         key=lambda a: ((a.name or "").lower(), a.url or "")):
            if (az.az_type or "").upper() == "LOCAL":
                continue
            kind = "xi" if (az.az_type or "").upper() == "XI" else "pc"
            for ident in (az.name, az.url, az.uuid):
                if ident:
                    self.az[ident] = (az.name or ident, az.url, kind, az.region)

    def __call__(self, name: str | None, ref: str | None) -> str:
        info = self.info
        if not (name or ref):
            return info.id
        if (name or "").lower().startswith("local") or info.matches(ref):
            return info.id
        for other in self.infos:
            if other is not info and other.matches(ref, name):
                return other.id
        if info.matches(name):
            return info.id
        kind, region = "pc", None
        for ident in (ref, name):
            if ident and ident in self.az:
                az_name, url, kind, region = self.az[ident]
                for other in self.infos:
                    if other is not info and other.matches(url, az_name):
                        return other.id
                name = az_name
                break
        return self.remotes.get(name or _url_host(ref) or "Availability zone", kind, region)


def dr_model(env: Environment) -> DrModel:
    """The disaster recovery design model for `env`."""
    infos = _sites(env)
    default = infos[0].host

    def src(obj: Any) -> str:
        return (getattr(obj, "source_pc", None) or default) if env.is_combined else default

    def mine(objs: list[Any], host: str) -> list[Any]:
        return [o for o in objs if src(o) == host]

    remotes = _Remotes()
    resolvers = {i.host: _Resolver(env, i, infos, remotes, mine) for i in infos}
    site_of: dict[str, str] = {}
    clusters: dict[str, Any] = {}
    for c in env.clusters:
        site_of[c.uuid] = f"site:{src(c)}"
        clusters[c.uuid] = c
    by_name: dict[tuple[str, str], str] = {(site_of[c.uuid], c.name or ""): c.uuid
                                           for c in env.clusters}

    flows: dict[tuple[str, str], dict[str, Any]] = {}
    referenced: set[str] = set()
    zone_level: set[str] = set()  # sites with a policy naming no clusters
    remote_refs: dict[str, set[str]] = {}
    remote_names: dict[str, set[str]] = {}

    def endpoints(site: str, uuids: list[str], names: list[str]) -> list[str]:
        if site.startswith("az:"):
            refs = remote_refs.setdefault(site, set())
            for i, u in enumerate(uuids):
                refs.add(u)
                nm = names[i] if i < len(names) else None
                if nm and not _UUID.match(nm) and nm != u:
                    remote_names.setdefault(site, set()).add(nm)
            if not uuids:
                for nm in names:
                    if nm and not _UUID.match(nm):
                        refs.add(nm)
                        remote_names.setdefault(site, set()).add(nm)
            return [site]
        found = [u for u in uuids if u in clusters and site_of[u] == site]
        found += [by_name[(site, n)] for n in names if (site, n) in by_name
                  and by_name[(site, n)] not in found]
        if not found:
            zone_level.add(site)
            return [site]
        referenced.update(found)
        return [f"cluster:{u}" for u in sorted(set(found))]

    def add(ea: str, eb: str, sa: str, sb: str, rep: Repl, policy: str) -> None:
        if ea == eb:
            return
        key = (ea, eb) if ea <= eb else (eb, ea)
        entry = flows.setdefault(key, {"dirs": {}, "sites": {ea: sa, eb: sb}, "p": set()})
        entry["dirs"].setdefault((ea, eb), set()).add((rep.type, rep.rpo_seconds))
        entry["p"].add(policy)

    n_policies = 0
    for p in env.protection_policies:
        resolve = resolvers.get(src(p))
        if resolve is None:
            continue
        n_policies += 1
        pname = p.name or p.uuid[:8]
        if p.replications and p.locations:
            locs = {loc.label: loc for loc in p.locations}
            for loc in p.locations:  # every named cluster is protected, used or not
                s = resolve(loc.az_name, loc.az_ref)
                endpoints(s, list(loc.cluster_uuids), list(loc.cluster_names))
            for r in p.replications:
                sl, tl = locs.get(r.source_label), locs.get(r.target_label)
                if sl is None or tl is None or sl is tl:
                    continue
                sa, sb = resolve(sl.az_name, sl.az_ref), resolve(tl.az_name, tl.az_ref)
                ea = endpoints(sa, list(sl.cluster_uuids), list(sl.cluster_names))
                eb = endpoints(sb, list(tl.cluster_uuids), list(tl.cluster_names))
                rep = _repl(r.replication_type, r.rpo_seconds)
                for x in ea:
                    for y in eb:
                        add(x, y, sa, sb, rep, pname)
        elif p.target_az:
            sa, sb = resolve(p.source_az, None), resolve(p.target_az, None)
            ea = endpoints(sa, [], list(p.source_clusters))
            eb = endpoints(sb, [], list(p.target_clusters))
            rep = _repl(p.replication_type, p.rpo_seconds)
            for x in ea:
                for y in eb:
                    add(x, y, sa, sb, rep, pname)

    plan_pairs: dict[tuple[str, str], dict[str, Any]] = {}
    n_plans = 0
    for rp in env.recovery_plans:
        resolve = resolvers.get(src(rp))
        if resolve is None:
            continue
        n_plans += 1
        a = resolve(rp.primary_location, None)
        b = resolve(rp.recovery_location, None) if rp.recovery_location else a
        key = (a, b) if a <= b else (b, a)
        e = plan_pairs.setdefault(key, {"n": 0, "w": False})
        e["n"] += 1
        e["w"] = e["w"] or bool(rp.witness_uuid)

    site_ids = [i.id for i in infos]
    names = {i.id: i.host for i in infos}
    pairs = [(e["sites"][a], e["sites"][b]) for (a, b), e in flows.items()]
    order = _order_sites(site_ids, pairs, names)
    rank = {sid: i for i, sid in enumerate(order)}

    sites: list[DrSite] = []
    for sid in order:
        cl = []
        for c in env.clusters:
            if site_of[c.uuid] != sid:
                continue
            prot: bool | None = c.uuid in referenced
            if not prot and sid in zone_level:
                prot = None
            hyps = [x for x in dict.fromkeys(hypervisor_label(t) for t in c.hypervisor_types)
                    if x]
            name = c.name or c.uuid[:8]
            cl.append(DrCluster(id=f"cluster:{c.uuid}", name=name,
                                hypervisor=", ".join(hyps) or None, nodes=c.num_nodes,
                                rf=c.redundancy_factor, nearsync=c.nearsync_capable,
                                protected=prot,
                                data={"cluster_uuid": c.uuid, "name": name,
                                      "prism_central": names[sid]}))
        cl.sort(key=lambda k: (k.protected is False, k.name.lower(), k.id))
        sites.append(DrSite(id=sid, name=names[sid], clusters=cl,
                            data={"prism_central": names[sid], "name": names[sid]}))

    crank = {c.id: (rank[s.id], i) for s in sites for i, c in enumerate(s.clusters)}

    def erank(ident: str, site: str) -> tuple[int, int, str]:
        if ident in crank:
            r, i = crank[ident]
            return (r, i, ident)
        return (rank.get(site, 10**6), -1, ident)

    out_flows: list[DrFlow] = []
    for (x, y), e in flows.items():
        sx, sy = e["sites"][x], e["sites"][y]
        if erank(y, sy) < erank(x, sx):
            x, y, sx, sy = y, x, sy, sx
        fwd = sorted((Repl(t, r) for t, r in e["dirs"].get((x, y), ())), key=_rk)
        bwd = sorted((Repl(t, r) for t, r in e["dirs"].get((y, x), ())), key=_rk)
        if bwd and not fwd:  # a one-way flow reads from its source
            x, y, sx, sy, fwd, bwd = y, x, sy, sx, bwd, fwd
        out_flows.append(DrFlow(id=f"flow:{x}->{y}", a=x, b=y, site_a=sx, site_b=sy,
                                forward=fwd, backward=bwd, policies=sorted(e["p"])))
    out_flows.sort(key=lambda f: (erank(f.a, f.site_a), erank(f.b, f.site_b), f.id))

    rem: list[DrRemote] = []
    for az in sorted(remotes.by_name.values(), key=lambda a: (a.name.lower(), a.id)):
        rem.append(DrRemote(id=az.id, name=az.name, kind=az.kind, region=az.region,
                            clusters=len(remote_refs.get(az.id, set())),
                            cluster_names=sorted(remote_names.get(az.id, set())),
                            data=dict(az.data)))
    plans = []
    for (a, b), e in sorted(plan_pairs.items()):
        digest = hashlib.sha256(f"{a}|{b}".encode()).hexdigest()[:8]
        plans.append(DrPlans(id=f"plans:{digest}", a=a, b=b, count=e["n"], witness=e["w"]))
    return DrModel(sites=sites, remotes=rem, flows=out_flows, plans=plans,
                   policies=n_policies, recovery_plans=n_plans)


# ─── text shared by the layout, the summary and the tables ─────────────


def flow_text(f: DrFlow) -> str:
    """"NearSync 1 min", "Async 1 h, both ways", "NearSync 1 min, back Async 1 h"."""
    fwd = {(r.type, r.rpo_seconds): r for r in f.forward}
    bwd = {(r.type, r.rpo_seconds): r for r in f.backward}
    common = [fwd[k] for k in fwd if k in bwd]
    only_f = [fwd[k] for k in fwd if k not in bwd]
    only_b = [bwd[k] for k in bwd if k not in fwd]
    if common and not (only_f or only_b):
        return ", ".join(fmt.repl_group(common)) + ", both ways"
    parts = []
    if common:
        parts.append(", ".join(fmt.repl_group(common)) + " both ways")
    if only_f:
        parts.append(", ".join(fmt.repl_group(only_f)))
    if only_b:
        parts.append("back " + ", ".join(fmt.repl_group(only_b)))
    return ", ".join(parts)


def mutual(f: DrFlow) -> bool:
    """The same schedules run both ways."""
    return {(r.type, r.rpo_seconds) for r in f.forward} == \
        {(r.type, r.rpo_seconds) for r in f.backward}


def cluster_line(c: DrCluster) -> str:
    if c.protected is False:
        return "No protection policy"
    parts = [c.hypervisor or "Hypervisor N/A"]
    if c.nodes is not None:
        parts.append(fmt.plural(c.nodes, "node"))
    if c.rf:
        parts.append(f"RF{c.rf}")
    text = ", ".join(parts)
    return text + (" · NearSync capable" if c.nearsync else "")


def protected_line(s: DrSite) -> str:
    known = [c for c in s.clusters if c.protected is not None]
    n = sum(1 for c in s.clusters if c.protected)
    if len(known) < len(s.clusters):
        return f"{fmt.plural(n, 'cluster')} named in policies, of {len(s.clusters):,}"
    return f"{n:,} of {fmt.plural(len(s.clusters), 'cluster')} protected"


def remote_cluster_line(r: DrRemote) -> str | None:
    if r.cluster_names:
        names = r.cluster_names[:2]
        more = f" +{len(r.cluster_names) - 2:,}" if len(r.cluster_names) > 2 else ""
        word = "cluster" if len(r.cluster_names) == 1 else "clusters"
        return f"{word} {', '.join(names)}{more} (remote)"
    # A bare count of unnamed remote clusters adds nothing the flows do not show.
    return None


# ─── design summary ────────────────────────────────────────────────────

MAX_BULLETS = 5


def _zones(m: DrModel) -> int:
    used = {f.site_a for f in m.flows} | {f.site_b for f in m.flows}
    return len(used) or len(m.sites)


def bullets(m: DrModel) -> list[str]:
    pols = fmt.plural(m.policies, 'protection policy', 'protection policies')
    zones = fmt.plural(_zones(m), 'availability zone')
    out = [f"{pols} and {fmt.plural(m.recovery_plans, 'recovery plan')} across {zones}."
           if m.recovery_plans else f"{pols} across {zones}; no recovery plans.",
           "Type follows the RPO: 0 Sync, up to 15 min NearSync, else Async."]
    inner = [f for f in m.flows if not f.site_b.startswith("az:")
             and not f.site_a.startswith("az:")]
    inner.sort(key=lambda f: (_rk(f.repl[0]) if f.repl else (10**12, ""), f.id))
    if inner:
        both = [f for f in inner if mutual(f)]
        one = [f for f in inner if not mutual(f)]
        texts = []
        for f in one[:2]:
            texts.append(f"{m.name_of(f.a)} to {m.name_of(f.b)}: {flow_text(f)}")
        for f in both[:max(0, 2 - len(texts))]:
            texts.append(f"{m.name_of(f.a)} and {m.name_of(f.b)} protect each other, "
                         f"{flow_text(f).removesuffix(', both ways')}")
        more = len(inner) - len(texts)
        out.append(". ".join(texts) + "." + (f" {more:,} more flows in the figure."
                                             if more > 0 else ""))
    remote_flows: dict[str, set[str]] = {}
    for f in m.flows:
        for other, site in ((f.a, f.site_b), (f.b, f.site_a)):
            if site.startswith("az:"):
                remote_flows.setdefault(site, set()).add(m.name_of(other))
    if remote_flows:
        parts = []
        for rid in sorted(remote_flows, key=lambda r: m.name_of(r).lower()):
            who = sorted(remote_flows[rid])
            src_txt = fmt.join_names(who) if len(who) <= 2 else fmt.plural(len(who),
                                                                           "cluster")
            parts.append(f"{src_txt} {'replicates' if len(who) == 1 else 'replicate'} "
                         f"to {m.name_of(rid)}")
        if len(parts) > 2:
            out.append(f"Replication to {fmt.plural(len(parts), 'availability zone')} "
                       "outside this inventory.")
        else:
            out.append("; ".join(parts) + ", outside this inventory.")
    unprotected = [c.name for s in m.sites for c in s.clusters if c.protected is False]
    if unprotected:
        if len(unprotected) <= 4:
            out.append(f"Not protected: {fmt.join_names(unprotected)}.")
        else:
            out.append(f"{fmt.plural(len(unprotected), 'cluster')} have no protection "
                       "policy.")
    return out[:MAX_BULLETS]


# ─── policy table ──────────────────────────────────────────────────────

POLICY_HEADERS = ["Policy", "Source (AZ / cluster)", "Target (AZ / cluster)", "Type", "RPO",
                  "Retention, local / remote", "Recovery plans"]


def policy_rows(env: Environment, m: DrModel) -> list[list[object]]:
    """One row per policy and replication direction; the reverse of a two-way rule is
    folded into its row as "(both ways)"."""
    infos = _sites(env)
    default = infos[0].host

    def src(obj: Any) -> str:
        return (getattr(obj, "source_pc", None) or default) if env.is_combined else default

    def mine(objs: list[Any], host: str) -> list[Any]:
        return [o for o in objs if src(o) == host]

    remotes = _Remotes()
    resolvers = {i.host: _Resolver(env, i, infos, remotes, mine) for i in infos}
    cname = {c.uuid: c.name for c in env.clusters}

    def where(site: str, uuids: list[str], names: list[str]) -> str:
        if site.startswith("az:"):
            r = next((x for x in remotes.by_name.values() if x.id == site), None)
            zone = r.name if r else site
        else:
            zone = m.name_of(site)
        known: list[str] = [n for u in uuids if (n := cname.get(u))]
        known += [n for n in names if n and not _UUID.match(n) and n not in known]
        if site.startswith("az:"):
            return f"{zone} / {', '.join(known)} (remote)" if known else f"{zone} (remote)"
        return f"{zone} / {', '.join(known)}" if known else zone

    rows: list[tuple[tuple[str, ...], list[object]]] = []
    for p in env.protection_policies:
        resolve = resolvers.get(src(p))
        if resolve is None:
            continue
        name = p.name or p.uuid[:8]
        plans = 0
        if p.replications and p.locations:
            locs = {loc.label: loc for loc in p.locations}
            pairs = {(r.source_label, r.target_label) for r in p.replications}
            done: set[tuple[str | None, str | None]] = set()
            for r in sorted(p.replications, key=lambda r: (r.source_label or "",
                                                           r.target_label or "")):
                key = (r.source_label, r.target_label)
                if key in done:
                    continue
                sl, tl = locs.get(r.source_label), locs.get(r.target_label)
                if sl is None or tl is None:
                    continue
                both = (r.target_label, r.source_label) in pairs
                done |= {key, (r.target_label, r.source_label)}
                sa, sb = resolve(sl.az_name, sl.az_ref), resolve(tl.az_name, tl.az_ref)
                plans = _plans_for(env, src(p), resolve, sa, sb, set(sl.cluster_uuids))
                rep = _repl(r.replication_type, r.rpo_seconds)
                rows.append(((name, key[0] or "", key[1] or ""), [
                    name, where(sa, list(sl.cluster_uuids), list(sl.cluster_names)),
                    where(sb, list(tl.cluster_uuids), list(tl.cluster_names))
                    + (" (both ways)" if both else ""),
                    rep.type, fmt.rpo(r.rpo_seconds) if r.rpo_seconds is not None else None,
                    _retention(r.local_retention, r.remote_retention), plans]))
        else:
            sa = resolve(p.source_az, None)
            tb: str | None = resolve(p.target_az, None) if p.target_az else None
            rep = _repl(p.replication_type, p.rpo_seconds)
            if tb:
                plans = _plans_for(env, src(p), resolve, sa, tb, set())
            rows.append(((name, "", ""), [
                name, where(sa, [], list(p.source_clusters)),
                where(tb, [], list(p.target_clusters)) if tb else None,
                rep.type if tb else None,
                fmt.rpo(p.rpo_seconds) if p.rpo_seconds is not None else None,
                _retention(p.local_retention, p.remote_retention), plans]))
    rows.sort(key=lambda kv: (kv[0][0].lower(), kv[0]))
    return [r for _, r in rows]


def _retention(local: int | None, remote: int | None) -> str | None:
    if local is None and remote is None:
        return None
    a = f"{local:,}" if local is not None else "N/A"
    b = f"{remote:,}" if remote is not None else "N/A"
    return f"{a} / {b} snapshots"


def _plans_for(env: Environment, host: str, resolve: Any, sa: str, sb: str,
               clusters: set[str]) -> int:
    """Recovery plans of this policy's site between the same zones (and, when both name
    clusters, sharing a source cluster)."""
    n = 0
    for rp in env.recovery_plans:
        if (getattr(rp, "source_pc", None) or host) != host and env.is_combined:
            continue
        a = resolve(rp.primary_location, None)
        b = resolve(rp.recovery_location, None) if rp.recovery_location else a
        if {a, b} != {sa, sb}:
            continue
        if clusters and rp.primary_cluster_uuids and \
                not clusters & set(rp.primary_cluster_uuids):
            continue
        n += 1
    return n


__all__ = ["DrCluster", "DrFlow", "DrModel", "DrPlans", "DrRemote", "DrSite",
           "applies", "bullets", "dr_model", "flow_text", "from_doc", "policy_rows",
           "to_doc"]
