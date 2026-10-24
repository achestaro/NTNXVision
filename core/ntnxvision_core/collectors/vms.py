"""Virtual machine collector (v4 `vmm` when served, else v3 `vms/list`).

VMs are the richest object type — the core of any RVTools-style inventory.

**The v4 path is a hybrid, and deliberately so.** Neither API is sufficient alone:

* *Coverage* is v4's. There is no hypervisor-agnostic v4 VM endpoint, only
  `vmm/v4.0/ahv/config/vms` and `.../esxi/config/vms`, so both are read and unioned.
  That matters: on the lab PC v3's `vms/list` returns **one** VM for the ESXi cluster
  while the v4 ESXi endpoint returns around sixty. v3 only sees ESXi VMs that exist as
  Prism Central VM intents.
* *Detail* is v3's. The v4 VM payload — list **and** detail, they carry identical keys —
  has no `categories`, `guestOs`, `description` or `gpus`. Those are read from v3 and
  merged by UUID where v3 knows the VM. A VM only v4 can see is still reported, just
  without those fields: present and thinner beats absent.

The hypervisor is recovered from which endpoint answered, since v4 does not carry it.

The ESXi endpoint also 500s on individual unserializable records (two of 57 on the lab
PC), which fails any page containing them, hence `v4_list_tolerant`, which salvages the
rest of the page and counts what it had to skip. Those records fail on every v4 path
(single GET included, PLAT-10006).

**ESXi clusters are completed from their Prism Element** (v2.0 through the Prism Central
proxy, `collectors.esxi`): the VMs v4 could not serve are read whole, with disks and
NICs, and every v4 ESXi VM gains what v4 leaves out (disks outside Nutanix storage
containers, boot firmware, port group VLANs). Only what neither source returns is read
from the groups API (``mh_vm`` plus ``virtual_nic`` / ``virtual_disk``), marked
``v3-groups (partial)``; only when that fails too are VMs skipped with a note. Optional
vCenter enrichment runs later, in the engine (`collectors.vcenter`).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.collectors.element import _map_vm, disk_key, usable_ip
from ntnxvision_core.collectors.esxi import PeReadError, esxi_clusters, pe_networks, pe_vms
from ntnxvision_core.collectors.normalize import AHV, ESXI, hypervisor_name
from ntnxvision_core.models import Nic, VirtualDisk, VirtualMachine
from ntnxvision_core.numfmt import fmt_count
from ntnxvision_core.scope import names_label, v4_list_scoped

#: v4 VM endpoints, and the hypervisor each one implies.
V4_VM_ENDPOINTS = (
    ("vmm/v4.0/ahv/config/vms", AHV),
    ("vmm/v4.0/esxi/config/vms", ESXI),
)

#: groups (``mh_vm``) attributes read for a VM v4 could not serve. Values arrive as strings.
GROUPS_VM_ATTRIBUTES = [
    "vm_name", "cluster_name", "cluster", "hypervisor_type", "num_vcpus",
    "memory_size_bytes", "power_state", "is_cvm", "vm_type", "host_name", "node",
    "capacity_bytes", "guest_os_name", "ip_addresses",
]

#: groups ``virtual_nic`` / ``virtual_disk`` attributes, for the NICs and disks of a VM
#: recovered from ``mh_vm`` (attribute names verified on a live pc.7.5.1.1).
GROUPS_NIC_ATTRIBUTES = ["vm", "mac_address", "ipv4_addresses", "connected",
                         "virtual_network"]
GROUPS_DISK_ATTRIBUTES = ["vm", "capacity_bytes", "container", "container_name",
                          "disk_address", "is_cdrom"]

#: Provenance of a VM recovered from the groups API: core sizing only.
GROUPS_PARTIAL_SOURCE = "v3-groups (partial)"


@dataclass
class _EsxiResult:
    """What the Prism Element pass did: VMs added, VMs enriched, clusters it failed on."""

    added: list[VirtualMachine] = field(default_factory=list)
    enriched: int = 0
    failed: list[str] = field(default_factory=list)


class _V3Detail:
    """v3-only VM fields, looked up from a v4 VM.

    Matching is by uuid, which works for AHV: both APIs report the same one. **ESXi VMs
    do not share a uuid** — v3 reports the Prism Central VM intent's uuid while the v4
    ESXi endpoint reports the VM's own identifier — so those fall back to matching on
    name, and only when that name is unique across the whole v3 result. An ambiguous
    name yields nothing rather than a guess: a wrong guest OS or protection status
    attributed to the wrong VM is worse than an empty cell.
    """

    def __init__(
        self, by_uuid: dict[str, dict[str, Any]], by_name: dict[str, list[dict[str, Any]]]
    ) -> None:
        self.by_uuid = by_uuid
        self.unique_by_name = {n: rows[0] for n, rows in by_name.items() if len(rows) == 1}

    def for_vm(self, uuid: str, name: str) -> dict[str, Any]:
        found = self.by_uuid.get(uuid)
        if found is not None:
            return found
        return self.unique_by_name.get(name, {})


class VmCollector(Collector):
    name = "vms"
    label = "Virtual Machines"

    def collect(self, env: Any) -> None:
        if self.conn.v4_ready("vms"):
            note = self._collect_v4(env)
        else:
            self._collect_v3(env)
            # v3 lists no ESXi VMs (only the odd Prism Central intent): Prism Element
            # v2.0 through the PC proxy is the only Nutanix source for them here.
            note = _v4_note(0, [], self._esxi_from_prism_element(env, _V3Detail({}, {})), [])
        self._collect_cvms(env)
        env.collection_notes[self.name] = note

    # ─── v4 ────────────────────────────────────────────────────────

    def _collect_v4(self, env: Any) -> str:
        detail = self._v3_detail()
        skipped_total = 0
        failed_lists: list[str] = []
        for path, hypervisor in V4_VM_ENDPOINTS:
            try:
                # Scoped run: only the selected clusters' VMs, server-side when the PC
                # filters on the cluster reference (pruned afterwards either way).
                entities, skipped = v4_list_scoped(self.conn, path, "cluster/extId",
                                                   tolerant=True)
            except NutanixApiError as exc:
                # One hypervisor's endpoint being absent (404) is normal: a cluster of
                # the other type simply has none. A server error means that hypervisor's
                # VMs could not be listed at all, which the fallbacks can repair.
                if exc.status_code is not None and exc.status_code >= 500:
                    failed_lists.append(hypervisor)
                continue
            skipped_total += skipped
            for entity in entities:
                self._add_v4_vm(env, entity, hypervisor, detail)
        esxi = self._esxi_from_prism_element(env, detail)
        recovered: list[VirtualMachine] | None = []
        if (skipped_total > len(esxi.added) or AHV in failed_lists
                or (ESXI in failed_lists and not esxi.added)):
            # Last resort, for what neither v4 nor Prism Element could serve.
            recovered = self._recover_from_groups(env, detail)
        return _v4_note(skipped_total, failed_lists, esxi, recovered)

    # ─── ESXi: Prism Element v2.0 through the PC proxy ────────────

    def _esxi_from_prism_element(self, env: Any, detail: _V3Detail) -> _EsxiResult:
        """Complete the ESXi clusters' VMs from their Prism Element (v2.0 via PC).

        One paged GET of ``vms`` (with disks and NICs) and one of ``networks`` per
        in-scope ESXi cluster. Every VM v4 already returned keeps its v4 record and gains
        what v4 leaves out: disks outside Nutanix storage containers, boot firmware, the
        port group's VLAN and any guest IPs v4 did not report. Every VM Prism Element
        lists that v4 did not return (the records v4 answers HTTP 500 for) is added
        whole, marked ``v2.0``. Nothing outside the run's scope is read.
        """
        result = _EsxiResult()
        known = {v.uuid: v for v in [*env.vms, *env.system_vms]}
        for cluster_uuid, cluster_name in esxi_clusters(self.conn, env):
            label = cluster_name or cluster_uuid
            try:
                rows = pe_vms(self.conn, cluster_uuid)
            except PeReadError as exc:
                result.failed.append(f"{label} ({exc})")
                continue
            try:
                networks = {n["uuid"]: n for n in pe_networks(self.conn, cluster_uuid)
                            if n.get("uuid")}
            except PeReadError:
                networks = {}
            names = {u: str(n.get("name")) for u, n in networks.items() if n.get("name")}
            for row in rows:
                pe_vm = _map_vm(row, names, source="v2.0")
                pe_vm.cluster_uuid = cluster_uuid
                pe_vm.cluster_name = cluster_name
                pe_vm.hypervisor = ESXI
                for nic in pe_vm.nics:
                    nic.subnet_uuid = None  # a port group, not a Nutanix subnet
                current = known.get(pe_vm.uuid) or _unique_by_name(
                    known.values(), pe_vm.name, cluster_uuid, cluster_name)
                if current is not None:
                    if _merge_pe_detail(current, pe_vm):
                        result.enriched += 1
                    continue
                extra = detail.for_vm(pe_vm.uuid, pe_vm.name)
                pe_vm.categories = extra.get("categories") or {}
                pe_vm.protection_type = extra.get("protection_type")
                pe_vm.vm_type = _classify_vm(pe_vm.name, {}, {"categories": pe_vm.categories})
                known[pe_vm.uuid] = pe_vm
                result.added.append(pe_vm)
                if pe_vm.vm_type == "USER":
                    env.vms.append(pe_vm)
                else:
                    env.system_vms.append(pe_vm)
        return result

    def _add_v4_vm(
        self,
        env: Any,
        entity: dict[str, Any],
        hypervisor: str,
        detail: _V3Detail,
    ) -> None:
        uuid = entity.get("extId") or ""
        name = entity.get("name") or ""
        extra = detail.for_vm(uuid, name)
        categories = extra.get("categories") or {}

        vm = VirtualMachine(
            uuid=uuid,
            name=name,
            cluster_uuid=(entity.get("cluster") or {}).get("extId"),
            host_uuid=(entity.get("host") or {}).get("extId"),
            power_state=entity.get("powerState"),
            # The two schemas disagree: AHV reports numSockets, ESXi numCpus.
            num_vcpus=entity.get("numSockets") or _esxi_sockets(entity),
            num_cores_per_vcpu=entity.get("numCoresPerSocket"),
            # AHV only (verified on pc.7.5.1.1); the ESXi schema has neither.
            num_threads_per_core=_int_or_none(entity.get("numThreadsPerCore")),
            num_numa_nodes=_int_or_none(entity.get("numNumaNodes")),
            memory_bytes=entity.get("memorySizeBytes"),
            hypervisor=hypervisor,
            is_agent_vm=entity.get("isAgentVm", False),
            protection_type=entity.get("protectionType") or extra.get("protection_type"),
            boot_type=_v4_boot_type(entity.get("bootConfig")) or extra.get("boot_type"),
            creation_time=entity.get("createTime"),
            # Only v3 carries these; blank for a VM v3 cannot see.
            description=extra.get("description"),
            # ESXi VMs carry their own guest OS in v4; AHV ones only in v3.
            guest_os=entity.get("guestOsName") or extra.get("guest_os"),
            gpus=extra.get("gpus") or [],
            categories=categories,
            vm_type=_classify_vm(name, {}, {"categories": categories}),
            api_source="v4",
        )
        vm.disks = _v4_disks(entity)
        vm.nics = _v4_nics(entity)
        if vm.vm_type == "USER":
            env.vms.append(vm)
        else:
            env.system_vms.append(vm)

    def _recover_from_groups(self, env: Any, detail: _V3Detail) -> list[VirtualMachine] | None:
        """Read the VMs neither v4 nor Prism Element served from the groups API.

        The groups API, the one the Prism UI itself uses, still lists VMs the v4 ESXi
        endpoint answers HTTP 500 for. ``mh_vm`` carries their core sizing and guest
        IPs; ``virtual_nic`` and ``virtual_disk`` (filtered on the recovered VMs) add
        their NICs (MAC, IPv4, connection, network) and disks (size, container, bus).
        Every VM it reports that is not already collected is added and marked
        ``v3-groups (partial)``. The groups ``entity_id`` is the identifier v4 reports
        as ``extId``, for ESXi VMs too (checked on the lab PC), so matching is by uuid.

        Read-only queries (POST groups), scoped to the selected clusters. Returns the
        VMs added, or None when the ``mh_vm`` query failed (the caller keeps the skip
        note).
        """
        scope = getattr(self.conn, "scope", None)
        flt = "is_cvm==0"
        if scope is not None and scope.filtering and scope.selected:
            flt += ";cluster=in=" + "|".join(sorted(scope.selected))
        try:
            try:
                rows = self._groups("mh_vm", GROUPS_VM_ATTRIBUTES, flt, ("ip_addresses",))
            except NutanixApiError as exc:
                if exc.status_code != 400:
                    raise
                # A PC that rejects the filter syntax: read unfiltered, filter below.
                rows = self._groups("mh_vm", GROUPS_VM_ATTRIBUTES, None, ("ip_addresses",))
        except Exception:  # noqa: BLE001 — groups unavailable: keep the skip note
            return None
        seen = {v.uuid for v in env.vms} | {v.uuid for v in env.system_vms}
        pc_names = {v.name.casefold() for v in env.system_vms
                    if v.vm_type == "PRISM_CENTRAL" and v.name}
        added: list[VirtualMachine] = []
        for row in rows:
            vm = _groups_vm(row, detail)
            if vm is None or vm.uuid in seen:
                continue
            if scope is not None and scope.excludes(vm.cluster_uuid, vm.cluster_name):
                continue
            # CVMs come from `_collect_cvms`; never list them as workloads.
            if vm.vm_type == "CVM":
                continue
            if vm.vm_type == "PRISM_CENTRAL" and vm.name.casefold() in pc_names:
                continue
            seen.add(vm.uuid)
            added.append(vm)
            if vm.vm_type == "USER":
                env.vms.append(vm)
            else:
                env.system_vms.append(vm)
        if added:
            self._groups_nics_and_disks(added)
        return added

    def _groups(self, entity: str, attrs: list[str], flt: str | None,
                multi: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {}
        if flt:
            kwargs["filter_criteria"] = flt
        if multi:
            kwargs["multi_valued"] = multi
        return list(self.conn.v3_groups(entity, attrs, **kwargs))

    def _groups_nics_and_disks(self, vms: list[VirtualMachine]) -> None:
        """NICs and disks of groups-recovered VMs (``virtual_nic`` / ``virtual_disk``).

        Best-effort: a PC whose groups API does not serve these entity types leaves the
        VMs with their ``mh_vm`` sizing and guest IPs.
        """
        by_uuid = {v.uuid: v for v in vms}
        flt = "vm=in=" + "|".join(sorted(by_uuid))
        try:
            nics = self._groups("virtual_nic", GROUPS_NIC_ATTRIBUTES, flt, ("ipv4_addresses",))
        except Exception:  # noqa: BLE001 — optional detail
            nics = []
        try:
            disks = self._groups("virtual_disk", GROUPS_DISK_ATTRIBUTES, flt)
        except Exception:  # noqa: BLE001 — optional detail
            disks = []
        nic_rows: dict[str, list[Nic]] = {}
        for row in nics:
            vm = by_uuid.get(str(row.get("vm") or ""))
            if vm is None:
                continue
            ips = [str(ip) for ip in row.get("ipv4_addresses") or [] if usable_ip(str(ip))]
            nic_rows.setdefault(vm.uuid, []).append(Nic(
                uuid=row.get("entity_id"), mac_address=row.get("mac_address"),
                subnet_uuid=row.get("virtual_network") or None, ip_addresses=ips,
                is_connected=_flag(row.get("connected"))))
        for uuid, vm_nics in nic_rows.items():
            vm = by_uuid[uuid]
            # mh_vm's guest IPs belong to some NIC; keep any no NIC row reported.
            loose = [ip for n in vm.nics for ip in n.ip_addresses
                     if all(ip not in x.ip_addresses for x in vm_nics)]
            if loose:
                vm_nics[0].ip_addresses.extend(loose)
            vm.nics = vm_nics
        disk_rows: dict[str, list[VirtualDisk]] = {}
        for row in disks:
            vm = by_uuid.get(str(row.get("vm") or ""))
            if vm is None:
                continue
            address = str(row.get("disk_address") or "")
            bus = address.split(".", 1)[0].upper() if "." in address else None
            disk_rows.setdefault(vm.uuid, []).append(VirtualDisk(
                uuid=row.get("entity_id"), label=address or None,
                disk_size_bytes=_int_or_none(row.get("capacity_bytes")),
                storage_container_uuid=row.get("container") or None,
                storage_container_name=row.get("container_name") or None,
                bus_type=bus, is_cdrom=bool(_flag(row.get("is_cdrom")))))
        for uuid, vm_disks in disk_rows.items():
            by_uuid[uuid].disks = vm_disks

    def _v3_vms_in_scope(self) -> list[dict[str, Any]]:
        scope = getattr(self.conn, "scope", None)
        if scope is None or not scope.filtering or not scope.selected:
            return list(self.conn.v3_list("vms"))
        names = sorted({scope.registered[u].name for u in scope.selected
                        if scope.registered[u].name})
        if not names:
            return list(self.conn.v3_list("vms"))
        try:
            return list(self.conn.v3_list(
                "vms", filter=",".join(f"cluster_name=={n}" for n in names)))
        except NutanixApiError as exc:
            if exc.status_code != 400:
                raise
            return list(self.conn.v3_list("vms"))

    def _v3_detail(self) -> _V3Detail:
        """The fields v4 does not carry, indexed for lookup from a v4 VM.

        Best-effort: after the legacy sunset this returns empty and those columns go
        blank, but the v4 inventory itself is unaffected. A scoped run asks v3 for the
        selected clusters' VMs only (FIQL on ``cluster_name``); a PC that rejects the
        filter is read unfiltered, and only VMs collected from v4 are ever looked up.
        """
        try:
            entities = self._v3_vms_in_scope()
        except NutanixApiError:
            return _V3Detail({}, {})
        out: dict[str, dict[str, Any]] = {}
        names: dict[str, list[dict[str, Any]]] = {}
        for entity in entities:
            metadata = entity.get("metadata", {})
            uuid = metadata.get("uuid")
            if not uuid:
                continue
            status = entity.get("status", {})
            resources = status.get("resources", {})
            out[uuid] = {
                "description": status.get("description"),
                "guest_os": _guest_os(resources),
                # ESXi VMs carry no bootConfig and no protectionType in v4, but v3 has
                # both for the ones it can see.
                "boot_type": _nested(resources, "boot_config", "boot_type"),
                "protection_type": _protection_type(resources),
                "categories": metadata.get("categories_mapping")
                or metadata.get("categories")
                or {},
                "gpus": [
                    g.get("vendor", "") + " " + g.get("mode", "")
                    for g in resources.get("gpu_list", []) or []
                    if isinstance(g, dict)
                ],
            }
            vm_name = status.get("name")
            if vm_name:
                names.setdefault(vm_name, []).append(out[uuid])
        return _V3Detail(out, names)

    # ─── v3 ────────────────────────────────────────────────────────

    def _collect_v3(self, env: Any) -> None:
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
                num_threads_per_core=_int_or_none(resources.get("num_threads_per_core")),
                num_numa_nodes=_int_or_none(
                    (resources.get("vnuma_config") or {}).get("num_vnuma_nodes")),
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


# ─── groups fallback ───────────────────────────────────────────────────


def _groups_vm(row: dict[str, Any], detail: _V3Detail) -> VirtualMachine | None:
    """A partial VM from one ``mh_vm`` groups row, or None when it is unusable."""
    uuid = row.get("entity_id") or ""
    name = row.get("vm_name") or ""
    if not uuid or not name:
        return None
    is_cvm = (str(row.get("is_cvm") or "").lower() in ("1", "true")
              or str(row.get("vm_type") or "") == "kCVM")
    extra = detail.for_vm(uuid, name)
    categories = extra.get("categories") or {}
    power = row.get("power_state")
    ips = [ip for ip in _split_ips(row.get("ip_addresses")) if usable_ip(ip)]
    return VirtualMachine(
        nics=[Nic(ip_addresses=ips)] if ips else [],
        uuid=uuid,
        name=name,
        cluster_name=row.get("cluster_name") or None,
        cluster_uuid=row.get("cluster") or None,
        host_uuid=row.get("node") or None,
        host_name=row.get("host_name") or None,
        power_state=str(power).upper() if power else None,
        # groups reports the VM's total vCPUs, with no socket / core split.
        num_vcpus=_int_or_none(row.get("num_vcpus")),
        memory_bytes=_int_or_none(row.get("memory_size_bytes")),
        hypervisor=hypervisor_name(_groups_hypervisor(row.get("hypervisor_type"))),
        guest_os=row.get("guest_os_name") or extra.get("guest_os"),
        description=extra.get("description"),
        protection_type=extra.get("protection_type"),
        boot_type=extra.get("boot_type"),
        gpus=extra.get("gpus") or [],
        categories=categories,
        disk_capacity_bytes=_int_or_none(row.get("capacity_bytes")) or None,
        vm_type=_classify_vm(name, {"is_cvm": is_cvm}, {"categories": categories}),
        api_source=GROUPS_PARTIAL_SOURCE,
    )


def _groups_hypervisor(value: Any) -> str | None:
    """groups spells hypervisors as enum names: kKvm, kVMware, kHyperv."""
    if not value:
        return None
    v = str(value)
    return {"kkvm": "AHV", "kvmware": "VMware", "khyperv": "Hyper-V"}.get(v.lower(), v)


def _v4_note(skipped: int, failed_lists: list[str], esxi: _EsxiResult,
             recovered: list[VirtualMachine] | None) -> str:
    """The collection note for a VM run (no em dashes: it reaches the reports).

    Counts only what happened in this run's scope: a VM is reported as skipped only
    when the v4 API failed for it and neither Prism Element nor the groups API could
    stand in.
    """
    parts: list[str] = []
    if esxi.added:
        names = names_label(sorted((v.name for v in esxi.added), key=str.casefold), limit=10)
        parts.append(
            f"{fmt_count(len(esxi.added))} ESXi VM(s) read from Prism Element (v2.0 "
            f"through Prism Central) because the v4 API did not return them: {names}")
    if esxi.failed:
        parts.append("Prism Element could not be read for " + "; ".join(esxi.failed)
                     + ", so ESXi disks outside Nutanix containers, boot type and port "
                     "group VLANs may be missing")
    if recovered:
        names = names_label(sorted((v.name for v in recovered), key=str.casefold), limit=10)
        parts.append(
            f"{fmt_count(len(recovered))} VM(s) read from the groups API because no other "
            f"API returned them: {names}")
    remaining = max(0, skipped - len(esxi.added) - len(recovered or []))
    if remaining:
        parts.append(
            f"{fmt_count(remaining)} VM(s) skipped: the v4 endpoint returned an internal "
            "error for those individual records")
    if failed_lists and recovered is None and not esxi.added:
        parts.append(f"the v4 {' and '.join(failed_lists)} VM list could not be read")
    return "ok: " + "; ".join(parts) if parts else "ok"


def _unique_by_name(vms: Iterable[VirtualMachine], name: str, cluster_uuid: str,
                    cluster_name: str | None) -> VirtualMachine | None:
    """The one collected VM of that cluster with that name, else None.

    v3 reports an ESXi VM under its Prism Central intent's uuid, not the VM's own, so a
    v3-path VM is found by name; a name used twice on the cluster matches nothing.
    """
    found = [v for v in vms if v.name == name
             and (v.cluster_uuid == cluster_uuid
                  or (not v.cluster_uuid and cluster_name and v.cluster_name == cluster_name))]
    return found[0] if len(found) == 1 else None


def _merge_pe_detail(vm: VirtualMachine, pe: VirtualMachine) -> bool:
    """Fill what the VM's v4 (or v3) record lacks from its Prism Element record.

    Returns True when anything changed. The v4 record stays authoritative for what it
    has; Prism Element adds the disks v4 omits (those outside a Nutanix storage
    container: same disk uuid in both APIs), the boot firmware, cores per vCPU, the
    port group of a NIC and guest IPs v4 did not report (NICs match by uuid, else MAC).
    """
    changed = False
    have = {d.uuid for d in vm.disks if d.uuid}
    # Same disk in both: v4's disk extId is v2.0's `device_uuid`; the bus address
    # ("scsi.0") is the fallback key.
    keys = {disk_key(d.label) for d in vm.disks if not d.is_cdrom} - {None}
    for disk in pe.disks:
        if disk.is_cdrom or (disk.uuid and disk.uuid in have):
            continue
        key = disk_key(disk.label)
        if key and key in keys:
            continue
        if not disk.uuid and not key:
            continue  # nothing to tell it apart from a disk v4 already listed
        vm.disks.append(disk)
        changed = True
    for attr in ("boot_type", "num_cores_per_vcpu", "guest_os", "description"):
        if getattr(vm, attr) in (None, "") and getattr(pe, attr) not in (None, ""):
            setattr(vm, attr, getattr(pe, attr))
            changed = True
    by_uuid = {n.uuid: n for n in pe.nics if n.uuid}
    by_mac = {(n.mac_address or "").lower(): n for n in pe.nics if n.mac_address}
    for nic in vm.nics:
        other = by_uuid.get(nic.uuid or "") or by_mac.get((nic.mac_address or "").lower())
        if other is None:
            continue
        if not nic.subnet_name and other.subnet_name:
            nic.subnet_name = other.subnet_name
            changed = True
        if not nic.ip_addresses and other.ip_addresses:
            nic.ip_addresses = list(other.ip_addresses)
            changed = True
        if not nic.nic_type and other.nic_type:
            nic.nic_type = other.nic_type
            changed = True
    if not vm.nics and pe.nics:
        vm.nics = pe.nics
        changed = True
    return changed


def _esxi_sockets(entity: dict[str, Any]) -> int | None:
    """Sockets of an ESXi VM, so that sockets x cores per socket is its vCPU count.

    VMware's ``numCpus`` is the VM's total vCPUs (vCenter's ``cpu.count`` agrees on the
    lab), not a socket count like AHV's ``numSockets``; taken as sockets it would count
    a 4-vCPU VM with 2 cores per socket as 8.
    """
    total = entity.get("numCpus")
    per = entity.get("numCoresPerSocket")
    if isinstance(total, int) and isinstance(per, int) and per > 1 and total % per == 0:
        return total // per
    return total if isinstance(total, int) else None


def _flag(value: Any) -> bool | None:
    if value is None:
        return None
    return str(value).strip().lower() in ("1", "true", "yes", "connected")


# ─── v4 mappers ────────────────────────────────────────────────────────


def _v4_disks(entity: dict[str, Any]) -> list[VirtualDisk]:
    """Disks from a v4 VM.

    v4 references the storage container by extId only; the name is filled in by the
    engine's cross-reference pass from the collected containers.
    """
    out: list[VirtualDisk] = []
    for disk in entity.get("disks") or []:
        backing = disk.get("backingInfo") or {}
        address = disk.get("diskAddress") or {}
        bus = address.get("busType")
        index = address.get("index")
        source = ((backing.get("dataSource") or {}).get("reference") or {})
        out.append(
            VirtualDisk(
                uuid=disk.get("extId"),
                label=f"{bus or ''}.{index if index is not None else ''}".strip("."),
                disk_size_bytes=backing.get("diskSizeBytes"),
                storage_container_uuid=(backing.get("storageContainer") or {}).get("extId"),
                bus_type=bus,
                is_cdrom=False,
                source_image=source.get("imageExtId"),
            )
        )
    for cdrom in entity.get("cdRoms") or []:
        address = cdrom.get("diskAddress") or {}
        out.append(
            VirtualDisk(
                uuid=cdrom.get("extId"),
                label=f"{address.get('busType') or ''}."
                f"{address.get('index') if address.get('index') is not None else ''}".strip("."),
                bus_type=address.get("busType"),
                is_cdrom=True,
            )
        )
    return out


def _v4_boot_type(boot_config: Any) -> str | None:
    """Boot type in v3's vocabulary (LEGACY / UEFI / SECURE_BOOT).

    v4 has no `bootType` field: the kind is the payload's own discriminator —
    `vmm.v4.ahv.config.LegacyBoot` vs `UefiBoot` — and secure boot is a flag on the
    UEFI variant. ESXi VMs carry no bootConfig at all.
    """
    if not isinstance(boot_config, dict):
        return None
    kind = str(boot_config.get("$objectType") or "")
    if kind.endswith("LegacyBoot"):
        return "LEGACY"
    if kind.endswith("UefiBoot"):
        return "SECURE_BOOT" if boot_config.get("isSecureBootEnabled") else "UEFI"
    return None


def _v4_nics(entity: dict[str, Any]) -> list[Nic]:
    """NICs from a v4 VM, across two genuinely different schemas.

    AHV NICs reference a subnet by extId and carry `ipv4Config`; ESXi NICs name a
    vSwitch port group instead and expose an `ipAddressInfo` that is empty while the VM
    is powered off. Both spell the backing/network sections with and without the `nic`
    prefix, so both are consulted. Subnet name and VLAN for AHV come from the engine's
    cross-reference pass, as on the v3 path.
    """
    out: list[Nic] = []
    for nic in entity.get("nics") or []:
        backing = nic.get("nicBackingInfo") or nic.get("backingInfo") or {}
        network = nic.get("nicNetworkInfo") or nic.get("networkInfo") or {}
        ipv4 = (network.get("ipv4Config") or {}).get("ipAddress") or {}
        ips = [ipv4["value"]] if isinstance(ipv4.get("value"), str) else []
        for extra in (network.get("ipv4Info") or {}).get("learnedIpAddresses") or []:
            value = extra.get("value") if isinstance(extra, dict) else None
            if isinstance(value, str) and value not in ips:
                ips.append(value)
        # ESXi: the guest IPs VMware Tools reports. A live pc.7.5.1.1 names the lists
        # `ipv4Addresses` / `ipv6Addresses`; `ipAddress` is kept for older builds. Reading
        # only `ipAddress` left every ESXi VM without an IP address.
        info = network.get("ipAddressInfo") or {}
        for key in ("ipv4Addresses", "ipv6Addresses", "ipAddress"):
            for extra in info.get(key) or []:
                value = extra.get("value") if isinstance(extra, dict) else None
                if isinstance(value, str) and value not in ips and usable_ip(value):
                    ips.append(value)
        port_group = (network.get("portGroupInfo") or {}).get("name")
        out.append(
            Nic(
                uuid=nic.get("extId"),
                mac_address=backing.get("macAddress"),
                # ESXi has no subnet entity — the port group is the network's name.
                subnet_name=port_group,
                subnet_uuid=(network.get("subnet") or {}).get("extId"),
                ip_addresses=ips,
                is_connected=backing.get("isConnected"),
                vlan_id=None,
                nic_type=network.get("nicType") or backing.get("adapterType"),
            )
        )
    return out


# ─── v3 mappers ────────────────────────────────────────────────────────


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
    # Prism Central VMs are matched *before* the CVM rules below. A deployed PCVM is
    # named NTNX-<pc-ip>-PCVM-<epoch>, and "PCVM" contains the substring "CVM", so the
    # generic NTNX-/CVM test would otherwise claim it and leave the PC's own sizing blank.
    cats = metadata.get("categories_mapping") or metadata.get("categories") or {}
    cat_values = {str(v).upper() for vals in cats.values() for v in
                  (vals if isinstance(vals, list) else [vals])}
    if ("PCVM" in n or n.startswith("PC-") or n.startswith("NUTANIX-PC")
            or "PRISM_CENTRAL" in cat_values):
        return "PRISM_CENTRAL"
    # CVMs are named NTNX-<serial>-A / NTNX-*-CVM and may carry an is_cvm flag.
    if resources.get("is_cvm") or (n.startswith("NTNX-") and n.endswith(("-CVM", "-A", "-B"))):
        return "CVM"
    if "NTNX-" in n and "CVM" in n:
        return "CVM"
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
