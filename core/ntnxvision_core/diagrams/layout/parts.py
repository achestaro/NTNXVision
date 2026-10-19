"""Splitting a diagram's columns (availability zones) into parts, shared by the layouts.

The rule of the conceptual overview (`conceptual.plan_parts`), for any ids: parts of up
to `per`, linked ids kept together (connected components in input order, members in
input order); a component larger than `per` is cut into consecutive chunks.
"""

from __future__ import annotations

from collections.abc import Iterable


def group(ids: list[str], pairs: Iterable[tuple[str, str]], per: int) -> list[list[str]]:
    adj: dict[str, set[str]] = {i: set() for i in ids}
    for a, b in pairs:
        if a in adj and b in adj and a != b:
            adj[a].add(b)
            adj[b].add(a)
    seen: set[str] = set()
    comps: list[list[str]] = []
    for sid in ids:
        if sid in seen:
            continue
        found: set[str] = set()
        stack = [sid]
        while stack:
            node = stack.pop()
            if node in found:
                continue
            found.add(node)
            stack.extend(adj[node] - found)
        seen |= found
        comps.append([i for i in ids if i in found])
    parts: list[list[str]] = []
    cur: list[str] = []
    for comp in comps:
        if len(comp) > per:
            if cur:
                parts.append(cur)
                cur = []
            chunks = [comp[i:i + per] for i in range(0, len(comp), per)]
            parts.extend(chunks[:-1])
            cur = list(chunks[-1])
        elif len(cur) + len(comp) <= per:
            cur.extend(comp)
        else:
            parts.append(cur)
            cur = list(comp)
    if cur:
        parts.append(cur)
    return parts or [[]]
