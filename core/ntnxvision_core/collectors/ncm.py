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
        if self.conn.v4_ready("ncm_reports"):
            self._collect_v4(env)
            return
        self._collect_v3(env)

    # ─── v4 ────────────────────────────────────────────────────────

    def _collect_v4(self, env: Any) -> None:
        """Report definitions from `opsmgmt`.

        The namespace is `opsmgmt`, which is exclusively Prism Reports rather than NCM
        operations generally — `aiops`, the name the product map suggested, turned out
        to be capacity planning and serves no reports.

        The Schedule column stays empty, and not for want of asking: the v4 payload has
        no `schedule` key at all on this build (its keys are extId, name, description,
        sections, supportedFormats, timezone, defaultSectionEntityType, isSystemDefined,
        creationTime), exactly as v3 returns none. The lookup is kept because the field
        is documented and costs nothing if a later build starts populating it. Likewise
        `report_type` has no v4 equivalent, and both APIs return zero report *instances*
        here, so `last_run` is empty on either path.
        """
        last_run = self._latest_instances_v4()
        for entity in self.conn.v4_list("opsmgmt/v4.0/config/report-configs"):
            uuid = entity.get("extId")
            env.ncm_reports.append(
                NcmReport(
                    uuid=uuid,
                    name=entity.get("name"),
                    # No v4 equivalent either; keep the literal v3 has always emitted so
                    # the column does not change with the path.
                    report_type="REPORT",
                    schedule=(entity.get("schedule") or {}).get("scheduleInterval"),
                    last_run=last_run.get(uuid or ""),
                    api_source="v4",
                )
            )
        env.collection_notes[self.name] = "ok"

    def _latest_instances_v4(self) -> dict[str, str]:
        """config uuid → most recent generated-report timestamp."""
        try:
            instances = self.conn.v4_list("opsmgmt/v4.0/config/reports")
        except Exception:  # noqa: BLE001 — instances are optional
            return {}
        latest: dict[str, str] = {}
        for inst in instances:
            config_id = inst.get("configExtId")
            created = inst.get("creationTime")
            if config_id and created and created > latest.get(config_id, ""):
                latest[config_id] = created
        return latest

    # ─── v3 ────────────────────────────────────────────────────────

    def _collect_v3(self, env: Any) -> None:
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
