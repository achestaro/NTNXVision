"""Health alerts: collector, model normalization, chart, and export wiring."""

from __future__ import annotations

from typing import Any

from ntnxvision_core.collectors.alerts import AlertsCollector
from ntnxvision_core.exporters import diagrams
from ntnxvision_core.models import Cluster, Environment


class _FakeConn:
    def __init__(self, entities: list[dict[str, Any]]) -> None:
        self._entities = entities

    def v4_ready(self, area: str) -> bool:
        """These exercise the legacy path; the v4 one lives in test_v4_monitoring."""
        return False

    def v3_list(self, kind: str) -> list[dict[str, Any]]:
        assert kind == "alerts"
        return self._entities


def _entity(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "a1",
        "title": "CVM memory pressure",
        "message": "CVM memory pressure",
        "severity": "kCritical",
        "resolved": False,
        "acknowledged": False,
        "originating_cluster_uuid": "c1",
        "creation_time": "2026-08-01T00:00:00Z",
        "affected_entities": [{"entity_type": "host", "entity_name": "NX-01"}],
        "classifications": ["Hardware"],
    }
    base.update(overrides)
    return base


def test_collector_normalizes_severity_and_populates_summary() -> None:
    env = Environment()
    collector = AlertsCollector.__new__(AlertsCollector)
    collector.conn = _FakeConn([  # type: ignore[assignment]
        _entity(id="a1", severity="kCritical", resolved=False),
        _entity(id="a2", severity="kWarning", resolved=False),
        _entity(id="a3", severity="kInfo", resolved=True),
        _entity(id="a4", severity="kAudit", resolved=True),  # unrecognized -> OTHER
    ])
    collector.collect(env)

    assert env.collection_notes["alerts"] == "ok"
    assert len(env.alerts) == 4
    assert [a.severity for a in env.alerts] == ["CRITICAL", "WARNING", "INFO", "OTHER"]
    assert env.alerts[0].entity_name == "NX-01"
    assert env.alerts[0].cluster_uuid == "c1"

    s = env.alerts_summary
    assert s.total_count == 4
    assert s.critical_count == 1
    assert s.warning_count == 1
    assert s.info_count == 1
    assert s.other_count == 1
    assert s.unresolved_count == 2  # a1, a2 not resolved


def test_collector_handles_no_alerts() -> None:
    env = Environment()
    collector = AlertsCollector.__new__(AlertsCollector)
    collector.conn = _FakeConn([])  # type: ignore[assignment]
    collector.collect(env)
    assert env.collection_notes["alerts"] == "ok"
    assert env.alerts == []
    assert env.alerts_summary.total_count == 0


def test_cross_reference_resolves_cluster_name() -> None:
    from ntnxvision_core.engine import _resolve_cross_references

    env = Environment()
    env.clusters.append(Cluster(uuid="c1", name="clu-prod"))
    env = _collect_into(env, [_entity(originating_cluster_uuid="c1")])
    _resolve_cross_references(env)
    assert env.alerts[0].cluster_name == "clu-prod"


def _collect_into(env: Environment, entities: list[dict[str, Any]]) -> Environment:
    collector = AlertsCollector.__new__(AlertsCollector)
    collector.conn = _FakeConn(entities)  # type: ignore[assignment]
    collector.collect(env)
    return env


def test_alert_severity_chart_renders_and_omits_when_empty() -> None:
    env = _collect_into(Environment(), [
        _entity(id="a1", severity="kCritical"),
        _entity(id="a2", severity="kWarning"),
    ])
    diag = diagrams.alert_severity_chart(env)
    assert diag is not None
    assert diag.png.startswith(b"\x89PNG")
    assert diagrams.alert_severity_chart(Environment()) is None


def test_word_and_pdf_export_include_alerts_section(tmp_path: Any) -> None:
    from ntnxvision_core.exporters.pdf import PdfExporter
    from ntnxvision_core.exporters.word import WordExporter

    env = Environment(collected_at="2026-08-01T00:00:00Z")
    env.clusters.append(Cluster(uuid="c1", name="clu-prod", num_nodes=1))
    env = _collect_into(env, [_entity(id="a1", severity="kCritical")])

    docx_path = WordExporter().export(env, tmp_path / "add.docx")
    assert docx_path.exists()

    pdf_path = PdfExporter().export(env, tmp_path / "report.pdf")
    assert pdf_path.exists()
