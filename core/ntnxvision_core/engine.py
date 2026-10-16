"""Collection engine — orchestrates connect → collect → assemble Environment.

Runs each collector in order, isolating failures so one broken collector never aborts
the whole run (its error is recorded in `env.collection_notes`). Emits progress via an
optional callback so the UI/CLI can show a live progress bar.

NDB is optional and runs against a separate Era endpoint when credentials are supplied.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from functools import partial
from typing import Any

from ntnxvision_core.api import PrismConnection, PrismCredentials
from ntnxvision_core.api.element_client import ElementConnection, ElementCredentials
from ntnxvision_core.api.ndb_client import NdbConnection, NdbCredentials
from ntnxvision_core.areas import AREAS, NDB_AREA, Area
from ntnxvision_core.areas import select as select_areas
from ntnxvision_core.cancel import CancelToken, RunCancelled
from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.collectors.element import ELEMENT_STEPS
from ntnxvision_core.collectors.ndb import collect_ndb
from ntnxvision_core.compat import check_aos, check_prism_central
from ntnxvision_core.models import (
    Environment,
    PrismCentral,
    SourcedObject,
    SourceEnvironment,
)
from ntnxvision_core.runinfo import RunTracker, classify

# progress(step_index, total_steps, collector_label, status)
ProgressFn = Callable[[int, int, str, str], None]


def collect_environment(
    creds: PrismCredentials,
    collectors: list[type[Collector]] | None = None,
    progress: ProgressFn | None = None,
    ndb_creds: NdbCredentials | None = None,
    *,
    areas: list[str] | None = None,
    cancel: CancelToken | None = None,
    tracker: RunTracker | None = None,
) -> Environment:
    """Connect to Prism Central and run the selected collectors into an Environment.

    * `areas` limits the run to those area ids (see `ntnxvision_core.areas`); None or
      empty runs everything. `collectors` (an explicit class list) is the older,
      lower-level way to do the same and wins when given.
    * `cancel` is checked before every request and between collectors; a cancelled run
      raises `RunCancelled` and returns nothing — callers must not save partial data.
    * `tracker` receives the per-area progress (status, API, objects, duration).
    * `progress` is the legacy 4-argument callback the CLI uses.

    If `ndb_creds` is provided, an additional NDB step runs against the Era control
    plane after the Prism Central collectors.
    """
    if collectors is not None:
        run_areas = [a for a in AREAS if a.collector in collectors]
        run_areas += [
            Area(c.name, c.label, "Other", c, c.name)
            for c in collectors
            if c not in {a.collector for a in run_areas}
        ]
    else:
        run_areas = select_areas(areas, "prism_central")
    env = Environment(
        collected_at=datetime.now(timezone.utc).isoformat(),
        prism_central=PrismCentral(host=creds.host),
    )

    planned = [*run_areas, *([NDB_AREA] if ndb_creds else [])]
    total = len(planned)

    with PrismConnection(creds, cancel=cancel) as conn:
        env.prism_central.version = conn.pc_version

        # Compatibility check (warn but proceed).
        pc_compat = check_prism_central(conn.pc_version)
        env.prism_central.version_supported = pc_compat.in_range
        if pc_compat.warning:
            env.warnings.append(pc_compat.warning)

        if tracker is not None:
            tracker.start(planned, conn.pc_version)

        try:
            for idx, area in enumerate(run_areas):
                if cancel is not None:
                    cancel.check()
                assert area.collector is not None
                collector = area.collector(conn)
                _run_area(env, area, idx, total, partial(collector.collect, env),
                          progress, tracker, conn.negotiated, collector.label,
                          collector.name)
        except RunCancelled:
            if tracker is not None:
                tracker.cancel_pending()
            raise
        finally:
            # Read *after* the collectors run: v4 availability is negotiated lazily, on
            # the first call that asks for a feature area, so nothing is known at
            # connect time.
            env.prism_central.negotiated_api_versions = dict(conn.negotiated)

    if ndb_creds:
        if cancel is not None and cancel.cancelled:
            if tracker is not None:
                tracker.cancel_pending()
            raise RunCancelled("Run cancelled")
        _collect_ndb_step(ndb_creds, env, progress, len(run_areas), total, tracker, cancel)

    _resolve_cross_references(env)
    return env


def collect_element_environment(
    creds: ElementCredentials,
    progress: ProgressFn | None = None,
    *,
    areas: list[str] | None = None,
    cancel: CancelToken | None = None,
    tracker: RunTracker | None = None,
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
    selected = {a.id for a in select_areas(areas, "prism_element")}
    steps = [s for s in ELEMENT_STEPS if s[0] in selected]
    by_id = {a.id: a for a in AREAS}
    with ElementConnection(creds, cancel=cancel) as conn:
        env.prism_central.version = conn.aos_version  # AOS version for PE mode

        aos_compat = check_aos(conn.aos_version)
        env.prism_central.version_supported = aos_compat.in_range
        if aos_compat.warning:
            env.warnings.append(aos_compat.warning)

        if tracker is not None:
            tracker.start([by_id[s[0]] for s in steps], conn.aos_version)
        try:
            for idx, (area_id, label, fn) in enumerate(steps):
                if cancel is not None:
                    cancel.check()
                area = by_id[area_id]
                _run_area(env, area, idx, len(steps), partial(fn, conn, env), progress,
                          tracker, {}, label, area_id, api_override="v2")
        except RunCancelled:
            if tracker is not None:
                tracker.cancel_pending()
            raise

    _resolve_cross_references(env)
    return env


def _run_area(
    env: Environment,
    area: Area,
    idx: int,
    total: int,
    work: Callable[[], None],
    progress: ProgressFn | None,
    tracker: RunTracker | None,
    negotiated: dict[str, str],
    label: str,
    note_key: str,
    api_override: str | None = None,
) -> None:
    """Run one area, isolating its failure, and report it to both callbacks."""
    before = {f: len(getattr(env, f)) for f in area.fields}
    _emit(progress, idx, total, label, "running")
    if tracker is not None:
        tracker.begin(area.id)
    try:
        work()
        note = env.collection_notes.get(note_key, "ok")
        _emit(progress, idx, total, label, note)
    except RunCancelled:
        raise
    except Exception as exc:  # noqa: BLE001 — isolate collector failures
        note = f"error: {exc}"
        env.collection_notes[note_key] = note
        _emit(progress, idx, total, label, note)
    if tracker is None:
        return
    status, detail = classify(area.id, note)
    added = [item for f in area.fields for item in getattr(env, f)[before[f]:]]
    api = api_override or _area_api(area, added, negotiated)
    tracker.end(area.id, status, api=api, objects=len(added), detail=detail,
                clusters=len(env.clusters))


def _area_api(area: Area, added: list[Any], negotiated: dict[str, str]) -> str:
    """Which API actually served an area: from the objects' provenance when there are
    any, else from the namespace negotiation."""
    sources = {str(getattr(o, "api_source", "") or "") for o in added}
    if any(s.startswith("v4") for s in sources):
        return "v4"
    if any(s.startswith("v2") for s in sources):
        return "v2"
    if "ndb" in sources:
        return "era"
    if sources - {""}:
        return "v3"
    if area.v4_area and negotiated.get(area.v4_area) == "v4":
        return "v4"
    return area.legacy_api


def combine_environments(envs: list[Environment]) -> Environment:
    """Merge independently-collected Environments (one per Prism Central) into one.

    Every source's objects are concatenated as-is — Nutanix UUIDs are globally unique,
    so no cross-source collisions are expected; nothing is re-resolved or deduplicated.
    Object *names* are not unique across PCs though (two estates both running a
    "Cluster01" is routine), so each merged object is stamped with its source PC in
    `source_pc` and the exporters render that as a column — without it two identical-
    looking rows would be indistinguishable in a report.

    Only the roll-up summary counters are actually merged (summed), and each source's
    `collection_notes` are namespaced by its PC host so two PCs reporting the same
    collector differently don't overwrite each other.

    The combined `pc_config` is deliberately left empty: a combined run has no single
    management plane, and copying the first PC's sizing into it would make the reports
    state one PC's node count as the whole estate's. Per-PC version, sizing and counts
    are preserved in `source_environments` instead.
    """
    if not envs:
        raise ValueError("combine_environments requires at least one Environment")
    if len(envs) == 1:
        return envs[0]

    combined = Environment(
        collected_at=datetime.now(timezone.utc).isoformat(),
        prism_central=PrismCentral(host=f"{len(envs)} Prism Centrals (combined)"),
    )

    list_fields = [
        name for name, field in Environment.model_fields.items()
        if str(field.annotation).startswith("list[") and name != "source_environments"
    ]
    for field_name in list_fields:
        merged: list[Any] = []
        for env in envs:
            label = env.prism_central.host or "unknown"
            for item in getattr(env, field_name):
                # Tag top-level objects with the PC they came from. Plain values
                # (e.g. warnings, a list[str]) merge without attribution.
                if isinstance(item, SourcedObject) and item.source_pc is None:
                    item.source_pc = label
                merged.append(item)
        setattr(combined, field_name, merged)

    for summary_name in ("data_protection", "flow", "alerts_summary"):
        target = getattr(combined, summary_name)
        for env in envs:
            source = getattr(env, summary_name)
            for key in type(target).model_fields:
                setattr(target, key, getattr(target, key) + getattr(source, key))

    for env in envs:
        label = env.prism_central.host or "unknown"
        for key, status in env.collection_notes.items():
            combined.collection_notes[f"{label}: {key}"] = status

    combined.source_environments = [
        SourceEnvironment(
            prism_central=env.prism_central,
            pc_config=env.pc_config,
            summary=env.summary(),
        )
        for env in envs
    ]
    return combined


def _collect_ndb_step(
    ndb_creds: NdbCredentials,
    env: Environment,
    progress: ProgressFn | None,
    idx: int,
    total: int,
    tracker: RunTracker | None = None,
    cancel: CancelToken | None = None,
) -> None:
    def work() -> None:
        with NdbConnection(ndb_creds, cancel=cancel) as ndb:
            collect_ndb(ndb, env)

    try:
        _run_area(env, NDB_AREA, idx, total, work, progress, tracker, {},
                  "NDB (Databases)", "ndb", api_override="era")
    except RunCancelled:
        if tracker is not None:
            tracker.cancel_pending()
        raise


def _resolve_cross_references(env: Environment) -> None:
    """Fill in host counts on clusters and backfill name/VLAN fields after all data is in."""
    # uuid → name maps used to backfill fields that collectors only had as uuids.
    cluster_name_by_uuid = {c.uuid: c.name for c in env.clusters if c.uuid and c.name}
    host_name_by_uuid = {h.uuid: h.name for h in env.hosts if h.uuid and h.name}
    host_cluster_by_uuid = {h.uuid: h.cluster_uuid for h in env.hosts if h.uuid}
    vlan_by_subnet_uuid = {
        s.uuid: s.vlan_id for s in env.subnets if s.uuid and s.vlan_id is not None
    }
    # v4 references subnets and storage containers by uuid only, where v3 embedded the
    # name alongside it. Resolve both here so a VM reads the same either way.
    subnet_name_by_uuid = {s.uuid: s.name for s in env.subnets if s.uuid and s.name}
    container_name_by_uuid = {
        c.uuid: c.name for c in env.storage_containers if c.uuid and c.name
    }

    # For a single-cluster (PE) run, every host/VM belongs to that one cluster.
    sole_cluster = env.clusters[0] if len(env.clusters) == 1 else None

    host_by_cluster: dict[str, int] = {}
    for host in env.hosts:
        if not host.cluster_uuid and sole_cluster:
            host.cluster_uuid = sole_cluster.uuid
        if not host.cluster_name and host.cluster_uuid:
            host.cluster_name = cluster_name_by_uuid.get(host.cluster_uuid) or (
                sole_cluster.name if sole_cluster else None
            )
        if host.cluster_uuid:
            host_by_cluster[host.cluster_uuid] = host_by_cluster.get(host.cluster_uuid, 0) + 1
    for cluster in env.clusters:
        if not cluster.num_nodes and cluster.uuid in host_by_cluster:
            cluster.num_nodes = host_by_cluster[cluster.uuid]
        # v3 cluster config often lacks node_list/hypervisor data — derive from hosts.
        if not cluster.hypervisor_types:
            cluster.hypervisor_types = sorted(
                {
                    h.hypervisor_type
                    for h in env.hosts
                    if h.cluster_uuid == cluster.uuid and h.hypervisor_type
                }
            )

    _reclassify_pc_vms(env)

    # Backfill VM host/cluster names (collectors on some paths only capture uuids).
    for vm in [*env.vms, *env.system_vms]:
        if not vm.host_name and vm.host_uuid:
            vm.host_name = host_name_by_uuid.get(vm.host_uuid)
        if not vm.cluster_uuid and vm.host_uuid:
            vm.cluster_uuid = host_cluster_by_uuid.get(vm.host_uuid)
        if not vm.cluster_uuid and sole_cluster:
            vm.cluster_uuid = sole_cluster.uuid
        if not vm.cluster_name:
            vm.cluster_name = cluster_name_by_uuid.get(vm.cluster_uuid or "") or (
                sole_cluster.name if sole_cluster else None
            )
        # Backfill each NIC's VLAN and subnet name from its referenced subnet.
        for nic in vm.nics:
            if nic.vlan_id is None and nic.subnet_uuid:
                nic.vlan_id = vlan_by_subnet_uuid.get(nic.subnet_uuid)
            if not nic.subnet_name and nic.subnet_uuid:
                nic.subnet_name = subnet_name_by_uuid.get(nic.subnet_uuid)
        for disk in vm.disks:
            if not disk.storage_container_name and disk.storage_container_uuid:
                disk.storage_container_name = container_name_by_uuid.get(
                    disk.storage_container_uuid
                )

    # Per-host VM counts: v3 hosts/list often omits num_vms — count what we collected.
    vms_per_host: dict[str, int] = {}
    for vm in [*env.vms, *env.system_vms]:
        key = vm.host_uuid or vm.host_name
        if key:
            vms_per_host[key] = vms_per_host.get(key, 0) + 1
    for host in env.hosts:
        if host.num_vms is None:
            host.num_vms = vms_per_host.get(host.uuid) or (
                vms_per_host.get(host.name) if host.name else None
            )

    # Image placement: the collector records cluster uuids; resolve to names here.
    for image in env.images:
        if not image.cluster_names and image.cluster_uuids:
            image.cluster_names = [
                cluster_name_by_uuid.get(u, u) for u in image.cluster_uuids
            ]

    # Alerts carry only a cluster uuid; resolve it to the friendly name here.
    for alert in env.alerts:
        if not alert.cluster_name and alert.cluster_uuid:
            alert.cluster_name = cluster_name_by_uuid.get(alert.cluster_uuid)

    # VPC → overlay subnets (the subnet collector already knows its vpc).
    for vpc in env.vpcs:
        if not vpc.subnet_names:
            vpc.subnet_names = sorted(
                s.name for s in env.subnets if s.name and s.vpc_name == vpc.name
            )

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

    _resolve_availability_zone_names(env)

    # Roll up protected-VM count from protection policies' category matches is non-trivial;
    # approximate with VMs that carry any category (best-effort until v4 DR SDK).
    env.data_protection.protected_vm_count = sum(1 for v in env.vms if v.categories)


def _reclassify_pc_vms(env: Environment) -> None:
    """Move the Prism Central VM(s) from user VMs to system VMs by IP match.

    PC VMs carry arbitrary names, so name-based classification misses them. The PC's
    own IPs are known (the connect host + the multicluster-reported addresses); any VM
    holding one of those IPs is the Prism Central VM.
    """
    pc_ips = {ip for ip in env.pc_config.ip_addresses if ip}
    if env.prism_central.host:
        pc_ips.add(env.prism_central.host)
    if not pc_ips:
        return
    still_user: list[Any] = []
    for vm in env.vms:
        vm_ips = {ip for nic in vm.nics for ip in nic.ip_addresses}
        if vm_ips & pc_ips:
            vm.vm_type = "PRISM_CENTRAL"
            env.system_vms.append(vm)
        else:
            still_user.append(vm)
    if len(still_user) != len(env.vms):
        env.vms[:] = still_user

    # The pc_config collector sized the PC before this reclassification could run —
    # fill in anything it missed now that the PC VMs are identified.
    cfg = env.pc_config
    pc_vms = [v for v in env.system_vms if v.vm_type == "PRISM_CENTRAL"]
    if pc_vms:
        if not cfg.pc_vm_names:
            cfg.pc_vm_names = [v.name for v in pc_vms if v.name]
        if cfg.num_vcpus is None:
            cfg.num_vcpus = sum(v.vcpu_total or 0 for v in pc_vms) or None
        if cfg.memory_bytes is None:
            cfg.memory_bytes = sum(v.memory_bytes or 0 for v in pc_vms) or None
        if cfg.node_count is None:
            cfg.node_count = len(pc_vms)
            cfg.is_scale_out = len(pc_vms) > 1


def _resolve_availability_zone_names(env: Environment) -> None:
    """Replace raw AZ identifiers (UUIDs / URLs) on policies & plans with friendly names.

    Protection policies and recovery plans reference AZs by an opaque
    `availability_zone_url` (a UUID for the local AZ, a management URL for remote).
    Build a lookup from every identifier we know for each AZ to its display name, and
    label the local AZ as 'Local (<PC>)' when it matches the Prism Central.
    """
    az_names: dict[str, str] = {}
    local_id = env.prism_central.uuid
    for az in env.availability_zones:
        display = az.name or az.region or "Availability Zone"
        for ident in (az.uuid, az.url):
            if ident:
                az_names[ident] = display
    # The local AZ often appears as the PC's own uuid with no matching AZ entry.
    if local_id:
        az_names.setdefault(local_id, f"Local ({env.prism_central.host or 'PC'})")

    def resolve(ref: str | None) -> str | None:
        if not ref:
            return ref
        if ref in az_names:
            return az_names[ref]
        # A management URL like https://<host>:9440 → keep the host.
        if ref.startswith("http"):
            return ref.split("//", 1)[-1].split(":")[0].split("/")[0]
        return ref  # leave as-is if truly unknown

    for pp in env.protection_policies:
        pp.source_az = resolve(pp.source_az)
        pp.target_az = resolve(pp.target_az)
    for rp in env.recovery_plans:
        rp.primary_location = resolve(rp.primary_location)
        rp.recovery_location = resolve(rp.recovery_location)


def _emit(progress: ProgressFn | None, idx: int, total: int, label: str, status: str) -> None:
    if progress is not None:
        progress(idx, total, label, status)
