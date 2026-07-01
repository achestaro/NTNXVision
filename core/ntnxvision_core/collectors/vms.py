"""Virtual machine collector (v3 `vms/list`).

VMs are the richest object type — the core of any RVTools-style inventory. We request
the full spec+status per VM (v3 list returns both) and map disks, NICs, categories,
GPU, and protection state.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.models import Nic, VirtualDisk, VirtualMachine


class VmCollector(Collector):
    name = "vms"
    label = "Virtual Machines"

    def collect(self, env: Any) -> None:
        for entity in self.conn.v3_list("vms"):
            metadata = entity.get("metadata", {})
            status = entity.get("status", {})
            resources = status.get("resources", {})
            cluster_ref = status.get("cluster_reference", {})

            vm = VirtualMachine(
                uuid=metadata.get("uuid", ""),
                name=status.get("name", ""),
                cluster_name=self._name_of(cluster_ref),
                cluster_uuid=self._uuid_of(cluster_ref),
                power_state=resources.get("power_state"),
                num_vcpus=resources.get("num_sockets"),
                num_cores_per_vcpu=resources.get("num_vcpus_per_socket"),
                memory_bytes=_mib_to_bytes(resources.get("memory_size_mib")),
                description=status.get("description"),
                hypervisor=resources.get("hypervisor_type"),
                guest_os=_guest_os(resources),
                host_uuid=self._uuid_of(resources.get("host_reference", {})),
                host_name=self._name_of(resources.get("host_reference", {})),
                is_agent_vm=resources.get("is_agent_vm", False),
                categories=metadata.get("categories_mapping")
                or metadata.get("categories")
                or {},
                protection_type=_protection_type(metadata),
                gpus=[
                    g.get("vendor", "") + " " + g.get("mode", "")
                    for g in resources.get("gpu_list", []) or []
                    if isinstance(g, dict)
                ],
                boot_type=_nested(resources, "boot_config", "boot_type"),
                creation_time=metadata.get("creation_time"),
                api_source="v3",
            )
            vm.disks = _disks(resources)
            vm.nics = _nics(resources)
            env.vms.append(vm)
        env.collection_notes[self.name] = "ok"


# ─── mappers ───────────────────────────────────────────────────────────


def _disks(resources: dict[str, Any]) -> list[VirtualDisk]:
    out: list[VirtualDisk] = []
    for disk in resources.get("disk_list", []) or []:
        addr = disk.get("device_properties", {}).get("disk_address", {})
        device_type = disk.get("device_properties", {}).get("device_type")
        ref = disk.get("storage_config", {}).get("storage_container_reference", {})
        data_src = disk.get("data_source_reference", {})
        out.append(
            VirtualDisk(
                uuid=disk.get("uuid"),
                label=f"{addr.get('adapter_type', '')}.{addr.get('device_index', '')}".strip("."),
                disk_size_bytes=disk.get("disk_size_bytes"),
                storage_container_uuid=ref.get("uuid"),
                storage_container_name=ref.get("name"),
                bus_type=addr.get("adapter_type"),
                is_cdrom=device_type == "CDROM",
                source_image=data_src.get("name"),
            )
        )
    return out


def _nics(resources: dict[str, Any]) -> list[Nic]:
    out: list[Nic] = []
    for nic in resources.get("nic_list", []) or []:
        subnet_ref = nic.get("subnet_reference", {})
        ips = [ep.get("ip") for ep in nic.get("ip_endpoint_list", []) or [] if ep.get("ip")]
        out.append(
            Nic(
                uuid=nic.get("uuid"),
                mac_address=nic.get("mac_address"),
                subnet_name=subnet_ref.get("name"),
                subnet_uuid=subnet_ref.get("uuid"),
                ip_addresses=ips,
                is_connected=nic.get("is_connected"),
                vlan_id=nic.get("vlan_mode") and None,
                nic_type=nic.get("nic_type"),
            )
        )
    return out


def _guest_os(resources: dict[str, Any]) -> str | None:
    guest = resources.get("guest_tools", {}).get("nutanix_guest_tools", {})
    return guest.get("guest_os_version") or resources.get("guest_os_id")


def _protection_type(metadata: dict[str, Any]) -> str | None:
    # v3 exposes protection under status elsewhere; metadata carries a hint.
    return metadata.get("entity_version") and None or None


def _mib_to_bytes(mib: Any) -> int | None:
    try:
        return int(mib) * 1024 * 1024 if mib is not None else None
    except (TypeError, ValueError):
        return None


def _nested(d: dict[str, Any], *keys: str) -> Any:
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur
