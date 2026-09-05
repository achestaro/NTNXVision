"""The v4 NCM report path (the `opsmgmt` namespace)."""

from __future__ import annotations

from typing import Any

from ntnxvision_core.api import v4_client
from ntnxvision_core.collectors.ncm import NcmCollector
from ntnxvision_core.models import Environment

# Trimmed from a live pc.7.5.1.1 response. Note what is *absent*: no schedule key.
V4_REPORT = {
    "extId": "0d4e1c1e-9c2a-4e07-9a0b-2a1f6d1a7c11",
    "name": "Cluster Efficiency Summary",
    "description": "Efficiency across registered clusters",
    "isSystemDefined": True,
    "supportedFormats": ["PDF", "CSV"],
    "timezone": "UTC",
}


class _Conn:
    def __init__(self, v4: bool = True, instances: list[dict[str, Any]] | None = None) -> None:
        self._v4 = v4
        self._instances = instances or []

    def v4_ready(self, area: str) -> bool:
        assert area == "ncm_reports"
        return self._v4

    def v4_list(self, path: str) -> list[dict[str, Any]]:
        assert path.startswith("opsmgmt/"), "reports are served by opsmgmt, not aiops"
        if path.endswith("/reports"):
            return list(self._instances)
        return [dict(V4_REPORT)]

    def v3_list(self, kind: str) -> list[dict[str, Any]]:
        if kind == "report_configs":
            return [{"metadata": {"uuid": "r1"}, "spec": {"name": "legacy", "resources": {}}}]
        return []


def _reports(conn: _Conn) -> Any:
    env = Environment()
    NcmCollector(conn).collect(env)  # type: ignore[arg-type]
    return env


def test_area_is_registered_and_points_at_opsmgmt() -> None:
    ns = v4_client.NAMESPACES["ncm_reports"]
    assert ns.probe_path == "opsmgmt/v4.0/config/report-configs"


def test_v4_report_maps_identity() -> None:
    r = _reports(_Conn()).ncm_reports[0]
    assert r.uuid == V4_REPORT["extId"]
    assert r.name == "Cluster Efficiency Summary"
    assert r.api_source == "v4"


def test_report_type_keeps_the_literal_v3_has_always_emitted() -> None:
    """Neither API carries a report type; the column must not change with the path."""
    assert _reports(_Conn()).ncm_reports[0].report_type == "REPORT"


def test_schedule_is_absent_from_the_v4_payload_and_stays_empty() -> None:
    """v4's report-config has no schedule key at all on this build, exactly as v3."""
    assert "schedule" not in V4_REPORT
    assert _reports(_Conn()).ncm_reports[0].schedule is None


def test_last_run_takes_the_most_recent_instance() -> None:
    conn = _Conn(
        instances=[
            {"configExtId": V4_REPORT["extId"], "creationTime": "2026-09-01T00:00:00Z"},
            {"configExtId": V4_REPORT["extId"], "creationTime": "2026-09-04T00:00:00Z"},
        ]
    )
    assert _reports(conn).ncm_reports[0].last_run == "2026-09-04T00:00:00Z"


def test_unreadable_instances_do_not_fail_the_run() -> None:
    class _NoInstances(_Conn):
        def v4_list(self, path: str) -> list[dict[str, Any]]:
            if path.endswith("/reports"):
                raise RuntimeError("not served")
            return [dict(V4_REPORT)]

    env = _reports(_NoInstances())
    assert env.ncm_reports[0].last_run is None
    assert env.collection_notes["ncm_reports"] == "ok"


def test_v3_path_is_used_when_v4_is_not_served() -> None:
    env = _reports(_Conn(v4=False))
    assert env.ncm_reports[0].api_source == "v3"
    assert env.ncm_reports[0].name == "legacy"
