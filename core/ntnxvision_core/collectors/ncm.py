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
        entities = self._fetch_configs()
        if entities is None:
            env.collection_notes[self.name] = "skipped: NCM Reports not available"
            return

        last_run_by_config = self._latest_instances()
        for entity in entities:
            spec = entity.get("spec", {})
            resources = spec.get("resources", {})
            uuid = entity.get("metadata", {}).get("uuid")
            env.ncm_reports.append(
                NcmReport(
                    uuid=uuid,
                    name=spec.get("name") or resources.get("name"),
                    report_type=resources.get("report_type") or "REPORT",
                    schedule=_schedule(resources.get("schedule", {})),
                    last_run=last_run_by_config.get(uuid or ""),
                    api_source="v3",
                )
            )
        env.collection_notes[self.name] = "ok"

    def _fetch_configs(self) -> list[dict[str, Any]] | None:
        # The v3 kind is report_config → POST /report_configs/list. ('reports/configs'
        # was never a real endpoint, which made every PC look like it had no reports.)
        for kind in ("report_configs", "reports/configs"):
            try:
                return self.conn.v3_list(kind)
            except Exception:  # noqa: BLE001 — try the next shape
                continue
        return None

    def _latest_instances(self) -> dict[str, str]:
        """Map report config uuid → most recent generated-report timestamp."""
        try:
            instances = self.conn.v3_list("report_instances")
        except Exception:  # noqa: BLE001 — instances endpoint optional
            return {}
        latest: dict[str, str] = {}
        for inst in instances:
            resources = (inst.get("status", {}) or {}).get("resources", {}) or {}
            cfg_ref = resources.get("report_config_reference", {}) or {}
            cfg_uuid = cfg_ref.get("uuid")
            ts = (
                resources.get("generation_end_time")
                or resources.get("creation_time")
                or (inst.get("metadata", {}) or {}).get("creation_time")
            )
            if cfg_uuid and ts and ts > latest.get(cfg_uuid, ""):
                latest[cfg_uuid] = ts
        return latest


def _schedule(schedule: dict[str, Any]) -> str | None:
    if not schedule:
        return None
    return schedule.get("interval_type") or schedule.get("schedule_type")
