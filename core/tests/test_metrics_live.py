"""Insights > Metrics: the live collection against the mock API, parsing, cache, cancel."""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

import ntnxvision_core.sidecar as sc
from ntnxvision_core import metrics
from ntnxvision_core.api.errors import AuthenticationError, CertificateNotTrusted, NutanixApiError
from ntnxvision_core.cancel import RunCancelled
from ntnxvision_core.metrics import live

PC01 = {"host": "pc01.demo.local", "username": "admin", "password": "demo", "port": 9440,
        "insecure": False}


@pytest.fixture
def fresh(demo: Any) -> Any:
    metrics.clear_cache()
    demo.api.hits.clear()
    yield demo
    metrics.clear_cache()


def by_name(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["name"]: c for c in result["clusters"]}


# ─── the "now" part ────────────────────────────────────────────────────


def test_now_part_cards(fresh: Any) -> None:
    res = sc._m_metrics(None, {**PC01, "part": "now"})
    assert res["part"] == "now" and res["version"] == "pc.2024.3" and not res["notes"]
    names = [c["name"] for c in res["clusters"]]
    assert names == sorted(names) and len(names) == 4  # the PC pseudo-cluster is left out
    for c in res["clusters"]:
        cap = c["capacity"]
        assert c["utilization"]["storage_pct"] == pytest.approx(
            cap["storage_used_bytes"] / cap["storage_capacity_bytes"] * 100, abs=0.01)
        assert 0 < c["utilization"]["cpu_pct"] < 100
        assert cap["redundancy_factor"] in (2, 3)
        assert cap["usable_bytes"] == int(cap["storage_capacity_bytes"] * 0.95
                                          / cap["redundancy_factor"])
        assert c["efficiency"]["data_reduction_ratio"] > 1
        assert c["nodes"] and all(n["cvm_status"] == "ok" for n in c["nodes"])
        assert c["health"]["resiliency"]["ok"] is True
        for key, attr in (("cpu", "cpu_pct"), ("memory", "memory_pct"), ("iops", "iops"),
                          ("latency", "latency_usecs")):
            rows = c["top_vms"][key]
            assert 0 < len(rows) <= live.TOP_N
            vals = [r[attr] for r in rows]
            assert vals == sorted(vals, reverse=True)
            assert all(r["cluster_uuid"] == c["uuid"] for r in rows)
        assert c["network"]["nics"] and c["network"]["rx_kBps"] is not None
    total = res["total"]
    used = sum(c["capacity"]["storage_used_bytes"] for c in res["clusters"])
    assert total["capacity"]["storage_used_bytes"] == used
    assert total["health"]["nodes_total"] == sum(len(c["nodes"]) for c in res["clusters"])
    alerts = res["alerts"]
    assert alerts["critical_count"] == sum(c["alerts"]["critical"] for c in res["clusters"])
    assert all(a["severity"] == "critical" for a in alerts["critical"])
    created = [a["created_at"] for a in alerts["critical"]]
    assert created == sorted(created, reverse=True)


def test_health_follows_critical_alerts(fresh: Any) -> None:
    res = sc._m_metrics(None, {**PC01, "part": "now"})
    for c in res["clusters"]:
        if c["alerts"]["critical"]:
            assert c["health"]["status"] == "critical"
            assert c["health"]["reasons"][0].endswith(("critical alert", "critical alerts"))


def test_only_read_requests(fresh: Any) -> None:
    """Every request was a GET or one of the read-only POSTs the guard allows."""
    sc._m_metrics(None, {**PC01, "part": "now"})
    sc._m_metrics(None, {**PC01, "part": "trends"})
    hits = [k for k in fresh.api.hits if k[0] == "pc01.demo.local"]
    assert hits
    for _host, method, path in hits:
        assert method == "GET" or (method == "POST" and (
            path == "/api/nutanix/v3/groups" or path.endswith("/list"))), (method, path)


# ─── the "trends" part ─────────────────────────────────────────────────


def test_trends_and_runway(fresh: Any) -> None:
    res = sc._m_metrics(None, {**PC01, "part": "trends"})
    assert res["part"] == "trends" and res["history_days"] == 90
    clusters = by_name(res)
    for c in clusters.values():
        t = c["trends"]["t"]
        assert len(t) >= 89 and t == sorted(t)
        assert set(c["runway"]) == {"30", "60", "90"}
    # The demo's growing clusters show a date; the flat ones do not.
    storage = {t["pct"]: t for t in clusters["PHX-PROD-02"]["runway"]["90"]["storage"]
               ["thresholds"]}
    assert storage[80.0]["state"] == "projected" and 25 <= storage[80.0]["days"] <= 60
    memory = {t["pct"]: t for t in clusters["PHX-PROD-01"]["runway"]["90"]["memory"]
              ["thresholds"]}
    assert memory[80.0]["state"] == "projected" and 100 <= memory[80.0]["days"] <= 220
    assert clusters["PHX-DR-01"]["runway"]["90"]["cpu"]["status"] == "no_growth"
    total = res["total"]
    assert total["trends"]["t"] and set(total["runway"]) == {"30", "60", "90"}


def test_legacy_pc_without_v4(fresh: Any) -> None:
    res = sc._m_metrics(None, {**PC01, "host": "pc03.demo.local", "part": "now"})
    assert res["clusters"] and not res["notes"]
    assert any("v3 clusters" in s["api"] for s in res["sources"])


# ─── cache, refresh, cancel, refusals ──────────────────────────────────


def test_cache_and_refresh(fresh: Any) -> None:
    first = sc._m_metrics(None, {**PC01, "part": "now"})
    second = sc._m_metrics(None, {**PC01, "part": "now"})
    assert first["cached"] is False and second["cached"] is True
    assert second["ttl_seconds"] == metrics.CACHE_TTL
    third = sc._m_metrics(None, {**PC01, "part": "now", "refresh": True})
    assert third["cached"] is False
    other = sc._m_metrics(None, {**PC01, "part": "now", "password": "other"})
    assert other["cached"] is False  # a different password never reads the cache


def test_cancel(fresh: Any) -> None:
    assert sc._m_cancel_metrics(None, {"request_id": "nope"}) == {"cancelled": False}
    assert "cancel_metrics" in sc._INLINE_METHODS
    outcome: dict[str, Any] = {}

    def run() -> None:
        try:
            metrics.get_metrics({**PC01, "part": "now", "request_id": "r1"})
        except RunCancelled:
            outcome["cancelled"] = True

    fresh.api.latency_ms = 150
    try:
        worker = threading.Thread(target=run)
        worker.start()
        deadline = time.time() + 5
        while time.time() < deadline and not metrics.cancel("r1"):
            time.sleep(0.02)
        worker.join(10)
    finally:
        fresh.api.latency_ms = 0
    assert outcome.get("cancelled") is True


@pytest.mark.parametrize(("params", "error"), [
    ({"host": ""}, NutanixApiError),
    ({"mode": "prism_element"}, NutanixApiError),
    ({"password": ""}, AuthenticationError),
    ({"insecure": True}, CertificateNotTrusted),
    ({"part": "bogus"}, NutanixApiError),
])
def test_refusals(params: dict[str, Any], error: type[Exception]) -> None:
    with pytest.raises(error):
        metrics.get_metrics({**PC01, **params})


# ─── parsing (payload shapes as the lab PC returned them, example values) ──


def test_series_and_numbers() -> None:
    entity = {"entity_id": "c1", "data": [{"name": "hypervisor_cpu_usage_ppm", "values": [
        {"time": 1_792_800_000_000_000, "values": ["285390"]},
        {"time": 1_792_713_600_000_000, "values": ["275980"]},
        {"time": 1_792_627_200_000_000, "values": []}]}]}
    pts = live.series(entity, "hypervisor_cpu_usage_ppm")
    assert [p[0] for p in pts] == [1_792_627_200_000, 1_792_713_600_000, 1_792_800_000_000]
    assert pts[0][1] is None and pts[-1][1] == 285390
    assert live.num("-1") is None and live.num("12") == 12 and live.num(None) is None


def test_alert_title_placeholders() -> None:
    title = live.render_title("Boot RAID Volumes degraded on CVM: {service_vm_external_ip}",
                              ["host_ip", "service_vm_external_ip"], ["192.0.2.11", "192.0.2.21"])
    assert title == "Boot RAID Volumes degraded on CVM: 192.0.2.21"
    assert live.render_title("{msg_title}", [], []) == "{msg_title}"
    assert live.render_title(None, [], []) == "Alert"


def test_node_health_from_pe() -> None:
    row = {"entity_id": "h1", "data": [{"name": "name", "values": [{"values": ["n1"]}]},
                                       {"name": "node_status", "values": [{"values": ["kNormal"]}]}]}
    ok = live._node("h1", row, {"state": "NORMAL", "metadata_store_status": "kNormalMode",
                                "is_degraded": False, "host_in_maintenance_mode": False})
    assert ok["status"] == "ok" and ok["metadata_store"] == "Normal mode"
    bad = live._node("h1", row, {"state": "NORMAL", "metadata_store_status": "kKickedOutOfRing"})
    assert bad["status"] == "error" and bad["cvm_status"] == "error"
    maint = live._node("h1", row, {"state": "NORMAL", "host_in_maintenance_mode": True})
    assert maint["status"] == "warn"
