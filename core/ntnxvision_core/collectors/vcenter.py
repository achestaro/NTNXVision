"""Optional vCenter enrichment of ESXi clusters (vSphere Automation REST, read-only).

Nutanix (v4, plus Prism Element v2.0 through the PC proxy) already gives an ESXi VM its
sizing, disks, NICs, port groups and the guest IPs VMware Tools reports. What only vCenter
knows, measured against a lab vCenter 7.0.3 managing a Nutanix ESXi cluster:

* the **datastore** of every disk, including those outside a Nutanix storage container
  (53 of 166 disks on the lab cluster sit on local or foreign VMFS volumes, for which
  Nutanix reports a size only);
* the **switch type** of each port group (standard, distributed, NSX segment);
* guest IPs and the running guest OS for VMs whose NIC Nutanix reports without them.

It runs after the Nutanix collectors, only for the ESXi clusters in the run's scope: the
vCenter hosts are matched to the collected Nutanix hosts (by name or hypervisor IP), and
only the VMs on those hosts are read. A vCenter VM is matched to a collected VM by NIC MAC
address, else by a name unique on that cluster; vCenter never adds VMs, it only fills
gaps. Read-only GETs (plus the API session login / logout). Any failure is isolated by the
engine and lands in ``collection_notes["vcenter"]``.

Requests: the host, network and VM lists, then per matched VM its detail and, when it is
powered on, its guest networking (``503`` while VMware Tools is not running).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ntnxvision_core.api.vcenter_client import VcenterConnection
from ntnxvision_core.collectors.element import disk_key, usable_ip
from ntnxvision_core.collectors.normalize import ESXI, hypervisor_name
from ntnxvision_core.models import Nic, PortGroup, VirtualDisk, VirtualMachine
from ntnxvision_core.numfmt import fmt_count

#: vCenter network types → `PortGroup.switch_type`.
SWITCH_TYPES = {"STANDARD_PORTGROUP": "STANDARD", "DISTRIBUTED_PORTGROUP": "DISTRIBUTED",
                "OPAQUE_NETWORK": "OPAQUE"}
#: vCenter boot firmware → the model's boot type vocabulary.
BOOT_TYPES = {"BIOS": "LEGACY", "EFI": "UEFI"}
_DATASTORE = re.compile(r"^\[([^\]]+)\]")
#: Hosts per VM-list request (the filter goes on the query string).
_HOST_BATCH = 20


@dataclass
class VcenterResult:
    matched: int = 0
    unmatched: int = 0
    ips: int = 0
    nics: int = 0
    disks_added: int = 0
    datastores: int = 0
    port_groups: int = 0
    guest_os: int = 0
    other: int = 0
    no_hosts: list[str] = field(default_factory=list)

    def note(self, host: str, version: str | None) -> str:
        """The collection note (no em dashes: it reaches the reports)."""
        where = f"vCenter {host}" + (f" ({version})" if version else "")
        if self.no_hosts and not self.matched:
            return (f"ok: {where} manages none of the hosts of "
                    f"{', '.join(self.no_hosts)}; nothing enriched")
        parts = [f"{fmt_count(self.matched)} VM(s) matched"]
        for count, label in ((self.ips, "NIC(s) given guest IPs"),
                             (self.nics, "NIC(s) added"),
                             (self.disks_added, "disk(s) added"),
                             (self.datastores, "disk datastore(s) named"),
                             (self.port_groups, "port group(s) typed or added"),
                             (self.guest_os, "guest OS filled"),
                             (self.other, "other field(s) filled")):
            if count:
                parts.append(f"{fmt_count(count)} {label}")
        if self.unmatched:
            parts.append(f"{fmt_count(self.unmatched)} vCenter VM(s) on these hosts not "
                         "collected from Nutanix (left out)")
        if self.no_hosts:
            parts.append("no vCenter hosts for " + ", ".join(self.no_hosts))
        return f"ok: {where}: " + "; ".join(parts)


def enrich_from_vcenter(vc: VcenterConnection, env: Any) -> str:
    """Fill the ESXi gaps of `env` from vCenter; returns the collection note."""
    result = VcenterResult()
    clusters = [c for c in env.clusters
                if ESXI in {hypervisor_name(t) for t in c.hypervisor_types or []}]
    if not clusters:
        return "skipped: no ESXi cluster in this collection"
    host_cluster = _match_hosts(vc, env, clusters, result)
    if not host_cluster:
        return result.note(vc.creds.host, vc.version)
    networks = {str(n.get("network")): n for n in vc.get("vcenter/network") or []
                if isinstance(n, dict)}
    vms_by_cluster = _collected_vms(env)
    host_ids = sorted(host_cluster)
    for i in range(0, len(host_ids), _HOST_BATCH):
        batch = host_ids[i:i + _HOST_BATCH]
        for summary in vc.get("vcenter/vm", {"hosts": batch}) or []:
            if not isinstance(summary, dict) or not summary.get("vm"):
                continue
            _enrich_vm(vc, env, summary, host_cluster, batch, networks, vms_by_cluster,
                       result)
    _type_port_groups(env, networks, result)
    return result.note(vc.creds.host, vc.version)


# ─── hosts ─────────────────────────────────────────────────────────────


def _match_hosts(vc: VcenterConnection, env: Any, clusters: list[Any],
                 result: VcenterResult) -> dict[str, str]:
    """vCenter host id → Nutanix cluster uuid, for the hosts of the collected ESXi
    clusters. A vCenter host is added by name or IP; either can match."""
    wanted = {c.uuid for c in clusters}
    keys: dict[str, str] = {}
    for h in env.hosts:
        if h.cluster_uuid not in wanted:
            continue
        for key in (h.name, h.hypervisor_ip, (h.name or "").split(".")[0]):
            if key:
                keys[key.casefold()] = h.cluster_uuid
    out: dict[str, str] = {}
    for host in vc.get("vcenter/host") or []:
        if not isinstance(host, dict):
            continue
        name = str(host.get("name") or "")
        cluster = keys.get(name.casefold()) or keys.get(name.split(".")[0].casefold())
        if cluster and host.get("host"):
            out[str(host["host"])] = cluster
    found = set(out.values())
    result.no_hosts = [c.name or c.uuid for c in clusters if c.uuid not in found]
    return out


# ─── VMs ───────────────────────────────────────────────────────────────


def _collected_vms(env: Any) -> dict[str, list[VirtualMachine]]:
    """Collected VMs per cluster uuid (a CVM read from groups carries only the name)."""
    by_name = {c.name: c.uuid for c in env.clusters if c.name}
    out: dict[str, list[VirtualMachine]] = {}
    for vm in [*env.vms, *env.system_vms]:
        cluster = vm.cluster_uuid or by_name.get(vm.cluster_name or "")
        if cluster:
            out.setdefault(cluster, []).append(vm)
    return out


def _enrich_vm(vc: VcenterConnection, env: Any, summary: dict[str, Any],
               host_cluster: dict[str, str], batch: list[str], networks: dict[str, Any],
               vms_by_cluster: dict[str, list[VirtualMachine]],
               result: VcenterResult) -> None:
    detail = vc.get(f"vcenter/vm/{summary['vm']}")
    if not isinstance(detail, dict):
        return
    # The VM list was filtered on these hosts; a one-host batch names the cluster.
    clusters = {host_cluster[h] for h in batch}
    nics = [n for n in (detail.get("nics") or {}).values() if isinstance(n, dict)]
    macs = {str(n.get("mac_address") or "").lower() for n in nics} - {""}
    candidates = [vm for c in clusters for vm in vms_by_cluster.get(c, [])]
    vm = _match(candidates, macs, str(summary.get("name") or detail.get("name") or ""))
    if vm is None:
        result.unmatched += 1
        return
    result.matched += 1
    if vm.vm_type == "CVM":
        return  # the Controller VM is sized from Nutanix; nothing to fill
    guest = None
    if str(summary.get("power_state") or detail.get("power_state")) == "POWERED_ON":
        guest = vc.get_optional(f"vcenter/vm/{summary['vm']}/guest/networking/interfaces")
        identity = vc.get_optional(f"vcenter/vm/{summary['vm']}/guest/identity")
        if not vm.guest_os and isinstance(identity, dict):
            name = (identity.get("full_name") or {}).get("default_message")
            if name:
                vm.guest_os = str(name)
                result.guest_os += 1
    _merge_nics(vm, nics, guest, networks, result)
    _merge_disks(vm, detail, result)
    boot = BOOT_TYPES.get(str((detail.get("boot") or {}).get("type") or ""))
    if not vm.boot_type and boot:
        vm.boot_type = boot
        result.other += 1
    cpu = detail.get("cpu") or {}
    if vm.num_vcpus is None and isinstance(cpu.get("count"), int):
        per = cpu.get("cores_per_socket") or 1
        vm.num_vcpus = cpu["count"] // per if cpu["count"] % per == 0 else cpu["count"]
        vm.num_cores_per_vcpu = per if cpu["count"] % per == 0 else 1
        result.other += 1


def _match(candidates: list[VirtualMachine], macs: set[str], name: str
           ) -> VirtualMachine | None:
    if macs:
        for vm in candidates:
            if any((n.mac_address or "").lower() in macs for n in vm.nics if n.mac_address):
                return vm
    named = [vm for vm in candidates if vm.name == name]
    return named[0] if len(named) == 1 else None


def _merge_nics(vm: VirtualMachine, nics: list[dict[str, Any]], guest: Any,
                networks: dict[str, Any], result: VcenterResult) -> None:
    ips_by_mac: dict[str, list[str]] = {}
    for iface in guest if isinstance(guest, list) else []:
        if not isinstance(iface, dict):
            continue
        mac = str(iface.get("mac_address") or "").lower()
        ips = [str(a.get("ip_address")) for a in (iface.get("ip") or {}).get("ip_addresses")
               or [] if isinstance(a, dict) and a.get("ip_address")]
        if mac:
            ips_by_mac[mac] = [ip for ip in ips if usable_ip(ip)]
    by_mac = {(n.mac_address or "").lower(): n for n in vm.nics if n.mac_address}
    for vc_nic in nics:
        mac = str(vc_nic.get("mac_address") or "").lower()
        backing = vc_nic.get("backing") or {}
        network = networks.get(str(backing.get("network") or "")) or {}
        pg_name = backing.get("network_name") or network.get("name")
        nic = by_mac.get(mac)
        if nic is None:
            nic = Nic(mac_address=vc_nic.get("mac_address"), subnet_name=pg_name,
                      nic_type=vc_nic.get("type"),
                      is_connected=str(vc_nic.get("state") or "") == "CONNECTED")
            vm.nics.append(nic)
            result.nics += 1
        else:
            if not nic.subnet_name and pg_name:
                nic.subnet_name = str(pg_name)
                result.other += 1
            if not nic.nic_type and vc_nic.get("type"):
                nic.nic_type = str(vc_nic["type"])
        if not nic.ip_addresses and ips_by_mac.get(mac):
            nic.ip_addresses = list(ips_by_mac[mac])
            result.ips += 1


def _merge_disks(vm: VirtualMachine, detail: dict[str, Any], result: VcenterResult) -> None:
    """Name each disk's datastore; add disks Nutanix did not list.

    A vCenter disk is matched to a collected one by bus address (``scsi0:1``: SCSI
    controller 0, unit 1, see `element.disk_key`), else by being the only disk of that
    capacity.
    """
    mine = [d for d in vm.disks if not d.is_cdrom]
    for vc_disk in (detail.get("disks") or {}).values():
        if not isinstance(vc_disk, dict):
            continue
        bus = str(vc_disk.get("type") or "").lower()
        raw_addr = vc_disk.get(bus)
        addr: dict[str, Any] = raw_addr if isinstance(raw_addr, dict) else {}
        if bus == "ide" and addr:
            # IDE: primary/secondary channel, master/slave device.
            label: str | None = (f"ide{0 if addr.get('primary') else 1}:"
                                 f"{0 if addr.get('master') else 1}")
        elif bus and addr.get("unit") is not None:
            label = f"{bus}{addr.get('bus', 0)}:{addr['unit']}"
        else:
            label = None
        vmdk = str((vc_disk.get("backing") or {}).get("vmdk_file") or "")
        m = _DATASTORE.match(vmdk)
        datastore = m.group(1) if m else None
        size = vc_disk.get("capacity")
        disk = next((d for d in mine if label and disk_key(d.label) == label), None)
        if disk is None and isinstance(size, int):
            same = [d for d in mine if d.disk_size_bytes == size and not d.datastore]
            disk = same[0] if len(same) == 1 else None
        if disk is None:
            vm.disks.append(VirtualDisk(label=label, disk_size_bytes=size if isinstance(
                size, int) else None, bus_type=bus.upper() or None, datastore=datastore))
            result.disks_added += 1
            if datastore:
                result.datastores += 1
            continue
        if datastore and not disk.datastore:
            disk.datastore = datastore
            result.datastores += 1


# ─── port groups ───────────────────────────────────────────────────────


def _type_port_groups(env: Any, networks: dict[str, Any], result: VcenterResult) -> None:
    """Switch type of each collected port group; port groups a NIC uses that Nutanix
    did not list are added (named, typed, no VLAN: vCenter REST does not expose it)."""
    by_name = {str(n.get("name")): n for n in networks.values() if n.get("name")}
    for pg in env.port_groups:
        net = by_name.get(pg.name or "")
        kind = SWITCH_TYPES.get(str((net or {}).get("type") or ""))
        if kind and pg.switch_type != kind:
            pg.switch_type = kind
            result.port_groups += 1
    have = {(p.cluster_uuid, p.name) for p in env.port_groups}
    esxi = {c.uuid for c in env.clusters
            if ESXI in {hypervisor_name(t) for t in c.hypervisor_types or []}}
    for vm in [*env.vms, *env.system_vms]:
        if vm.cluster_uuid not in esxi:
            continue
        for nic in vm.nics:
            name = nic.subnet_name
            if not name or (vm.cluster_uuid, name) in have or name not in by_name:
                continue
            net = by_name[name]
            env.port_groups.append(PortGroup(
                uuid=str(net.get("network") or name), name=name,
                cluster_uuid=vm.cluster_uuid, cluster_name=vm.cluster_name,
                switch_type=SWITCH_TYPES.get(str(net.get("type") or "")),
                api_source="vcenter"))
            have.add((vm.cluster_uuid, name))
            result.port_groups += 1
