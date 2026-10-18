"""Display text for diagram values, shared by layouts, summaries and tables.

House rules: comma thousands, "N/A" for a missing value, no em dashes.
"""

from __future__ import annotations

from collections import Counter

from .model import Card, Link, Repl

TIB = 1024**4


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
    if r.type in ("Sync", "Metro") and not r.rpo_seconds:
        return f"{r.type} (RPO 0)"
    return f"{r.type} {rpo(r.rpo_seconds)}"


def repl_lines(link: Link, limit: int = 3) -> list[str]:
    """Distinct (type, RPO) pairs, shortest RPO first; many collapse to a range."""
    lines = list(dict.fromkeys(repl_text(r) for r in link.repl))
    if len(lines) <= limit:
        return lines
    rpos = [r.rpo_seconds for r in link.repl if r.rpo_seconds is not None]
    span = f"RPO {rpo(min(rpos))} to {rpo(max(rpos))}" if rpos else "RPO N/A"
    return [plural(link.policies or len(link.repl), "policy", "policies"), span]


def repl_strength(link: Link) -> str:
    types = {r.type for r in link.repl}
    if types & {"Sync", "Metro"}:
        return "sync"
    if "NearSync" in types:
        return "nearsync"
    return "async"


def tib(raw_bytes: int | None) -> str:
    return "N/A" if not raw_bytes else f"{raw_bytes / TIB:,.1f} TiB"


def model_text(card: Card) -> str | None:
    if not card.models:
        return None
    return card.models[0] + (f" +{len(card.models) - 1:,}" if len(card.models) > 1 else "")


def card_body(card: Card) -> str:
    parts = [card.hypervisor or "Hypervisor N/A"]
    if card.nodes is not None:
        parts.append(plural(card.nodes, "node"))
    if card.rf:
        parts.append(f"RF{card.rf}")
    if m := model_text(card):
        parts.append(m)
    return ", ".join(parts)


def storage_text(card: Card) -> str:
    parts = []
    if card.raw_bytes:
        parts.append(f"{tib(card.raw_bytes)} raw")
    if card.containers:
        parts.append(plural(card.containers, "container"))
    return ", ".join(parts)


def hypervisor_mix(cards: list[Card]) -> str:
    """"AHV 10, ESXi 1" (clusters per hypervisor, most first)."""
    c = Counter(k.hypervisor or "N/A" for k in cards)
    return ", ".join(f"{h} {n:,}" for h, n in sorted(c.items(), key=lambda kv: (-kv[1], kv[0])))


def join_names(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]
