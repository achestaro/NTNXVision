"""Nutanix Unified Storage collectors — Files, Objects, and Volumes.

Files (file servers + shares) and Objects (object stores + buckets) are surfaced through
Prism Central v3 group/list endpoints. Volume Groups come from v3 `volume_groups`.
Each collector degrades gracefully when the corresponding NUS service is not deployed.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.models import (
    FileServer,
    FileShare,
    ObjectStore,
    VolumeGroup,
)


class FilesCollector(Collector):
    name = "files"
    label = "Nutanix Files"

    def collect(self, env: Any) -> None:
        try:
            servers = self.conn.v3_list("file_server")
        except Exception:  # noqa: BLE001 — Files not deployed
            env.collection_notes[self.name] = "skipped: Nutanix Files not deployed"
            return

        for entity in servers:
            spec = entity.get("spec", {})
            resources = spec.get("resources", {})
            status = entity.get("status", {}).get("resources", {})
            env.file_servers.append(
                FileServer(
                    uuid=entity.get("metadata", {}).get("uuid", ""),
                    name=spec.get("name"),
                    cluster_name=entity.get("status", {}).get("cluster_reference", {}).get("name"),
                    version=status.get("version") or resources.get("version"),
                    num_fsvm=resources.get("nvms_count") or resources.get("num_fsvms"),
                    internal_network=_net_name(resources.get("internal_networks", [])),
                    external_network=_net_name(resources.get("external_networks", [])),
                    size_bytes=_as_int(resources.get("size_gib"), scale=1024**3),
                    used_bytes=_as_int(status.get("usage_bytes")),
                    api_source="v3",
                )
            )

        self._shares(env)
        for fs in env.file_servers:
            fs.share_count = sum(1 for s in env.file_shares if s.file_server_name == fs.name)
        env.collection_notes[self.name] = "ok"

    def _shares(self, env: Any) -> None:
        for kind in ("mount_target", "shares"):
            try:
                entities = self.conn.v3_list(kind)
            except Exception:  # noqa: BLE001
                continue
            for entity in entities:
                spec = entity.get("spec", {})
                resources = spec.get("resources", {})
                env.file_shares.append(
                    FileShare(
                        uuid=entity.get("metadata", {}).get("uuid", ""),
                        name=spec.get("name"),
                        file_server_name=resources.get("file_server_reference", {}).get("name"),
                        share_type=resources.get("share_type") or resources.get("protocol_type"),
                        max_size_bytes=_as_int(resources.get("max_size_gib"), scale=1024**3),
                        protocol=resources.get("protocol_type"),
                        api_source="v3",
                    )
                )
            if env.file_shares:
                break


class ObjectsCollector(Collector):
    name = "objects"
    label = "Nutanix Objects"

    def collect(self, env: Any) -> None:
        try:
            stores = self.conn.v3_list("objectstores")
        except Exception:  # noqa: BLE001 — Objects not deployed
            env.collection_notes[self.name] = "skipped: Nutanix Objects not deployed"
            return

        for entity in stores:
            spec = entity.get("spec", {})
            resources = spec.get("resources", {})
            status = entity.get("status", {}).get("resources", {})
            env.object_stores.append(
                ObjectStore(
                    uuid=entity.get("metadata", {}).get("uuid", ""),
                    name=spec.get("name"),
                    cluster_name=resources.get("cluster_reference", {}).get("name"),
                    deployment_version=status.get("deployment_version"),
                    num_worker_nodes=resources.get("num_worker_nodes"),
                    total_capacity_bytes=_as_int(
                        resources.get("total_capacity_gib"), scale=1024**3
                    ),
                    used_bytes=_as_int(status.get("usage_bytes")),
                    state=entity.get("status", {}).get("state"),
                    api_source="v3",
                )
            )

        for store in env.object_stores:
            store.bucket_count = sum(1 for b in env.buckets if b.object_store_name == store.name)
        env.collection_notes[self.name] = "ok"


class VolumesCollector(Collector):
    name = "volume_groups"
    label = "Volume Groups"

    def collect(self, env: Any) -> None:
        try:
            entities = self.conn.v3_list("volume_groups")
        except Exception:  # noqa: BLE001
            env.collection_notes[self.name] = "skipped: volume groups API unavailable"
            return

        for entity in entities:
            spec = entity.get("spec", {})
            resources = spec.get("resources", {})
            disks = resources.get("disk_list", []) or []
            attachments = resources.get("attachment_list", []) or []
            total = sum(d.get("disk_size_mib", 0) for d in disks) * 1024 * 1024
            env.volume_groups.append(
                VolumeGroup(
                    uuid=entity.get("metadata", {}).get("uuid", ""),
                    name=spec.get("name"),
                    cluster_name=entity.get("status", {}).get("cluster_reference", {}).get("name"),
                    disk_count=len(disks),
                    total_capacity_bytes=total or None,
                    is_shared=resources.get("sharing_status") == "SHARED",
                    iscsi_target=resources.get("iscsi_target_prefix"),
                    attachment_count=len(attachments),
                    flash_mode=resources.get("flash_mode"),
                    api_source="v3",
                )
            )
        env.collection_notes[self.name] = "ok"


# ─── helpers ───────────────────────────────────────────────────────────


def _net_name(networks: list[dict[str, Any]]) -> str | None:
    for n in networks or []:
        ref = n.get("subnet_reference", {})
        if ref.get("name"):
            return ref["name"]
    return None


def _as_int(value: Any, scale: int = 1) -> int | None:
    try:
        return int(value) * scale if value is not None else None
    except (TypeError, ValueError):
        return None
