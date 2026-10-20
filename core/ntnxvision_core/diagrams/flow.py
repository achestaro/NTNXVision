"""Microsegmentation Design (N3): model, facts, design summary and table rows.

Per availability zone, the Flow Network Security policies in one lane per policy type
(application, isolation, quarantine, then any other type), each with its enforcement
state, its secured groups (categories) and its inbound / outbound rule counts. When the
snapshot has the rules' peers (`SecurityPolicy.rules`), the sources and destinations
are named too; without them the figure is a posture view, not a traffic map, and the
caption says so. A count of None means the policy's detail could not be read: drawn as
"not reported", never as 0.

Availability zones whose policy sets are identical collapse into one figure that names
them ("Applies to en-pc01 and en-pc03").
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from ntnxvision_core.models import Environment

from . import fmt
from .facts import short_name
from .netcommon import azs, default_host, natural, src_of

LANES = ("APPLICATION", "ISOLATION", "QUARANTINE")
LANE_TITLES = {"APPLICATION": "APPLICATION POLICIES", "ISOLATION": "ISOLATION POLICIES",
               "QUARANTINE": "QUARANTINE POLICIES", "OTHER": "OTHER POLICIES"}
STATE_ORDER = {"ENFORCE": 0, "MONITOR": 1, "SAVE": 2}
STATE_WORDS = {"ENFORCE": "enforced", "MONITOR": "monitor", "SAVE": "saved, not applied"}


@dataclass
class FPolicy:
    id: str  # policy:<uuid> | user:<uuid4>
    name: str
    kind: str = "policy"  # policy | user (a planned policy) | note
    type: str = "APPLICATION"
    state: str | None = None
    groups: list[str] = field(default_factory=list)
    inbound: int | None = None
    outbound: int | None = None
    #: Rule peers (F0), None when the snapshot has none for this policy.
    peers_in: list[str] | None = None
    peers_out: list[str] | None = None
    note: str | None = None
    data: dict[str, str] = field(default_factory=dict)


@dataclass
class Lane:
    id: str
    type: str
    policies: list[FPolicy] = field(default_factory=list)


@dataclass
class FSite:
    id: str
    name: str
    kind: str = "az"
    #: AZ names this figure stands for when identical policy sets collapsed (2 or more).
    applies_to: list[str] = field(default_factory=list)
    lanes: list[Lane] = field(default_factory=list)
    service_groups: int = 0
    address_groups: int = 0
    data: dict[str, str] = field(default_factory=dict)


@dataclass
class FlowModel:
    sites: list[FSite] = field(default_factory=list)
    title: str = "Microsegmentation design"
    edited: bool = False


def to_doc(m: FlowModel) -> dict[str, Any]:
    return asdict(m)


def _pick(cls: Any, d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if k in cls.__dataclass_fields__}


def from_doc(d: dict[str, Any]) -> FlowModel:
    def lane(x: dict[str, Any]) -> Lane:
        return Lane(id=x["id"], type=x.get("type", "OTHER"),
                    policies=[FPolicy(**_pick(FPolicy, p)) for p in x.get("policies", [])])

    return FlowModel(
        sites=[FSite(**{**_pick(FSite, s), "lanes": [lane(x) for x in s.get("lanes", [])]})
               for s in d.get("sites", [])],
        title=d.get("title", "Microsegmentation design"), edited=bool(d.get("edited", False)))


# ─── facts ─────────────────────────────────────────────────────────────


def applies(env: Environment) -> bool:
    return bool(env.security_policies)


def split_groups(secured: str | None) -> list[str]:
    """"Env:Dev,Prod; App:Web" -> ["Env:Dev", "Env:Prod", "App:Web"]."""
    if not secured:
        return []
    out: list[str] = []
    for part in secured.split(";"):
        key, sep, values = part.strip().partition(":")
        if not key:
            continue
        if not sep:
            out.append(key)
            continue
        out += [f"{key}:{v.strip()}" for v in values.split(",") if v.strip()] or [key]
    return out


def _peers(rules: Any, direction: str) -> list[str] | None:
    if rules is None:
        return None
    labels = {r.peer or "Not reported" for r in rules if r.direction == direction}
    order = {"All sources": 0, "All destinations": 0}
    return sorted(labels, key=lambda x: (order.get(x, 1), natural(x)))


def _policy_key(p: FPolicy) -> tuple[Any, ...]:
    return (STATE_ORDER.get((p.state or "").upper(), 3), natural(p.name), p.id)


def flow_model(env: Environment) -> FlowModel:
    default = default_host(env)
    sites: list[FSite] = []
    for az in azs(env):
        pols = [p for p in env.security_policies if src_of(env, p, default) == az.name]
        if not pols:
            continue
        by: dict[str, list[FPolicy]] = {}
        for p in pols:
            t = (p.policy_type or "OTHER").upper()
            t = t if t in LANES else "OTHER"
            by.setdefault(t, []).append(FPolicy(
                id=f"policy:{p.uuid}", name=p.name or p.uuid[:8], type=t,
                state=(p.state or "").upper() or None, groups=split_groups(p.secured_group),
                inbound=p.inbound_rule_count if t == "APPLICATION" else None,
                outbound=p.outbound_rule_count if t == "APPLICATION" else None,
                peers_in=_peers(p.rules, "IN") if t == "APPLICATION" else None,
                peers_out=_peers(p.rules, "OUT") if t == "APPLICATION" else None,
                data={"policy_uuid": p.uuid, "prism_central": az.name,
                      "policy_type": p.policy_type or "N/A"}))
        lanes = [Lane(id=f"lane:{az.name}:{t}", type=t,
                      policies=sorted(by[t], key=_policy_key))
                 for t in (*LANES, "OTHER") if t in by]
        sites.append(FSite(
            id=az.id, name=az.name, kind=az.kind, lanes=lanes,
            service_groups=sum(1 for g in env.service_groups
                               if src_of(env, g, default) == az.name and not g.is_system),
            address_groups=sum(1 for g in env.address_groups
                               if src_of(env, g, default) == az.name and not g.is_system),
            data={"name": az.name}))
    return FlowModel(sites=_collapse(sites))


def _signature(s: FSite) -> tuple[Any, ...]:
    return (s.service_groups, s.address_groups,
            tuple((x.type, tuple((p.name, p.state, tuple(p.groups), p.inbound, p.outbound,
                                  tuple(p.peers_in or ()), tuple(p.peers_out or ()))
                                 for p in x.policies)) for x in s.lanes))


def _collapse(sites: list[FSite]) -> list[FSite]:
    out: list[FSite] = []
    first: dict[tuple[Any, ...], FSite] = {}
    for s in sites:
        sig = _signature(s)
        if sig in first:
            keep = first[sig]
            keep.applies_to = (keep.applies_to or [keep.name]) + [s.name]
            continue
        first[sig] = s
        out.append(s)
    return out


# ─── text ──────────────────────────────────────────────────────────────


def policies(s: FSite) -> list[FPolicy]:
    return [p for x in s.lanes for p in x.policies if p.kind == "policy"]


def rules_text(n: int | None, what: str) -> str:
    if n is None:
        return f"{what} rules: not reported"
    if n == 0:
        return f"no {what} rules"
    return f"{n:,} {what} rule{'s' if n != 1 else ''}"


def peers_text(peers: list[str] | None, limit: int = 2) -> str | None:
    if not peers:
        return None
    shown = ", ".join(peers[:limit])
    return shown + (f" +{len(peers) - limit:,}" if len(peers) > limit else "")


def site_title(s: FSite) -> str:
    if len(s.applies_to) > 1:
        return ", ".join(short_name(n) for n in s.applies_to)
    return s.name


def site_line(s: FSite) -> str:
    pols = policies(s)
    st = Counter((p.state or "") for p in pols)
    parts = [f"{st['ENFORCE']:,} enforced"]
    if st["MONITOR"]:
        parts.append(f"{st['MONITOR']:,} monitoring")
    if st["SAVE"]:
        parts.append(f"{st['SAVE']:,} saved, not applied")
    lead = (f"{len(s.applies_to):,} availability zones with the same policies"
            if len(s.applies_to) > 1 else "Availability zone")
    return f"{lead}, {fmt.plural(len(pols), 'security policy', 'security policies')}: " \
        + ", ".join(parts)


def has_peers(s: FSite) -> bool:
    return any(p.peers_in is not None or p.peers_out is not None for p in policies(s))


def bullets(s: FSite) -> list[str]:
    pols = policies(s)
    if not pols:
        return []
    out: list[str] = []
    types = Counter(p.type for p in pols)
    parts = [f"{types[t]:,} {t.lower()}" for t in (*LANES, "OTHER") if types[t]]
    out.append(f"{fmt.plural(len(pols), 'Flow policy', 'Flow policies')}: "
               f"{', '.join(parts)}.")
    if len(s.applies_to) > 1:
        out.append(f"The same policies apply on {fmt.join_names(s.applies_to)}.")
    st = Counter(p.state or "" for p in pols)
    mon = [p.name for p in pols if p.state == "MONITOR"]
    saved = [p.name for p in pols if p.state == "SAVE"]
    line = f"{st['ENFORCE']:,} enforced"
    if mon:
        line += (f"; {fmt.join_names(mon)} {'is' if len(mon) == 1 else 'are'} in monitor mode"
                 if len(mon) <= 2 else f"; {len(mon):,} in monitor mode")
    if saved:
        line += (f"; {fmt.join_names(saved)} {'is' if len(saved) == 1 else 'are'} saved, "
                 "not applied" if len(saved) <= 2 else f"; {len(saved):,} saved, not applied")
    out.append(line + ".")
    iso = [p for p in pols if p.type == "ISOLATION" and len(p.groups) >= 2]
    if iso:
        p = iso[0]
        out.append(f"{p.groups[0]} is isolated from {fmt.join_names(p.groups[1:])}"
                   + (f" ({len(iso) - 1:,} more isolation policies)" if len(iso) > 1 else "")
                   + ".")
    app = [p for p in pols if p.type == "APPLICATION" and (p.inbound or p.outbound)]
    if app:
        top = max(app, key=lambda p: ((p.inbound or 0) + (p.outbound or 0),
                                      [-ord(c) for c in p.name]))
        def n(v: int | None) -> str:
            return "N/A" if v is None else f"{v:,}"
        out.append(f"{top.name} has the most rules: {n(top.inbound)} inbound, "
                   f"{n(top.outbound)} outbound.")
    if s.service_groups or s.address_groups:
        out.append(f"Shared objects: {fmt.plural(s.service_groups, 'service group')}, "
                   f"{fmt.plural(s.address_groups, 'address group')}.")
    return out[:5]


HEADERS = ["Policy", "Type", "State", "Inbound", "Outbound", "Secured Group",
           "Inbound peers", "Outbound peers"]


def table_rows(env: Environment, s: FSite) -> tuple[list[list[object]], list[str]]:
    """Every policy of the AZs the figure stands for, in the figure's order, and the
    Prism Central of each row."""
    default = default_host(env)
    names = s.applies_to or [s.name]
    rows: list[list[object]] = []
    srcs: list[str] = []
    for name in names:
        pols = [p for p in env.security_policies if src_of(env, p, default) == name]

        def key(p: Any) -> tuple[Any, ...]:
            t = (p.policy_type or "OTHER").upper()
            lane = (*LANES, "OTHER").index(t if t in LANES else "OTHER")
            return (lane, STATE_ORDER.get((p.state or "").upper(), 3), natural(p.name),
                    p.uuid)
        for p in sorted(pols, key=key):
            app = (p.policy_type or "").upper() == "APPLICATION"
            rows.append([p.name, p.policy_type, p.state,
                         p.inbound_rule_count if app else None,
                         p.outbound_rule_count if app else None, p.secured_group,
                         ", ".join(_peers(p.rules, "IN") or []) if app else None,
                         ", ".join(_peers(p.rules, "OUT") or []) if app else None])
            srcs.append(name)
    return rows, srcs
