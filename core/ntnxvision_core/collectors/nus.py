"""Nutanix Unified Storage collectors — Files, Objects, and Volumes.

Files (file servers + shares) and Objects (object stores + buckets) are surfaced through
Prism Central v3 group/list endpoints. Volume Groups come from v3 `volume_groups`.
Each collector degrades gracefully when the corresponding NUS service is not deployed.

Files and Objects also record whether the service is deployed at all
(`PlatformService` in ``pc_config.services``), which the conceptual diagram needs: v3 lists
404 when the product is absent, and the v4 namespaces tell the remaining cases apart. A
v4 ``files`` list that answers with no file server means Files is installed but not
deployed; ``objects`` answering 503 (``endpoint ...:7301 failed`` on the lab PC) means
Objects is not enabled. When v3 has no file server list but v4 lists file servers, those
are recorded from v4 (name only: the v4 model carries nothing else).
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.api import v4_client
from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.models import (
    FileServer,
    FileShare,
    ObjectStore,
    PlatformService,
    VolumeGroup,
)

FILE_SERVERS_V4 = "files/v4.0/config/file-servers"


def record_service(env: Any, name: str, status: str, count: int,
                   api_source: str | None) -> None:
    """Set the deployment status of one data service on ``pc_config.services``."""
    services = [s for s in env.pc_config.services if s.name != name]
    services.append(PlatformService(name=name, status=status, count=count,
                                    api_source=api_source))
    env.pc_config.services = sorted(services, key=lambda s: s.name)


def _service_status(conn: Any, area: str, count: int, api: str
                    ) -> tuple[str, str | None, str]:
    """(status, reason when unknown, API that decided it) for a data service.

    Only a 503 from the v4 probe means "not enabled" (the service is not running); any
    other probe failure leaves the status UNKNOWN rather than claiming it is absent.
    """
    if count:
        return "DEPLOYED", None, api
    verdict = getattr(conn, "negotiated", {}).get(area)
    if verdict == v4_client.UNAVAILABLE:
        code = getattr(conn, "__dict__", {}).get("probe_status", {}).get(area)
        if code == 503:
            return "NOT_ENABLED", None, "v4"
        return "UNKNOWN", f"HTTP {code}" if code else "request failed", "v4"
    return "NOT_DEPLOYED", None, "v4" if verdict == v4_client.AVAILABLE else "v3"


def _skip_note(product: str, status: str, reason: str | None) -> str:
    if status == "UNKNOWN":
        return f"skipped: {product} status unknown ({reason})"
    if status == "NOT_ENABLED":
        return f"skipped: {product} not enabled"
    return f"skipped: {product} not deployed"


class FilesCollector(Collector):
    name = "files"
    label = "Nutanix Files"

    def collect(self, env: Any) -> None:
        v4 = self.conn.v4_ready("files")
        try:
            servers = self.conn.v3_list("file_server")
        except Exception:  # noqa: BLE001 — Files not deployed (or v3 has no list)
            servers = None
        if servers is None:
            found = self._v4_servers(env) if v4 else 0
            status, reason, api = _service_status(self.conn, "files", found, "v4")
            record_service(env, "FILES", status, found, api)
            env.collection_notes[self.name] = (
                "ok" if found else _skip_note("Nutanix Files", status, reason))
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
        status, _reason, api = _service_status(self.conn, "files", len(servers), "v3")
        record_service(env, "FILES", status, len(servers), api)
        env.collection_notes[self.name] = "ok"

    def _v4_servers(self, env: Any) -> int:
        try:
            rows = self.conn.v4_list(FILE_SERVERS_V4)
        except NutanixApiError:
            return 0
        for row in rows:
            env.file_servers.append(FileServer(uuid=str(row.get("extId") or ""),
                                               name=row.get("name"),
                                               version=row.get("version") or None,
                                               api_source="v4"))
        return len(rows)

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
        # Probed for the status only: 503 is "Objects not enabled" (see the module doc).
        self.conn.v4_ready("objects")
        try:
            stores = self.conn.v3_list("objectstores")
        except Exception:  # noqa: BLE001 — Objects not deployed
            status, reason, api = _service_status(self.conn, "objects", 0, "v3")
            record_service(env, "OBJECTS", status, 0, api)
            env.collection_notes[self.name] = (
                "skipped: Nutanix Objects not deployed" if status == "NOT_DEPLOYED"
                else _skip_note("Nutanix Objects", status, reason))
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
        status, _reason, api = _service_status(self.conn, "objects", len(stores), "v3")
        record_service(env, "OBJECTS", status, len(stores), api)
        env.collection_notes[self.name] = "ok"


class VolumesCollector(Collector):
    name = "volume_groups"
    label = "Volume Groups"

    def collect(self, env: Any) -> None:
        if self.conn.v4_ready("volumes"):
            self._collect_v4(env)
            env.collection_notes[self.name] = "ok"
            return
        self._collect_v3(env)

    # ─── v4 ────────────────────────────────────────────────────────

    def _collect_v4(self, env: Any) -> None:
        """Volume groups from `volumes/v4.0/config/volume-groups`.

        Disk and attachment counts are the awkward part. v4 exposes them only as
        per-group sub-resources (`/disks`, `/vm-attachments`) — two extra requests per
        volume group, 70 of them on the lab PC, where v3 returns the same aggregates
        for every group in a single call. So v3 is preferred while it exists and the
        sub-resource walk is the fallback for after the sunset; the numbers are
        identical either way, only the cost differs.

        v4 also reports the cluster by uuid where v3 embedded its name. Clusters are
        collected in phase 1, so `env.clusters` is already populated here.
        """
        cluster_names = {c.uuid: c.name for c in env.clusters if c.uuid and c.name}
        aggregates = self._v3_aggregates()

        for entity in self.conn.v4_list("volumes/v4.0/config/volume-groups"):
            uuid = entity.get("extId") or ""
            flash = (entity.get("storageFeatures") or {}).get("flashMode") or {}
            if uuid in aggregates:
                disk_count, total, attachments = aggregates[uuid]
            else:
                disk_count, total, attachments = self._v4_aggregates(uuid)
            env.volume_groups.append(
                VolumeGroup(
                    uuid=uuid,
                    name=entity.get("name"),
                    cluster_name=cluster_names.get(entity.get("clusterReference") or ""),
                    cluster_uuid=entity.get("clusterReference") or None,
                    disk_count=disk_count,
                    total_capacity_bytes=total,
                    is_shared=entity.get("sharingStatus") == "SHARED",
                    iscsi_target=entity.get("targetName"),
                    attachment_count=attachments,
                    flash_mode=flash.get("isEnabled"),
                    api_source="v4",
                )
            )

    def _v3_aggregates(self) -> dict[str, tuple[int, int | None, int]]:
        """uuid → (disk_count, total_bytes, attachment_count) from one v3 call."""
        try:
            entities = self.conn.v3_list("volume_groups")
        except Exception:  # noqa: BLE001 — absent after the legacy sunset
            return {}
        out: dict[str, tuple[int, int | None, int]] = {}
        for entity in entities:
            uuid = entity.get("metadata", {}).get("uuid")
            if not uuid:
                continue
            resources = entity.get("spec", {}).get("resources", {})
            disks = resources.get("disk_list", []) or []
            total = sum(d.get("disk_size_mib", 0) for d in disks) * 1024 * 1024
            out[uuid] = (
                len(disks),
                total or None,
                len(resources.get("attachment_list", []) or []),
            )
        return out

    def _v4_aggregates(self, uuid: str) -> tuple[int, int | None, int]:
        """The same three numbers via v4 sub-resources: two requests per group."""
        base = f"volumes/v4.0/config/volume-groups/{uuid}"
        try:
            disks = self.conn.v4_list(f"{base}/disks")
        except Exception:  # noqa: BLE001
            disks = []
        try:
            attachments = self.conn.v4_list(f"{base}/vm-attachments")
        except Exception:  # noqa: BLE001
            attachments = []
        total = sum(d.get("diskSizeBytes") or 0 for d in disks)
        return len(disks), total or None, len(attachments)

    # ─── v3 ────────────────────────────────────────────────────────

    def _collect_v3(self, env: Any) -> None:
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
                    cluster_uuid=entity.get("status", {}).get("cluster_reference", {}).get("uuid"),
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
