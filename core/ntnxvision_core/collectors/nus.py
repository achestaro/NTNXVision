"""Nutanix Unified Storage collectors — Files, Objects, and Volumes.

Files (file servers + shares) and Objects (object stores + buckets) are surfaced through
Prism Central v3 group/list endpoints. Volume Groups come from v3 `volume_groups`.
Each collector degrades gracefully when the corresponding NUS service is not deployed.

Files and Objects also record whether the service is deployed at all
(`PlatformService` in ``pc_config.services``), which the conceptual diagram needs: v3 lists
404 when the product is absent, and the v4 namespaces tell the remaining cases apart. A
v4 ``files`` list that answers with no file server means Files is installed but not
deployed; ``objects`` answering 503 (``endpoint ...:7301 failed`` on the lab PC) means
Objects is not enabled. When v3 has no file server list (pc.7.5 answers 404) but v4 lists
file servers, those are recorded from v4: the v4 file server carries only its name, so the
cluster, Files version, FSVM count and usage come from the groups entity
`file_server_service`, and the shares from each server's v4 mount targets.

Checked against Files 5.3.0.3 on pc.7.5.1.6 (2026-11-06). Wherever Files v4 answers (either
path), each server is enriched from:

* ``files/v4.0.a2/config/file-servers``: the only Files namespace that returns the full file
  server (``files/v4.0`` answers extId and name only, even with ``$select=*``): cluster,
  networks (``externalNetworks`` = client, ``internalNetworks`` = storage, with the storage
  VIP), DNS domain / servers, NTP, per-FSVM vCPU / memory, size, and ``vms``: one entry per
  FSVM with its name, VM uuid and client / storage IPs. There is no FSVM sub-resource.
* the FSVMs' own VM records (already collected VMs, else ``GET v3 vms/{uuid}``) for the host
  and power state. Without the a2 namespace the FSVMs come from the groups entity
  ``file_server_service`` (``nvm_uuid_list`` holds their VM uuids) and those VM records.
* ``files/v4.0/stats/file-servers/{id}`` and ``.../mount-targets/{id}`` with ``$select=*``
  (without it only the file count and latency come back): used bytes and file counts, as
  one-point time series (the latest point is kept). Neither the mount target nor any groups
  entity (``file_server_share``) carries usage, so the shares' used space comes only from here.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from ntnxvision_core.api import v4_client
from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.models import (
    Bucket,
    FileServer,
    FileServerVm,
    FileShare,
    ObjectStore,
    PlatformService,
    VolumeGroup,
)

FILE_SERVERS_V4 = "files/v4.0/config/file-servers"
#: The full file server (networks, DNS, FSVMs); `files/v4.0` returns extId and name only.
FILE_SERVERS_A2 = "files/v4.0.a2/config/file-servers"
FILES_STATS_V4 = "files/v4.0/stats/file-servers"
FILE_SERVER_GROUP_ATTRS = ["name", "cluster_name", "afs_version", "nvm_uuid_list",
                           "last_used_size_bytes"]
SHARE_TYPES = {"GENERAL": "Standard", "HOMES": "Distributed"}
OBJECT_STORES_V4 = "objects/v4.0/config/object-stores"
OSS_V3 = "/oss/api/nutanix/v3"
OBJECT_STORE_GROUP_ATTRS = ["name", "cluster_name", "usage_bytes", "num_buckets", "num_objects",
                            "client_access_network_ip_list"]
OBJECT_STORE_OSS_ATTRS = ["state", "deployment_version", "num_msp_workers", "total_capacity_gib",
                          "domain"]
BUCKET_GROUP_ATTRS = ["name", "owner_name", "versioning", "worm", "retention_duration_days",
                      "lifecycle", "cors", "website", "nfs", "bucket_notification_state",
                      "inbound_replication_status", "outbound_replication_status",
                      "object_count", "storage_usage_bytes"]


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
        if v4:
            self._enrich(env, self._server_groups())
        status, _reason, api = _service_status(self.conn, "files", len(servers), "v3")
        record_service(env, "FILES", status, len(servers), api)
        env.collection_notes[self.name] = "ok"

    def _v4_servers(self, env: Any) -> int:
        try:
            rows = self.conn.v4_list(FILE_SERVERS_V4)
        except NutanixApiError:
            return 0
        # The v4 file server (list and detail) carries only extId and name; the groups
        # entity `file_server_service` adds the cluster, Files version, FSVMs and usage.
        extra = self._server_groups()
        for row in rows:
            uuid = str(row.get("extId") or "")
            g = extra.get(uuid, {})
            env.file_servers.append(FileServer(
                uuid=uuid,
                name=row.get("name"),
                cluster_name=g.get("cluster_name"),
                version=row.get("version") or g.get("afs_version"),
                num_fsvm=len(g.get("nvm_uuid_list") or []) or None,
                used_bytes=_as_int(g.get("last_used_size_bytes")),
                api_source="v4",
            ))
            self._v4_shares(env, uuid, row.get("name"))
        for fs in env.file_servers:
            fs.share_count = sum(1 for s in env.file_shares if s.file_server_name == fs.name)
        self._enrich(env, extra)
        return len(rows)

    # ─── enrichment (Files v4) ─────────────────────────────────────

    def _enrich(self, env: Any, groups: dict[str, dict[str, Any]]) -> None:
        """FSVMs, networks, DNS and usage of every collected file server (best effort)."""
        try:
            full = {str(r.get("extId")): r for r in self.conn.v4_list(FILE_SERVERS_A2)}
        except Exception:  # noqa: BLE001 — the a2 namespace is optional enrichment
            full = {}
        vms = {v.uuid: v for v in [*env.vms, *env.system_vms] if v.uuid}
        # Collected VMs get their host name only when the run resolves references (after
        # every collector): name the host from the hosts already collected.
        hosts = {h.uuid: h.name for h in env.hosts if h.uuid and h.name}
        for vm in vms.values():
            if not vm.host_name and vm.host_uuid in hosts:
                vms[vm.uuid] = vm.model_copy(update={"host_name": hosts[vm.host_uuid]})
        for fs in env.file_servers:
            detail = full.get(fs.uuid)
            if detail:
                self._apply_detail(fs, detail)
            fsvms = self._fsvms(fs, detail, groups.get(fs.uuid, {}), vms)
            env.file_server_vms.extend(fsvms)
            if fsvms and not fs.num_fsvm:
                fs.num_fsvm = len(fsvms)
            self._server_stats(fs)
        servers = {fs.name: fs.uuid for fs in env.file_servers}
        for share in env.file_shares:
            server = servers.get(share.file_server_name)
            if server and share.uuid:
                self._share_stats(share, server)

    @staticmethod
    def _apply_detail(fs: FileServer, d: dict[str, Any]) -> None:
        external = d.get("externalNetworks") or []
        internal = d.get("internalNetworks") or []
        fs.cluster_uuid = fs.cluster_uuid or d.get("clusterExtId")
        fs.version = fs.version or d.get("version")
        fs.num_fsvm = fs.num_fsvm or _as_int(d.get("nvmsCount"))
        fs.external_network = fs.external_network or _v4_net_name(external)
        fs.internal_network = fs.internal_network or _v4_net_name(internal)
        fs.storage_vip = next((ip for ip in (_ip(n.get("virtualIpAddress")) for n in internal)
                               if ip), None)
        fs.dns_domain = d.get("dnsDomainName") or None
        fs.dns_servers = [x for x in (_addr(a) for a in d.get("dnsServers") or []) if x]
        fs.ntp_servers = [x for x in (_addr(a) for a in d.get("ntpServers") or []) if x]
        fs.fsvm_vcpus = _as_int(d.get("vcpus"))
        memory = d.get("memoryGib", d.get("memoryGiBytes"))
        fs.fsvm_memory_bytes = _as_int(memory, scale=1024**3)
        size = d.get("sizeInGib", d.get("sizeGiBytes"))
        fs.size_bytes = fs.size_bytes or _as_int(size, scale=1024**3)

    def _fsvms(self, fs: FileServer, detail: dict[str, Any] | None, group: dict[str, Any],
               vms: dict[str, Any]) -> list[FileServerVm]:
        entries = list((detail or {}).get("vms") or [])
        if not entries:
            entries = [{"extId": u} for u in group.get("nvm_uuid_list") or [] if u]
        out = []
        for e in entries:
            uuid = str(e.get("extId") or "")
            if not uuid:
                continue
            vm = vms.get(uuid)
            if vm is None or not vm.host_name:
                vm = self._vm(uuid) or vm
            memory = e.get("memoryGib", e.get("memoryGiBytes"))
            out.append(FileServerVm(
                uuid=uuid,
                name=e.get("name") or getattr(vm, "name", None),
                cluster_name=fs.cluster_name or getattr(vm, "cluster_name", None),
                cluster_uuid=fs.cluster_uuid or getattr(vm, "cluster_uuid", None),
                file_server_name=fs.name,
                file_server_uuid=fs.uuid,
                client_ips=_nic_ips(e.get("externalNetworks")),
                storage_ips=_nic_ips(e.get("internalNetworks")),
                num_vcpus=_as_int(e.get("vcpus")) or getattr(vm, "num_vcpus", None),
                memory_bytes=_as_int(memory, scale=1024**3) or getattr(vm, "memory_bytes", None),
                host_name=getattr(vm, "host_name", None),
                power_state=getattr(vm, "power_state", None),
                version=fs.version,
                api_source="v4" if detail else "v3",
            ))
        return out

    def _vm(self, uuid: str) -> Any:
        """An FSVM's VM record (host, power state, sizing) when the VMs were not collected."""
        try:
            entity = self.conn.get_json(f"{self.conn.creds.base}/api/nutanix/v3/vms/{uuid}")
        except Exception:  # noqa: BLE001 — host / power state stay unknown
            return None
        status = entity.get("status") or {}
        res = status.get("resources") or {}
        sockets, per = res.get("num_sockets"), res.get("num_vcpus_per_socket") or 1
        return SimpleNamespace(
            name=status.get("name"),
            cluster_name=(status.get("cluster_reference") or {}).get("name"),
            cluster_uuid=(status.get("cluster_reference") or {}).get("uuid"),
            num_vcpus=sockets * per if isinstance(sockets, int) else None,
            memory_bytes=_as_int(res.get("memory_size_mib"), scale=1024**2),
            host_name=(res.get("host_reference") or {}).get("name"),
            power_state=res.get("power_state"),
        )

    def _server_stats(self, fs: FileServer) -> None:
        stats = self._stats(f"{FILES_STATS_V4}/{fs.uuid}")
        used = _latest(stats.get("totalSpaceUsedBytes"))
        if used is not None:
            fs.used_bytes = used  # the sum of its shares' usedBytes (groups' figure is not)
        fs.file_count = _latest(stats.get("numberOfFiles"))

    def _share_stats(self, share: FileShare, server_uuid: str) -> None:
        stats = self._stats(f"{FILES_STATS_V4}/{server_uuid}/mount-targets/{share.uuid}")
        used = _latest(stats.get("usedBytes"))
        if used is not None:
            share.used_bytes = used
        share.file_count = _latest(stats.get("numberOfFiles"))

    def _stats(self, path: str) -> dict[str, Any]:
        try:
            data = self.conn.v4_get(path, params={"$select": "*"}).get("data")
        except Exception:  # noqa: BLE001 — usage is enrichment only
            return {}
        return data if isinstance(data, dict) else {}

    def _server_groups(self) -> dict[str, dict[str, Any]]:
        try:
            rows = self.conn.v3_groups("file_server_service", FILE_SERVER_GROUP_ATTRS,
                                       multi_valued=("nvm_uuid_list",))
        except Exception:  # noqa: BLE001 — enrichment only; the v4 rows still stand
            return {}
        return {str(r.get("entity_id")): r for r in rows}

    def _v4_shares(self, env: Any, server_uuid: str, server_name: str | None) -> None:
        """Shares (v4 "mount targets") of one file server."""
        try:
            rows = self.conn.v4_list(f"{FILE_SERVERS_V4}/{server_uuid}/mount-targets")
        except NutanixApiError:
            return
        for row in rows:
            smb = row.get("smbProperties") or {}
            secondary = [p for p in row.get("secondaryProtocol") or [] if p and p != "NONE"]
            env.file_shares.append(FileShare(
                uuid=str(row.get("extId") or ""),
                name=row.get("name"),
                file_server_name=server_name,
                # GENERAL is a standard share, HOMES a distributed (home-directory) one.
                share_type=SHARE_TYPES.get(row.get("type") or "", row.get("type")),
                # 0 GiB means no limit.
                max_size_bytes=_as_int(row.get("maxSizeGB") or None, scale=1024**3),
                protocol="/".join([row.get("protocol") or "", *secondary]).strip("/") or None,
                path=row.get("path"),
                description=row.get("description"),
                compression=row.get("isCompressionEnabled"),
                read_only=row.get("isReadOnly"),
                previous_versions=row.get("isPreviousVersionEnabled"),
                access_based_enumeration=smb.get("isAccessBasedEnumerationEnabled"),
                continuous_availability=smb.get("isCaEnabled"),
                smb3_encryption=smb.get("isSmb3EncryptionEnabled"),
                api_source="v4",
            ))

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
    """Object stores and their buckets.

    Checked against Objects 5.3 on pc.7.5.1.6 (2026-11-06): the store list is v4
    `objects/v4.0/config/object-stores` (no v3 list exists: Objects lives under ``/oss``);
    usage, bucket and object counts come from Objects' groups entity `objectstore` (below v4
    GA, the stores themselves too), and the
    buckets from each store's own groups query (entity `bucket`) behind
    ``objectstore_proxy/<uuid>``. Both groups queries are reads the transport guard admits.
    """

    name = "objects"
    label = "Nutanix Objects"

    def collect(self, env: Any) -> None:
        # Probed for the status too: 503 is "Objects not enabled" (see the module doc).
        v4 = self.conn.v4_ready("objects")
        stores = self._v4_stores(env) if v4 else None
        if stores is None:
            stores = self._oss_stores(env)
        if stores is None:
            stores = self._v3_stores(env)
        if stores is None:
            status, reason, api = _service_status(self.conn, "objects", 0, "v3")
            record_service(env, "OBJECTS", status, 0, api)
            env.collection_notes[self.name] = (
                "skipped: Nutanix Objects not deployed" if status == "NOT_DEPLOYED"
                else _skip_note("Nutanix Objects", status, reason))
            return
        self._usage(env)
        for store in env.object_stores:
            if store.state in ("COMPLETE", "OBJECT_STORE_AVAILABLE"):
                self._buckets(env, store)
        for store in env.object_stores:
            found = [b for b in env.buckets if b.object_store_name == store.name]
            if found or store.bucket_count is None:
                store.bucket_count = len(found)
        status, _reason, api = _service_status(self.conn, "objects", stores, "v4" if v4 else "v3")
        record_service(env, "OBJECTS", status, stores, api)
        env.collection_notes[self.name] = "ok"

    def _v4_stores(self, env: Any) -> int | None:
        try:
            rows = self.conn.v4_list(OBJECT_STORES_V4)
        except NutanixApiError:
            return None
        for row in rows:
            env.object_stores.append(ObjectStore(
                uuid=str(row.get("extId") or ""),
                name=row.get("name"),
                cluster_uuid=row.get("clusterExtId"),
                deployment_version=row.get("deploymentVersion"),
                num_worker_nodes=row.get("numWorkerNodes"),
                total_capacity_bytes=_as_int(row.get("totalCapacityGiB"), scale=1024**3),
                state=row.get("state"),
                domain=row.get("domain"),
                client_ips=[ip for ip in (_ip(x) for x in row.get("publicNetworkIps") or []) if ip],
                storage_vip=_ip(row.get("storageNetworkVip")),
                api_source="v4",
            ))
        return len(rows)

    def _oss_stores(self, env: Any) -> int | None:
        """Below v4 GA: the stores from Objects' own groups query (then usage is known)."""
        try:
            rows = self.conn.v3_groups("objectstore", [*OBJECT_STORE_GROUP_ATTRS,
                                                       *OBJECT_STORE_OSS_ATTRS],
                                       path=f"{OSS_V3}/groups",
                                       multi_valued=("client_access_network_ip_list",))
        except Exception:  # noqa: BLE001 — Objects not deployed / not served
            return None
        if not rows:
            return None
        for r in rows:
            env.object_stores.append(ObjectStore(
                uuid=str(r.get("entity_id") or ""),
                name=r.get("name"),
                cluster_name=r.get("cluster_name"),
                deployment_version=r.get("deployment_version"),
                num_worker_nodes=_as_int(r.get("num_msp_workers")),
                total_capacity_bytes=_as_int(r.get("total_capacity_gib"), scale=1024**3),
                state=r.get("state"),
                domain=r.get("domain"),
                api_source="objects",
            ))
        return len(rows)

    def _v3_stores(self, env: Any) -> int | None:
        """Older Prism Centrals: the v3 objectstores list, where one is served."""
        try:
            stores = self.conn.v3_list("objectstores")
        except Exception:  # noqa: BLE001 — Objects not deployed
            return None
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
        return len(stores)

    def _usage(self, env: Any) -> None:
        """Usage, cluster name and bucket / object counts per store (best effort)."""
        try:
            rows = self.conn.v3_groups("objectstore", OBJECT_STORE_GROUP_ATTRS,
                                       path=f"{OSS_V3}/groups",
                                       multi_valued=("client_access_network_ip_list",))
        except Exception:  # noqa: BLE001 — enrichment only
            return
        by_id = {str(r.get("entity_id")): r for r in rows}
        for store in env.object_stores:
            g = by_id.get(store.uuid)
            if not g:
                continue
            store.cluster_name = store.cluster_name or g.get("cluster_name")
            store.used_bytes = store.used_bytes or _as_int(g.get("usage_bytes"))
            store.bucket_count = _as_int(g.get("num_buckets"))
            store.object_count = _as_int(g.get("num_objects"))
            store.client_ips = store.client_ips or list(g.get("client_access_network_ip_list")
                                                        or [])

    def _buckets(self, env: Any, store: ObjectStore) -> None:
        try:
            rows = self.conn.v3_groups("bucket", BUCKET_GROUP_ATTRS,
                                       path=f"{OSS_V3}/objectstore_proxy/{store.uuid}/groups")
        except Exception:  # noqa: BLE001 — the store's own service may be down
            return
        for r in rows:
            replication = [d for d, key in (("Outbound", "outbound_replication_status"),
                                            ("Inbound", "inbound_replication_status"))
                           if (r.get(key) or "None") != "None"]
            env.buckets.append(Bucket(
                name=r.get("name") or "",
                object_store_name=store.name,
                cluster_name=store.cluster_name,
                owner=r.get("owner_name"),
                versioning=_enabled(r.get("versioning")),
                worm_enabled=_enabled(r.get("worm")),
                retention_days=_as_int(r.get("retention_duration_days")) or None,
                lifecycle=_enabled(r.get("lifecycle")),
                cors=_enabled(r.get("cors")),
                website=_enabled(r.get("website")),
                nfs=_enabled(r.get("nfs")),
                notifications=_enabled(r.get("bucket_notification_state")),
                replication=", ".join(replication) or None,
                object_count=_as_int(r.get("object_count")),
                used_bytes=_as_int(r.get("storage_usage_bytes")),
                api_source="objects",
            ))


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


def _latest(series: Any) -> int | None:
    """The newest value of a v4 stats time series (``[{timestamp, value}, ...]``)."""
    if not isinstance(series, list):
        return None
    points = [p for p in series if isinstance(p, dict) and p.get("value") is not None]
    if not points:
        return None
    return _as_int(max(points, key=lambda p: str(p.get("timestamp") or ""))["value"])


def _addr(value: Any) -> str | None:
    """An IPv4Address, IPAddress or IPAddressOrFQDN as text."""
    if not isinstance(value, dict):
        return None
    if value.get("value"):
        return str(value["value"])
    fqdn = value.get("fqdn") or {}
    return fqdn.get("value") or _ip(value)


def _nic_ips(nics: Any) -> list[str]:
    return [ip for n in nics or [] for ip in (_addr(a) for a in n.get("ipAddresses") or [])
            if ip]


def _v4_net_name(networks: list[dict[str, Any]]) -> str | None:
    return next((n.get("virtualNetworkName") for n in networks if n.get("virtualNetworkName")),
                None)


def _enabled(value: Any) -> bool | None:
    """Objects' groups flags: "true" / "false", "Enabled" / "Disabled" (also "Suspended")."""
    if value is None or value == "":
        return None
    return str(value).lower() in ("true", "enabled")


def _ip(value: Any) -> str | None:
    """A v4 `common.v1.config.IPAddress` as text."""
    if not isinstance(value, dict):
        return None
    v4 = value.get("ipv4") or {}
    v6 = value.get("ipv6") or {}
    return v4.get("value") or v6.get("value") or None


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
