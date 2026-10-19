"""Host networking collector: AHV virtual switches, uplink bonds and host physical NICs.

What the host network design needs, and where it comes from:

* **Virtual switches** (``networking/v4.0/config/virtual-switches``): one call gives each
  switch's bond mode, MTU and, per cluster and host, the bridge (``br0``) and the uplink
  NIC names. AHV only. Split into one `VirtualSwitch` per switch and cluster.
* **Uplink bonds** (``networking/v4.0/config/uplink-bonds``): the bond name (``br0-up``),
  type and LACP status per host and switch, merged into the switch's host entries.
* **Host NICs** (``clustermgmt/v4.0/config/host-nics``): model, driver, firmware, link
  speed, MTU and, where the switch advertises it, the LLDP/CDP neighbour (vendor/model
  string, chassis MAC, port, management IP). v4 is strictly richer than Prism Element
  here: on the lab PC, v2.0 returned null for every switch field that v4 filled. Below
  the v4 line, each in-scope cluster's Prism Element is read instead (v2.0
  ``hosts/{uuid}/host_nics`` through the PC proxy).

ESXi is not covered by these APIs: v4 lists an ESXi host's NICs with only their name and
link speed, and there are no virtual switch, bond or neighbour fields for it. Nothing is
inferred: the fields stay None and the diagrams say so.

No server-side cluster filter: ``host-nics`` does filter on ``clusterExtId``, but ESXi NICs
carry no cluster id and would be lost, and the lab PC answers HTTP 500 to a
``virtual-switches`` filter. The run's scope prunes both lists afterwards; a NIC whose
cluster cannot be established in a scoped run is dropped rather than kept.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.collectors.esxi import scoped_clusters
from ntnxvision_core.models import HostNic, VirtualSwitch, VirtualSwitchHost

SWITCHES_PATH = "networking/v4.0/config/virtual-switches"
BONDS_PATH = "networking/v4.0/config/uplink-bonds"
HOST_NICS_PATH = "clustermgmt/v4.0/config/host-nics"


class HostNetworkCollector(Collector):
    name = "host_networking"
    label = "Host networking"

    def collect(self, env: Any) -> None:
        notes: list[str] = []
        switches: list[VirtualSwitch] = []
        if self.conn.v4_ready("subnets"):
            try:
                switches = self._switches()
            except NutanixApiError as exc:
                notes.append(f"virtual switches unavailable ({_reason(exc)})")
            if switches:
                try:
                    _merge_bonds(switches, self.conn.v4_list(BONDS_PATH))
                except NutanixApiError as exc:
                    notes.append(f"uplink bonds unavailable ({_reason(exc)})")
        env.virtual_switches.extend(switches)

        if self.conn.v4_ready("hosts"):
            try:
                nics = [_nic_v4(n) for n in self.conn.v4_list(HOST_NICS_PATH)]
            except NutanixApiError as exc:
                nics = []
                notes.append(f"host NICs unavailable ({_reason(exc)})")
        else:
            nics, failed = self._nics_pe(env)
            if failed:
                notes.append("host NICs unavailable for " + "; ".join(failed))
        env.host_nics.extend(self._placed(env, nics, switches))
        env.collection_notes[self.name] = "ok: " + "; ".join(notes) if notes else "ok"

    # ─── virtual switches ──────────────────────────────────────────

    def _switches(self) -> list[VirtualSwitch]:
        out: list[VirtualSwitch] = []
        for sw in self.conn.v4_list(SWITCHES_PATH):
            uuid = str(sw.get("extId") or "")
            for cluster in sw.get("clusters") or []:
                if not isinstance(cluster, dict) or not cluster.get("extId"):
                    continue
                out.append(VirtualSwitch(
                    uuid=uuid,
                    name=sw.get("name"),
                    cluster_uuid=cluster.get("extId"),
                    description=sw.get("description") or None,
                    is_default=sw.get("isDefault"),
                    bond_mode=_enum(sw.get("bondMode")),
                    mtu=_int(sw.get("mtu")),
                    owner_type=_enum(sw.get("ownerType")),
                    gateway_ip=_value(cluster.get("gatewayIpAddress")),
                    vlan_id=_int(cluster.get("vlanIdentifier")),
                    hosts=sorted(
                        (VirtualSwitchHost(
                            host_uuid=h.get("extId"),
                            bridge=h.get("internalBridgeName"),
                            uplinks=[str(n) for n in h.get("hostNics") or []],
                            active_uplink=h.get("activeUplink") or None,
                            ip_address=_value((h.get("ipAddress") or {}).get("ip")),
                        ) for h in cluster.get("hosts") or [] if isinstance(h, dict)),
                        key=lambda h: h.host_uuid or ""),
                    api_source="v4",
                ))
        return out

    # ─── host NICs below v4 ────────────────────────────────────────

    def _nics_pe(self, env: Any) -> tuple[list[HostNic], list[str]]:
        """Host NICs from each in-scope cluster's Prism Element (v2.0, via the PC)."""
        nics: list[HostNic] = []
        failed: list[str] = []
        for uuid, name, _hvs in scoped_clusters(self.conn, env):
            try:
                hosts = [h.uuid for h in env.hosts if h.cluster_uuid == uuid] or [
                    str(h.get("uuid")) for h in self.conn.pe_v2_list(uuid, "hosts")
                    if h.get("uuid")]
                for host_uuid in hosts:
                    payload = self.conn.pe_get(uuid, f"hosts/{host_uuid}/host_nics")
                    items = payload if isinstance(payload, list) else (
                        (payload or {}).get("entities") or [])
                    nics += [_nic_v2(n, host_uuid, uuid) for n in items if isinstance(n, dict)]
            except NutanixApiError as exc:
                failed.append(f"{name or uuid} ({_reason(exc)})")
        return nics, failed

    # ─── placement ─────────────────────────────────────────────────

    def _placed(self, env: Any, nics: list[HostNic],
                switches: list[VirtualSwitch]) -> list[HostNic]:
        """Fill each NIC's cluster (from its host) and switch name; drop the NICs whose
        cluster cannot be established in a scoped run."""
        host_cluster = {h.uuid: h.cluster_uuid for h in env.hosts if h.uuid}
        switch_names = {s.uuid: s.name for s in switches if s.name}
        scope = getattr(self.conn, "scope", None)
        kept: list[HostNic] = []
        for nic in nics:
            if not nic.cluster_uuid and nic.host_uuid:
                nic.cluster_uuid = host_cluster.get(nic.host_uuid)
            if nic.virtual_switch_uuid and not nic.virtual_switch_name:
                nic.virtual_switch_name = switch_names.get(nic.virtual_switch_uuid)
            if scope is not None and scope.filtering and not nic.cluster_uuid:
                continue
            kept.append(nic)
        return sorted(kept, key=lambda n: (n.host_uuid or "", n.name or "", n.uuid))


def _merge_bonds(switches: list[VirtualSwitch], bonds: list[dict[str, Any]]) -> None:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for bond in bonds:
        info = bond.get("virtualSwitchInfo") or {}
        key = (str(info.get("reference") or ""), str(bond.get("hostReference") or ""))
        by_key.setdefault(key, bond)
    for sw in switches:
        for host in sw.hosts:
            found = by_key.get((sw.uuid, host.host_uuid or ""))
            if found is None:
                continue
            host.bond_name = found.get("name")
            host.bond_type = _enum(found.get("type"))
            host.lacp_status = _enum(found.get("lacpStatus"))


def _nic_v4(n: dict[str, Any]) -> HostNic:
    status = n.get("interfaceStatus")
    return HostNic(
        uuid=str(n.get("extId") or ""),
        name=n.get("name"),
        host_uuid=n.get("nodeUuid"),
        cluster_uuid=n.get("clusterExtId") or None,
        model=n.get("hostDescription") or None,
        pci_model_id=n.get("pciModelId") or None,
        mac_address=n.get("macAddress") or None,
        driver_version=n.get("driverVersion") or None,
        firmware_version=n.get("firmwareVersion") or None,
        link_speed_kbps=_int(n.get("linkSpeedInKbps")),
        link_capacity_mbps=_int(n.get("linkCapacityInMbps")),
        link_up=None if status in (None, "") else str(status).strip() == "1",
        mtu=_int(n.get("mtuInBytes")),
        virtual_switch_uuid=n.get("virtualSwitchExtId") or None,
        discovery_protocol=n.get("discoveryProtocol") or None,
        switch_vendor=n.get("switchVendorInfo") or None,
        switch_mac=n.get("switchMacAddress") or None,
        switch_device_id=n.get("switchDeviceId") or None,
        switch_port_id=n.get("switchPortId") or None,
        switch_management_ip=_value(n.get("switchManagementIp")),
        switch_vlan_id=_int(n.get("switchVlanId")),
        api_source="v4",
    )


def _nic_v2(n: dict[str, Any], host_uuid: str, cluster_uuid: str) -> HostNic:
    mgmt = n.get("switch_management_ip")
    return HostNic(
        uuid=str(n.get("uuid") or n.get("id") or ""),
        name=n.get("name"),
        host_uuid=n.get("node_uuid") or host_uuid,
        cluster_uuid=cluster_uuid,
        mac_address=n.get("mac_address") or None,
        link_speed_kbps=_int(n.get("link_speed_in_kbps")),
        mtu=_int(n.get("mtu_in_bytes")),
        discovery_protocol=n.get("discovery_protocol") or None,
        switch_vendor=n.get("switch_vendor_info") or None,
        switch_mac=n.get("switch_mac_address") or None,
        switch_device_id=n.get("switch_device_id") or None,
        switch_port_id=n.get("switch_port_id") or None,
        switch_management_ip=mgmt if isinstance(mgmt, str) and mgmt else _value(mgmt),
        switch_vlan_id=_int(n.get("switch_vlan_id")),
        api_source="v2.0",
    )


def _value(address: Any) -> str | None:
    """The address in v4's nested IPAddress / IPv4Address shapes."""
    if not isinstance(address, dict):
        return None
    if isinstance(address.get("value"), str):
        return address["value"] or None
    for key in ("ipv4", "ipv6", "fqdn"):
        inner = address.get(key)
        if isinstance(inner, dict) and isinstance(inner.get("value"), str):
            return inner["value"] or None
    return None


def _enum(value: Any) -> str | None:
    if not isinstance(value, str) or not value or value.startswith("$"):
        return None
    return value


def _int(value: Any) -> int | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _reason(exc: NutanixApiError) -> str:
    if exc.status_code:
        return f"HTTP {exc.status_code}"
    return str(exc).split(":", 1)[0] or "request failed"
