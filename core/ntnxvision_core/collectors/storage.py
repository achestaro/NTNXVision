"""Storage container collector.

Prism Central does not expose storage containers via a dedicated `.../list` endpoint
(that returns 404); they are read through the groups API. We try the groups API first and
fall back to the legacy list endpoint for any environment where it exists.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.models import StorageContainer

# Group attributes to request for each storage container.
_ATTRS = [
    "container_name",
    "cluster_name",
    "cluster",
    "replication_factor",
    "compression_enabled",
    "compression_delay_in_secs",
    "on_disk_dedup",
    "erasure_code",
    "max_capacity",
    "storage.user_container_max_capacity_bytes",
    "storage.capacity_bytes",
    "storage.user_usage_bytes",
    "storage.user_free_bytes",
]


class StorageCollector(Collector):
    name = "storage_containers"
    label = "Storage Containers"

    def collect(self, env: Any) -> None:
        rows = self._via_groups()
        if rows is None:
            rows = self._via_list()

        for row in rows:
            env.storage_containers.append(row)
        env.collection_notes[self.name] = "ok"

    def _via_groups(self) -> list[StorageContainer] | None:
        try:
            entities = self.conn.v3_groups("storage_container", _ATTRS)
        except NutanixApiError:
            return None
        out: list[StorageContainer] = []
        for e in entities:
            max_cap = _as_int(e.get("storage.user_container_max_capacity_bytes")) or _as_int(
                e.get("storage.capacity_bytes")
            ) or _as_int(e.get("max_capacity"))
            out.append(
                StorageContainer(
                    uuid=e.get("entity_id", ""),
                    name=e.get("container_name"),
                    cluster_name=e.get("cluster_name"),
                    cluster_uuid=e.get("cluster"),
                    replication_factor=_as_int(e.get("replication_factor")),
                    compression_enabled=_truthy(e.get("compression_enabled")),
                    compression_delay_secs=_as_int(e.get("compression_delay_in_secs")),
                    deduplication=e.get("on_disk_dedup"),
                    erasure_coding=e.get("erasure_code"),
                    max_capacity_bytes=max_cap,
                    used_bytes=_as_int(e.get("storage.user_usage_bytes")),
                    free_bytes=_as_int(e.get("storage.user_free_bytes")),
                    api_source="v3-groups",
                )
            )
        return out

    def _via_list(self) -> list[StorageContainer]:
        out: list[StorageContainer] = []
        try:
            entities = self.conn.v3_list("storage_containers")
        except NutanixApiError:
            return out
        for entity in entities:
            status = entity.get("status", {}) or {}
            resources = status.get("resources", {}) or {}
            cluster_ref = status.get("cluster_reference", {}) or {}
            adv = resources.get("advertised_capacity", {}) or {}
            usage = resources.get("usage_stats", {}) or {}
            compression = resources.get("compression", {}) or {}
            out.append(
                StorageContainer(
                    uuid=entity.get("metadata", {}).get("uuid", ""),
                    name=status.get("name"),
                    cluster_uuid=self._uuid_of(cluster_ref),
                    cluster_name=self._name_of(cluster_ref),
                    replication_factor=resources.get("replication_factor"),
                    compression_enabled=compression.get("enabled"),
                    compression_delay_secs=compression.get("delay_in_secs"),
                    deduplication=_dedup(resources),
                    erasure_coding=resources.get("erasure_code"),
                    max_capacity_bytes=_as_int(usage.get("storage.capacity_bytes"))
                    or _as_int(adv.get("max_capacity_bytes")),
                    advertised_capacity_bytes=_as_int(adv.get("max_capacity_bytes")),
                    used_bytes=_as_int(usage.get("storage.user_usage_bytes")),
                    free_bytes=_as_int(usage.get("storage.user_free_bytes")),
                    api_source="v3",
                )
            )
        return out


def _dedup(resources: dict[str, Any]) -> str | None:
    if resources.get("on_disk_dedup"):
        return resources["on_disk_dedup"]
    if resources.get("finger_print_on_write"):
        return "INLINE" if resources["finger_print_on_write"] == "ON" else None
    return None


def _truthy(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "enabled", "on", "yes")


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
