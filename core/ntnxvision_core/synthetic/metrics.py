"""Synthetic performance and capacity metrics, for Insights > Metrics in demo mode.

Serves exactly the requests `ntnxvision_core.metrics.live` sends, shaped like the lab PC
(pc.7.5.1.1) answered them:

* groups ``cluster`` (current values, and 90 days of daily points when the body carries
  ``interval_start_ms`` / ``interval_end_ms`` / ``downsampling_interval``; points newest
  first, ``time`` in microseconds);
* groups ``host`` and ``mh_vm`` when they ask for a metric, a sort or a grouping (other
  ``host`` / ``mh_vm`` queries keep their existing handlers);
* groups ``alert`` (from the estate's alerts, so Inventory and Metrics agree), with
  ``resolved==`` / ``severity==`` filters, sorting and ``originating_cluster_uuid`` grouping;
* the Prism Element proxy's ``hosts``, ``cluster`` and ``hosts/{uuid}/host_nics`` gain the
  health, resiliency and NIC ``stats`` fields a real PE returns (existing fields untouched).

Everything is a pure function of the estate and the day, so a refresh shows the same
picture. The day is real time, so the series always end today. Cluster 0 grows in memory
and cluster 1 in storage, so their runway shows a date; the rest are flat or seasonal.
"""

from __future__ import annotations

import hashlib
import math
import time
from typing import Any

from ntnxvision_core.synthetic.estate import PcEstate

Response = tuple[int, Any, dict[str, str]]
DAY_MS = 86_400_000

#: Attributes that mark a host / mh_vm groups query as a metrics query.
_METRIC_ATTRS = {"hypervisor_cpu_usage_ppm", "hypervisor_memory_usage_ppm",
                 "memory_usage_ppm", "controller_num_iops", "controller_avg_io_latency_usecs",
                 "node_status", "host_maintenance_state"}


def _unit(*parts: Any) -> float:
    """A stable pseudo-random number in [0, 1) from the given parts."""
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


# ─── the model: a resource's percentage on a given day ─────────────────


def _profile(c: dict[str, Any]) -> dict[str, float]:
    """Level (today) and growth (points per day) of CPU, memory and storage."""
    u = c["uuid"]
    idx = c.get("index", 0)
    prof = {
        "cpu": 22 + 18 * _unit(u, "cpu"), "cpu_g": 0.0,
        "mem": 48 + 14 * _unit(u, "mem"), "mem_g": 0.004,
        "sto": 34 + 18 * _unit(u, "sto"), "sto_g": 0.02,
    }
    if idx == 0:   # memory growing: about 160 days to 80%
        prof.update(mem=63.0, mem_g=0.11)
    elif idx == 1:  # storage growing fast: about 40 days to 80%
        prof.update(sto=69.5, sto_g=0.26, cpu=41.0, cpu_g=0.03)
    return prof


def _value(c: dict[str, Any], key: str, days_ago: float) -> float:
    p = _profile(c)
    level, growth = p[key], p[f"{key}_g"]
    day = math.floor(days_ago)
    noise = (_unit(c["uuid"], key, day) - 0.5) * (3.0 if key == "cpu" else 1.0)
    weekly = 4.0 * math.sin(2 * math.pi * day / 7) if key == "cpu" else 0.0
    return max(1.0, min(99.0, level - growth * days_ago + noise + weekly))


def _today_ms(now_ms: float) -> float:
    return now_ms - now_ms % DAY_MS


# ─── estate lookups ────────────────────────────────────────────────────


def _hosts_of(est: PcEstate, cluster_uuid: str) -> list[dict[str, Any]]:
    return [h for h in est.hosts if h["cluster_uuid"] == cluster_uuid]


def _cluster_figures(est: PcEstate, c: dict[str, Any], days_ago: float) -> dict[str, float]:
    hosts = _hosts_of(est, c["uuid"])
    cap = sum(h["storage_raw"] for h in hosts) or 1
    mem = sum(h["memory"] for h in hosts)
    used = cap * _value(c, "sto", days_ago) / 100
    nodes = max(1, len(hosts))
    esxi = c["hypervisor"] == "ESXi"
    iops = 380 * nodes * (0.7 + 0.6 * _unit(c["uuid"], "iops", math.floor(days_ago)))
    latency = (1300 if esxi else 650) * (0.8 + 0.5 * _unit(c["uuid"], "lat",
                                                           math.floor(days_ago)))
    reduction = 1.25 + 0.5 * _unit(c["uuid"], "dr")
    return {
        "cpu": _value(c, "cpu", days_ago), "mem": _value(c, "mem", days_ago),
        "cap": cap, "used": used, "mem_cap": mem,
        "threads": sum(h["threads"] for h in hosts), "iops": iops, "latency": latency,
        "reduction": reduction, "overall": reduction * (3 + 4 * _unit(c["uuid"], "ov")),
    }


def _cluster_now(est: PcEstate, c: dict[str, Any], attr: str) -> Any:
    f = _cluster_figures(est, c, 0.2)
    reduction = f["reduction"]
    compression = 1 + (reduction - 1) * 0.8
    dedup = reduction / compression
    values: dict[str, Any] = {
        "cluster_name": c["name"],
        "hypervisor_cpu_usage_ppm": int(f["cpu"] * 10_000),
        "hypervisor_memory_usage_ppm": int(f["mem"] * 10_000),
        "storage.usage_bytes": int(f["used"]),
        "storage.capacity_bytes": int(f["cap"]),
        "storage.free_bytes": int(f["cap"] - f["used"]),
        "storage.logical_usage_bytes": int(f["used"] * reduction),
        "controller_num_iops": int(f["iops"]),
        "controller_num_read_iops": int(f["iops"] * 0.38),
        "controller_num_write_iops": int(f["iops"]) - int(f["iops"] * 0.38),
        "controller_avg_io_latency_usecs": int(f["latency"]),
        "controller_io_bandwidth_kBps": int(f["iops"] * 24),
        "data_reduction.saving_ratio_ppm": int(reduction * 1_000_000),
        "data_reduction.overall.saving_ratio_ppm": int(f["overall"] * 1_000_000),
        "data_reduction.saved_bytes": int(f["used"] * (reduction - 1)),
        "data_reduction.compression.saving_ratio_ppm": int(compression * 1_000_000),
        "data_reduction.dedup.saving_ratio_ppm": int(dedup * 1_000_000),
        "data_reduction.erasure_coding.saving_ratio_ppm": 1_000_000,
        "num_cpus": f["threads"], "memory_capacity_bytes": f["mem_cap"],
        "capacity.runway": 365, "capacity.cpu_runway": 365,
        "capacity.memory_runway": 365 if c.get("index") != 0 else 160,
        "capacity.storage_runway": 365 if c.get("index") != 1 else 41,
    }
    if values["capacity.memory_runway"] < 365 or values["capacity.storage_runway"] < 365:
        values["capacity.runway"] = min(values["capacity.memory_runway"],
                                        values["capacity.storage_runway"])
    return values.get(attr)


def _cluster_series(est: PcEstate, c: dict[str, Any], attr: str, start_ms: float,
                    end_ms: float, step_ms: float) -> list[dict[str, Any]]:
    """Daily points from the interval's last day back to its first, newest first."""
    today = _today_ms(end_ms)
    out = []
    t = today
    while t >= start_ms:
        days_ago = (today - t) / DAY_MS
        f = _cluster_figures(est, c, days_ago)
        value = {"hypervisor_cpu_usage_ppm": f["cpu"] * 10_000,
                 "hypervisor_memory_usage_ppm": f["mem"] * 10_000,
                 "storage.usage_bytes": f["used"],
                 "controller_num_iops": f["iops"],
                 "controller_avg_io_latency_usecs": f["latency"]}.get(attr)
        out.append({"time": int(t * 1000),
                    "values": [] if value is None else [str(int(value))]})
        t -= step_ms
    return out


def _host_value(est: PcEstate, h: dict[str, Any], attr: str) -> Any:
    c = next((x for x in est.clusters if x["uuid"] == h["cluster_uuid"]), None)
    if c is None:
        return None
    f = _cluster_figures(est, c, 0.2)
    spread = (_unit(h["uuid"], "spread") - 0.5) * 10
    return {
        "name": h["name"], "cluster": h["cluster_uuid"],
        "node_status": "kNormal",
        "host_maintenance_state": h.get("maintenance") or "normal",
        "hypervisor_cpu_usage_ppm": int(max(1.0, f["cpu"] + spread) * 10_000),
        "hypervisor_memory_usage_ppm": int(max(1.0, min(99.0, f["mem"] - spread / 2))
                                           * 10_000),
        "num_vms": sum(1 for v in est.vms if v["host_uuid"] == h["uuid"]
                       and v["power"] == "ON"),
    }.get(attr)


def _vm_value(vm: dict[str, Any], attr: str) -> Any:
    u = vm["uuid"]
    # Skewed: most VMs idle, a few busy (so the top lists look like a real estate's).
    busy = _unit(u, "busy") ** 3
    return {
        "vm_name": vm["name"], "cluster": vm["cluster_uuid"],
        "hypervisor_cpu_usage_ppm": int((1 + 88 * busy) * 10_000),
        "memory_usage_ppm": int((12 + 80 * _unit(u, "mem") ** 1.5) * 10_000),
        "controller_num_iops": int(2400 * _unit(u, "io") ** 6),
        "controller_avg_io_latency_usecs": int(250 + 5200 * _unit(u, "lat") ** 5),
        "num_vcpus": vm["sockets"] * vm["cores"], "memory_size_bytes": vm["memory"],
        "power_state": str(vm["power"]).lower(), "is_cvm": "0",
    }.get(attr)


def _alert_value(est: PcEstate, a: dict[str, Any], attr: str) -> Any:
    cname = next((c["name"] for c in est.clusters if c["uuid"] == a["cluster_uuid"]), None)
    from datetime import datetime

    created = datetime.fromisoformat(a["created"].replace("Z", "+00:00"))
    return {
        "title": a["title"], "severity": a["severity"].lower(),
        "resolved": "true" if a["resolved"] else "false",
        "acknowledged": "true" if a["acknowledged"] else "false",
        "originating_cluster_uuid": a["cluster_uuid"], "cluster_name": cname,
        "source_entity_name": a["entity"], "source_entity_type": a["type"],
        "_created_timestamp_usecs_": int(created.timestamp() * 1_000_000),
        "param_name_list": [], "param_value_list": [],
        "impact_type": "Availability" if a["type"] in ("host", "disk") else "Capacity",
    }.get(attr)


# ─── the groups API ────────────────────────────────────────────────────


def _criteria(body: dict[str, Any]) -> list[tuple[str, str]]:
    out = []
    for term in str(body.get("filter_criteria") or "").split(";"):
        key, op, value = term.partition("==")
        if op:
            out.append((key.strip(), value.strip()))
    return out


def _response(etype: str, groups: list[tuple[str | None, list[tuple[str, list[dict[str, Any]]]]]],
              total: int) -> dict[str, Any]:
    return {
        "entity_type": etype, "filtered_entity_count": total, "total_entity_count": total,
        "filtered_group_count": len(groups), "total_group_count": len(groups),
        "group_results": [
            {**({"group_by_column_value": key} if key is not None else {}),
             "total_entity_count": count,
             "entity_results": [{"entity_id": eid, "data": data} for eid, data in rows]}
            for key, rows, count in ((g[0], g[1], len(g[1])) for g in groups)
        ],
    }


def groups(est: PcEstate, body: dict[str, Any], now_ms: float | None = None
           ) -> Response | None:
    """A metrics groups query, or None to let the existing handler answer."""
    etype = body.get("entity_type")
    attrs = [a.get("attribute") for a in body.get("group_member_attributes") or []
             if a.get("attribute")]
    metric_query = bool(set(attrs) & _METRIC_ATTRS or body.get("grouping_attribute")
                        or body.get("group_member_sort_attribute"))
    now_ms = now_ms if now_ms is not None else time.time() * 1000
    if etype == "cluster":
        if body.get("interval_start_ms") is not None:
            start = float(body["interval_start_ms"])
            end = float(body.get("interval_end_ms") or now_ms)
            step = float(body.get("downsampling_interval") or 86_400) * 1000
            rows = [(c["uuid"], [{"name": a, "values": _cluster_series(est, c, a, start, end,
                                                                        step)} for a in attrs])
                    for c in est.clusters]
        else:
            rows = [(c["uuid"], [_cell(a, _cluster_now(est, c, a), now_ms) for a in attrs])
                    for c in est.clusters]
        return 200, _response("cluster", [(None, rows)], len(rows)), {}
    if etype == "host" and metric_query:
        rows = [(h["uuid"], [_cell(a, _host_value(est, h, a), now_ms) for a in attrs])
                for h in est.hosts]
        return 200, _response("host", [(None, rows)], len(rows)), {}
    if etype == "mh_vm" and metric_query:
        items = [v for v in est.vms]
        for key, value in _criteria(body):
            if key == "power_state":
                items = [v for v in items if str(v["power"]).lower() == value]
            elif key == "cluster":
                items = [v for v in items if v["cluster_uuid"] == value]
            elif key == "is_cvm" and value == "1":
                items = []
        return 200, _grouped("mh_vm", body, items, attrs, lambda v, a: _vm_value(v, a),
                             now_ms), {}
    if etype == "alert":
        items = list(est.alerts)
        for key, value in _criteria(body):
            if key == "resolved":
                items = [a for a in items if (a["resolved"] is True) == (value == "true")]
            elif key == "severity":
                items = [a for a in items if a["severity"].lower() == value.lower()]
        return 200, _grouped("alert", body, items, attrs,
                             lambda a, attr: _alert_value(est, a, attr), now_ms), {}
    return None


def _cell(attr: str, value: Any, now_ms: float) -> dict[str, Any]:
    values = list(value) if isinstance(value, list) else [] if value is None else [str(value)]
    return {"name": attr, "values": [{"values": values, "time": int(now_ms * 1000)}]}


def _grouped(etype: str, body: dict[str, Any], items: list[dict[str, Any]], attrs: list[str],
             value: Any, now_ms: float) -> dict[str, Any]:
    sort = body.get("group_member_sort_attribute")
    if sort:
        def key(item: dict[str, Any]) -> float:
            v = value(item, sort)
            try:
                return float(v)
            except (TypeError, ValueError):
                return 0.0
        items = sorted(items, key=key,
                       reverse=str(body.get("group_member_sort_order")) == "DESCENDING")
    count = int(body.get("group_member_count") or 20)
    by = body.get("grouping_attribute")
    buckets: dict[str | None, list[dict[str, Any]]] = {}
    for item in items:
        k = str(value(item, by)) if by else None
        buckets.setdefault(k, []).append(item)
    groups = []
    for k, members in buckets.items():
        rows = [(m["uuid"], [_cell(a, value(m, a), now_ms) for a in attrs])
                for m in members[:count]]
        groups.append((k, rows, len(members)))
    out = _response(etype, [], len(items))
    out["filtered_group_count"] = out["total_group_count"] = len(groups)
    out["group_results"] = [
        {**({"group_by_column_value": k} if k is not None else {}), "total_entity_count": n,
         "entity_results": [{"entity_id": eid, "data": data} for eid, data in rows]}
        for k, rows, n in groups]
    return out


# ─── Prism Element through the PC proxy ────────────────────────────────


def enrich_pe(est: PcEstate, path: str, query: dict[str, str], resp: Response) -> Response:
    """Add the health, resiliency and NIC stats fields to a proxied PE v2.0 answer."""
    status, body, headers = resp
    if status != 200:
        return resp
    what = path[len("/PrismGateway/services/rest/v2.0/"):].strip("/")
    if what == "cluster" and isinstance(body, dict):
        state = dict(body.get("cluster_redundancy_state") or {})
        c = next((x for x in est.clusters if x["uuid"] == query.get("proxyClusterUuid")), {})
        ft = int(c.get("fault_tolerance", (c.get("rf") or 2) - 1))
        state.update(current_cluster_fault_tolerance=ft, desired_cluster_fault_tolerance=ft)
        return status, {**body, "cluster_redundancy_state": state}, headers
    if what == "hosts" and isinstance(body, dict):
        entities = []
        for h in body.get("entities") or []:
            maint = next((x.get("maintenance") for x in est.hosts if x["uuid"] == h["uuid"]),
                         "normal")
            entities.append({**h, "state": "NORMAL", "is_degraded": False,
                             "metadata_store_status": "kNormalMode",
                             "host_in_maintenance_mode": maint not in (None, "normal")})
        return status, {**body, "entities": entities}, headers
    if what.startswith("hosts/") and what.endswith("/host_nics") and isinstance(body, list):
        out = []
        for nic in body:
            u = nic.get("uuid")
            speed = nic.get("link_speed_in_kbps") or 0
            active = speed and int(str(nic.get("name", "0"))[-1:] or 0) % 2 == 0
            rx = int(speed / 8 / 1000 * 0.06 * _unit(u, "rx")) if active else 0
            tx = int(speed / 8 / 1000 * 0.04 * _unit(u, "tx")) if active else 0
            errors = 3 if _unit(u, "err") > 0.93 else 0
            out.append({**nic, "stats": {
                "network.received_rate_kBps": str(rx), "network.transmitted_rate_kBps": str(tx),
                "network.error_received_pkts": str(errors),
                "network.error_transmitted_pkts": "0",
                "network.dropped_received_pkts": str(errors * 4),
                "network.dropped_transmitted_pkts": "0",
            }})
        return status, out, headers
    return resp
