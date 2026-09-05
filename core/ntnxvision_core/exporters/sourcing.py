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
