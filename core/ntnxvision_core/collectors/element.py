"""Prism Element (v2.0) collectors.

Direct-cluster inventory for a single cluster, used when connecting straight to a cluster's
Prism Element rather than through Prism Central. The v2.0 API returns flat entity objects,
so these mappers differ from the v3 (PC) collectors but populate the same normalized models.

These functions take an `ElementConnection` and populate the given `Environment`. They are
not subclasses of `Collector` (which is bound to `PrismConnection`); the engine's PE path
calls `collect_element` directly.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.api.element_client import ElementConnection
from ntnxvision_core.collectors.normalize import hypervisor_name
from ntnxvision_core.models import (
    Cluster,
    Host,
    Nic,
    StorageContainer,
    Subnet,
    VirtualDisk,
    VirtualMachine,
)

# (label, status) tuples emitted for progress.
ElementProgress = Any


def collect_element(conn: ElementConnection, env: Any, progress: Any = None) -> None:
    """Populate env from a direct Prism Element connection (v2.0 API)."""
    total = len(ELEMENT_STEPS)
    for idx, (_area, label, fn) in enumerate(ELEMENT_STEPS):
        if progress:
            progress(idx, total, label, "running")
        try:
            fn(conn, env)
            if progress:
                progress(idx, total, label, "ok")
        except Exception as exc:  # noqa: BLE001 — isolate per-step failures
            env.collection_notes[label] = f"error: {exc}"
            if progress:
                progress(idx, total, label, f"error: {exc}")


# ─── cluster ───────────────────────────────────────────────────────────


def _cluster(conn: ElementConnection, env: Any) -> None:
    data = conn.get("cluster")
    hypervisors = data.get("hypervisor_types", []) or []
    env.clusters.append(
        Cluster(
            uuid=data.get("uuid") or data.get("cluster_uuid", ""),
            name=data.get("name"),
            aos_version=data.get("version") or data.get("full_version"),
            ncc_version=data.get("ncc_version"),
            hypervisor_types=[_short_hyp(h) for h in hypervisors],
            redundancy_factor=data.get("cluster_redundancy_state", {}).get(
                "desired_redundancy_factor"
            ),
            num_nodes=data.get("num_nodes"),
            external_ip=data.get("cluster_external_ipaddress"),
            timezone=data.get("timezone"),
            encryption_enabled=data.get("encrypted"),
            operation_mode=data.get("operation_mode"),
            api_source="v2.0",
        )
    )
    env.collection_notes["clusters"] = "ok"


# ─── hosts ─────────────────────────────────────────────────────────────


def _hosts(conn: ElementConnection, env: Any) -> None:
    for host in conn.v2_list("hosts"):
        env.hosts.append(
            Host(
                uuid=host.get("uuid", ""),
                name=host.get("name"),
                cluster_uuid=host.get("cluster_uuid"),
                hypervisor_type=_short_hyp(host.get("hypervisor_type")),
                hypervisor_version=host.get("hypervisor_full_name"),
                cpu_model=host.get("cpu_model"),
                num_cpu_sockets=host.get("num_cpu_sockets"),
                num_cpu_cores=host.get("num_cpu_cores"),
                num_cpu_threads=host.get("num_cpu_threads"),
                cpu_capacity_hz=host.get("cpu_capacity_in_hz"),
                memory_bytes=host.get("memory_capacity_in_bytes"),
                block_serial=host.get("block_serial"),
                block_model=host.get("block_model_name"),
                serial_number=host.get("serial"),
                ipmi_ip=host.get("ipmi_address"),
                cvm_ip=host.get("service_vmexternal_ip") or host.get("controller_vm_backplane_ip"),
                hypervisor_ip=host.get("hypervisor_address"),
                boot_time=str(host.get("boot_time_in_usecs") or "") or None,
                num_vms=host.get("num_vms"),
                # The node's raw capacity, as the Prism Element hosts table shows it.
                storage_capacity_bytes=_as_int(
                    (host.get("usage_stats") or {}).get("storage.capacity_bytes")),
                api_source="v2.0",
            )
        )
    env.collection_notes["hosts"] = "ok"


# ─── storage ───────────────────────────────────────────────────────────


def _storage(conn: ElementConnection, env: Any) -> None:
    for sc in conn.v2_list("storage_containers"):
        usage = sc.get("usage_stats", {}) or {}
        env.storage_containers.append(
            StorageContainer(
                uuid=sc.get("storage_container_uuid", ""),
                name=sc.get("name"),
                cluster_uuid=sc.get("cluster_uuid"),
                replication_factor=sc.get("replication_factor"),
                compression_enabled=sc.get("compression_enabled"),
                compression_delay_secs=sc.get("compression_delay_in_secs"),
                deduplication=_dedup(sc),
                erasure_coding=sc.get("erasure_code"),
                max_capacity_bytes=_as_int(sc.get("max_capacity")),
                advertised_capacity_bytes=_as_int(sc.get("advertised_capacity")),
                used_bytes=_as_int(usage.get("storage.user_usage_bytes")),
                free_bytes=_as_int(usage.get("storage.user_free_bytes")),
                api_source="v2.0",
            )
        )
    env.collection_notes["storage_containers"] = "ok"


# ─── networks ──────────────────────────────────────────────────────────


def _networks(conn: ElementConnection, env: Any) -> None:
    for net in conn.v2_list("networks"):
        ip_config = net.get("ip_config", {}) or {}
        env.subnets.append(
            Subnet(
                uuid=net.get("uuid", ""),
                name=net.get("name"),
                cluster_uuid=net.get("cluster_uuid"),
                subnet_type="VLAN",
                vlan_id=net.get("vlan_id"),
                ip_prefix=_prefix(ip_config),
                gateway_ip=ip_config.get("default_gateway"),
                dhcp_enabled=bool(ip_config.get("dhcp_options")),
                api_source="v2.0",
            )
        )
    env.collection_notes["subnets"] = "ok"


# ─── VMs ───────────────────────────────────────────────────────────────


def _vms(conn: ElementConnection, env: Any) -> None:
    # The v2.0 /vms endpoint omits disk and NIC config unless these include flags are
    # passed, so request them on every page (paginated via v2_list).
    include = {"include_vm_disk_config": "true", "include_vm_nic_config": "true"}
    for vm in conn.v2_list("vms", extra_params=include):
        env.vms.append(_map_vm(vm))
    _cvms(conn, env)
    env.collection_notes["vms"] = "ok"


def _cvms(conn: ElementConnection, env: Any) -> None:
    """Controller VMs, for the cluster capacity figures (their vCPUs are subtracted).

    v2.0 `/vms` never lists CVMs. The v1 VM list does, flagged `controllerVm`, with each
    one's `numVCpus` and host. Best-effort and read-only (GET): a cluster that refuses the
    v1 call just leaves the CVM list empty, and CPU capacity then reads N/A.
    """
    try:
        rows = conn.v1_list("vms")
    except Exception:  # noqa: BLE001 - v1 unavailable or shaped differently
        return
    seen = {v.uuid for v in env.vms}
    for row in rows:
        if not row.get("controllerVm"):
            continue
        uuid = str(row.get("uuid") or row.get("vmId") or "")
        if not uuid or uuid in seen:
            continue
        ips = [ip for ip in (row.get("ipAddresses") or []) if isinstance(ip, str)]
        env.system_vms.append(
            VirtualMachine(
                uuid=uuid,
                name=str(row.get("vmName") or ""),
                power_state=(row.get("powerState") or "").upper() or None,
                num_vcpus=_as_int(row.get("numVCpus")),
                memory_bytes=_as_int(row.get("memoryCapacityInBytes")),
                host_uuid=row.get("hostUuid"),
                host_name=row.get("hostName"),
                nics=[Nic(ip_addresses=ips)] if ips else [],
                vm_type="CVM",
                api_source="v1",
            )
        )


def _map_vm(vm: dict[str, Any]) -> VirtualMachine:
    out = VirtualMachine(
        uuid=vm.get("uuid", ""),
        name=vm.get("name", ""),
        power_state=(vm.get("power_state") or "").upper() or None,
        num_vcpus=vm.get("num_vcpus") or vm.get("num_sockets"),
        num_cores_per_vcpu=vm.get("num_cores_per_vcpu"),
        memory_bytes=_as_int(vm.get("memory_mb"), scale=1024 * 1024),
        description=vm.get("description"),
        host_uuid=vm.get("host_uuid"),
        gpus=[g.get("device_name", "") for g in vm.get("vm_gpus", []) or [] if isinstance(g, dict)],
        api_source="v2.0",
    )
    out.disks = _disks(vm)
    out.nics = _nics(vm)
    return out


def _disks(vm: dict[str, Any]) -> list[VirtualDisk]:
    out: list[VirtualDisk] = []
    for disk in vm.get("vm_disk_info", []) or []:
        addr = disk.get("disk_address", {}) or {}
        out.append(
            VirtualDisk(
                uuid=addr.get("vmdisk_uuid"),
                label=f"{addr.get('device_bus', '')}.{addr.get('device_index', '')}".strip("."),
                disk_size_bytes=_as_int(disk.get("size")),
                storage_container_uuid=disk.get("storage_container_uuid"),
                bus_type=(addr.get("device_bus") or "").upper() or None,
                is_cdrom=disk.get("is_cdrom", False),
            )
        )
    return out


def _nics(vm: dict[str, Any]) -> list[Nic]:
    out: list[Nic] = []
    for nic in vm.get("vm_nics", []) or []:
        ips = [nic.get("ip_address")] if nic.get("ip_address") else []
        ips += [
            ep.get("ip_address")
            for ep in nic.get("ip_endpoint_list", []) or []
            if ep.get("ip_address")
        ]
        out.append(
            Nic(
                mac_address=nic.get("mac_address"),
                subnet_uuid=nic.get("network_uuid"),
                ip_addresses=[ip for ip in ips if ip],
                is_connected=nic.get("is_connected"),
            )
        )
    return out


#: (area id, label, step) in run order. The area ids match the Prism Central collectors'
#: so the UI's area list, progress events and run metadata read the same in both modes.
ELEMENT_STEPS: list[tuple[str, str, Any]] = [
    ("clusters", "Cluster", _cluster),
    ("hosts", "Hosts / Nodes", _hosts),
    ("storage_containers", "Storage Containers", _storage),
    ("subnets", "Networks", _networks),
    ("vms", "Virtual Machines", _vms),
]


# ─── helpers ───────────────────────────────────────────────────────────


def _short_hyp(value: str | None) -> str:
    return hypervisor_name(value) or ""


def _dedup(sc: dict[str, Any]) -> str | None:
    if sc.get("on_disk_dedup") and sc["on_disk_dedup"] != "OFF":
        return sc["on_disk_dedup"]
    if sc.get("finger_print_on_write") == "ON":
        return "INLINE"
    return None


def _prefix(ip_config: dict[str, Any]) -> str | None:
    network = ip_config.get("network_address")
    prefix = ip_config.get("prefix_length")
    if network and prefix is not None:
        return f"{network}/{prefix}"
    return network


def _as_int(value: Any, scale: int = 1) -> int | None:
    try:
        return int(value) * scale if value is not None else None
    except (TypeError, ValueError):
        return None
