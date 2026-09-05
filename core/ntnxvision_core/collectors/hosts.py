"""Host / node collector (v4 `clustermgmt` when served, else v3 `hosts/list`).

Two things differ between the APIs beyond field names.

v3 returns a pseudo-host for Prism Central itself — no CPU model, no core count, a
serial that is really the PC's IP ("10-13-37-4") and a `block` whose values are the
*string* "null". It has to be filtered on actual compute evidence; anything looser
(a truthy serial, a non-empty block dict) lets it through as a blank row in the Hosts
sheet. v4 does not return it at all.

v4's `cpuCapacityHz` is **not** v3's `cpu_capacity_hz`. v3 reports the per-core
frequency (2.5 GHz); v4's same-sounding field is the aggregate across cores
(2.5 GHz x 24) and its `cpuFrequencyHz` is the per-core figure. Mapping by name would
silently inflate the value by the core count.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.collectors.normalize import hypervisor_name
from ntnxvision_core.models import Host


def _is_physical_host(resources: dict[str, Any]) -> bool:
    """Does this v3 entity describe a real node?

    Keyed on compute evidence only. A real node always reports a CPU model, a core
    count or its memory; the Prism Central pseudo-host reports none of the three.
    """
    return bool(
        resources.get("cpu_model")
        or resources.get("num_cpu_cores")
        or resources.get("memory_capacity_mib")
    )


class HostCollector(Collector):
    name = "hosts"
    label = "Hosts / Nodes"

    def collect(self, env: Any) -> None:
        if self.conn.v4_ready("hosts"):
            self._collect_v4(env)
        else:
            self._collect_v3(env)
        env.collection_notes[self.name] = "ok"

    # ─── v4 ────────────────────────────────────────────────────────

    def _collect_v4(self, env: Any) -> None:
        for entity in self.conn.v4_list("clustermgmt/v4.0/config/hosts"):
            hypervisor = entity.get("hypervisor") or {}
            cluster = entity.get("cluster") or {}
            memory = entity.get("memorySizeBytes")
            env.hosts.append(
                Host(
                    uuid=entity.get("extId", ""),
                    name=entity.get("hostName"),
                    cluster_uuid=cluster.get("uuid"),
                    cluster_name=cluster.get("name"),
                    hypervisor_type=hypervisor_name(hypervisor.get("type")),
                    hypervisor_version=hypervisor.get("fullName"),
                    cpu_model=entity.get("cpuModel"),
                    num_cpu_sockets=entity.get("numberOfCpuSockets"),
                    num_cpu_cores=entity.get("numberOfCpuCores"),
                    num_cpu_threads=entity.get("numberOfCpuThreads"),
                    # Per-core frequency, to match v3. See the module docstring.
                    cpu_capacity_hz=entity.get("cpuFrequencyHz"),
                    memory_bytes=memory,
                    block_serial=entity.get("blockSerial"),
                    block_model=entity.get("blockModel"),
                    serial_number=entity.get("nodeSerial"),
                    ipmi_ip=_v4_ipv4((entity.get("ipmi") or {}).get("ip")),
                    cvm_ip=_v4_ipv4(
                        (entity.get("controllerVm") or {}).get("externalAddress")
                    ),
                    hypervisor_ip=_v4_ipv4(hypervisor.get("externalAddress")),
                    maintenance_state=_v4_maintenance_state(entity),
                    num_vms=hypervisor.get("numberOfVms"),
                    # v4 pads this with an empty string when a host has no GPU.
                    gpu_list=[g for g in (entity.get("gpuList") or []) if g],
                    api_source="v4",
                )
            )

    # ─── v3 ────────────────────────────────────────────────────────

    def _collect_v3(self, env: Any) -> None:
        for entity in self.conn.v3_list("hosts"):
            status = entity.get("status", {})
            resources = status.get("resources", {})
            if not resources or not _is_physical_host(resources):
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
                    hypervisor_type=_hypervisor_type(resources),
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
                    maintenance_state=_maintenance_state(resources),
                    num_vms=resources.get("num_vms"),
                    gpu_list=[g for g in gpus if g],
                    api_source="v3",
                )
            )


def _v4_ipv4(address: Any) -> str | None:
    """Plain address from v4's nested common.v1.config.IPAddress shape."""
    if not isinstance(address, dict):
        return None
    ipv4 = address.get("ipv4")
    if not isinstance(ipv4, dict):
        return None
    value = ipv4.get("value")
    return value if isinstance(value, str) else None


def _v4_maintenance_state(entity: dict[str, Any]) -> str | None:
    """Maintenance state in v3's vocabulary.

    v4 reports a lowercase `maintenanceState` ("normal"); v3 reports NORMAL /
    ENTERED_MAINTENANCE_MODE. Upper-case so a report does not depend on which API
    served the host. Falls back to the CVM's own flag when the field is absent.
    """
    state = entity.get("maintenanceState")
    if isinstance(state, str) and state:
        return state.upper()
    flag = (entity.get("controllerVm") or {}).get("isInMaintenanceMode")
    if flag is None:
        return None
    return "ENTERED_MAINTENANCE_MODE" if flag else "NORMAL"


def _hypervisor_type(resources: dict[str, Any]) -> str | None:
    """Derive the hypervisor type from the v3 host resources.

    v3 does not always populate `hypervisor.hypervisor_type`; infer it from the
    hypervisor's full name (AHV builds report 'Nutanix'/'AHV'; ESXi reports 'VMware').
    """
    explicit = _nested(resources, "hypervisor", "hypervisor_type")
    if explicit:
        return _short(explicit)
    full = _nested(resources, "hypervisor", "hypervisor_full_name") or ""
    return _short(full)


def _short(value: str) -> str | None:
    return hypervisor_name(value)


def _maintenance_state(resources: dict[str, Any]) -> str | None:
    """Derive a host's maintenance state from v3 host resources.

    v3 exposes this as `node_state` (e.g. NORMAL / ENTERED_MAINTENANCE_MODE) and/or a
    boolean `host_in_maintenance_mode`; fall back gracefully when neither is present.
    """
    node_state = resources.get("node_state")
    if node_state:
        return node_state
    flag = resources.get("host_in_maintenance_mode")
    if flag is None:
        return None
    return "ENTERED_MAINTENANCE_MODE" if flag else "NORMAL"


def _nested(d: dict[str, Any], *keys: str) -> Any:
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur
