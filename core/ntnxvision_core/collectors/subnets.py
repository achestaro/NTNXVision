"""Subnet collector (v3 `subnets/list`), plus the port groups of ESXi clusters.

ESXi VMs attach to vSphere port groups, which no Prism Central subnet API lists; they
are read per ESXi cluster from its Prism Element (v2.0 ``networks`` through the PC
proxy, see `collectors.esxi`) into `Environment.port_groups`.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.collectors.element import port_group_from_v2
from ntnxvision_core.collectors.esxi import PeReadError, esxi_clusters, pe_networks
from ntnxvision_core.models import Subnet


class SubnetCollector(Collector):
    name = "subnets"
    label = "Subnets / Networks"

    def collect(self, env: Any) -> None:
        vswitch_names = self._vswitch_names()
        for entity in self.conn.v3_list("subnets"):
            status = entity.get("status", {})
            spec_res = (entity.get("spec", {}) or {}).get("resources", {}) or {}
            resources = status.get("resources", {})
            cluster_ref = status.get("cluster_reference", {})
            vpc_ref = resources.get("vpc_reference", {})

            dhcp = resources.get("ip_config", {}).get("dhcp_options") is not None
            ip_config = resources.get("ip_config", {}) or {}

            env.subnets.append(
                Subnet(
                    uuid=entity.get("metadata", {}).get("uuid", ""),
                    name=status.get("name"),
                    cluster_uuid=self._uuid_of(cluster_ref),
                    cluster_name=self._name_of(cluster_ref),
                    subnet_type=resources.get("subnet_type"),
                    vlan_id=resources.get("vlan_id"),
                    virtual_switch=_vswitch(resources, spec_res, vswitch_names),
                    vpc_name=self._name_of(vpc_ref),
                    ip_prefix=_prefix(ip_config),
                    gateway_ip=ip_config.get("default_gateway_ip"),
                    dhcp_enabled=dhcp,
                    api_source="v3",
                )
            )
        failed = self._port_groups(env)
        env.collection_notes[self.name] = (
            "ok: port groups unavailable for " + "; ".join(failed) if failed else "ok")

    def _port_groups(self, env: Any) -> list[str]:
        """Port groups of the in-scope ESXi clusters, from Prism Element v2.0 via PC.

        Prism Central's subnet APIs list nothing for an ESXi cluster. Returns
        "<cluster> (<reason>)" for every cluster whose networks could not be read.
        """
        failed: list[str] = []
        for uuid, name in esxi_clusters(self.conn, env):
            try:
                networks = pe_networks(self.conn, uuid)
            except PeReadError as exc:
                failed.append(f"{name or uuid} ({exc})")
                continue
            for net in networks:
                env.port_groups.append(port_group_from_v2(net, uuid, name))
        return failed

    def _vswitch_names(self) -> dict[str, str]:
        """uuid → virtual switch name, best-effort via the v4 networking API.

        v3 subnets usually carry only virtual_switch_uuid (or vswitch_name on the
        spec side); this map lets us render a proper name either way.
        """
        if not self.conn.v4_ready("subnets"):
            return {}
        try:
            switches = self.conn.v4_list("networking/v4.0/config/virtual-switches")
        except Exception:  # noqa: BLE001 — never fail the subnet list over a nicety
            return {}
        out: dict[str, str] = {}
        for sw in switches:
            ext_id = sw.get("extId") or sw.get("uuid")
            name = sw.get("name")
            if ext_id and name:
                out[ext_id] = name
        return out


def _vswitch(
    resources: dict[str, Any],
    spec_res: dict[str, Any],
    vswitch_names: dict[str, str],
) -> str | None:
    ref = resources.get("virtual_switch_reference", {}) or {}
    if isinstance(ref, dict) and ref.get("name"):
        return ref["name"]
    for source in (resources, spec_res):
        name = source.get("vswitch_name")
        if isinstance(name, str) and name:
            return name
    uuid = (
        resources.get("virtual_switch_uuid")
        or (ref.get("uuid") if isinstance(ref, dict) else None)
        or spec_res.get("virtual_switch_uuid")
    )
    if uuid:
        return vswitch_names.get(uuid, uuid)
    return None


def _prefix(ip_config: dict[str, Any]) -> str | None:
    subnet_ip = ip_config.get("subnet_ip")
    prefix_len = ip_config.get("prefix_length")
    if subnet_ip and prefix_len is not None:
        return f"{subnet_ip}/{prefix_len}"
    return subnet_ip
