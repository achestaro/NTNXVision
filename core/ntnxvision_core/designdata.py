"""Cross-references for the design data (blocks, CVMs, host networking, PC, DR).

The collectors that feed the architecture diagrams record what each API gives, often
only uuids: a host NIC names its node, a CVM its node, a protection policy's site its
cluster ext ids. `resolve_design_data` runs once after every area (from
`engine._resolve_cross_references`) and fills the names and links in from the rest of the
Environment, so exporters and diagrams never have to join anything themselves:

* hosts get their block slot and rack id from the blocks;
* CVMs, host NICs, virtual switches, blocks and external arrays get cluster and host
  names; NICs their cluster (through their host) and virtual switch name;
* when the v4 CVM endpoint gave nothing (older PCs, Prism Element runs), the CVMs are
  taken from the CVM system VMs the VM area collected;
* containers name their external array, and each array lists its containers;
* the PC's hosting cluster and the PC VMs' clusters get names;
* protection policies list their source and target cluster names, recovery plans theirs
  (a cluster outside the inventory, at a remote AZ, keeps its uuid).

Every step is idempotent: names already set are kept, and the DR cluster names are
recomputed from the uuids, so running it again on a combined Environment (where the other
Prism Centrals' clusters are now known) names the remote clusters it can. Lists are
sorted by a stable key so the output does not depend on API response order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ntnxvision_core.collectors.normalize import hypervisor_name
from ntnxvision_core.models import Cvm

if TYPE_CHECKING:  # pragma: no cover
    from ntnxvision_core.models import Environment


def resolve_design_data(env: Environment) -> None:
    cluster_names = {c.uuid: c.name for c in env.clusters if c.uuid and c.name}
    host_names = {h.uuid: h.name for h in env.hosts if h.uuid and h.name}
    host_cluster = {h.uuid: h.cluster_uuid for h in env.hosts if h.uuid and h.cluster_uuid}
    # Name → uuid, for objects an older API names but does not identify. A name two
    # clusters (or hosts) share decides nothing.
    cluster_by_name = _unique({c.name: c.uuid for c in env.clusters if c.name and c.uuid},
                              [c.name for c in env.clusters if c.name])
    host_by_name = _unique({h.name: h.uuid for h in env.hosts if h.name and h.uuid},
                           [h.name for h in env.hosts if h.name])

    def cname(uuid: str | None) -> str | None:
        return cluster_names.get(uuid or "")

    # Blocks → hosts (slot, rack id).
    for block in env.blocks:
        block.cluster_name = block.cluster_name or cname(block.cluster_uuid)
    slot: dict[str, tuple[str, int | None, int | None]] = {}
    for block in env.blocks:
        for node in block.nodes:
            if node.host_uuid:
                slot[node.host_uuid] = (block.uuid, node.position, block.rack_id)
    for host in env.hosts:
        found = slot.get(host.uuid)
        if found is None:
            continue
        unit, position, rack = found
        host.rackable_unit_uuid = host.rackable_unit_uuid or unit
        if host.block_position is None:
            host.block_position = position
        if host.rack_id is None:
            host.rack_id = rack

    # CVMs: from the v4 endpoint, else from the CVM system VMs.
    if not env.cvms:
        for vm in env.system_vms:
            if vm.vm_type != "CVM":
                continue
            ips = [ip for nic in vm.nics for ip in nic.ip_addresses if ip]
            env.cvms.append(Cvm(
                uuid=vm.uuid, name=vm.name, cluster_uuid=vm.cluster_uuid,
                cluster_name=vm.cluster_name, host_uuid=vm.host_uuid, host_name=vm.host_name,
                num_vcpus=vm.vcpu_total, memory_bytes=vm.memory_bytes,
                ip_address=ips[0] if ips else None, hypervisor_type=vm.hypervisor,
                api_source=vm.api_source))
    for cvm in env.cvms:
        cvm.host_uuid = cvm.host_uuid or host_by_name.get(cvm.host_name or "")
        cvm.cluster_uuid = (cvm.cluster_uuid or host_cluster.get(cvm.host_uuid or "")
                            or cluster_by_name.get(cvm.cluster_name or ""))
        cvm.hypervisor_type = hypervisor_name(cvm.hypervisor_type)
        cvm.cluster_name = cvm.cluster_name or cname(cvm.cluster_uuid)
        cvm.host_name = cvm.host_name or host_names.get(cvm.host_uuid or "")
    env.cvms.sort(key=lambda c: ((c.cluster_name or "").casefold(),
                                 (c.host_name or "").casefold(), c.uuid))

    # Host networking.
    switch_names = {s.uuid: s.name for s in env.virtual_switches if s.uuid and s.name}
    for sw in env.virtual_switches:
        sw.cluster_name = sw.cluster_name or cname(sw.cluster_uuid)
        for member in sw.hosts:
            member.host_name = member.host_name or host_names.get(member.host_uuid or "")
    for nic in env.host_nics:
        nic.cluster_uuid = nic.cluster_uuid or host_cluster.get(nic.host_uuid or "")
        nic.cluster_name = nic.cluster_name or cname(nic.cluster_uuid)
        nic.host_name = nic.host_name or host_names.get(nic.host_uuid or "")
        if nic.virtual_switch_uuid and not nic.virtual_switch_name:
            nic.virtual_switch_name = switch_names.get(nic.virtual_switch_uuid)
    env.virtual_switches.sort(key=lambda s: ((s.cluster_name or "").casefold(),
                                             (s.name or "").casefold(), s.uuid))
    env.host_nics.sort(key=lambda n: ((n.cluster_name or "").casefold(),
                                      (n.host_name or "").casefold(), n.name or "", n.uuid))

    # External storage ↔ containers.
    arrays = {a.uuid: a for a in env.external_storages}
    on_array: dict[str, list[str]] = {}
    for ctr in env.storage_containers:
        array = arrays.get(ctr.external_storage_uuid or "")
        if array is None:
            continue
        if not ctr.external_storage_name and array.name:
            ctr.external_storage_name = array.name
        if ctr.name:
            on_array.setdefault(array.uuid, []).append(ctr.name)
    for array in env.external_storages:
        array.cluster_name = array.cluster_name or cname(array.cluster_uuid)
        if not array.container_names:
            array.container_names = sorted(set(on_array.get(array.uuid, [])))

    # Prism Central placement.
    cfg = env.pc_config
    cfg.hosting_cluster_name = cfg.hosting_cluster_name or cname(cfg.hosting_cluster_uuid)
    for pcvm in env.pc_vms:
        pcvm.hosting_cluster_name = (pcvm.hosting_cluster_name
                                     or cname(pcvm.hosting_cluster_uuid))

    # DR: cluster names per site, recomputed from the uuids (see the module doc).
    def names(uuids: list[str], previous: list[str]) -> list[str]:
        old = previous if len(previous) == len(uuids) else [""] * len(uuids)
        return [cname(u) or (o if o and o != u else u) for u, o in zip(uuids, old, strict=True)]

    for pp in env.protection_policies:
        for loc in pp.locations:
            loc.cluster_names = names(loc.cluster_uuids, loc.cluster_names)
        if pp.locations:
            pp.source_clusters = list(pp.locations[0].cluster_names)
            pp.target_clusters = [n for loc in pp.locations[1:] for n in loc.cluster_names]
    for rp in env.recovery_plans:
        rp.primary_clusters = names(rp.primary_cluster_uuids, rp.primary_clusters)
        rp.recovery_clusters = names(rp.recovery_cluster_uuids, rp.recovery_clusters)


def _unique(mapping: dict[str, str], all_names: list[str]) -> dict[str, str]:
    seen: dict[str, int] = {}
    for name in all_names:
        seen[name] = seen.get(name, 0) + 1
    return {k: v for k, v in mapping.items() if seen.get(k) == 1}
