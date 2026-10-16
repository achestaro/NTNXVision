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

The ESXi endpoint also 500s on individual unserializable records (two of ~60 on the lab
PC), which fails any page containing them — hence `v4_list_tolerant`, which salvages the
rest of the page and reports what it had to skip.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.collectors.normalize import AHV, ESXI
from ntnxvision_core.models import Nic, VirtualDisk, VirtualMachine
from ntnxvision_core.scope import v4_list_scoped

#: v4 VM endpoints, and the hypervisor each one implies.
V4_VM_ENDPOINTS = (
    ("vmm/v4.0/ahv/config/vms", AHV),
    ("vmm/v4.0/esxi/config/vms", ESXI),
)


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
            note = "ok"
        self._collect_cvms(env)
        env.collection_notes[self.name] = note

    # ─── v4 ────────────────────────────────────────────────────────

    def _collect_v4(self, env: Any) -> str:
        detail = self._v3_detail()
        skipped_total = 0
        for path, hypervisor in V4_VM_ENDPOINTS:
            try:
                # Scoped run: only the selected clusters' VMs, server-side when the PC
                # filters on the cluster reference (pruned afterwards either way).
                entities, skipped = v4_list_scoped(self.conn, path, "cluster/extId",
                                                   tolerant=True)
            except NutanixApiError:
                # One hypervisor's endpoint being absent is normal — a cluster of the
                # other type simply has none.
                continue
            skipped_total += skipped
            for entity in entities:
                self._add_v4_vm(env, entity, hypervisor, detail)
        if skipped_total:
            return (
                f"ok: {skipped_total:,} VM(s) skipped: the v4 endpoint returned an "
                "internal error for those individual records"
            )
        return "ok"

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
            num_vcpus=entity.get("numSockets") or entity.get("numCpus"),
            num_cores_per_vcpu=entity.get("numCoresPerSocket"),
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

    def _v3_detail(self) -> _V3Detail:
        """The fields v4 does not carry, indexed for lookup from a v4 VM.

        Best-effort: after the legacy sunset this returns empty and those columns go
        blank, but the v4 inventory itself is unaffected.
        """
        try:
            entities = self.conn.v3_list("vms")
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
        for extra in (network.get("ipAddressInfo") or {}).get("ipAddress") or []:
            value = extra.get("value") if isinstance(extra, dict) else None
            if isinstance(value, str) and value not in ips:
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
