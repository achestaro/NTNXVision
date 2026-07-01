"""Subnet collector (v3 `subnets/list`)."""

from __future__ import annotations

from typing import Any

from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.models import Subnet


class SubnetCollector(Collector):
    name = "subnets"
    label = "Subnets / Networks"

    def collect(self, env: Any) -> None:
        for entity in self.conn.v3_list("subnets"):
            status = entity.get("status", {})
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
                    virtual_switch=self._name_of(resources.get("virtual_switch_reference", {})),
                    vpc_name=self._name_of(vpc_ref),
                    ip_prefix=_prefix(ip_config),
                    gateway_ip=ip_config.get("default_gateway_ip"),
                    dhcp_enabled=dhcp,
                    api_source="v3",
                )
            )
        env.collection_notes[self.name] = "ok"


def _prefix(ip_config: dict[str, Any]) -> str | None:
    subnet_ip = ip_config.get("subnet_ip")
    prefix_len = ip_config.get("prefix_length")
    if subnet_ip and prefix_len is not None:
        return f"{subnet_ip}/{prefix_len}"
    return subnet_ip
