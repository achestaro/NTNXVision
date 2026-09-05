"""X-Play stays on v3, and must actually read the branch v3 fills in.

There is no v4 namespace for playbooks (see the collector docstring), so these tests
pin the v3 mapping rather than a migration.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.collectors.xplay import XPlayCollector, _trigger_type
from ntnxvision_core.models import Environment

# Trimmed from a live pc.7.5.1.1 `POST /api/nutanix/v3/action_rules/list`. `spec` really
# is null there — the entire definition arrives under `status`.
V3_ACTION_RULE: dict[str, Any] = {
    "spec": None,
    "metadata": {"kind": "action_rule", "uuid": "c8c27a1c-f950-4d44-b08e-71beacf61cd0"},
    "status": {
        "name": "Report a cluster resource shortage",
        "resources": {
            "name": "Report a cluster resource shortage",
            "is_enabled": False,
            "description": "Emails a Just-in-Time-Forecast report on a cluster runway alert.",
            "rule_type": "action_rule",
            "trigger_list": [
                {
                    "action_trigger_type_reference": {
                        "kind": "action_trigger_type",
                        "name": "all_alerts_trigger",
                        "uuid": "0adf906b-65ce-47a9-5ce9-f26b086e1c85",
                    },
                    "display_name": "Alerts Matching Criteria",
                    "instance_uuid": "bf6b081a-b11f-4df6-9614-5448d262a912",
                }
            ],
            "action_list": [
                {"display_name": "Generate Forecast Report"},
                {"display_name": "Email"},
            ],
        },
    },
}


class _Conn:
    def __init__(self, entities: list[dict[str, Any]]) -> None:
        self._entities = entities

    def v3_list(self, kind_path: str, **kw: Any) -> list[dict[str, Any]]:
        return [dict(e) for e in self._entities]


def _collect(entities: list[dict[str, Any]]) -> Environment:
    env = Environment()
    XPlayCollector(_Conn(entities)).collect(env)  # type: ignore[arg-type]
    return env


def test_playbook_fields_come_from_status_not_spec() -> None:
    """Reading `spec` produced a row per playbook with every field blank."""
    playbook = _collect([V3_ACTION_RULE]).playbooks[0]
    assert playbook.uuid == "c8c27a1c-f950-4d44-b08e-71beacf61cd0"
    assert playbook.name == "Report a cluster resource shortage"
    assert playbook.description.startswith("Emails a Just-in-Time-Forecast")
    assert playbook.is_enabled is False
    assert playbook.action_count == 2
    assert playbook.api_source == "v3"


def test_trigger_type_reads_the_trigger_type_reference() -> None:
    """The list API names the trigger family only via the reference, never a `*_trigger` key."""
    assert _collect([V3_ACTION_RULE]).playbooks[0].trigger_type == "ALERT"
    assert _trigger_type({"action_trigger_type_reference": {"name": "alert_trigger"}}) == "ALERT"
    assert _trigger_type({"action_trigger_type_reference": {"name": "time_trigger"}}) == "SCHEDULED"
    assert _trigger_type({"action_trigger_type_reference": {"name": "manual_trigger"}}) == "MANUAL"
    # An unknown family is surfaced rather than blanked.
    unknown = {"action_trigger_type_reference": {"name": "quantum_trigger"}}
    assert _trigger_type(unknown) == "QUANTUM"


def test_spec_is_still_honoured_when_populated() -> None:
    """The per-entity GET fills `spec`; that shape must keep working."""
    entity = {
        "metadata": {"uuid": "u1"},
        "status": None,
        "spec": {
            "name": "Manual play",
            "resources": {
                "name": "Manual play",
                "is_enabled": True,
                "trigger_list": [{"manual_trigger": {}}],
                "action_list": [{}],
            },
        },
    }
    playbook = _collect([entity]).playbooks[0]
    assert playbook.name == "Manual play"
    assert playbook.trigger_type == "MANUAL"
    assert playbook.is_enabled is True


def test_missing_xplay_is_a_note_not_a_failure() -> None:
    class _Broken:
        def v3_list(self, kind_path: str, **kw: Any) -> list[dict[str, Any]]:
            raise RuntimeError("no such endpoint")

    env = Environment()
    XPlayCollector(_Broken()).collect(env)  # type: ignore[arg-type]
    assert env.playbooks == []
    assert "skipped" in env.collection_notes["xplay"]
