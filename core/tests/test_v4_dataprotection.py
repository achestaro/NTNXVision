"""The v4 protection-policy path (the `datapolicies` namespace)."""

from __future__ import annotations

from typing import Any

from ntnxvision_core.collectors.dataprotection import DataProtectionCollector
from ntnxvision_core.models import Environment

# Trimmed from a live pc.7.5.1.1 response.
V4_POLICY = {
    "extId": "2e00ef02-2908-43a7-a1e7-950f686baf18",
    "name": "pp-portops-async-1h-dc2-to-dc1",
    "description": "Port Ops board - 1h AsyncDR, DC2->DC1",
    "categoryIds": ["26cf96a9-afea-4da8-7d23-d10543a89b62"],
    "replicationLocations": [
        # Deliberately out of order: the mapper must sort on `label`.
        {"label": "1", "domainManagerExtId": "e807ab87", "isPrimary": False},
        {"label": "0", "domainManagerExtId": "9ea74d5c", "isPrimary": True},
    ],
    "replicationConfigurations": [
        {
            "sourceLocationLabel": "0",
            "remoteLocationLabel": "1",
            "schedule": {
                "retention": {"local": 24, "remote": 24},
                "recoveryPointType": "CRASH_CONSISTENT",
                "recoveryPointObjectiveTimeSeconds": 3600,
            },
        }
    ],
}

V3_POLICY = {
    "metadata": {"uuid": V4_POLICY["extId"]},
    "spec": {
        "name": V4_POLICY["name"],
        "resources": {"category_filter": {"params": {"DRTier": ["PortOps"]}}},
    },
}


class _Conn:
    def __init__(self, v4: bool = True, v3: list[dict[str, Any]] | None = None) -> None:
        self._v4 = v4
        self._v3 = [dict(V3_POLICY)] if v3 is None else v3

    def v4_ready(self, area: str) -> bool:
        return self._v4

    def v4_list(self, path: str) -> list[dict[str, Any]]:
        assert path.startswith("datapolicies/"), "protection policies are not in dataprotection/"
        return [dict(V4_POLICY)]

    def v3_list(self, kind: str) -> list[dict[str, Any]]:
        return list(self._v3) if kind == "protection_rules" else []


def _policies(conn: _Conn) -> Any:
    env = Environment()
    DataProtectionCollector(conn).collect(env)  # type: ignore[arg-type]
    return env.protection_policies


def test_v4_policy_maps_schedule_and_retention() -> None:
    p = _policies(_Conn())[0]
    assert p.name == "pp-portops-async-1h-dc2-to-dc1"
    assert p.rpo_seconds == 3600
    assert p.replication_type == "ASYNC"
    assert p.local_retention == 24
    assert p.remote_retention == 24
    assert p.api_source == "v4"


def test_availability_zones_keep_v3s_ordering_and_identifiers() -> None:
    """`label` mirrors v3's availability_zone_label; domainManagerExtId matches its url,
    so the engine's AZ-name resolution keeps working."""
    p = _policies(_Conn())[0]
    assert p.source_az == "9ea74d5c"  # label "0", despite being listed second
    assert p.target_az == "e807ab87"


def test_categories_come_from_v3_in_readable_form() -> None:
    """v4 reports bare category uuids; the reports show `DRTier:PortOps`."""
    assert _policies(_Conn())[0].categories == ["DRTier:PortOps"]


def test_missing_v3_leaves_the_policy_intact_without_categories() -> None:
    p = _policies(_Conn(v3=[]))[0]
    assert p.rpo_seconds == 3600
    assert p.categories == []


def test_v3_path_is_used_when_v4_is_not_served() -> None:
    conn = _Conn(v4=False, v3=[{**V3_POLICY, "spec": {**V3_POLICY["spec"], "resources": {}}}])
    assert _policies(conn)[0].api_source == "v3"
