"""X-Play (Playbooks) collector.

X-Play automation is a v3-only feature exposed via `action_rules` (Playbooks). Degrades
gracefully when X-Play is not in use.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.models import Playbook


class XPlayCollector(Collector):
    name = "xplay"
    label = "X-Play (Playbooks)"

    def collect(self, env: Any) -> None:
        try:
            entities = self.conn.v3_list("action_rules")
        except Exception:  # noqa: BLE001 — X-Play not available
            env.collection_notes[self.name] = "skipped: X-Play / Playbooks not available"
            return

        for entity in entities:
            spec = entity.get("spec") or {}
            resources = spec.get("resources") or {}
            trigger = _first(resources.get("trigger_list") or [])
            actions = resources.get("action_list") or []
            env.playbooks.append(
                Playbook(
                    uuid=entity.get("metadata", {}).get("uuid", ""),
                    name=spec.get("name"),
                    description=spec.get("description"),
                    trigger_type=_trigger_type(trigger),
                    action_count=len(actions),
                    is_enabled=resources.get("is_enabled"),
                    api_source="v3",
                )
            )
        env.collection_notes[self.name] = "ok"


def _first(items: list[Any]) -> dict[str, Any]:
    first = items[0] if items else None
    return first if isinstance(first, dict) else {}


def _trigger_type(trigger: dict[str, Any]) -> str | None:
    if not trigger:
        return None
    if trigger.get("alert_trigger"):
        return "ALERT"
    if trigger.get("manual_trigger"):
        return "MANUAL"
    if trigger.get("schedule_trigger"):
        return "SCHEDULED"
    if trigger.get("webhook_trigger"):
        return "WEBHOOK"
    return trigger.get("type")
