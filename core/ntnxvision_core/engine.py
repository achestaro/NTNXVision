"""Collection engine — orchestrates connect → collect → assemble Environment.

Runs each collector in order, isolating failures so one broken collector never aborts
the whole run (its error is recorded in `env.collection_notes`). Emits progress via an
optional callback so the UI/CLI can show a live progress bar.

NDB is optional and runs against a separate Era endpoint when credentials are supplied.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from ntnxvision_core.api import PrismConnection, PrismCredentials
from ntnxvision_core.api.element_client import ElementConnection, ElementCredentials
from ntnxvision_core.api.ndb_client import NdbConnection, NdbCredentials
from ntnxvision_core.collectors import ALL_COLLECTORS
from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.collectors.element import collect_element
from ntnxvision_core.collectors.ndb import collect_ndb
from ntnxvision_core.compat import check_aos, check_prism_central
from ntnxvision_core.models import Environment, PrismCentral

# progress(step_index, total_steps, collector_label, status)
ProgressFn = Callable[[int, int, str, str], None]


def collect_environment(
    creds: PrismCredentials,
    collectors: list[type[Collector]] | None = None,
    progress: ProgressFn | None = None,
    ndb_creds: NdbCredentials | None = None,
) -> Environment:
    """Connect to Prism Central and run all collectors, returning a populated Environment.

    If `ndb_creds` is provided, an additional NDB collection step runs against the Era
    control plane after the Prism Central collectors.
    """
    collector_types = collectors or ALL_COLLECTORS
    env = Environment(
        collected_at=datetime.now(timezone.utc).isoformat(),
        prism_central=PrismCentral(host=creds.host),
    )

    # Total steps: PC collectors + optional NDB step.
    total = len(collector_types) + (1 if ndb_creds else 0)

    with PrismConnection(creds) as conn:
        env.prism_central.version = conn.pc_version
        env.prism_central.negotiated_api_versions = dict(conn.negotiated)

        # Compatibility check (warn but proceed).
        pc_compat = check_prism_central(conn.pc_version)
        env.prism_central.version_supported = pc_compat.in_range
        if pc_compat.warning:
            env.warnings.append(pc_compat.warning)

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

    if ndb_creds:
        _collect_ndb_step(ndb_creds, env, progress, len(collector_types), total)

    _resolve_cross_references(env)
    return env


def collect_element_environment(
    creds: ElementCredentials,
    progress: ProgressFn | None = None,
) -> Environment:
    """Connect directly to a cluster's Prism Element (v2.0) and inventory it.

    Used when a cluster is not registered to a Prism Central, or for single-cluster
    sites. Covers infrastructure only (clusters/hosts/VMs/storage/networks) — the
    platform services (DR, Flow, Files, NDB, …) are Prism-Central-scoped.
    """
    env = Environment(
        collected_at=datetime.now(timezone.utc).isoformat(),
        source_type="prism_element",
        prism_central=PrismCentral(host=creds.host),
    )
    with ElementConnection(creds) as conn:
        env.prism_central.version = conn.aos_version  # AOS version for PE mode

        aos_compat = check_aos(conn.aos_version)
        env.prism_central.version_supported = aos_compat.in_range
        if aos_compat.warning:
            env.warnings.append(aos_compat.warning)

        collect_element(conn, env, progress=progress)

    _resolve_cross_references(env)
    return env


def _collect_ndb_step(
    ndb_creds: NdbCredentials,
    env: Environment,
    progress: ProgressFn | None,
    idx: int,
    total: int,
) -> None:
    _emit(progress, idx, total, "NDB (Databases)", "running")
    try:
        with NdbConnection(ndb_creds) as ndb:
            collect_ndb(ndb, env)
        _emit(progress, idx, total, "NDB (Databases)", env.collection_notes.get("ndb", "ok"))
    except Exception as exc:  # noqa: BLE001
        env.collection_notes["ndb"] = f"error: {exc}"
        _emit(progress, idx, total, "NDB (Databases)", f"error: {exc}")


def _resolve_cross_references(env: Environment) -> None:
    """Fill in host counts on clusters and any name backfills after all data is in."""
    host_by_cluster: dict[str, int] = {}
    for host in env.hosts:
        if host.cluster_uuid:
            host_by_cluster[host.cluster_uuid] = host_by_cluster.get(host.cluster_uuid, 0) + 1
    for cluster in env.clusters:
        if not cluster.num_nodes and cluster.uuid in host_by_cluster:
            cluster.num_nodes = host_by_cluster[cluster.uuid]

    # Per-cluster AOS compatibility check (warn but proceed); de-duplicated.
    # Skipped in PE mode, where the AOS check already ran at connect time.
    if env.source_type != "prism_element":
        seen_aos: set[str] = set()
        for cluster in env.clusters:
            if cluster.aos_version and cluster.aos_version not in seen_aos:
                seen_aos.add(cluster.aos_version)
                aos_compat = check_aos(cluster.aos_version)
                if aos_compat.warning:
                    env.warnings.append(f"Cluster '{cluster.name}': {aos_compat.warning}")

    # Roll up protected-VM count from protection policies' category matches is non-trivial;
    # approximate with VMs that carry any category (best-effort until v4 DR SDK).
    env.data_protection.protected_vm_count = sum(1 for v in env.vms if v.categories)


def _emit(progress: ProgressFn | None, idx: int, total: int, label: str, status: str) -> None:
    if progress is not None:
        progress(idx, total, label, status)
