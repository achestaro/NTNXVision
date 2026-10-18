"""Design-summary bullets and the supporting table rows, derived from the model.

The bullets state what the figure shows in words (2 to 5, only facts present in the
data); the tables carry every value, so nothing the figure summarises is lost.
"""

from __future__ import annotations

from collections import Counter

from . import fmt
from .facts import short_name
from .model import Card, ConceptualModel, Link
from .scene import Scene

MAX_BULLETS = 5

#: Hardware platform family from a block model string.
_VENDORS = (("NX-", "Nutanix NX"), ("XC", "Dell XC"), ("DX", "HPE DX"), ("HPE", "HPE DX"),
            ("HX", "Lenovo HX"), ("UCS", "Cisco UCS"), ("PRIMERGY", "Fujitsu"),
            ("SMC", "Supermicro"))


def vendor(model: str) -> str | None:
    m = model.upper()
    for prefix, name in _VENDORS:
        if m.startswith(prefix) or f" {prefix}" in m:
            return name
    return None


def _clusters(model: ConceptualModel) -> list[Card]:
    return [c for s in model.sites for c in s.cards if c.kind == "cluster"]


def _site_short(model: ConceptualModel, ident: str) -> str:
    s = model.site(ident)
    return short_name(s.name) if s else model.name_of(ident)


def conceptual_bullets(model: ConceptualModel, scenes: list[Scene],
                       clusters_table: str | None = None) -> list[str]:
    cards = _clusters(model)
    nodes = sum(c.nodes or 0 for c in cards)
    n_sites = len(model.sites)
    out: list[str] = []
    if model.sites and model.sites[0].kind == "pe":
        out.append(f"Collected directly from Prism Element {model.sites[0].name}: "
                   f"{fmt.plural(len(cards), 'cluster')} and {fmt.plural(nodes, 'node')}. "
                   "Prism Central services (DR, Flow, AZ pairing) are not visible here.")
    elif n_sites == 1:
        out.append(f"1 availability zone, managed by Prism Central "
                   f"{short_name(model.sites[0].name)}: {fmt.plural(len(cards), 'cluster')} "
                   f"and {fmt.plural(nodes, 'node')}.")
    else:
        out.append(f"{n_sites:,} availability zones, one Prism Central each: "
                   f"{fmt.plural(len(cards), 'cluster')} and {fmt.plural(nodes, 'node')}.")

    parts = scenes[-1].part[1] if scenes else 1
    summarised = sum(len(sc.meta.get("summarised", [])) for sc in scenes)
    if parts > 1:
        out.append(f"Drawn in {parts:,} parts of up to 3 availability zones; paired zones "
                   "stay in the same part where they fit.")
    if summarised:
        where = clusters_table or "the clusters table"
        out.append(f"Per availability zone the figure shows the PC host and the largest "
                   f"clusters; {where} lists all {fmt.plural(len(cards), 'cluster')}.")

    hyp = Counter(c.hypervisor or "N/A" for c in cards)
    if len(hyp) == 1 and cards:
        only = next(iter(hyp))
        if len(cards) == 1:
            out.append(f"The cluster runs {only}.")
        elif len(cards) == 2:
            out.append(f"Both clusters run {only}.")
        else:
            out.append(f"All {len(cards):,} clusters run {only}.")
    elif cards:
        parts_txt = [fmt.plural(n, f"{h} cluster") for h, n in
                     sorted(hyp.items(), key=lambda kv: (-kv[1], kv[0]))]
        minority = [c for c in cards if c.hypervisor != "AHV"]
        extra = ""
        if 0 < len(minority) <= 2:
            vc = all("vCenter" in c.services for c in minority)
            extra = f" ({fmt.join_names([c.name for c in minority])}" + \
                (", vCenter managed)" if vc else ")")
        out.append(", ".join(parts_txt) + extra + ".")

    scale = [s for s in model.sites if s.pc and (s.pc.vms or 1) > 1]
    if scale:
        names = fmt.join_names([short_name(s.name) for s in scale[:3]])
        more = f" and {len(scale) - 3:,} more" if len(scale) > 3 else ""
        out.append(f"{names}{more} run{'s' if len(scale) == 1 and not more else ''} a "
                   f"scale-out Prism Central ({scale[0].pc.vms if scale[0].pc else 3} PCVMs).")

    out.extend(_link_bullets(model))

    ext = [(c, x) for c in cards for x in c.external]
    if ext:
        c, x = ext[0]
        name = f" {x.name}" if x.name else ""
        more = f" ({len(ext) - 1:,} more arrays in the clusters table)" if len(ext) > 1 else ""
        out.append(f"{c.name} has {fmt.plural(x.containers, 'container')} on {x.product}"
                   f"{name}{more}.")

    vendors = sorted({v for c in cards for m in c.models if (v := vendor(m))})
    if len(vendors) > 1:
        out.append(f"Hardware from {len(vendors):,} platform families: "
                   f"{fmt.join_names(vendors)}.")
    return out[:MAX_BULLETS]


def _repl_phrase(lk: Link) -> str:
    return ", ".join(fmt.repl_lines(lk))


def _link_bullets(model: ConceptualModel) -> list[str]:
    out: list[str] = []
    site_ids = {s.id for s in model.sites}
    inner = [lk for lk in model.links if lk.a in site_ids and lk.b in site_ids]
    pairs: dict[frozenset[str], dict[str, Link]] = {}
    for lk in inner:
        pairs.setdefault(frozenset((lk.a, lk.b)), {})[lk.kind] = lk
    pair_lines = []
    for key in sorted(pairs, key=lambda k: sorted(k)):
        kinds = pairs[key]
        a, b = sorted(key, key=lambda i: [s.id for s in model.sites].index(i))
        names = f"{_site_short(model, a)} and {_site_short(model, b)}"
        rep = kinds.get("replication")
        if rep:
            direction = ", both directions" if rep.both else (
                f", {_site_short(model, rep.a)} to {_site_short(model, rep.b)}")
            verb = "are paired" if "pairing" in kinds else "replicate"
            pair_lines.append(f"{names} {verb}: {_repl_phrase(rep)}{direction}.")
        else:
            pair_lines.append(f"{names} are paired (no protection policy between them).")
    if len(pair_lines) <= 2:
        out.extend(pair_lines)
    else:
        out.append(f"{len(pair_lines):,} pairs of availability zones in this inventory are "
                   "paired with each other.")

    # (remote id, direction) -> links; direction "to" = sites replicate to the remote.
    remotes: dict[tuple[str, str], list[Link]] = {}
    for lk in model.links:
        if lk.kind != "replication":
            continue
        if lk.b.startswith("az:"):
            remotes.setdefault((lk.b, "both" if lk.both else "to"), []).append(lk)
        elif lk.a.startswith("az:"):
            remotes.setdefault((lk.a, "both" if lk.both else "from"), []).append(lk)
    lines = []
    for rid, way in sorted(remotes, key=lambda k: (model.name_of(k[0]).lower(), k[1])):
        lks = remotes[(rid, way)]
        srcs = sorted({_site_short(model, lk.a if lk.b == rid else lk.b) for lk in lks})
        who = fmt.join_names(srcs) if len(srcs) <= 3 else \
            f"{', '.join(srcs[:2])} and {len(srcs) - 2:,} more"
        types = ", ".join(list(dict.fromkeys(t for lk in lks for t in fmt.repl_lines(lk)))[:3])
        remote = model.name_of(rid)
        if way == "from":
            lines.append(f"{remote}, outside this inventory, replicates to {who} ({types}).")
        else:
            verb = "replicates" if len(srcs) == 1 else "replicate"
            both = " in both directions" if way == "both" else ""
            lines.append(f"{who} {verb} with {remote}{both}, outside this inventory "
                         f"({types})." if both else
                         f"{who} {verb} to {remote}, outside this inventory ({types}).")
    if len(lines) <= 2:
        out.extend(lines)
    else:
        out.append(f"Replication to {len(lines):,} availability zones outside this "
                   "inventory.")
    return out


def deployment(model_site: object) -> str:
    pc = getattr(model_site, "pc", None)
    if pc is None:
        return "N/A"
    vms = pc.vms or 1
    text = "Single VM" if vms == 1 else f"Scale-out, {vms:,} PCVMs"
    return f"{text}, {pc.size}" if pc.size else text


def az_rows(model: ConceptualModel) -> list[list[object]]:
    """Availability zone | PC version | Deployment | Clusters | Nodes | Hypervisors |
    PC services in use | Paired with."""
    rows: list[list[object]] = []
    for s in model.sites:
        cards = [c for c in s.cards if c.kind == "cluster"]
        partners = []
        for lk in model.links:
            if lk.kind != "pairing" or s.id not in (lk.a, lk.b):
                continue
            other = lk.b if lk.a == s.id else lk.a
            partners.append(model.name_of(other) + (" (remote)" if other.startswith("az:")
                                                    else ""))
        rows.append([s.name, s.pc.version if s.pc else None, deployment(s), len(cards),
                     sum(c.nodes or 0 for c in cards), fmt.hypervisor_mix(cards) or None,
                     ", ".join(v.label for v in s.pc.services) if s.pc and s.pc.services
                     else None, ", ".join(partners) or None])
    for r in model.remotes:
        partners = sorted({model.name_of(lk.a if lk.b == r.id else lk.b)
                           for lk in model.links if r.id in (lk.a, lk.b)})
        rows.append([r.name, None, "Outside this inventory", None, None, None, None,
                     ", ".join(partners) or None])
    return rows


AZ_HEADERS = ["Availability zone", "PC version", "Deployment", "Clusters", "Nodes",
              "Hypervisors", "PC services in use", "Paired with"]
CLUSTER_HEADERS = ["Cluster", "Hypervisor", "Nodes", "RF", "Model", "Raw (TiB)",
                   "In figure"]


def cluster_rows(model: ConceptualModel, scenes: list[Scene], labels: list[str]
                 ) -> tuple[list[list[object]], list[str]]:
    """Rows for the clusters-per-AZ table and each row's site name (for attribution).
    `labels` are the figure labels of `scenes` ("Figure 5-2")."""
    where: dict[str, str] = {}
    for sc, label in zip(scenes, labels, strict=True):
        for cid in sc.meta.get("drawn", []):
            where[cid] = label
        for cid in sc.meta.get("summarised", []):
            where[cid] = f"Summary card, {label}"
    rows: list[list[object]] = []
    srcs: list[str] = []
    for s in model.sites:
        for c in s.cards:
            if c.kind != "cluster":
                continue
            fig = where.get(c.id, "N/A")
            if c.hosts_pc and not fig.startswith("Summary"):
                fig += " (hosts PC)"
            rows.append([c.name, c.hypervisor, c.nodes, c.rf, ", ".join(c.models) or None,
                         round(c.raw_bytes / fmt.TIB, 1) if c.raw_bytes else None, fig])
            srcs.append(s.name)
    return rows, srcs
