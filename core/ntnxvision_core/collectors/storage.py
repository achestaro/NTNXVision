"""Storage container collector (v3 `storage_containers/list`)."""

from __future__ import annotations

from typing import Any

from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.models import StorageContainer


class StorageCollector(Collector):
    name = "storage_containers"
    label = "Storage Containers"

    def collect(self, env: Any) -> None:
        for entity in self.conn.v3_list("storage_containers"):
            status = entity.get("status", {})
            resources = status.get("resources", {})
            cluster_ref = status.get("cluster_reference", {})

            adv = resources.get("advertised_capacity", {}) or {}
            usage = resources.get("usage_stats", {}) or {}
            compression = resources.get("compression", {}) or {}
            erasure = resources.get("erasure_code")

            env.storage_containers.append(
                StorageContainer(
                    uuid=entity.get("metadata", {}).get("uuid", ""),
                    name=status.get("name"),
                    cluster_uuid=self._uuid_of(cluster_ref),
                    cluster_name=self._name_of(cluster_ref),
                    replication_factor=resources.get("replication_factor"),
                    compression_enabled=compression.get("enabled"),
                    compression_delay_secs=compression.get("delay_in_secs"),
                    deduplication=_dedup(resources),
                    erasure_coding=erasure,
                    max_capacity_bytes=_as_int(adv.get("max_capacity_bytes")),
                    advertised_capacity_bytes=_as_int(adv.get("max_capacity_bytes")),
                    used_bytes=_as_int(usage.get("storage.user_usage_bytes")),
                    free_bytes=_as_int(usage.get("storage.user_free_bytes")),
                    api_source="v3",
                )
            )
        env.collection_notes[self.name] = "ok"


def _dedup(resources: dict[str, Any]) -> str | None:
    if resources.get("on_disk_dedup"):
        return resources["on_disk_dedup"]
    if resources.get("finger_print_on_write"):
        return "INLINE" if resources["finger_print_on_write"] == "ON" else None
    return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
