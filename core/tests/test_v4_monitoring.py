"""The v4 monitoring (health alerts) path, and the traps it set.

Payloads are trimmed from live pc.7.5.1.1 responses: the `$reserved` / `$objectType`
envelopes every v4 record carries are dropped, everything the collector reads is real.
"""

from __future__ import annotations

from typing import Any

import pytest

from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.collectors import alerts as alerts_module
from ntnxvision_core.collectors.alerts import AlertsCollector
from ntnxvision_core.models import Cluster, Environment

DC1 = "0000d596-d86d-45b7-adae-2662c463d891"
DC2 = "0000e44d-bc9d-4f50-a736-cc12243058af"
PC = "0000b6a9-8ca6-44e0-a8d4-d8d661d90f4c"

#: A warning about a VM — the common case (2163 of the lab PC's 2425 alerts).
V4_VM_ALERT = {
    "extId": "00000721-119c-4b1f-ae1f-0133422b5375",
    "isAcknowledged": False,
    "isAutoResolved": False,
    "isResolved": False,
    "sourceEntity": {
        "type": "vm",
        "name": "ubuntu-server-01",
        "extId": "00008707-42e6-4d10-a360-44e241f92f68",
    },
    "affectedEntities": [
        {
            "type": "vm",
            "name": "ubuntu-server-01",
            "extId": "00008707-42e6-4d10-a360-44e241f92f68",
        }
    ],
    "title": "Recovery Point Replication Skipped.",
    "alertType": "A130162",
    "classifications": ["DR"],
    "clusterUUID": DC1,
    "creationTime": "2026-09-05T12:17:26.118249Z",
    "lastUpdatedTime": "2026-09-05T12:17:26.122652Z",
    "message": "Skipped replication of Recovery point created at '{recovery_point_"
    "create_time}' UTC  of the VM: '{vm_name}' because of '{reason}'.",
    "originatingClusterUUID": DC1,
    "severity": "WARNING",
    "clusterName": "lab-cluster-01",
}

#: An LCM failure. Note the affected cluster carries no `name` at all, and
#: `classifications` is an explicit null rather than an absent key.
V4_CLUSTER_ALERT = {
    "extId": "000098a8-1845-4dee-af05-1197343e472f",
    "isAcknowledged": False,
    "isAutoResolved": False,
    "isResolved": False,
    "sourceEntity": {"type": "cluster", "extId": DC2},
    "affectedEntities": [{"type": "cluster", "extId": DC2}],
    "title": "Failed to upgrade the cluster",
    "alertType": "NTNX_LCM_UPGRADE_FAILURE_ALERT",
    "classifications": None,
    "clusterUUID": DC2,
    "creationTime": "2026-09-05T03:21:20.868722Z",
    "message": "NTNX_LCM_Upgrade_Failure_Alert : Upgrade operation failed.",
    "originatingClusterUUID": DC2,
    "severity": "CRITICAL",
    "serviceName": "LCM",
    "clusterName": "lab-cluster-02",
}

#: Raised by Prism Central *about* lab-cluster-01: the two cluster keys disagree, and
#: `clusterName` names the cluster the alert is about rather than the one that raised it.
V4_CROSS_CLUSTER_ALERT = {
    "extId": "00003f04-1c49-4067-a26d-fd8950049813",
    "isAcknowledged": True,
    "isAutoResolved": True,
    "isResolved": True,
    "sourceEntity": {"type": "cluster", "name": "lab-cluster-01", "extId": DC1},
    "affectedEntities": [{"type": "cluster", "name": "lab-cluster-01", "extId": DC1}],
    "title": "Cluster {cluster_name} running out of Memory",
    "clusterUUID": DC1,
    "originatingClusterUUID": PC,
    "clusterName": "lab-cluster-01",
    "creationTime": "2026-08-29T05:26:21.604194Z",
    "severity": "CRITICAL",
    "classifications": ["Cluster"],
}

#: Same cluster uuid as V4_VM_ALERT, but recorded under the name the cluster had when
#: the alert fired nine months earlier.
V4_STALE_NAME_ALERT = {
    "extId": "0000c4ca-b964-4584-a0f4-98c1d38b934c",
    "isAcknowledged": True,
    "isAutoResolved": True,
    "isResolved": True,
    "sourceEntity": {"type": "cluster", "extId": DC1},
    "affectedEntities": [{"type": "cluster", "extId": DC1}],
    "title": "Cluster Service {services} Restarting Frequently",
    "clusterUUID": DC1,
    "originatingClusterUUID": DC1,
    "clusterName": "ntnx01",
    "creationTime": "2025-12-10T05:29:23.656775Z",
    "severity": "WARNING",
    "classifications": ["Cluster"],
}

#: The v3 answer for V4_CLUSTER_ALERT, in the nested shape PC's alerts/list really uses.
V3_NESTED_ALERT = {
    "status": {
        "state": "COMPLETE",
        "resources": {
            "type": "NTNX_LCM_UPGRADE_FAILURE_ALERT",
            "title": "Failed to upgrade the cluster",
            "default_message": "NTNX_LCM_Upgrade_Failure_Alert : Upgrade operation failed.",
            "severity": "critical",
            "originating_cluster_uuid": DC2,
            "classification_list": [],
            "affected_entity_list": [{"name": "", "type": "cluster", "uuid": DC2}],
            "creation_time": "2026-09-05T03:21:20Z",
            "resolution_status": {"is_true": False},
            "acknowledged_status": {"is_true": False},
        },
    },
    "metadata": {"uuid": "000098a8-1845-4dee-af05-1197343e472f", "kind": "alert"},
    "spec": {},
}


class _Conn:
    """A Prism Central that serves v4 alerts in pages, optionally rate-limiting."""

    def __init__(
        self,
        pages: list[list[dict[str, Any]]] | None = None,
        v4: bool = True,
        rate_limit: int = 0,
        v3: list[dict[str, Any]] | None = None,
    ) -> None:
        self._pages = pages if pages is not None else [[dict(V4_VM_ALERT)]]
        self._v4 = v4
        self._rate_limit = rate_limit
        self._v3 = v3 if v3 is not None else [dict(V3_NESTED_ALERT)]
        self.requests: list[dict[str, Any]] = []

    def v4_ready(self, area: str) -> bool:
        assert area == "monitoring"
        return self._v4

    def v4_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        assert path == "monitoring/v4.0/serviceability/alerts"
        assert params is not None
        self.requests.append(params)
        if self._rate_limit > 0:
            self._rate_limit -= 1
            raise NutanixApiError("rate limit exceeded", status_code=429)
        page = params["$page"]
        data = self._pages[page] if page < len(self._pages) else []
        total = sum(len(p) for p in self._pages)
        return {"data": data, "metadata": {"totalAvailableResults": total}}

    def v3_list(self, kind: str) -> list[dict[str, Any]]:
        assert kind == "alerts"
        return list(self._v3)


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop the real pacing so the suite does not sleep through its own rate limit."""
    monkeypatch.setattr(alerts_module, "_V4_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(alerts_module, "_V4_BACKOFF", 0.0)


def _collect(conn: _Conn) -> Environment:
    env = Environment()
    AlertsCollector(conn).collect(env)  # type: ignore[arg-type]
    return env


# ─── mapping ───────────────────────────────────────────────────────────


def test_v4_alert_maps_every_reported_field() -> None:
    a = _collect(_Conn()).alerts[0]
    assert a.uuid == V4_VM_ALERT["extId"]
    assert a.title == "Recovery Point Replication Skipped."
    assert a.severity == "WARNING"
    assert a.entity_type == "vm"
    assert a.entity_name == "ubuntu-server-01"
    assert a.cluster_uuid == DC1
    assert a.resolved is False
    assert a.acknowledged is False
    assert a.classifications == ["DR"]
    assert a.api_source == "v4"


def test_severity_is_normalized_from_the_v4_spelling() -> None:
    """v4 upper-cases what v3 lower-cases; both must land on the same bucket."""
    env = _collect(_Conn(pages=[[dict(V4_VM_ALERT), dict(V4_CLUSTER_ALERT)]]))
    assert [a.severity for a in env.alerts] == ["WARNING", "CRITICAL"]
    assert env.alerts_summary.critical_count == 1
    assert env.alerts_summary.warning_count == 1
    assert env.alerts_summary.other_count == 0


def test_created_at_keeps_the_second_resolution_v3_reports() -> None:
    """The trap: v4 adds microseconds to the identical instant v3 reports.

    The string goes straight into the Excel and Word "Created" columns, so the two
    paths have to agree character for character.
    """
    a = _collect(_Conn()).alerts[0]
    assert a.created_at == "2026-09-05T12:17:26Z"
    assert alerts_module._v4_timestamp("2026-08-29T05:26:21.604194+02:00") == (
        "2026-08-29T05:26:21+02:00"
    )
    # A timestamp that never had a fraction is passed through untouched.
    assert alerts_module._v4_timestamp("2026-09-05T03:21:20Z") == "2026-09-05T03:21:20Z"
    assert alerts_module._v4_timestamp(None) is None


def test_cluster_uuid_is_the_originating_one_not_the_reporting_one() -> None:
    """The two keys disagree for alerts a PC raises about a member cluster.

    `originatingClusterUUID` is the one v3 reported and the one the reports group on;
    taking `clusterUUID` would file this alert under the wrong cluster.
    """
    a = _collect(_Conn(pages=[[dict(V4_CROSS_CLUSTER_ALERT)]])).alerts[0]
    assert a.cluster_uuid == PC
    assert a.cluster_uuid != V4_CROSS_CLUSTER_ALERT["clusterUUID"]


def test_cluster_name_comes_from_the_live_cluster_not_the_alert_record() -> None:
    """The trap: v4's `clusterName` is whatever the cluster was called at alert time.

    On the lab PC one cluster uuid appears under two names across its own alerts. The
    collector leaves the field to the engine's uuid cross-reference, so a renamed
    cluster reports one name in every row rather than one per era.
    """
    from ntnxvision_core.engine import _resolve_cross_references

    env = Environment()
    env.clusters.append(Cluster(uuid=DC1, name="lab-cluster-01"))
    AlertsCollector(  # type: ignore[arg-type]
        _Conn(pages=[[dict(V4_VM_ALERT), dict(V4_STALE_NAME_ALERT)]])
    ).collect(env)
    _resolve_cross_references(env)

    assert [a.cluster_name for a in env.alerts] == ["lab-cluster-01", "lab-cluster-01"]


def test_unnamed_affected_entity_is_none_rather_than_blank() -> None:
    """v4 omits the name where v3 sends `""` — both must read as "not reported"."""
    v4 = _collect(_Conn(pages=[[dict(V4_CLUSTER_ALERT)]])).alerts[0]
    v3 = _collect(_Conn(v4=False)).alerts[0]
    assert v4.entity_name is None
    assert v3.entity_name is None
    assert v4.entity_type == v3.entity_type == "cluster"
    # An explicit null `classifications` must not become [None] or crash.
    assert v4.classifications == []


def test_v4_fills_auto_resolved_where_v3_leaves_it_empty() -> None:
    """A gain, not a regression: v3 only carries the flag once an alert resolves."""
    assert _collect(_Conn(pages=[[dict(V4_CLUSTER_ALERT)]])).alerts[0].auto_resolved is False
    assert _collect(_Conn(v4=False)).alerts[0].auto_resolved is None


# ─── paging and the rate limit ─────────────────────────────────────────


def test_paging_walks_every_page_at_the_endpoint_maximum() -> None:
    """2425 alerts at the endpoint's hard `$limit` ceiling of 100 is 25 requests."""
    pages = [
        [dict(V4_VM_ALERT, extId=f"a{p}-{i}") for i in range(100)] for p in range(3)
    ]
    pages.append([dict(V4_VM_ALERT, extId="tail")])
    env = _collect(conn := _Conn(pages=pages))

    assert len(env.alerts) == 301
    assert env.alerts_summary.total_count == 301
    assert [r["$page"] for r in conn.requests] == [0, 1, 2, 3]
    assert {r["$limit"] for r in conn.requests} == {100}


def test_a_rate_limited_page_is_retried_and_reported() -> None:
    """The trap: this namespace allows two requests a second and 429s above it.

    `v4_list` has no pacing or retry, so it dies on the fifth page of a 25-page walk.
    """
    env = _collect(_Conn(rate_limit=2))
    assert len(env.alerts) == 1
    assert "rate limiting" in env.collection_notes["alerts"]


def test_persistent_rate_limiting_falls_back_to_v3_rather_than_losing_alerts() -> None:
    """The budget is per Prism Central, so a busy PC can outlast the retries."""
    conn = _Conn(rate_limit=99)
    env = _collect(conn)
    assert env.collection_notes["alerts"] == (
        "ok: v4 alerts endpoint rate-limited, collected over v3"
    )
    assert len(env.alerts) == 1
    assert env.alerts[0].api_source == "v3"
    assert env.alerts[0].title == "Failed to upgrade the cluster"
    # Exactly the configured attempts, then it gives up rather than hammering the PC.
    assert len(conn.requests) == alerts_module._V4_RATE_LIMIT_RETRIES + 1


def test_a_non_rate_limit_error_is_not_retried() -> None:
    """Only 429 earns a backoff; anything else falls straight through to v3."""

    class _Broken(_Conn):
        def v4_get(
            self, path: str, params: dict[str, Any] | None = None
        ) -> dict[str, Any]:
            self.requests.append(params or {})
            raise NutanixApiError("boom", status_code=500)

    conn = _Broken()
    env = _collect(conn)
    assert len(conn.requests) == 1
    assert env.collection_notes["alerts"] == "ok: v4 alerts endpoint HTTP 500, collected over v3"
    assert env.alerts[0].api_source == "v3"


# ─── the legacy path ───────────────────────────────────────────────────


def test_v3_reads_prism_centrals_nested_alert_shape() -> None:
    """Every alert field lives under `status.resources`, not at the top level.

    Reading only the flat keys is why the pre-migration output carried nothing but a
    uuid and an OTHER severity for all 2424 rows.
    """
    a = _collect(_Conn(v4=False)).alerts[0]
    assert a.uuid == "000098a8-1845-4dee-af05-1197343e472f"
    assert a.title == "Failed to upgrade the cluster"
    assert a.message == "NTNX_LCM_Upgrade_Failure_Alert : Upgrade operation failed."
    assert a.severity == "CRITICAL"
    assert a.cluster_uuid == DC2
    assert a.created_at == "2026-09-05T03:21:20Z"
    assert a.resolved is False
    assert a.acknowledged is False
    assert a.api_source == "v3"


def test_v3_and_v4_agree_on_the_same_alert() -> None:
    """The pair the lab matched on: same uuid, same row whichever path served it."""
    v4 = _collect(_Conn(pages=[[dict(V4_CLUSTER_ALERT)]])).alerts[0]
    v3 = _collect(_Conn(v4=False)).alerts[0]
    for field in (
        "uuid",
        "title",
        "message",
        "severity",
        "resolved",
        "acknowledged",
        "entity_type",
        "entity_name",
        "cluster_uuid",
        "created_at",
        "classifications",
    ):
        assert getattr(v4, field) == getattr(v3, field), field
