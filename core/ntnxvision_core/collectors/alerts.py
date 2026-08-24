"""Health alerts collector.

Prism Central health alerts, via the v3 `alerts` kind (present across the whole
supported PC range — no v4 equivalent yet). Severity is normalized to
CRITICAL / WARNING / INFO / OTHER so exporters don't need to know the API's
raw `kCritical`-style enum spelling.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.models import Alert, AlertSummary, Environment

_SEVERITY_MAP = {
    "CRITICAL": "CRITICAL",
    "KCRITICAL": "CRITICAL",
    "WARNING": "WARNING",
    "KWARNING": "WARNING",
    "INFO": "INFO",
    "KINFO": "INFO",
}


class AlertsCollector(Collector):
    name = "alerts"
    label = "Health Alerts"

    def collect(self, env: Environment) -> None:
        for entity in self.conn.v3_list("alerts"):
            env.alerts.append(_to_alert(entity))
        env.alerts_summary = _summarize(env.alerts)
        env.collection_notes[self.name] = "ok"


# ─── helpers ───────────────────────────────────────────────────────────


def _severity(raw: str | None) -> str:
    if not raw:
        return "OTHER"
    return _SEVERITY_MAP.get(raw.upper(), "OTHER")


def _first_affected_entity(entity: dict[str, Any]) -> dict[str, Any]:
    affected = entity.get("affected_entities") or entity.get("context_types") or []
    return affected[0] if isinstance(affected, list) and affected else {}


def _to_alert(entity: dict[str, Any]) -> Alert:
    affected = _first_affected_entity(entity)
    return Alert(
        uuid=entity.get("id") or entity.get("uuid") or entity.get("metadata", {}).get("uuid"),
        title=entity.get("title") or entity.get("message"),
        message=entity.get("message") or entity.get("detailed_message"),
        severity=_severity(entity.get("severity")),
        resolved=entity.get("resolved"),
        acknowledged=entity.get("acknowledged"),
        auto_resolved=entity.get("auto_resolved"),
        entity_type=affected.get("entity_type") or affected.get("type"),
        entity_name=affected.get("entity_name") or affected.get("name"),
        cluster_uuid=entity.get("originating_cluster_uuid") or entity.get("cluster_uuid"),
        created_at=entity.get("creation_time") or entity.get("created_time"),
        classifications=list(entity.get("classifications") or []),
        api_source="v3",
    )


def _summarize(alerts: list[Alert]) -> AlertSummary:
    summary = AlertSummary(total_count=len(alerts))
    for alert in alerts:
        if alert.severity == "CRITICAL":
            summary.critical_count += 1
        elif alert.severity == "WARNING":
            summary.warning_count += 1
        elif alert.severity == "INFO":
            summary.info_count += 1
        else:
            summary.other_count += 1
        if not alert.resolved:
            summary.unresolved_count += 1
    return summary
