"""Host / node collector (v4 `clustermgmt` when served, else v3 `hosts/list`).

Two things differ between the APIs beyond field names.

v3 returns a pseudo-host for Prism Central itself — no CPU model, no core count, a
serial that is really the PC's IP ("198-51-100-4") and a `block` whose values are the
*string* "null". It has to be filtered on actual compute evidence; anything looser
(a truthy serial, a non-empty block dict) lets it through as a blank row in the Hosts
sheet. v4 does not return it at all.

v4's `cpuCapacityHz` is **not** v3's `cpu_capacity_hz`. v3 reports the per-core
frequency (2.5 GHz); v4's same-sounding field is the aggregate across cores
(2.5 GHz x 24) and its `cpuFrequencyHz` is the per-core figure. Mapping by name would
silently inflate the value by the core count.

**Raw storage per node** (for the cluster capacity figures in `capacity.py`): v4 lists the
node's physical disks under `disk[]` with a `sizeInBytes` each, and their sum is the raw
size. v3 `hosts/list` carries no disk sizes, so the v3 path (and a v4 host without a disk
list) reads the node's `storage.capacity_bytes` from the groups API instead, the same
attribute the Prism hosts table shows as disk capacity. Best-effort: a PC that rejects the
query just leaves the field empty and the storage figures read N/A.

**Blocks and Controller VMs** (for the logical cluster design). v4 lists them per cluster:
``clusters/{c}/rackable-units`` (block model, serial, each node's slot, the rack id when
rack awareness is configured) and ``clusters/{c}/cvms`` (CVM vCPU, memory, IP per node).
Both are called only for the clusters in scope. Below the v4 line the blocks come from
each cluster's Prism Element (v2.0 ``hosts`` through the PC proxy: block model, serial and
slot), and the CVMs from the system VMs the VM area collects (see the engine). A cluster
whose blocks or CVMs cannot be read is named in the area note; the hosts themselves are
unaffected.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.collectors.esxi import scoped_clusters
from ntnxvision_core.collectors.normalize import hypervisor_name
from ntnxvision_core.models import Block, BlockNode, Cvm, Host


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
        v4 = self.conn.v4_ready("hosts")
        if v4:
            self._collect_v4(env)
        else:
            self._collect_v3(env)
        self._backfill_storage_capacity(env)
        failed = self._blocks_and_cvms(env, v4)
        env.collection_notes[self.name] = (
            "ok: " + "; ".join(failed) if failed else "ok")

    # ─── blocks (rackable units) and CVMs ─────────────────────────

    def _blocks_and_cvms(self, env: Any, v4: bool) -> list[str]:
        """Blocks and CVMs of the clusters in scope, each read on its own: returns
        "<part> of <cluster> unavailable (<reason>)" for every part that failed."""
        failed: list[str] = []
        for uuid, name, _hvs in scoped_clusters(self.conn, env):
            parts = ((self._v4_blocks, "blocks"), (self._v4_cvms, "CVMs")) if v4 \
                else ((self._pe_blocks, "blocks"),)
            for read, what in parts:
                try:
                    read(env, uuid, name)
                except NutanixApiError as exc:
                    failed.append(f"{what} of {name or uuid} unavailable ({_reason(exc)})")
        return failed

    def _v4_blocks(self, env: Any, cluster_uuid: str, cluster_name: str | None) -> None:
        base = f"clustermgmt/v4.0/config/clusters/{cluster_uuid}"
        for unit in self.conn.v4_list(f"{base}/rackable-units"):
            rack = unit.get("rack") or {}
            env.blocks.append(Block(
                uuid=str(unit.get("extId") or unit.get("serial") or ""),
                cluster_uuid=cluster_uuid,
                cluster_name=cluster_name,
                model_name=unit.get("modelName"),
                model=unit.get("model"),
                serial=unit.get("serial"),
                rack_id=_int_or_none(rack.get("id")),
                rack_uuid=rack.get("uuid"),
                nodes=[BlockNode(host_uuid=n.get("uuid"), position=_int_or_none(n.get("position")))
                       for n in unit.get("nodes") or [] if isinstance(n, dict)],
                api_source="v4",
            ))
    def _v4_cvms(self, env: Any, cluster_uuid: str, cluster_name: str | None) -> None:
        base = f"clustermgmt/v4.0/config/clusters/{cluster_uuid}"
        for cvm in self.conn.v4_list(f"{base}/cvms"):
            env.cvms.append(Cvm(
                uuid=str(cvm.get("extId") or ""),
                name=cvm.get("name"),
                cluster_uuid=cvm.get("clusterExtId") or cluster_uuid,
                cluster_name=cluster_name,
                host_uuid=cvm.get("nodeExtId"),
                num_vcpus=_int_or_none(cvm.get("numVcpus")),
                memory_bytes=_int_or_none(cvm.get("memorySizeBytes")),
                ip_address=_v4_ipv4(cvm.get("ipAddress")),
                backplane_ip=_v4_ipv4(cvm.get("backplaneIpAddress")),
                hypervisor_type=hypervisor_name(cvm.get("hypervisorType")),
                api_source="v4",
            ))

    def _pe_blocks(self, env: Any, cluster_uuid: str, cluster_name: str | None) -> None:
        """Blocks from the cluster's Prism Element (v2.0 hosts), grouped by block serial."""
        blocks: dict[str, Block] = {}
        for h in self.conn.pe_v2_list(cluster_uuid, "hosts"):
            serial = h.get("block_serial")
            if not serial:
                continue
            block = blocks.get(serial)
            if block is None:
                block = blocks[serial] = Block(
                    uuid=str(h.get("rackable_unit_uuid") or serial),
                    cluster_uuid=cluster_uuid, cluster_name=cluster_name,
                    model_name=h.get("block_model_name"), serial=serial,
                    api_source="v2.0")
            position = h.get("position") or {}
            block.nodes.append(BlockNode(host_uuid=h.get("uuid"),
                                         position=_int_or_none(position.get("ordinal"))))
        env.blocks.extend(blocks[k] for k in sorted(blocks))

    def _backfill_storage_capacity(self, env: Any) -> None:
        """Fill `storage_capacity_bytes` from the groups API where the list gave none."""
        missing = [h for h in env.hosts if h.storage_capacity_bytes is None]
        if not missing:
            return
        try:
            rows = self.conn.v3_groups("host", ["host_name", "storage.capacity_bytes"])
        except Exception:  # noqa: BLE001 - groups schema differs or the API is gone
            return
        by_uuid: dict[str, int] = {}
        by_name: dict[str, int] = {}
        for row in rows:
            value = _int_or_none(row.get("storage.capacity_bytes"))
            if not value:
                continue
            if row.get("entity_id"):
                by_uuid[str(row["entity_id"])] = value
            if row.get("host_name"):
                by_name[str(row["host_name"])] = value
        for host in missing:
            host.storage_capacity_bytes = by_uuid.get(host.uuid) or (
                by_name.get(host.name) if host.name else None)

    # ─── v4 ────────────────────────────────────────────────────────

    def _collect_v4(self, env: Any) -> None:
        for entity in self.conn.v4_list("clustermgmt/v4.0/config/hosts"):
            hypervisor = entity.get("hypervisor") or {}
            cluster = entity.get("cluster") or {}
            memory = entity.get("memorySizeBytes")
            disks = [d for d in (entity.get("disk") or entity.get("disks") or [])
                     if isinstance(d, dict)]
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
                    disk_count=len(disks) if disks else None,
                    storage_capacity_bytes=_v4_raw_bytes(disks),
                    host_type=_enum(entity.get("hostType")),
                    rackable_unit_uuid=entity.get("rackableUnitUuid") or None,
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
                    host_type=_enum(resources.get("host_type")),
                    api_source="v3",
                )
            )


def _enum(value: Any) -> str | None:
    if not isinstance(value, str) or not value or value.startswith("$"):
        return None
    return value


def _reason(exc: NutanixApiError) -> str:
    return f"HTTP {exc.status_code}" if exc.status_code else (
        str(exc).split(":", 1)[0] or "request failed")


def _v4_raw_bytes(disks: list[dict[str, Any]]) -> int | None:
    """Sum of a v4 host's physical disk sizes, or None when no size is reported."""
    sizes = [_int_or_none(d.get("sizeInBytes")) for d in disks]
    known = [s for s in sizes if s]
    return sum(known) if known else None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


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
