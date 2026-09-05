"""X-Play (Playbooks) collector.

X-Play automation is a v3-only feature exposed via `action_rules` (Playbooks). Degrades
gracefully when X-Play is not in use.

**There is no v4 home for this.** Probed against a live pc.7.5.1.1 and cross-checked
against every published `ntnx-*-py-client` v4 SDK: no namespace exposes playbooks or
action rules. `aiops` covers capacity planning only (scenarios/simulations/sources), and
`opsmgmt` covers reports. The single v4 mention of playbooks is the `ACTION_RULE` member
of `prism.v4.config.ResourceType`, which only labels a category association — it cannot
enumerate playbooks. So this collector stays on v3 until Nutanix ships the API, and the
X-Play section goes dark at the Q4-2026 v3 retirement if they do not.

**Everything is read from `status`, not `spec`.** `POST /action_rules/list` returns
`spec: null` for every entity on Prism Central and puts the whole playbook definition
under `status.resources`. Reading `spec` therefore yielded a row per playbook with every
field blank — the right count, no content — which looks like a populated section rather
than a broken one. `spec` is still consulted as a fallback because the per-entity GET
does populate it.
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
            resources = _resources(entity)
            trigger = _first(resources.get("trigger_list") or [])
            actions = resources.get("action_list") or []
            env.playbooks.append(
                Playbook(
                    uuid=entity.get("metadata", {}).get("uuid", ""),
                    name=resources.get("name") or (entity.get("status") or {}).get("name"),
                    description=resources.get("description"),
                    trigger_type=_trigger_type(trigger),
                    action_count=len(actions),
                    is_enabled=resources.get("is_enabled"),
                    api_source="v3",
                )
            )
        env.collection_notes[self.name] = "ok"


def _resources(entity: dict[str, Any]) -> dict[str, Any]:
    """The playbook definition, wherever this endpoint happened to put it."""
    for branch in ("status", "spec"):
        resources = (entity.get(branch) or {}).get("resources")
        if isinstance(resources, dict) and resources:
            return resources
    return {}


def _first(items: list[Any]) -> dict[str, Any]:
    first = items[0] if items else None
    return first if isinstance(first, dict) else {}


def _trigger_type(trigger: dict[str, Any]) -> str | None:
    """Classify a trigger into the ALERT/MANUAL/SCHEDULED/WEBHOOK vocabulary.

    Real triggers name their kind indirectly, through
    `action_trigger_type_reference.name` (`alert_trigger`, `all_alerts_trigger`, …), so
    the family is matched on a substring rather than an exact name — X-Play ships a
    growing catalogue of trigger types and new ones follow the same naming. The
    `*_trigger` key forms are checked first because they are what the per-entity GET
    returns.
    """
    if not trigger:
        return None
    # Membership, not truthiness: a manual trigger legitimately carries an empty payload,
    # and testing the value would classify it as untyped.
    for key, kind in (
        ("alert_trigger", "ALERT"),
        ("manual_trigger", "MANUAL"),
        ("schedule_trigger", "SCHEDULED"),
        ("webhook_trigger", "WEBHOOK"),
    ):
        if key in trigger:
            return kind

    ref = (trigger.get("action_trigger_type_reference") or {}).get("name") or ""
    for needle, kind in (
        ("alert", "ALERT"),
        ("manual", "MANUAL"),
        ("schedule", "SCHEDULED"),
        ("time", "SCHEDULED"),
        ("webhook", "WEBHOOK"),
        ("event", "EVENT"),
    ):
        if needle in ref:
            return kind
    if ref:
        # An unrecognised trigger family: report what the API called it rather than
        # blanking the column, so a new X-Play trigger type is visible instead of absent.
        return ref.removesuffix("_trigger").upper()
    return trigger.get("type")
