"""Cluster scope: collect only some of the clusters registered to a Prism Central.

The user picks the registered Prism Element clusters to collect (New collection, step 2).
Three pieces live here:

* `registered_clusters` lists the clusters registered to a PC, without the PC's own
  pseudo-cluster (the PC VM "cluster", typed ``PRISM_CENTRAL`` in v3's ``service_list`` and
  v4's ``clusterFunction``). It is what the ``list_clusters`` sidecar method answers and
  what a scoped run resolves its selection against. GET / v3 ``list`` calls only.
* `Scope` is a run's selection. Collectors ask it for a v4 ``$filter`` so the server
  returns only selected clusters' objects where the API supports that; whatever comes back
  is then pruned (`Scope.prune`) right after each area runs, so objects of unselected
  clusters never reach the snapshot, whichever API served them.
* `describe` renders the scope line the app and every report show
  ("Scope: 3 of 5 clusters (PHX-PROD-01, ...)").

The pruning rule is deliberately narrow: an object is dropped only when its cluster
(by uuid, else by name) is a registered cluster the user left out. Objects with no
cluster, or with a cluster that is not a registered PE cluster (the PC itself, for LCM
entries and alerts raised by Prism Central), are Prism-Central-wide and are kept.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.models import ClusterScope, ScopeCluster
from ntnxvision_core.numfmt import fmt_count

if TYPE_CHECKING:  # pragma: no cover
    from ntnxvision_core.api import PrismConnection
    from ntnxvision_core.api.element_client import ElementConnection
    from ntnxvision_core.models import Environment

#: Environment lists whose objects carry `cluster_uuid` and/or `cluster_name`.
CLUSTER_BOUND_FIELDS: tuple[str, ...] = (
    "hosts", "vms", "system_vms", "storage_containers", "subnets", "licenses", "alerts",
    "lcm_entities", "ovas", "volume_groups", "file_servers", "object_stores", "remote_sites",
)

#: What stays Prism-Central-wide in a scoped run (shown in the docs and the reports).
PC_WIDE_AREAS: tuple[str, ...] = (
    "protection policies", "recovery plans", "availability zones", "Flow security policies",
    "categories", "service and address groups", "VPCs and floating IPs", "templates",
    "users, roles and projects", "X-Play playbooks", "NCM reports", "Prism Central sizing",
    "NDB",
)


# ─── registered clusters ───────────────────────────────────────────────


@dataclass
class RegisteredCluster:
    uuid: str
    name: str | None = None
    aos_version: str | None = None
    num_nodes: int | None = None
    hypervisors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"uuid": self.uuid, "name": self.name, "aos_version": self.aos_version,
                "num_nodes": self.num_nodes, "hypervisors": list(self.hypervisors)}


def registered_clusters(conn: PrismConnection) -> tuple[list[RegisteredCluster], str]:
    """The PE clusters registered to this Prism Central, and the API that listed them.

    v4 ``clustermgmt`` when the PC serves it, else v3 ``clusters/list`` (the routing rule).
    The PC's own pseudo-cluster is excluded on both paths.
    """
    if conn.v4_ready("clusters"):
        return _registered_v4(conn), "v4"
    return _registered_v3(conn), "v3"


def _registered_v4(conn: PrismConnection) -> list[RegisteredCluster]:
    from ntnxvision_core.collectors.normalize import hypervisor_name

    out: list[RegisteredCluster] = []
    for entity in conn.v4_list("clustermgmt/v4.0/config/clusters"):
        config = entity.get("config") or {}
        if "PRISM_CENTRAL" in (config.get("clusterFunction") or []):
            continue
        uuid = entity.get("extId")
        if not uuid:
            continue
        build = config.get("buildInfo") or {}
        out.append(RegisteredCluster(
            uuid=uuid,
            name=entity.get("name"),
            aos_version=build.get("version"),
            num_nodes=(entity.get("nodes") or {}).get("numberOfNodes"),
            hypervisors=sorted({n for h in (config.get("hypervisorTypes") or [])
                                if (n := hypervisor_name(h))}),
        ))
    return _sorted(out)


def _registered_v3(conn: PrismConnection) -> list[RegisteredCluster]:
    from ntnxvision_core.collectors.normalize import hypervisor_name

    out: list[RegisteredCluster] = []
    for entity in conn.v3_list("clusters"):
        status = entity.get("status") or {}
        resources = status.get("resources") or {}
        config = resources.get("config") or {}
        if "PRISM_CENTRAL" in (config.get("service_list") or []):
            continue
        uuid = (entity.get("metadata") or {}).get("uuid")
        if not uuid:
            continue
        nos = (config.get("software_map") or {}).get("NOS") or {}
        node_list = config.get("node_list") or []
        servers = (resources.get("nodes") or {}).get("hypervisor_server_list") or []
        types = [n.get("hypervisor_type") for n in node_list] + [s.get("type") for s in servers]
        out.append(RegisteredCluster(
            uuid=uuid,
            name=status.get("name"),
            aos_version=nos.get("version") if isinstance(nos, dict) else None,
            num_nodes=len(node_list) or len(servers) or None,
            hypervisors=sorted({n for t in types if t and (n := hypervisor_name(t))}),
        ))
    return _sorted(out)


def _sorted(clusters: list[RegisteredCluster]) -> list[RegisteredCluster]:
    return sorted(clusters, key=lambda c: ((c.name or "").casefold(), c.uuid))


def element_cluster(conn: ElementConnection) -> RegisteredCluster:
    """The one cluster a Prism Element connection reaches."""
    from ntnxvision_core.collectors.normalize import hypervisor_name

    info = conn.cluster_info or {}
    types = info.get("hypervisor_types") or []
    return RegisteredCluster(
        uuid=str(info.get("uuid") or info.get("cluster_uuid") or info.get("id") or ""),
        name=info.get("name"),
        aos_version=conn.aos_version,
        num_nodes=info.get("num_nodes"),
        hypervisors=sorted({n for t in types if t and (n := hypervisor_name(t))}),
    )


# ─── a run's scope ─────────────────────────────────────────────────────


class Scope:
    """One run's cluster selection, resolved against the registered clusters."""

    def __init__(self, registered: list[RegisteredCluster], selected: list[str] | None) -> None:
        self.registered = {c.uuid: c for c in registered}
        wanted = set(self.registered) if selected is None else set(selected)
        self.selected = {u for u in self.registered if u in wanted}
        self.excluded = set(self.registered) - self.selected
        selected_names = {(self.registered[u].name or "").casefold() for u in self.selected}
        # Matching by name is the fallback for objects that carry no cluster uuid. A name
        # shared by a selected and an excluded cluster cannot decide anything: keep those.
        self._excluded_names = {
            (self.registered[u].name or "").casefold() for u in self.excluded
        } - selected_names - {""}
        #: v4 paths whose `$filter` the server rejected (fell back to client filtering).
        self.filter_rejected: set[str] = set()
        #: v4 paths filtered server-side during the run.
        self.filtered_paths: set[str] = set()

    @property
    def filtering(self) -> bool:
        """True when some registered cluster is left out (otherwise nothing to do)."""
        return bool(self.excluded)

    def odata(self, field_path: str) -> str | None:
        """A v4 ``$filter`` keeping only the selected clusters, or None when unscoped."""
        if not self.filtering or not self.selected:
            return None
        return " or ".join(f"{field_path} eq '{u}'" for u in sorted(self.selected))

    def excludes(self, uuid: str | None, name: str | None = None) -> bool:
        """Does an object with this cluster uuid / name belong to a left-out cluster?"""
        if uuid:
            return uuid in self.excluded
        if name:
            # Some collectors fall back to the raw uuid when they cannot name a cluster.
            return name.casefold() in self._excluded_names or name in self.excluded
        return False

    def prune(self, env: Environment) -> int:
        """Drop every object of a left-out cluster from `env`; returns how many went."""
        if not self.filtering:
            return 0
        removed = 0

        kept_clusters = [c for c in env.clusters if c.uuid not in self.excluded]
        removed += len(env.clusters) - len(kept_clusters)
        env.clusters[:] = kept_clusters

        for name in CLUSTER_BOUND_FIELDS:
            items = getattr(env, name)
            kept = [o for o in items if not self.excludes(getattr(o, "cluster_uuid", None),
                                                          getattr(o, "cluster_name", None))]
            if len(kept) != len(items):
                removed += len(items) - len(kept)
                items[:] = kept

        # Shares and buckets follow the file server / object store they live on.
        servers = {s.name for s in env.file_servers}
        kept_shares = [s for s in env.file_shares
                       if not s.file_server_name or s.file_server_name in servers]
        removed += len(env.file_shares) - len(kept_shares)
        env.file_shares[:] = kept_shares
        stores = {s.name for s in env.object_stores}
        kept_buckets = [b for b in env.buckets
                        if not b.object_store_name or b.object_store_name in stores]
        removed += len(env.buckets) - len(kept_buckets)
        env.buckets[:] = kept_buckets

        # Images are placed on clusters: keep the selected placements, drop an image that
        # lives only on left-out clusters (an image with no placement is PC-wide).
        kept_images = []
        for image in env.images:
            if image.cluster_uuids:
                placements = [u for u in image.cluster_uuids if u not in self.excluded]
                if not placements:
                    removed += 1
                    continue
                image.cluster_uuids = placements
            if image.cluster_names:
                image.cluster_names = [n for n in image.cluster_names
                                       if not self.excludes(None, n)]
            kept_images.append(image)
        env.images[:] = kept_images

        if removed:
            from ntnxvision_core.collectors.alerts import summarize

            env.alerts_summary = summarize(env.alerts)
        return removed

    def record(self) -> ClusterScope:
        def entry(uuid: str) -> ScopeCluster:
            return ScopeCluster(uuid=uuid, name=self.registered[uuid].name)

        def ordered(uuids: set[str]) -> list[ScopeCluster]:
            return [entry(u) for u in sorted(
                uuids, key=lambda u: ((self.registered[u].name or "").casefold(), u))]

        return ClusterScope(selected=ordered(self.selected), excluded=ordered(self.excluded),
                            registered_count=len(self.registered))


def v4_list_scoped(conn: PrismConnection, path: str, field_path: str, *,
                   tolerant: bool = False) -> tuple[list[dict[str, Any]], int]:
    """List a v4 collection, asking the server for the selected clusters only.

    Returns ``(items, skipped)`` like `PrismConnection.v4_list_tolerant`. The filter is
    tried with one cheap ``$limit=1`` request first: a PC that rejects it (HTTP 400, a
    field it does not filter on) is read unfiltered instead, and `Scope.prune` still
    removes the left-out clusters' objects afterwards. Either way the result is correct;
    the filter only saves transfer on large estates.
    """
    flt = server_filter(conn, path, field_path)
    # Only pass `filter` when there is one: test doubles mimic the unfiltered signature.
    if tolerant:
        return conn.v4_list_tolerant(path, filter=flt) if flt else conn.v4_list_tolerant(path)
    return (conn.v4_list(path, filter=flt) if flt else conn.v4_list(path)), 0


def server_filter(conn: PrismConnection, path: str, field_path: str) -> str | None:
    """The ``$filter`` to send for `path`, or None (unscoped run, or rejected by the PC).

    Probed once per path with ``$limit=1``: HTTP 400 means this PC does not filter on
    `field_path`, so the caller reads unfiltered and `Scope.prune` does the work.
    """
    scope: Scope | None = getattr(conn, "scope", None)
    if scope is None or path in scope.filter_rejected:
        return None
    flt = scope.odata(field_path)
    if not flt:
        return None
    if path in scope.filtered_paths:
        return flt
    try:
        conn.v4_get(path, params={"$limit": 1, "$filter": flt})
    except NutanixApiError as exc:
        if exc.status_code != 400:
            raise
        scope.filter_rejected.add(path)
        return None
    scope.filtered_paths.add(path)
    return flt


# ─── wording ───────────────────────────────────────────────────────────


def names_label(names: list[str], limit: int = 4) -> str:
    shown = [n for n in names if n]
    if len(shown) <= limit:
        return ", ".join(shown)
    return f"{', '.join(shown[:limit])} + {fmt_count(len(shown) - limit)} more"


def describe(scope: ClusterScope | None, *, limit: int | None = 4) -> str | None:
    """"3 of 5 clusters (PHX-PROD-01, PHX-PROD-02, PHX-DR-01)", "All 4 clusters",
    or None for a snapshot without a recorded scope."""
    if scope is None:
        return None
    names = [c.name or c.uuid for c in scope.selected]
    total = scope.registered_count or len(names)
    if not scope.is_partial:
        return "1 cluster" if total == 1 else f"All {fmt_count(total)} clusters"
    label = ", ".join(names) if limit is None else names_label(names, limit)
    return f"{fmt_count(len(names))} of {fmt_count(total)} clusters ({label})"


def scope_dict(scope: ClusterScope | None) -> dict[str, Any] | None:
    """The scope as run metadata / IPC carries it."""
    if scope is None:
        return None
    return {
        "selected": [c.model_dump() for c in scope.selected],
        "excluded": [c.model_dump() for c in scope.excluded],
        "registered_count": scope.registered_count,
        "label": describe(scope),
    }
