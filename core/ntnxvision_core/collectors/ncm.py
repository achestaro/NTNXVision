"""NCM (Cloud Manager) collector — currently the report definitions (v3).

NCM's fuller surface (blueprints, runbooks, cost, compliance) spans multiple internal
endpoints that vary by version; Phase 2 captures report definitions, which are the most
universally available NCM artifact. Degrades gracefully when NCM is not licensed.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.models import NcmReport


class NcmCollector(Collector):
    name = "ncm_reports"
    label = "NCM Reports"

    def collect(self, env: Any) -> None:
        try:
            entities = self.conn.v3_list("reports/configs")
        except Exception:  # noqa: BLE001 — NCM/Reports not available
            env.collection_notes[self.name] = "skipped: NCM Reports not available"
            return

        for entity in entities:
            spec = entity.get("spec", {})
            resources = spec.get("resources", {})
            env.ncm_reports.append(
                NcmReport(
                    uuid=entity.get("metadata", {}).get("uuid"),
                    name=spec.get("name"),
                    report_type=resources.get("report_type"),
                    schedule=_schedule(resources.get("schedule", {})),
                    api_source="v3",
                )
            )
        env.collection_notes[self.name] = "ok"


def _schedule(schedule: dict[str, Any]) -> str | None:
    if not schedule:
        return None
    return schedule.get("interval_type") or schedule.get("schedule_type")
