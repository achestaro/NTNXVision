"""Live metrics of one Prism Central target, for Insights > Metrics.

Read-only, and only requests the transport guard already allows: the v3 groups query
(``POST /api/nutanix/v3/groups``), the registered-cluster list (`scope.registered_clusters`:
v4 ``clustermgmt`` when served, else v3 ``clusters/list``) and GETs of Prism Element v2.0
through the Prism Central proxy. Every attribute below was checked against a live
pc.7.5.1.1 (an AHV and an ESXi cluster) before it was used; the groups API is the base
across the supported range (pc.2024.1 and later).

=========================  ==================================================================
Card                       Source
=========================  ==================================================================
Utilization, efficiency    groups ``cluster``: ``hypervisor_cpu_usage_ppm``,
                           ``hypervisor_memory_usage_ppm``, ``storage.usage_bytes`` /
                           ``capacity_bytes`` / ``free_bytes`` / ``logical_usage_bytes``,
                           ``data_reduction.saving_ratio_ppm`` (compression + dedup + EC),
                           ``data_reduction.overall.saving_ratio_ppm`` (incl. thin
                           provisioning, clones, snapshots), ``data_reduction.saved_bytes``
Performance                groups ``cluster``: ``controller_num_iops`` (+ read / write),
                           ``controller_avg_io_latency_usecs``, ``controller_io_bandwidth_kBps``
Trends, runway             the same groups query with ``interval_start_ms`` /
                           ``interval_end_ms`` and ``downsampling_interval`` 86 400 (one
                           point per day, 90 days); runway from `runway.py`
Top VMs                    groups ``mh_vm`` grouped by ``cluster``, sorted descending on
                           ``hypervisor_cpu_usage_ppm`` / ``memory_usage_ppm`` /
                           ``controller_num_iops`` / ``controller_avg_io_latency_usecs``,
                           filter ``is_cvm==0;power_state==on``
Alerts                     groups ``alert``, filter ``resolved==false;severity==critical``
                           (list) and counts per ``originating_cluster_uuid``
Nodes                      groups ``host`` (``name``, ``node_status``, ``host_maintenance_state``,
                           CPU / memory ppm) and PE v2.0 ``hosts`` (``state``, ``is_degraded``,
                           ``metadata_store_status``, ``host_in_maintenance_mode``,
                           ``service_vmexternal_ip``)
Resiliency                 PE v2.0 ``cluster``: ``cluster_redundancy_state``
Network                    PE v2.0 ``hosts/{uuid}/host_nics``: each NIC's ``stats``
                           (``network.received_rate_kBps``, ``transmitted_rate_kBps``,
                           ``error_*_pkts``, ``dropped_*_pkts``; -1 = not reported)
=========================  ==================================================================

Values arrive as strings; a negative number is the API's "not reported" and becomes None.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from typing import Any

from ntnxvision_core.api import PrismConnection
from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.cancel import RunCancelled
from ntnxvision_core.capacity import STORAGE_RESERVE_FACTOR
from ntnxvision_core.metrics.runway import DAY_MS, RANGES, runways
from ntnxvision_core.scope import RegisteredCluster, registered_clusters
from ntnxvision_core.versions import pc_version

#: Days of daily history fetched once; the page slices 30 / 60 / 90 from it.
HISTORY_DAYS = max(RANGES)
#: VMs per top list, per cluster.
TOP_N = 5
#: Critical alerts listed (the count is exact whatever this is).
ALERT_LIST = 50

_CLUSTER_NOW = [
    "cluster_name", "hypervisor_cpu_usage_ppm", "hypervisor_memory_usage_ppm",
    "storage.usage_bytes", "storage.capacity_bytes", "storage.free_bytes",
    "storage.logical_usage_bytes", "controller_num_iops", "controller_num_read_iops",
    "controller_num_write_iops", "controller_avg_io_latency_usecs",
    "controller_io_bandwidth_kBps", "data_reduction.saving_ratio_ppm",
    "data_reduction.overall.saving_ratio_ppm", "data_reduction.saved_bytes",
    "data_reduction.compression.saving_ratio_ppm", "data_reduction.dedup.saving_ratio_ppm",
    "data_reduction.erasure_coding.saving_ratio_ppm", "num_cpus", "memory_capacity_bytes",
    "capacity.runway", "capacity.cpu_runway", "capacity.memory_runway",
    "capacity.storage_runway",
]
_CLUSTER_SERIES = [
    "hypervisor_cpu_usage_ppm", "hypervisor_memory_usage_ppm", "storage.usage_bytes",
    "controller_num_iops", "controller_avg_io_latency_usecs",
]
_HOST = ["name", "cluster", "node_status", "host_maintenance_state",
         "hypervisor_cpu_usage_ppm", "hypervisor_memory_usage_ppm", "num_vms"]
_VM = ["vm_name", "cluster", "hypervisor_cpu_usage_ppm", "memory_usage_ppm",
       "controller_num_iops", "controller_avg_io_latency_usecs", "num_vcpus",
       "memory_size_bytes"]
#: Top list key → the groups attribute it sorts on.
TOP_METRICS = {
    "cpu": "hypervisor_cpu_usage_ppm",
    "memory": "memory_usage_ppm",
    "iops": "controller_num_iops",
    "latency": "controller_avg_io_latency_usecs",
}
_ALERT = ["title", "severity", "originating_cluster_uuid", "cluster_name",
          "source_entity_name", "source_entity_type", "_created_timestamp_usecs_",
          "param_name_list", "param_value_list", "acknowledged", "impact_type"]


# ─── groups helpers ────────────────────────────────────────────────────


def _groups(conn: PrismConnection, body: dict[str, Any]) -> dict[str, Any]:
    return conn.post_json(f"{conn.creds.base}/api/nutanix/v3/groups", body)


def _attrs(names: list[str]) -> list[dict[str, str]]:
    return [{"attribute": a} for a in names]


def _entities(page: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for g in page.get("group_results") or []
            for e in g.get("entity_results") or [] if isinstance(e, dict)]


def _values(entity: dict[str, Any], name: str) -> list[dict[str, Any]]:
    for data in entity.get("data") or []:
        if data.get("name") == name:
            return [v for v in data.get("values") or [] if isinstance(v, dict)]
    return []


def _first(entity: dict[str, Any], name: str) -> Any:
    vals = _values(entity, name)
    inner = (vals[0].get("values") or []) if vals else []
    return inner[0] if inner else None


def _all(entity: dict[str, Any], name: str) -> list[Any]:
    vals = _values(entity, name)
    return list((vals[0].get("values") or []) if vals else [])


def num(value: Any) -> float | None:
    """A groups / v2.0 value as a number; None when missing or negative ("not reported")."""
    if value is None or isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def _int(value: Any) -> int | None:
    n = num(value)
    return int(n) if n is not None else None


def _pct(ppm: Any) -> float | None:
    n = num(ppm)
    return round(n / 10_000, 2) if n is not None else None


def _ratio(ppm: Any) -> float | None:
    n = num(ppm)
    return round(n / 1_000_000, 2) if n is not None and n > 0 else None


def series(entity: dict[str, Any], name: str) -> list[tuple[float, float | None]]:
    """A time-series attribute as ascending (epoch ms, value) points.

    The groups API answers newest first with ``time`` in microseconds.
    """
    out: list[tuple[float, float | None]] = []
    for point in _values(entity, name):
        t = point.get("time")
        inner = point.get("values") or []
        if not isinstance(t, (int, float)):
            continue
        out.append((t / 1000.0, num(inner[0]) if inner else None))
    out.sort(key=lambda p: p[0])
    return out


def render_title(title: str | None, names: list[Any], values: list[Any]) -> str:
    """An alert title with its ``{placeholders}`` filled from the alert's parameters."""
    params = {str(n): str(v) for n, v in zip(names, values, strict=False) if n}

    def sub(m: re.Match[str]) -> str:
        return params.get(m.group(1), m.group(0))

    return re.sub(r"\{([A-Za-z0-9_]+)\}", sub, title or "").strip() or "Alert"


def _iso_usecs(value: Any) -> str | None:
    n = num(value)
    if n is None:
        return None
    return datetime.fromtimestamp(n / 1_000_000, tz=UTC).isoformat(timespec="seconds")


# ─── collection ────────────────────────────────────────────────────────


class _Notes:
    """Partial failures: one card's source failing never fails the page."""

    def __init__(self) -> None:
        self.items: list[str] = []

    def run(self, what: str, fn: Callable[[], Any], default: Any = None) -> Any:
        try:
            return fn()
        except RunCancelled:
            raise
        except NutanixApiError as exc:
            self.items.append(f"{what}: {exc}"[:300])
        except (KeyError, TypeError, ValueError) as exc:
            self.items.append(f"{what}: unexpected response ({type(exc).__name__})")
        return default


#: The two parts of the page: ``now`` (every card but trends, a few seconds) and
#: ``trends`` (90 days of daily points and the runway computed from them). Prism Central
#: answers a 90-day series query in several seconds per attribute (13 s for the five here
#: on the lab PC, and in series even when sent in parallel), so the page asks for the two
#: parts at once and shows the cards while the trends are still on their way.
PARTS = ("now", "trends")


def collect(conn: PrismConnection, part: str = "now",
            now_ms: float | None = None) -> dict[str, Any]:
    """One part's data for the connected Prism Central (see the module table)."""
    if part not in PARTS:
        raise ValueError(f"Unknown metrics part {part!r}")
    now_ms = now_ms if now_ms is not None else time.time() * 1000
    notes = _Notes()
    registered, list_api = registered_clusters(conn)
    known = {c.uuid for c in registered}
    now_rows = {e.get("entity_id"): e for e in _entities(notes.run(
        "Cluster utilization", lambda: _groups(conn, {
            "entity_type": "cluster", "group_member_count": 500,
            "group_member_attributes": _attrs(_CLUSTER_NOW)}), {}))}
    head = {
        "part": part,
        "host": conn.creds.host,
        "version": pc_version(conn.pc_version),
        "collected_at": datetime.fromtimestamp(now_ms / 1000, tz=UTC)
        .isoformat(timespec="seconds"),
        "ranges": list(RANGES),
        "history_days": HISTORY_DAYS,
    }
    if part == "trends":
        start_ms = int(now_ms - HISTORY_DAYS * DAY_MS)
        ts_rows = {e.get("entity_id"): e for e in _entities(notes.run(
            "Cluster trends", lambda: _groups(conn, {
                "entity_type": "cluster", "group_member_count": 500,
                "group_member_attributes": _attrs(_CLUSTER_SERIES),
                "interval_start_ms": start_ms, "interval_end_ms": int(now_ms),
                "downsampling_interval": 86_400}), {}))}
        trend_rows = [_cluster_trends(rc, now_rows.get(rc.uuid), ts_rows.get(rc.uuid))
                      for rc in registered]
        return {**head, "clusters": trend_rows,
                "total": _total_trends(trend_rows) if trend_rows else None,
                "notes": notes.items}

    hosts = _entities(notes.run("Nodes", lambda: _groups(conn, {
        "entity_type": "host", "group_member_count": 500,
        "group_member_attributes": _attrs(_HOST)}), {}))
    top: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for key, attr in TOP_METRICS.items():
        top[key] = notes.run(f"Top VMs by {key}", partial(_top_vms, conn, attr), {})
    alerts = notes.run("Alerts", lambda: _alerts(conn, known), _empty_alerts())
    clusters = [_cluster(conn, rc, now_rows.get(rc.uuid),
                         [h for h in hosts if _first(h, "cluster") == rc.uuid],
                         {k: v.get(rc.uuid, []) for k, v in top.items()}, alerts, notes)
                for rc in registered]
    return {
        **head,
        "clusters": clusters,
        "total": _total(clusters) if clusters else None,
        "alerts": {"critical": alerts["critical"], "critical_count": alerts["critical_count"],
                   "warning_count": alerts["warning_count"],
                   "listed": len(alerts["critical"])},
        "sources": _sources(list_api),
        "notes": notes.items,
    }


def _capacity_weights(now: dict[str, Any] | None) -> dict[str, int | None]:
    e = now or {}
    return {"cpu_count": _int(_first(e, "num_cpus")),
            "memory_bytes": _int(_first(e, "memory_capacity_bytes")),
            "storage_capacity_bytes": _int(_first(e, "storage.capacity_bytes"))}


def _cluster_trends(rc: RegisteredCluster, now: dict[str, Any] | None,
                    ts: dict[str, Any] | None) -> dict[str, Any]:
    weights = _capacity_weights(now)
    trends = _trends(ts, weights["storage_capacity_bytes"])
    return {"uuid": rc.uuid, "name": rc.name or rc.uuid, "weights": weights,
            "trends": trends, "runway": _cluster_runways(trends)}


def _total_trends(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The target's daily series: CPU weighted by CPU count, memory by memory capacity,
    storage by storage capacity (so storage % is total used over total capacity). A day
    missing on any cluster is left out."""
    sets = [set(r["trends"]["t"]) for r in rows]
    times = sorted(set.intersection(*sets)) if all(sets) else []
    index = [{t: i for i, t in enumerate(r["trends"]["t"])} for r in rows]

    def day(col: str, weight: str, t: int) -> float | None:
        acc, tw = 0.0, 0.0
        for r, idx in zip(rows, index, strict=True):
            v = r["trends"][col][idx[t]]
            w = r["weights"].get(weight)
            if v is None or not w:
                return None
            acc, tw = acc + v * w, tw + w
        return round(acc / tw, 2) if tw else None

    def perf(t: int) -> tuple[int | None, int | None]:
        """Target IOPS (sum) and IOPS-weighted latency for one day."""
        iops = [r["trends"]["iops"][idx[t]] for r, idx in zip(rows, index, strict=True)]
        lat = [r["trends"]["latency_usecs"][idx[t]] for r, idx in zip(rows, index, strict=True)]
        if any(v is None for v in iops):
            return None, None
        total = sum(v for v in iops if v is not None)
        pairs = [(la, io) for la, io in zip(lat, iops, strict=True) if la is not None and io]
        weight = sum(io for _, io in pairs)
        return total, (int(sum(la * io for la, io in pairs) / weight) if weight else None)

    perf_days = [perf(t) for t in times]
    trends = {
        "t": times,
        "cpu_pct": [day("cpu_pct", "cpu_count", t) for t in times],
        "memory_pct": [day("memory_pct", "memory_bytes", t) for t in times],
        "storage_pct": [day("storage_pct", "storage_capacity_bytes", t) for t in times],
        "iops": [p[0] for p in perf_days],
        "latency_usecs": [p[1] for p in perf_days],
    }
    return {"name": "All clusters", "trends": trends, "runway": _cluster_runways(trends)}


def _empty_alerts() -> dict[str, Any]:
    return {"critical": [], "critical_count": 0, "warning_count": 0, "by_cluster": {}}


def _top_vms(conn: PrismConnection, attr: str) -> dict[str, list[dict[str, Any]]]:
    """The `TOP_N` powered-on guest VMs per cluster on one attribute (one request)."""
    page = _groups(conn, {
        "entity_type": "mh_vm", "group_member_count": TOP_N, "group_count": 500,
        "grouping_attribute": "cluster", "group_member_sort_attribute": attr,
        "group_member_sort_order": "DESCENDING", "filter_criteria": "is_cvm==0;power_state==on",
        "group_member_attributes": _attrs(_VM)})
    out: dict[str, list[dict[str, Any]]] = {}
    for e in _entities(page):
        cluster = _first(e, "cluster")
        value = num(_first(e, attr))
        if not cluster or value is None:
            continue
        out.setdefault(cluster, []).append({
            "uuid": e.get("entity_id"), "name": _first(e, "vm_name") or e.get("entity_id"),
            "cluster_uuid": cluster,
            "cpu_pct": _pct(_first(e, "hypervisor_cpu_usage_ppm")),
            "memory_pct": _pct(_first(e, "memory_usage_ppm")),
            "iops": _int(_first(e, "controller_num_iops")),
            "latency_usecs": _int(_first(e, "controller_avg_io_latency_usecs")),
            "vcpus": _int(_first(e, "num_vcpus")),
            "memory_bytes": _int(_first(e, "memory_size_bytes")),
        })
    sort_key = {"hypervisor_cpu_usage_ppm": "cpu_pct", "memory_usage_ppm": "memory_pct",
                "controller_num_iops": "iops",
                "controller_avg_io_latency_usecs": "latency_usecs"}[attr]
    for rows in out.values():
        rows.sort(key=lambda r: -(r[sort_key] or 0))
        del rows[TOP_N:]
    return out


def _alert_counts(conn: PrismConnection, severity: str) -> tuple[int, dict[str, int]]:
    page = _groups(conn, {
        "entity_type": "alert", "group_member_count": 1, "group_count": 500,
        "grouping_attribute": "originating_cluster_uuid",
        "filter_criteria": f"resolved==false;severity=={severity}",
        "group_member_attributes": _attrs(["title"])})
    by: dict[str, int] = {}
    for g in page.get("group_results") or []:
        key = g.get("group_by_column_value")
        if key:
            by[str(key)] = int(g.get("total_entity_count") or 0)
    total = page.get("filtered_entity_count")
    return (int(total) if total is not None else sum(by.values())), by


def _alerts(conn: PrismConnection, known: set[str]) -> dict[str, Any]:
    crit_total, crit_by = _alert_counts(conn, "critical")
    warn_total, warn_by = _alert_counts(conn, "warning")
    page = _groups(conn, {
        "entity_type": "alert", "group_member_count": ALERT_LIST,
        "group_member_sort_attribute": "_created_timestamp_usecs_",
        "group_member_sort_order": "DESCENDING",
        "filter_criteria": "resolved==false;severity==critical",
        "group_member_attributes": _attrs(_ALERT)})
    items = []
    for e in _entities(page):
        cluster = _first(e, "originating_cluster_uuid")
        items.append({
            "id": e.get("entity_id"),
            "title": render_title(_first(e, "title"), _all(e, "param_name_list"),
                                  _all(e, "param_value_list")),
            "severity": "critical",
            "cluster_uuid": cluster if cluster in known else None,
            "cluster_name": _first(e, "cluster_name") if cluster in known else None,
            "source": _first(e, "source_entity_name"),
            "source_type": _first(e, "source_entity_type"),
            "created_at": _iso_usecs(_first(e, "_created_timestamp_usecs_")),
            "acknowledged": str(_first(e, "acknowledged")).lower() == "true",
            "impact": _first(e, "impact_type"),
        })
    items.sort(key=lambda a: a["created_at"] or "", reverse=True)
    return {"critical": items, "critical_count": crit_total, "warning_count": warn_total,
            "by_cluster": {u: {"critical": crit_by.get(u, 0), "warning": warn_by.get(u, 0)}
                           for u in known}}


def _cluster(conn: PrismConnection, rc: RegisteredCluster, now: dict[str, Any] | None,
             host_rows: list[dict[str, Any]],
             top: dict[str, list[dict[str, Any]]], alerts: dict[str, Any],
             notes: _Notes) -> dict[str, Any]:
    name = rc.name or rc.uuid
    e = now or {}
    used = _int(_first(e, "storage.usage_bytes"))
    cap = _int(_first(e, "storage.capacity_bytes"))
    free = _int(_first(e, "storage.free_bytes"))
    storage_pct = round(used / cap * 100, 2) if used is not None and cap else None

    pe_hosts = notes.run(f"{name}: node health", lambda: conn.pe_get(rc.uuid, "hosts"), {})
    pe_cluster = notes.run(f"{name}: resiliency", lambda: conn.pe_get(rc.uuid, "cluster"), {})
    nodes = _nodes(host_rows, (pe_hosts or {}).get("entities") or [] if isinstance(
        pe_hosts, dict) else [])
    network = _network(conn, rc.uuid, name, nodes, notes)
    redundancy = (pe_cluster or {}).get("cluster_redundancy_state") or {} \
        if isinstance(pe_cluster, dict) else {}
    rf = _int(redundancy.get("current_redundancy_factor"))
    resiliency = _resiliency(redundancy)

    counts = alerts["by_cluster"].get(rc.uuid, {"critical": 0, "warning": 0})
    usable = None
    usable_free = None
    if cap and rf:
        usable = int(cap * STORAGE_RESERVE_FACTOR / rf)
        if used is not None:
            usable_free = max(0, int((cap * STORAGE_RESERVE_FACTOR - used) / rf))
    prism = {k: _int(_first(e, f"capacity.{k}_runway")) for k in ("cpu", "memory", "storage")}
    prism["overall"] = _int(_first(e, "capacity.runway"))
    return {
        "uuid": rc.uuid, "name": name, "aos_version": rc.aos_version,
        "hypervisors": list(rc.hypervisors), "num_nodes": rc.num_nodes or len(nodes) or None,
        "health": _health(counts, nodes, resiliency),
        "utilization": {
            "cpu_pct": _pct(_first(e, "hypervisor_cpu_usage_ppm")),
            "memory_pct": _pct(_first(e, "hypervisor_memory_usage_ppm")),
            "storage_pct": storage_pct,
        },
        "capacity": {
            "cpu_count": _int(_first(e, "num_cpus")),
            "memory_bytes": _int(_first(e, "memory_capacity_bytes")),
            "storage_used_bytes": used, "storage_capacity_bytes": cap,
            "storage_free_bytes": free,
            "storage_logical_bytes": _int(_first(e, "storage.logical_usage_bytes")),
            "redundancy_factor": rf,
            "usable_bytes": usable, "usable_free_bytes": usable_free,
        },
        "performance": {
            "iops": _int(_first(e, "controller_num_iops")),
            "read_iops": _int(_first(e, "controller_num_read_iops")),
            "write_iops": _int(_first(e, "controller_num_write_iops")),
            "latency_usecs": _int(_first(e, "controller_avg_io_latency_usecs")),
            "bandwidth_kbps": _int(_first(e, "controller_io_bandwidth_kBps")),
        },
        "efficiency": {
            "data_reduction_ratio": _ratio(_first(e, "data_reduction.saving_ratio_ppm")),
            "overall_ratio": _ratio(_first(e, "data_reduction.overall.saving_ratio_ppm")),
            "saved_bytes": _int(_first(e, "data_reduction.saved_bytes")),
            "compression_ratio": _ratio(_first(e, "data_reduction.compression.saving_ratio_ppm")),
            "dedup_ratio": _ratio(_first(e, "data_reduction.dedup.saving_ratio_ppm")),
            "erasure_coding_ratio": _ratio(
                _first(e, "data_reduction.erasure_coding.saving_ratio_ppm")),
        },
        "alerts": counts,
        "prism_runway_days": prism if any(v is not None for v in prism.values()) else None,
        "nodes": nodes,
        "network": network,
        "top_vms": top,
    }


def _trends(ts: dict[str, Any] | None, capacity: int | None) -> dict[str, Any]:
    """Columnar daily series: ``t`` (epoch ms) and one list per metric, None for gaps."""
    if not ts:
        return {"t": [], "cpu_pct": [], "memory_pct": [], "storage_pct": [],
                "storage_used_bytes": [], "iops": [], "latency_usecs": []}
    cols = {a: dict(series(ts, a)) for a in _CLUSTER_SERIES}
    times = sorted({t for c in cols.values() for t in c})
    cpu = cols["hypervisor_cpu_usage_ppm"]
    mem = cols["hypervisor_memory_usage_ppm"]
    sto = cols["storage.usage_bytes"]

    def pct(v: float | None) -> float | None:
        return round(v / 10_000, 2) if v is not None else None

    return {
        "t": [int(t) for t in times],
        "cpu_pct": [pct(cpu.get(t)) for t in times],
        "memory_pct": [pct(mem.get(t)) for t in times],
        "storage_pct": [_share(sto.get(t), capacity) for t in times],
        "storage_used_bytes": [_int(sto.get(t)) for t in times],
        "iops": [_int(cols["controller_num_iops"].get(t)) for t in times],
        "latency_usecs": [_int(cols["controller_avg_io_latency_usecs"].get(t))
                          for t in times],
    }


def _share(used: float | None, capacity: int | None) -> float | None:
    return round(used / capacity * 100, 2) if used is not None and capacity else None


def _cluster_runways(trends: dict[str, Any]) -> dict[str, dict[str, Any]]:
    times = trends.get("t") or []
    per: dict[str, dict[str, dict[str, Any]]] = {}
    for key, col in (("cpu", "cpu_pct"), ("memory", "memory_pct"),
                     ("storage", "storage_pct")):
        for r, v in runways(list(zip(times, trends.get(col) or [], strict=False))).items():
            per.setdefault(r, {})[key] = v
    return per


def _nodes(host_rows: list[dict[str, Any]], pe_hosts: list[dict[str, Any]]
           ) -> list[dict[str, Any]]:
    pe = {h.get("uuid"): h for h in pe_hosts if isinstance(h, dict)}
    out = []
    seen = set()
    for row in host_rows:
        uuid = row.get("entity_id")
        seen.add(uuid)
        out.append(_node(uuid, row, pe.get(uuid) or {}))
    for uuid, h in pe.items():
        if uuid not in seen:
            out.append(_node(uuid, None, h))
    out.sort(key=lambda n: str(n["name"] or "").lower())
    return out


def _node(uuid: Any, row: dict[str, Any] | None, pe: dict[str, Any]) -> dict[str, Any]:
    status = str(_first(row, "node_status") or "") if row else ""
    maint_state = str(_first(row, "host_maintenance_state") or "") if row else ""
    state = pe.get("state")
    metadata = pe.get("metadata_store_status")
    degraded = pe.get("is_degraded") is True
    maintenance = pe.get("host_in_maintenance_mode") is True or \
        (maint_state not in ("", "normal"))
    node_ok = (status in ("", "kNormal")) and (state in (None, "NORMAL")) and not degraded
    cvm_ok = metadata in (None, "kNormalMode") and not degraded
    if not node_ok or not cvm_ok:
        tone = "error"
    elif maintenance:
        tone = "warn"
    else:
        tone = "ok"
    return {
        "uuid": uuid,
        "name": (_first(row, "name") if row else None) or pe.get("name") or uuid,
        "status": tone,
        "node_state": state or (status[1:].upper() if status.startswith("k") else status)
        or None,
        "maintenance": maintenance,
        "degraded": degraded,
        "cvm_ip": pe.get("service_vmexternal_ip"),
        "cvm_status": None if metadata is None else
        ("ok" if metadata == "kNormalMode" else "error"),
        "metadata_store": _metadata_label(metadata),
        "cpu_pct": _pct(_first(row, "hypervisor_cpu_usage_ppm")) if row else None,
        "memory_pct": _pct(_first(row, "hypervisor_memory_usage_ppm")) if row else None,
        "num_vms": _int(_first(row, "num_vms")) if row else _int(pe.get("num_vms")),
    }


def _metadata_label(value: Any) -> str | None:
    if not value:
        return None
    text = str(value)
    if text.startswith("k"):
        text = text[1:]
    # kNormalMode → "Normal mode"
    words = re.sub(r"(?<!^)([A-Z])", r" \1", text).lower()
    return words[:1].upper() + words[1:]


def _resiliency(state: dict[str, Any]) -> dict[str, Any] | None:
    current = _int(state.get("current_cluster_fault_tolerance"))
    desired = _int(state.get("desired_cluster_fault_tolerance"))
    if current is None and desired is None:
        rf_now = _int(state.get("current_redundancy_factor"))
        rf_want = _int(state.get("desired_redundancy_factor"))
        if rf_now is None:
            return None
        return {"current": rf_now, "desired": rf_want, "ok": rf_want is None or
                rf_now >= rf_want, "kind": "rf"}
    return {"current": current, "desired": desired,
            "ok": desired is None or (current is not None and current >= desired),
            "kind": "fault_tolerance"}


def _health(counts: dict[str, int], nodes: list[dict[str, Any]],
            resiliency: dict[str, Any] | None) -> dict[str, Any]:
    reasons: list[str] = []
    bad_nodes = [n for n in nodes if n["status"] == "error"]
    maint = [n for n in nodes if n["maintenance"]]
    if counts.get("critical"):
        reasons.append(f"{counts['critical']:,} critical alert"
                       f"{'s' if counts['critical'] != 1 else ''}")
    if bad_nodes:
        reasons.append(f"{len(bad_nodes):,} node{'s' if len(bad_nodes) != 1 else ''} "
                       "not healthy")
    if resiliency and not resiliency["ok"]:
        reasons.append("Data resiliency below target")
    status = "critical" if reasons else "ok"
    if status == "ok":
        if maint:
            reasons.append(f"{len(maint):,} node{'s' if len(maint) != 1 else ''} "
                           "in maintenance")
        if counts.get("warning"):
            reasons.append(f"{counts['warning']:,} warning"
                           f"{'s' if counts['warning'] != 1 else ''}")
        if reasons:
            status = "warning"
    return {"status": status, "reasons": reasons,
            "critical_alerts": counts.get("critical", 0),
            "warning_alerts": counts.get("warning", 0),
            "nodes_total": len(nodes), "nodes_ok": len(nodes) - len(bad_nodes),
            "resiliency": resiliency}


def _network(conn: PrismConnection, cluster_uuid: str, name: str,
             nodes: list[dict[str, Any]], notes: _Notes) -> dict[str, Any]:
    nics: list[dict[str, Any]] = []
    for node in nodes:
        raw = notes.run(f"{name}: network of {node['name']}",
                        partial(conn.pe_get, cluster_uuid, f"hosts/{node['uuid']}/host_nics"),
                        [])
        listed = raw if isinstance(raw, list) else (raw or {}).get("entities") or [] \
            if isinstance(raw, dict) else []
        for nic in listed:
            if not isinstance(nic, dict):
                continue
            stats = nic.get("stats") or {}
            nics.append({
                "host": node["name"], "name": nic.get("name"),
                "link_speed_kbps": _int(nic.get("link_speed_in_kbps")) or None,
                "rx_kBps": num(stats.get("network.received_rate_kBps")),
                "tx_kBps": num(stats.get("network.transmitted_rate_kBps")),
                "rx_errors": _int(stats.get("network.error_received_pkts")),
                "tx_errors": _int(stats.get("network.error_transmitted_pkts")),
                "rx_dropped": _int(stats.get("network.dropped_received_pkts")),
                "tx_dropped": _int(stats.get("network.dropped_transmitted_pkts")),
            })

    def total(key: str) -> float | None:
        vals = [n[key] for n in nics if n[key] is not None]
        return sum(vals) if vals else None

    errors = [total("rx_errors"), total("tx_errors")]
    drops = [total("rx_dropped"), total("tx_dropped")]
    return {
        "rx_kBps": total("rx_kBps"), "tx_kBps": total("tx_kBps"),
        "errors": int(sum(v for v in errors if v is not None))
        if any(v is not None for v in errors) else None,
        "dropped": int(sum(v for v in drops if v is not None))
        if any(v is not None for v in drops) else None,
        "nics": nics,
        "links_up": sum(1 for n in nics if n["link_speed_kbps"]),
    }


def _total(clusters: list[dict[str, Any]]) -> dict[str, Any]:
    """The target as a whole: sums, and CPU / memory weighted by each cluster's capacity.

    CPU is weighted by logical CPU count (the groups API reports no clock capacity), memory
    by memory capacity and storage by storage capacity.
    """
    def weighted(pct_key: str, weight: Callable[[dict[str, Any]], float | None]) -> float | None:
        pairs: list[tuple[float, float]] = []
        for c in clusters:
            p, w = c["utilization"][pct_key], weight(c)
            if p is not None and w:
                pairs.append((p, w))
        tw = sum(w for _, w in pairs)
        return round(sum(p * w for p, w in pairs) / tw, 2) if tw else None

    def cap(c: dict[str, Any], key: str) -> float | None:
        return c["capacity"].get(key)

    def total_of(key: str) -> int | None:
        vals = [c["capacity"][key] for c in clusters if c["capacity"].get(key) is not None]
        return sum(vals) if vals else None

    used, capacity = total_of("storage_used_bytes"), total_of("storage_capacity_bytes")
    perf = [c["performance"] for c in clusters]
    iops = [p["iops"] for p in perf if p["iops"] is not None]
    lat = [(p["latency_usecs"], p["iops"]) for p in perf
           if p["latency_usecs"] is not None and p["iops"]]
    return {
        "name": "All clusters",
        "clusters": len(clusters),
        "utilization": {
            "cpu_pct": weighted("cpu_pct", lambda c: cap(c, "cpu_count")),
            "memory_pct": weighted("memory_pct", lambda c: cap(c, "memory_bytes")),
            "storage_pct": round(used / capacity * 100, 2) if used is not None and capacity
            else None,
        },
        "capacity": {
            "storage_used_bytes": used, "storage_capacity_bytes": capacity,
            "storage_free_bytes": total_of("storage_free_bytes"),
            "usable_bytes": total_of("usable_bytes"),
            "usable_free_bytes": total_of("usable_free_bytes"),
            "storage_logical_bytes": total_of("storage_logical_bytes"),
        },
        "performance": {
            "iops": sum(iops) if iops else None,
            # IOPS-weighted, the way a target-wide average latency is meant.
            "latency_usecs": int(sum(la * i for la, i in lat) / sum(i for _, i in lat))
            if lat else None,
        },
        "efficiency": {"saved_bytes": _saved_total(clusters)},
        "health": {
            "critical": sum(1 for c in clusters if c["health"]["status"] == "critical"),
            "warning": sum(1 for c in clusters if c["health"]["status"] == "warning"),
            "ok": sum(1 for c in clusters if c["health"]["status"] == "ok"),
            "nodes_total": sum(c["health"]["nodes_total"] for c in clusters),
            "nodes_ok": sum(c["health"]["nodes_ok"] for c in clusters),
        },
        "alerts": {"critical": sum(c["alerts"]["critical"] for c in clusters),
                   "warning": sum(c["alerts"]["warning"] for c in clusters)},
    }


def _saved_total(clusters: list[dict[str, Any]]) -> int | None:
    vals = [c["efficiency"]["saved_bytes"] for c in clusters
            if c["efficiency"].get("saved_bytes") is not None]
    return sum(vals) if vals else None


def _sources(list_api: str) -> list[dict[str, str]]:
    """Which API fed each card (shown on the page, so a number can be traced)."""
    return [
        {"card": "Clusters", "api": f"{list_api} clusters"},
        {"card": "Health, alerts", "api": "v3 groups (alert) · PE v2.0 hosts, cluster"},
        {"card": "Utilization, efficiency, performance", "api": "v3 groups (cluster)"},
        {"card": "Trends and runway", "api": "v3 groups time series, daily, 90 days"},
        {"card": "Top VMs", "api": "v3 groups (mh_vm)"},
        {"card": "Nodes and CVMs", "api": "v3 groups (host) · PE v2.0 hosts"},
        {"card": "Network", "api": "PE v2.0 host_nics"},
    ]
