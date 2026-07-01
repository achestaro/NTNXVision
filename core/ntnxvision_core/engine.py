"""Collection engine — orchestrates connect → collect → assemble Environment.

Runs each collector in order, isolating failures so one broken collector never aborts
the whole run (its error is recorded in `env.collection_notes`). Emits progress via an
optional callback so the UI/CLI can show a live progress bar.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from ntnxvision_core.api import PrismConnection, PrismCredentials
from ntnxvision_core.collectors import PHASE1_COLLECTORS
from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.models import Environment, PrismCentral

# progress(step_index, total_steps, collector_label, status)
ProgressFn = Callable[[int, int, str, str], None]


def collect_environment(
    creds: PrismCredentials,
    collectors: list[type[Collector]] | None = None,
    progress: ProgressFn | None = None,
) -> Environment:
    """Connect to Prism Central and run all collectors, returning a populated Environment."""
    collector_types = collectors or PHASE1_COLLECTORS
    env = Environment(
        collected_at=datetime.now(timezone.utc).isoformat(),
        prism_central=PrismCentral(host=creds.host),
    )

    total = len(collector_types)
    with PrismConnection(creds) as conn:
        env.prism_central.version = conn.pc_version
        env.prism_central.negotiated_api_versions = dict(conn.negotiated)

        for idx, collector_cls in enumerate(collector_types):
            collector = collector_cls(conn)
            _emit(progress, idx, total, collector.label, "running")
            try:
                collector.collect(env)
                note = env.collection_notes.get(collector.name, "ok")
                _emit(progress, idx, total, collector.label, note)
            except Exception as exc:  # noqa: BLE001 — isolate collector failures
                env.collection_notes[collector.name] = f"error: {exc}"
                _emit(progress, idx, total, collector.label, f"error: {exc}")

    _resolve_cross_references(env)
    return env


def _resolve_cross_references(env: Environment) -> None:
    """Fill in host counts on clusters and any name backfills after all data is in."""
    host_by_cluster: dict[str, int] = {}
    for host in env.hosts:
        if host.cluster_uuid:
            host_by_cluster[host.cluster_uuid] = host_by_cluster.get(host.cluster_uuid, 0) + 1
    for cluster in env.clusters:
        if not cluster.num_nodes and cluster.uuid in host_by_cluster:
            cluster.num_nodes = host_by_cluster[cluster.uuid]


def _emit(progress: ProgressFn | None, idx: int, total: int, label: str, status: str) -> None:
    if progress is not None:
        progress(idx, total, label, status)
