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
            # Skip non-physical host entries: Prism Central hosts have no resources, and
            # some clusters return a placeholder entry with resources but no real node
            # data (no CPU model, serial, or block info) — those produce empty rows.
            if not resources:
                continue
            if not (
                resources.get("cpu_model")
                or resources.get("serial_number")
                or resources.get("block")
                or resources.get("num_cpu_cores")
            ):
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
        env.collection_notes[self.name] = "ok"


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
    v = value.lower()
    if not v:
        return None
    if "ahv" in v or "nutanix" in v or "kvm" in v or "el" in v.split():
        return "AHV"
    if "esx" in v or "vmware" in v:
        return "ESXi"
    if "hyper-v" in v or "hyperv" in v:
        return "Hyper-V"
    return value or None


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
