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
                protection_type=_protection_type(resources),
                gpus=[
                    g.get("vendor", "") + " " + g.get("mode", "")
                    for g in resources.get("gpu_list", []) or []
                    if isinstance(g, dict)
                ],
                boot_type=_nested(resources, "boot_config", "boot_type"),
                creation_time=metadata.get("creation_time"),
                vm_type=_classify_vm(status.get("name", ""), resources, metadata),
                api_source="v3",
            )
            vm.disks = _disks(resources)
            vm.nics = _nics(resources)
            # Route Nutanix system VMs (CVMs, Prism Central) to a separate list so the
            # user-VM inventory reflects actual workloads.
            if vm.vm_type == "USER":
                env.vms.append(vm)
            else:
                env.system_vms.append(vm)
        self._collect_cvms(env)
        env.collection_notes[self.name] = "ok"

    def _collect_cvms(self, env: Any) -> None:
        """Collect Controller VMs via the groups API.

        v3 `vms/list` never returns CVMs (they aren't v3 VM intents); the groups API —
        the same one the Prism UI uses — does, when filtered on `is_cvm`. Best-effort:
        a PC version whose groups schema differs just leaves the CVM list empty.
        """
        seen = {v.uuid for v in env.system_vms} | {v.uuid for v in env.vms}
        try:
            rows = self.conn.v3_groups(
                "vm",
                ["vm_name", "power_state", "cluster_name", "host_name",
                 "memory_size_bytes", "num_vcpus", "ip_addresses", "hypervisor_type"],
                filter_criteria="is_cvm==1",
            )
        except Exception:  # noqa: BLE001 — groups filter unsupported on this PC
            return
        for row in rows:
            uuid = row.get("entity_id") or ""
            name = row.get("vm_name") or ""
            if not uuid or uuid in seen or not name.upper().startswith("NTNX-"):
                continue
            ips = row.get("ip_addresses")
            env.system_vms.append(
                VirtualMachine(
                    uuid=uuid,
                    name=name,
                    cluster_name=row.get("cluster_name"),
                    power_state=row.get("power_state"),
                    num_vcpus=_int_or_none(row.get("num_vcpus")),
                    memory_bytes=_int_or_none(row.get("memory_size_bytes")),
                    host_name=row.get("host_name"),
                    hypervisor=row.get("hypervisor_type"),
                    nics=[Nic(ip_addresses=[ip for ip in _split_ips(ips) if ip])]
                    if ips else [],
                    vm_type="CVM",
                    api_source="v3-groups",
                )
            )


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
                # VLAN lives on the referenced subnet, not the NIC; backfilled from
                # the collected subnets in the engine's cross-reference pass.
                vlan_id=None,
                nic_type=nic.get("nic_type"),
            )
        )
    return out


def _classify_vm(name: str, resources: dict[str, Any], metadata: dict[str, Any]) -> str:
    """Classify a VM as USER / CVM / PRISM_CENTRAL / SYSTEM.

    Nutanix Controller VMs and Prism Central VMs are infrastructure, not workloads, and
    belong in a separate inventory section from user VMs.
    """
    n = (name or "").upper()
    # CVMs are named NTNX-<serial>-A / NTNX-*-CVM and may carry an is_cvm flag.
    if resources.get("is_cvm") or (n.startswith("NTNX-") and n.endswith(("-CVM", "-A", "-B"))):
        return "CVM"
    if "NTNX-" in n and "CVM" in n:
        return "CVM"
    # Prism Central VMs are named PC-* or carry a PC category.
    cats = metadata.get("categories_mapping") or metadata.get("categories") or {}
    cat_values = {str(v).upper() for vals in cats.values() for v in
                  (vals if isinstance(vals, list) else [vals])}
    if n.startswith("PC-") or "PRISM_CENTRAL" in cat_values or n.startswith("NUTANIX-PC"):
        return "PRISM_CENTRAL"
    return "USER"


def _guest_os(resources: dict[str, Any]) -> str | None:
    guest = resources.get("guest_tools", {}).get("nutanix_guest_tools", {})
    return guest.get("guest_os_version") or resources.get("guest_os_id")


def _protection_type(resources: dict[str, Any]) -> str | None:
    """VM protection status (v3): UNPROTECTED / RULE_PROTECTED / PD_PROTECTED / …"""
    return resources.get("protection_type") or resources.get("protection_policy_state")


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _split_ips(value: Any) -> list[str]:
    """Groups API returns ip_addresses as a single value or comma-separated string."""
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        return [p.strip() for p in value.split(",") if p.strip()]
    return []


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
