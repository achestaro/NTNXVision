"""Storage container collector (v4 `clustermgmt` when served, else the v3 groups API).

Containers are served by the **clustermgmt** namespace, not the `storage` one the
`ntnx-storage-py-client` package name suggests — `storage/v4.0/config/storage-containers`
404s. Before v4 they had no dedicated `.../list` endpoint at all and had to be read
through the groups API, which is still the fallback.

**Usage figures stay on v3.** The v4 config payload carries `maxCapacityBytes` but not
used/free; those live on a separate per-container stats endpoint that requires a
time-series window (it 400s without one). That would be one request per container for two
numbers, where the groups API returns them for every container in a single call — so the
v4 path takes its configuration from v4 and backfills usage from that one groups request.
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

#: The subset the v4 path still needs from the groups API (usage + user-level ceiling).
_USAGE_ATTRS = [
    "storage.user_usage_bytes",
    "storage.user_free_bytes",
    "storage.user_container_max_capacity_bytes",
    "storage.capacity_bytes",
]


class StorageCollector(Collector):
    name = "storage_containers"
    label = "Storage Containers"

    def collect(self, env: Any) -> None:
        rows: list[StorageContainer]
        if self.conn.v4_ready("storage_containers"):
            rows = self._via_v4()
        else:
            grouped = self._via_groups()
            rows = grouped if grouped is not None else self._via_list()

        for row in rows:
            env.storage_containers.append(row)
        env.collection_notes[self.name] = "ok"

    def _via_v4(self) -> list[StorageContainer]:
        usage = self._usage_by_uuid()
        out: list[StorageContainer] = []
        for e in self.conn.v4_list("clustermgmt/v4.0/config/storage-containers"):
            uuid = e.get("containerExtId") or ""
            used, free, max_cap = usage.get(uuid, (None, None, None))
            out.append(
                StorageContainer(
                    uuid=uuid,
                    name=e.get("name"),
                    cluster_name=e.get("clusterName"),
                    cluster_uuid=e.get("clusterExtId"),
                    replication_factor=e.get("replicationFactor"),
                    compression_enabled=e.get("isCompressionEnabled"),
                    compression_delay_secs=e.get("compressionDelaySecs"),
                    deduplication=e.get("onDiskDedup"),
                    # v4 upper-cases this where v3 does not; keep v3's spelling so the
                    # column does not change appearance depending on which path ran.
                    erasure_coding=_lower(e.get("erasureCode")),
                    # v4's maxCapacityBytes is the *raw* figure — byte-for-byte v3's
                    # `max_capacity`, not the `storage.capacity_bytes` v3 actually
                    # reported. Used and free are user-level, so the ceiling must be
                    # too or the three numbers stop agreeing. v4's raw value is the
                    # last resort only.
                    max_capacity_bytes=max_cap or e.get("maxCapacityBytes"),
                    used_bytes=used,
                    free_bytes=free,
                    api_source="v4",
                )
            )
        return out

    def _usage_by_uuid(self) -> dict[str, tuple[int | None, int | None, int | None]]:
        """uuid → (used, free, max), from the one groups call that returns them in bulk.

        Best-effort: after the legacy sunset these columns fall back to v4's raw
        capacity rather than the run failing.
        """
        try:
            entities = self.conn.v3_groups("storage_container", _USAGE_ATTRS)
        except NutanixApiError:
            return {}
        out: dict[str, tuple[int | None, int | None, int | None]] = {}
        for e in entities:
            uuid = e.get("entity_id")
            if not uuid:
                continue
            out[uuid] = (
                _as_int(e.get("storage.user_usage_bytes")),
                _as_int(e.get("storage.user_free_bytes")),
                # Same preference order the v3 path uses.
                _as_int(e.get("storage.user_container_max_capacity_bytes"))
                or _as_int(e.get("storage.capacity_bytes")),
            )
        return out

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


def _lower(value: Any) -> str | None:
    """Lower-case an enum-ish string, preserving None."""
    return value.lower() if isinstance(value, str) and value else None
