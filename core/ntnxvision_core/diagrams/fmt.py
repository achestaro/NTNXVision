"""Display text for diagram values, shared by layouts, summaries and tables.

House rules: comma thousands, "N/A" for a missing value, no em dashes.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .model import Card, Link, Repl

TIB = 1024**4
#: A no-break space: keeps "Table 5-2" or "HPE DX360 Gen10 Plus" on one line.
NBSP = " "


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def rpo(seconds: int | None) -> str:
    if seconds is None:
        return "RPO N/A"
    if seconds == 0:
        return "0 s"
    for unit, n in (("d", 86400), ("h", 3600), ("min", 60)):
        if seconds >= n and seconds % n == 0:
            return f"{seconds // n:,} {unit}"
    return f"{seconds:,} s"


def repl_text(r: Repl) -> str:
    """"NearSync 1 min", "Async 1 h"; synchronous replication reads just "Sync"."""
    if r.type == "Sync" or not r.rpo_seconds and r.rpo_seconds is not None:
        return "Sync"
    return f"{r.type} {rpo(r.rpo_seconds)}"


def repl_group(reps: list[Repl], limit: int = 3) -> list[str]:
    """Distinct (type, RPO) texts, shortest RPO first; more than `limit` collapse into
    a range ("Sync to 1 d", "RPO 1 min to 1 d")."""
    lines = list(dict.fromkeys(repl_text(r) for r in reps))
    if len(lines) <= limit:
        return lines
    rpos = sorted(r.rpo_seconds for r in reps if r.rpo_seconds is not None)
    if not rpos:
        return [plural(len(lines), "schedule")]
    lo = "Sync" if rpos[0] == 0 else f"RPO {rpo(rpos[0])}"
    span = f"{lo} to {rpo(rpos[-1])}".replace(" ", NBSP)
    return [f"{plural(len(lines), 'schedule')}, {span}"]


@dataclass(frozen=True)
class LabelLine:
    text: str
    role: str  # type | direction | note


def link_lines(link: Link, a_name: str, b_name: str) -> list[LabelLine]:
    """A replication link's label, by direction: schedules present both ways first
    (then "both directions"), then each one-way set under "a to b"."""
    fwd = {(r.type, r.rpo_seconds): r for r in link.forward}
    bwd = {(r.type, r.rpo_seconds): r for r in link.backward}
    common = [fwd[k] for k in fwd if k in bwd]
    only_f = [fwd[k] for k in fwd if k not in bwd]
    only_b = [bwd[k] for k in bwd if k not in fwd]
    out: list[LabelLine] = []
    if common:
        out += [LabelLine(t, "type") for t in repl_group(common)]
        out.append(LabelLine("both directions", "note"))
    for reps, x, y in ((only_f, a_name, b_name), (only_b, b_name, a_name)):
        if not reps:
            continue
        if common or (only_f and only_b):
            out.append(LabelLine(f"{x} to {y}:", "direction"))
        out += [LabelLine(t, "type") for t in repl_group(reps)]
    return out


def direction_lines(reps: list[Repl], both: bool) -> list[str]:
    """One edge's label in a bus: its schedules, plus "both directions"."""
    return repl_group(reps) + (["both directions"] if both else [])


def repl_strength(reps: list[Repl]) -> str:
    types = {r.type for r in reps}
    if "Sync" in types or "Metro" in types:
        return "sync"
    if "NearSync" in types:
        return "nearsync"
    return "async"


def tib(raw_bytes: int | None) -> str:
    return "N/A" if not raw_bytes else f"{raw_bytes / TIB:,.1f} TiB"


def model_text(card: Card) -> str | None:
    if not card.models:
        return None
    first = card.models[0].replace(" ", NBSP)
    return first + (f"{NBSP}+{len(card.models) - 1:,}" if len(card.models) > 1 else "")


def card_body(card: Card) -> str:
    parts = [card.hypervisor or "Hypervisor N/A"]
    if card.nodes is not None:
        parts.append(plural(card.nodes, "node").replace(" ", NBSP))
    if card.compute_only:
        parts.append("compute-only")
    if card.rf:
        parts.append(f"RF{card.rf}")
    if m := model_text(card):
        parts.append(m)
    return ", ".join(parts)


def storage_text(card: Card) -> str:
    parts = []
    if card.raw_bytes:
        parts.append(f"{tib(card.raw_bytes)} raw".replace(" ", NBSP))
    if card.containers:
        parts.append(plural(card.containers, "container").replace(" ", NBSP))
    return ", ".join(parts)


def hypervisor_mix(cards: list[Card]) -> str:
    """"10 AHV, 1 ESXi" (clusters per hypervisor, most first)."""
    c = Counter(k.hypervisor or "N/A" for k in cards)
    return ", ".join(f"{n:,} {h}" for h, n in sorted(c.items(), key=lambda kv: (-kv[1], kv[0])))


#: Region values as the APIs spell them, as people read them.
_REGIONS = {"onprem": "on-premises", "onpremises": "on-premises", "onpremise": "on-premises"}


def region_label(value: str | None) -> str | None:
    """"OnPrem" (an AZ region enum) -> "on-premises"; other regions as given."""
    if not value:
        return None
    key = value.strip().lower().replace("-", "").replace("_", "").replace(" ", "")
    return _REGIONS.get(key, value.strip())


def join_names(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]
