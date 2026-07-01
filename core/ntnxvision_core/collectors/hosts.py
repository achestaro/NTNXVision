"""Host / node collector (v3 `hosts/list`)."""

from __future__ import annotations

from typing import Any

from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.models import Host


class HostCollector(Collector):
    name = "hosts"
    label = "Hosts / Nodes"

    def collect(self, env: Any) -> None:
        for entity in self.conn.v3_list("hosts"):
            status = entity.get("status", {})
            resources = status.get("resources", {})
            if not resources:
                # Prism Central host entries have no resources; skip.
                continue

            cluster_ref = status.get("cluster_reference", {})
            block = resources.get("block", {})
            gpus = [
                g.get("name") or g.get("mode")
                for g in resources.get("gpu_list", []) or []
                if isinstance(g, dict)
            ]

            env.hosts.append(
                Host(
                    uuid=entity.get("metadata", {}).get("uuid", ""),
                    name=status.get("name"),
                    cluster_uuid=self._uuid_of(cluster_ref),
                    cluster_name=self._name_of(cluster_ref),
                    hypervisor_type=_nested(resources, "hypervisor", "hypervisor_type"),
                    hypervisor_version=_nested(resources, "hypervisor", "hypervisor_full_name"),
                    cpu_model=resources.get("cpu_model"),
                    num_cpu_sockets=resources.get("num_cpu_sockets"),
                    num_cpu_cores=resources.get("num_cpu_cores"),
                    num_cpu_threads=resources.get("num_cpu_threads"),
                    cpu_capacity_hz=resources.get("cpu_capacity_hz"),
                    memory_bytes=resources.get("memory_capacity_mib", 0) * 1024 * 1024
                    if resources.get("memory_capacity_mib")
                    else None,
                    block_serial=block.get("block_serial_number"),
                    block_model=block.get("block_model"),
                    serial_number=resources.get("serial_number"),
                    ipmi_ip=_nested(resources, "ipmi", "ip"),
                    cvm_ip=_nested(resources, "controller_vm", "ip"),
                    hypervisor_ip=_nested(resources, "hypervisor", "ip"),
                    maintenance_state=resources.get("host_nics_id_list") and None,
                    num_vms=resources.get("num_vms"),
                    gpu_list=[g for g in gpus if g],
                    api_source="v3",
                )
            )
        env.collection_notes[self.name] = "ok"


def _nested(d: dict[str, Any], *keys: str) -> Any:
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur
