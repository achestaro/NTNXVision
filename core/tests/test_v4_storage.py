"""The v4 storage-container path and the capacity trap it set."""

from __future__ import annotations

from typing import Any

from ntnxvision_core.collectors.storage import StorageCollector
from ntnxvision_core.models import Environment

# Trimmed from a live pc.7.5.1.1 response.
V4_CONTAINER = {
    "containerExtId": "0371fdd4-ad03-48a4-b263-a3d682012780",
    "name": "flow_data",
    "clusterName": "lab-cluster-01",
    "clusterExtId": "0006442f-1fc6-3ee1-0000-000000016d34",
    "replicationFactor": 2,
    "isCompressionEnabled": True,
    "compressionDelaySecs": 0,
    "onDiskDedup": "OFF",
    "erasureCode": "OFF",
    # The *raw* ceiling — byte-for-byte v3's `max_capacity`, ~1.85x the user-level figure.
    "maxCapacityBytes": 4056315446261,
}

GROUPS_ROW = {
    "entity_id": V4_CONTAINER["containerExtId"],
    "storage.user_usage_bytes": "0",
    "storage.user_free_bytes": "1098217853434",
    "storage.capacity_bytes": "2196435706869",
}


class _Conn:
    def __init__(self, v4: bool = True, groups: list[dict[str, Any]] | None = None) -> None:
        self._v4 = v4
        self.groups = groups if groups is not None else [dict(GROUPS_ROW)]

    def v4_ready(self, area: str) -> bool:
        return self._v4

    def v4_list(self, path: str) -> list[dict[str, Any]]:
        return [dict(V4_CONTAINER)]

    def v3_groups(self, entity: str, attrs: list[str], **kw: Any) -> list[dict[str, Any]]:
        return list(self.groups)


def _collect(conn: _Conn) -> Environment:
    env = Environment()
    StorageCollector(conn).collect(env)  # type: ignore[arg-type]
    return env


def test_v4_container_maps_config_fields() -> None:
    c = _collect(_Conn()).storage_containers[0]
    assert c.name == "flow_data"
    assert c.cluster_name == "lab-cluster-01"
    assert c.replication_factor == 2
    assert c.deduplication == "OFF"
    assert c.api_source == "v4"
    # v3's groups query never returned these at all.
    assert c.compression_enabled is True
    assert c.compression_delay_secs == 0


def test_erasure_coding_keeps_the_v3_spelling() -> None:
    """v4 upper-cases it; the column must not change appearance with the path."""
    assert _collect(_Conn()).storage_containers[0].erasure_coding == "off"


def test_capacity_ceiling_is_the_user_level_figure_not_the_raw_one() -> None:
    """The trap: v4's maxCapacityBytes is raw, while used/free are user-level.

    Reporting the raw ceiling alongside user-level usage made a half-full container
    look nearly three-quarters full.
    """
    c = _collect(_Conn()).storage_containers[0]
    assert c.max_capacity_bytes == 2196435706869
    assert c.max_capacity_bytes != V4_CONTAINER["maxCapacityBytes"]
    assert c.used_bytes == 0
    assert c.free_bytes == 1098217853434


def test_raw_capacity_is_the_last_resort_when_v3_is_gone() -> None:
    """After the legacy sunset the ceiling is v4's raw value rather than nothing."""
    c = _collect(_Conn(groups=[])).storage_containers[0]
    assert c.max_capacity_bytes == V4_CONTAINER["maxCapacityBytes"]
    assert c.used_bytes is None


def test_v3_groups_path_is_used_when_v4_is_not_served() -> None:
    env = _collect(_Conn(v4=False, groups=[{**GROUPS_ROW, "container_name": "legacy"}]))
    assert env.storage_containers[0].api_source == "v3-groups"
    assert env.collection_notes["storage_containers"] == "ok"
