"""Cluster collector (v4 `clustermgmt` when served, else v3 `clusters/list`).

Maps registered clusters into `Cluster` models. Prism Central itself appears as a
"cluster" in both APIs — typed PRISM_CENTRAL in v3's service_list and in v4's
clusterFunction — and is filtered out of the physical cluster list either way.

**One field does not survive the move.** v4 exposes no cluster encryption status on
pc.7.5.1.1: it is absent from `clustermgmt/v4.0/config/clusters`, and the per-cluster
detail endpoint returns exactly the same keys as the list. v3's `encryption_status` is
still the only source, and dropping it would silently blank the Encryption column and
the security posture section of the ADD. So the v4 path fills that one field from v3
while v3 exists, and leaves it None once it does not. Everything else comes from v4,
which is also strictly richer here — v3 leaves `is_lts` null, v4 reports it.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.collectors.normalize import hypervisor_name
from ntnxvision_core.models import Cluster


class ClusterCollector(Collector):
    name = "clusters"
    label = "Clusters"

    def collect(self, env: Any) -> None:
        if self.conn.v4_ready("clusters"):
            self._collect_v4(env)
        else:
            self._collect_v3(env)
        env.collection_notes[self.name] = "ok"

    # ─── v4 ────────────────────────────────────────────────────────

    def _collect_v4(self, env: Any) -> None:
        encryption = self._v3_encryption_by_uuid()
        for entity in self.conn.v4_list("clustermgmt/v4.0/config/clusters"):
            config = entity.get("config") or {}
            functions = config.get("clusterFunction") or []
            uuid = entity.get("extId") or ""

            # The Prism Central pseudo-cluster, as v3's service_list does.
            if "PRISM_CENTRAL" in functions:
                if not env.prism_central.uuid:
                    env.prism_central.uuid = uuid
                    env.prism_central.version = _v4_software(config, "PRISM_CENTRAL")
                continue

            network = entity.get("network") or {}
            build = config.get("buildInfo") or {}
            env.clusters.append(
                Cluster(
                    uuid=uuid,
                    name=entity.get("name"),
                    # buildInfo.version is the clean "7.5"; the NOS entry in
                    # clusterSoftwareMap is the full el8.5-release-ganges-… build string.
                    aos_version=build.get("version") or _v4_software(config, "NOS"),
                    ncc_version=_v4_software(config, "NCC"),
                    # v4 spells it "ESX" where v3 says "ESXi" — normalize, or the same
                    # cluster reads differently depending on which path served it.
                    hypervisor_types=[
                        n
                        for h in (config.get("hypervisorTypes") or [])
                        if (n := hypervisor_name(h))
                    ],
                    cluster_arch=config.get("clusterArch"),
                    redundancy_factor=config.get("redundancyFactor"),
                    num_nodes=(entity.get("nodes") or {}).get("numberOfNodes"),
                    external_ip=_v4_ipv4(network.get("externalAddress")),
                    data_services_ip=_v4_ipv4(network.get("externalDataServiceIp")),
                    timezone=config.get("timezone"),
                    encryption_enabled=encryption.get(uuid),
                    is_lts=config.get("isLts"),
                    operation_mode=config.get("operationMode"),
                    api_source="v4",
                )
            )

    def _v3_encryption_by_uuid(self) -> dict[str, bool | None]:
        """uuid → encryption state, read from v3 because v4 does not expose it.

        Best-effort by design: once the legacy APIs are retired this returns empty and
        the column goes blank rather than the run failing.
        """
        try:
            entities = self.conn.v3_list("clusters")
        except NutanixApiError:
            return {}
        out: dict[str, bool | None] = {}
        for entity in entities:
            uuid = entity.get("metadata", {}).get("uuid")
            config = entity.get("status", {}).get("resources", {}).get("config", {})
            if uuid:
                out[uuid] = _encryption_enabled(config.get("encryption_status"))
        return out

    # ─── v3 ────────────────────────────────────────────────────────

    def _collect_v3(self, env: Any) -> None:
        for entity in self.conn.v3_list("clusters"):
            status = entity.get("status", {})
            resources = status.get("resources", {})
            config = resources.get("config", {})
            network = resources.get("network", {})

            service_list = config.get("service_list", []) or []
            # Skip the Prism Central "cluster" pseudo-entity.
            if "PRISM_CENTRAL" in service_list:
                if not env.prism_central.uuid:
                    env.prism_central.uuid = entity.get("metadata", {}).get("uuid")
                    env.prism_central.version = _software_version(config)
                continue

            hypervisors = list(
                {
                    h.get("hypervisor_type")
                    for h in config.get("node_list", [])
                    if h.get("hypervisor_type")
                }
            ) or config.get("supported_hypervisors", []) or []

            env.clusters.append(
                Cluster(
                    uuid=entity.get("metadata", {}).get("uuid", ""),
                    name=status.get("name"),
                    aos_version=_software_version(config),
                    ncc_version=_ncc_version(config),
                    hypervisor_types=[h for h in hypervisors if h],
                    cluster_arch=config.get("cluster_arch"),
                    redundancy_factor=config.get("redundancy_factor"),
                    num_nodes=len(config.get("node_list", []) or []) or None,
                    external_ip=network.get("external_ip"),
                    data_services_ip=network.get("external_data_services_ip") or None,
                    timezone=config.get("timezone"),
                    encryption_enabled=_encryption_enabled(config.get("encryption_status")),
                    operation_mode=config.get("operation_mode"),
                    api_source="v3",
                )
            )


def _v4_software(config: dict[str, Any], software_type: str) -> str | None:
    """Version for one entry of v4's clusterSoftwareMap (NOS / NCC / PRISM_CENTRAL)."""
    for entry in config.get("clusterSoftwareMap") or []:
        if entry.get("softwareType") == software_type:
            version = entry.get("version")
            return version if isinstance(version, str) else None
    return None


def _v4_ipv4(address: Any) -> str | None:
    """Pull the plain address out of v4's nested common.v1.config.IPAddress shape."""
    if not isinstance(address, dict):
        return None
    ipv4 = address.get("ipv4")
    if not isinstance(ipv4, dict):
        return None
    value = ipv4.get("value")
    return value if isinstance(value, str) else None


def _encryption_enabled(status: Any) -> bool | None:
    """True only for explicitly enabled states.

    v3 reports values like ENABLED / PARTIALLY_ENABLED / DISABLED / NOT_SUPPORTED —
    treating anything != DISABLED as encrypted produced false positives.
    """
    if not isinstance(status, str) or not status:
        return None
    return status.upper() in ("ENABLED", "PARTIALLY_ENABLED", "PARTIAL")


def _software_version(config: dict[str, Any]) -> str | None:
    versions = config.get("software_map", {}) or {}
    nos = versions.get("NOS", {})
    return nos.get("version") if isinstance(nos, dict) else None


def _ncc_version(config: dict[str, Any]) -> str | None:
    versions = config.get("software_map", {}) or {}
    ncc = versions.get("NCC", {})
    return ncc.get("version") if isinstance(ncc, dict) else None
