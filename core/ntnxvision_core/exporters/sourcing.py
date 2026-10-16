"""Source-Prism-Central attribution shared by the Excel, PDF and Word exporters.

A combined (multi-PC) Environment merges objects from several independent estates whose
*names* routinely collide — two customers' PCs both running a "Cluster01" is ordinary.
Every exporter therefore renders the same extra "Prism Central" column, driven by the
`source_pc` each object was stamped with in `engine.combine_environments`.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.models import Environment


def sources(env: Environment, objs: list[Any]) -> list[str] | None:
    """Per-row source-PC labels for `objs`, or None when the run is not combined.

    None means "no attribution column"; the table helpers take it as the signal to
    render exactly what a single-PC report has always rendered.
    """
    if not env.is_combined:
        return None
    return [getattr(o, "source_pc", None) or "unknown" for o in objs]


def source_hosts(env: Environment) -> list[str]:
    """Hostnames of the Prism Centrals combined into `env`, in collection order."""
    return [s.prism_central.host or "unknown" for s in env.source_environments]


#: Header of the attribution column in combined exports.
SOURCE_HEADER = "Prism Central"


def with_sources(headers: list[str], rows: list[list[Any]], srcs: list[str] | None
                 ) -> tuple[list[str], list[list[Any]]]:
    """`headers` / `rows` with the "Prism Central" attribution column added.

    It goes right after a leading Cluster column (the house rule keeps Cluster first, and
    "which cluster, on which PC" then reads left to right), else at the front (VM tables,
    and tables with no cluster column). `srcs` None returns the table unchanged.
    """
    if srcs is None:
        return headers, rows
    at = 1 if headers and headers[0] == "Cluster" else 0
    return ([*headers[:at], SOURCE_HEADER, *headers[at:]],
            [[*r[:at], s, *r[at:]] for s, r in zip(srcs, rows, strict=True)])
