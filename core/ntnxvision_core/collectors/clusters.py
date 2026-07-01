"""Cluster collector (v3 `clusters/list`).

Maps registered clusters into `Cluster` models. Prism Central itself appears as a
"cluster" of type PRISM_CENTRAL in v3; we filter it out of the physical cluster list.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.models import Cluster


class ClusterCollector(Collector):
    name = "clusters"
    label = "Clusters"

    def collect(self, env: Any) -> None:
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
                    timezone=config.get("timezone"),
                    encryption_enabled=config.get("encryption_status") not in (None, "DISABLED"),
                    operation_mode=config.get("operation_mode"),
                    api_source="v3",
                )
            )
        env.collection_notes[self.name] = "ok"


def _software_version(config: dict[str, Any]) -> str | None:
    versions = config.get("software_map", {}) or {}
    nos = versions.get("NOS", {})
    return nos.get("version") if isinstance(nos, dict) else None


def _ncc_version(config: dict[str, Any]) -> str | None:
    versions = config.get("software_map", {}) or {}
    ncc = versions.get("NCC", {})
    return ncc.get("version") if isinstance(ncc, dict) else None
