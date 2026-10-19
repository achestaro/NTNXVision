"""Design-summary bullets and the supporting table rows, derived from the model.

The bullets state what the figure shows in words (2 to 5, only facts present in the
data); the tables carry every value, so nothing the figure summarises is lost.
"""

from __future__ import annotations

from collections import Counter

from . import fmt
from .facts import short_name
from .model import Card, ConceptualModel, Link, Repl
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


def _flows_phrase(lk: Link, a: str, b: str) -> str:
    """"Async 1 h both ways; md-pc01 to md-pc02 also NearSync 1 min"."""
    fwd = {(r.type, r.rpo_seconds): r for r in lk.forward}
    bwd = {(r.type, r.rpo_seconds): r for r in lk.backward}
    common = [fwd[k] for k in fwd if k in bwd]
    parts = []
    if common:
        parts.append(f"{', '.join(fmt.repl_group(common))} both ways")
    also = " also" if common else ""
    for reps, x, y in (([fwd[k] for k in fwd if k not in bwd], a, b),
                       ([bwd[k] for k in bwd if k not in fwd], b, a)):
        if reps:
            parts.append(f"{x} to {y}{also} {', '.join(fmt.repl_group(reps))}")
    return "; ".join(parts)


def _link_bullets(model: ConceptualModel) -> list[str]:
    out: list[str] = []
    order = [s.id for s in model.sites]
    site_ids = set(order)
    pairs: dict[frozenset[str], dict[str, Link]] = {}
    for lk in model.links:
        if lk.a in site_ids and lk.b in site_ids:
            pairs.setdefault(frozenset((lk.a, lk.b)), {})[lk.kind] = lk
    pair_lines = []
    for key in sorted(pairs, key=lambda k: sorted(order.index(i) for i in k)):
        kinds = pairs[key]
        a, b = sorted(key, key=order.index)
        names = f"{_site_short(model, a)} and {_site_short(model, b)}"
        rep = kinds.get("replication")
        pairing = kinds.get("pairing")
        down = (f" (pairing {pairing.state or 'not connected'})"
                if pairing and not pairing.connected else "")
        if rep:
            phrase = _flows_phrase(rep, _site_short(model, rep.a), _site_short(model, rep.b))
            verb = "are paired" if pairing else "replicate"
            pair_lines.append(f"{names} {verb}{down}: {phrase}.")
        else:
            pair_lines.append(f"{names} are paired{down}, with no protection policy "
                              "between them.")
    if len(pair_lines) <= 2:
        out.extend(pair_lines)
    else:
        out.append(f"{len(pair_lines):,} pairs of availability zones in this inventory are "
                   "paired with each other.")

    # remote id -> {"to": [(site, reps)], "from": [(site, reps)]}
    remotes: dict[str, dict[str, list[tuple[str, list[Repl]]]]] = {}
    for lk in model.links:
        if lk.kind != "replication" or not (lk.a.startswith("az:") or lk.b.startswith("az:")):
            continue
        site, rid = (lk.a, lk.b) if lk.b.startswith("az:") else (lk.b, lk.a)
        out_reps = lk.forward if lk.a == site else lk.backward
        in_reps = lk.backward if lk.a == site else lk.forward
        entry = remotes.setdefault(rid, {"to": [], "from": []})
        if out_reps:
            entry["to"].append((_site_short(model, site), out_reps))
        if in_reps:
            entry["from"].append((_site_short(model, site), in_reps))
    lines = []
    for rid in sorted(remotes, key=lambda r: model.name_of(r).lower()):
        remote = model.name_of(rid)
        for way in ("to", "from"):
            flows = remotes[rid][way]
            if not flows:
                continue
            who_list = sorted({w for w, _ in flows})
            who = fmt.join_names(who_list) if len(who_list) <= 3 else \
                f"{', '.join(who_list[:2])} and {len(who_list) - 2:,} more"
            types = ", ".join(fmt.repl_group([r for _, reps in flows for r in reps]))
            if way == "to":
                verb = "replicates" if len(who_list) == 1 else "replicate"
                lines.append(f"{who} {verb} to {remote}, outside this inventory ({types}).")
            else:
                lines.append(f"{remote}, outside this inventory, replicates to {who} "
                             f"({types}).")
    if len(lines) <= 2:
        out.extend(lines)
    else:
        out.append(f"Replication with {len(remotes):,} availability zones outside this "
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
