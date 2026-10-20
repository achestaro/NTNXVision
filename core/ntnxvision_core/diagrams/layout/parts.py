"""Splitting a diagram's columns (availability zones) into parts: one rule for every
figure drawn per AZ (conceptual overview, management plane, DR design).

* `plan`: parts of up to `per` in model order; a linked group (paired or replicating
  AZs) stays together, one larger than a part is cut into consecutive chunks, and an
  unlinked AZ takes the first free slot of an earlier part.
* `split`: a part that does not fit gives up its last linked group (paired AZs stay
  together), or its last AZ when the whole part is one group.
* `fit`: plan, then split every part that does not fit and plan the rest again, so
  every figure type cuts the estate the same way unless its own height forces a cut.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable


def components(ids: list[str], pairs: Iterable[tuple[str, str]]) -> list[list[str]]:
    """Linked groups in `ids` order, members in `ids` order."""
    adj: dict[str, set[str]] = {i: set() for i in ids}
    for a, b in pairs:
        if a in adj and b in adj and a != b:
            adj[a].add(b)
            adj[b].add(a)
    seen: set[str] = set()
    out: list[list[str]] = []
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
        out.append([i for i in ids if i in found])
    return out


def plan(ids: list[str], pairs: Iterable[tuple[str, str]], per: int) -> list[list[str]]:
    parts: list[list[str]] = []
    for comp in components(ids, list(pairs)):
        if len(comp) == 1:  # an unlinked AZ takes the first free slot
            free = next((p for p in parts if len(p) < per), None)
            if free is not None:
                free.append(comp[0])
            else:
                parts.append(list(comp))
        elif len(comp) > per:
            parts.extend(comp[k:k + per] for k in range(0, len(comp), per))
        elif parts and len(parts[-1]) + len(comp) <= per:
            parts[-1].extend(comp)
        else:
            parts.append(list(comp))
    return parts or [[]]


def split(ids: list[str], pairs: Iterable[tuple[str, str]]) -> tuple[list[str], list[str]]:
    comps = components(ids, list(pairs))
    if len(comps) > 1:
        move = set(comps[-1])
        return [i for i in ids if i not in move], [i for i in ids if i in move]
    return ids[:-1], ids[-1:]


def fit(ids: list[str], pairs: Iterable[tuple[str, str]], per: int,
        fits: Callable[[list[str], int, list[list[str]]], bool]) -> list[list[str]]:
    """`plan`, then split each part `fits(part, index, parts)` rejects (and re-plan the
    AZs after it)."""
    pairs = list(pairs)
    parts = plan(ids, pairs, per)
    pi = 0
    while pi < len(parts):
        if len(parts[pi]) > 1 and not fits(parts[pi], pi, parts):
            keep, move = split(parts[pi], pairs)
            rest = move + [i for p in parts[pi + 1:] for i in p]
            parts = parts[:pi] + [keep] + plan(rest, pairs, per)
            continue
        pi += 1
    return parts


def group(ids: list[str], pairs: Iterable[tuple[str, str]], per: int) -> list[list[str]]:
    """Backward-compatible name for `plan`."""
    return plan(ids, pairs, per)
