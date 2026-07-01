"""Flow collectors — Network Security (microsegmentation) and Virtual Networking.

Flow security policies, categories, and service/address groups come from the v3 API.
VPCs and floating IPs come from v3 as well (v4 `networking` covers these on newer PC,
but v3 is universally present where Flow VN is enabled).
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.models import (
    AddressGroup,
    Category,
    FloatingIp,
    SecurityPolicy,
    ServiceGroup,
    Vpc,
)


class FlowSecurityCollector(Collector):
    name = "flow_security"
    label = "Flow Network Security"

    def collect(self, env: Any) -> None:
        self._policies(env)
        self._categories(env)
        self._service_groups(env)
        self._address_groups(env)

        env.flow.security_policy_count = len(env.security_policies)
        env.flow.category_count = len(env.categories)
        env.flow.service_group_count = len(env.service_groups)
        env.flow.address_group_count = len(env.address_groups)
        env.collection_notes[self.name] = "ok"

    def _policies(self, env: Any) -> None:
        for entity in self.conn.v3_list("network_security_rules"):
            spec = entity.get("spec", {})
            resources = spec.get("resources", {})
            app_rule = resources.get("app_rule", {}) or {}
            isolation = resources.get("isolation_rule", {}) or {}
            quarantine = resources.get("quarantine_rule", {}) or {}

            if isolation:
                policy_type = "ISOLATION"
                secured = _target_group(isolation.get("first_entity_filter", {}))
            elif quarantine:
                policy_type = "QUARANTINE"
                secured = None
            else:
                policy_type = "APPLICATION"
                secured = _target_group(app_rule.get("target_group", {}))

            env.security_policies.append(
                SecurityPolicy(
                    uuid=entity.get("metadata", {}).get("uuid", ""),
                    name=spec.get("name"),
                    policy_type=policy_type,
                    state=(app_rule or isolation or quarantine).get("action"),
                    app_rule_action=app_rule.get("action"),
                    inbound_rule_count=len(app_rule.get("inbound_allow_list", []) or []),
                    outbound_rule_count=len(app_rule.get("outbound_allow_list", []) or []),
                    secured_group=secured,
                    api_source="v3",
                )
            )

    def _categories(self, env: Any) -> None:
        try:
            entities = self.conn.v3_list("categories")
        except Exception:  # noqa: BLE001
            # Categories use a different endpoint shape on some versions; skip quietly.
            return
        for entity in entities:
            # v3 categories/list returns keys; values require a per-key call. We record
            # the key with its value count to stay a single, cheap pass.
            env.categories.append(
                Category(
                    name=entity.get("name", ""),
                    value=None,
                    description=entity.get("description"),
                    api_source="v3",
                )
            )

    def _service_groups(self, env: Any) -> None:
        for entity in self.conn.v3_list("service_groups"):
            svc = entity.get("service_group", entity.get("status", {}).get("resources", {})) or {}
            tcp, udp, icmp = _service_ports(svc)
            env.service_groups.append(
                ServiceGroup(
                    uuid=entity.get("uuid") or entity.get("metadata", {}).get("uuid", ""),
                    name=svc.get("name") or entity.get("name"),
                    is_system=svc.get("is_system_defined"),
                    tcp_ports=tcp,
                    udp_ports=udp,
                    icmp=icmp,
                    api_source="v3",
                )
            )

    def _address_groups(self, env: Any) -> None:
        for entity in self.conn.v3_list("address_groups"):
            ag = entity.get("address_group", entity.get("status", {}).get("resources", {})) or {}
            ranges = [
                f"{b.get('ip')}/{b.get('prefix_length')}"
                for b in ag.get("ip_address_block_list", []) or []
                if b.get("ip")
            ]
            env.address_groups.append(
                AddressGroup(
                    uuid=entity.get("uuid") or entity.get("metadata", {}).get("uuid", ""),
                    name=ag.get("name") or entity.get("name"),
                    is_system=ag.get("is_system_defined"),
                    ip_ranges=ranges,
                    api_source="v3",
                )
            )


class FlowNetworkingCollector(Collector):
    name = "flow_networking"
    label = "Flow Virtual Networking"

    def collect(self, env: Any) -> None:
        self._vpcs(env)
        self._floating_ips(env)
        env.flow.vpc_count = len(env.vpcs)
        env.collection_notes[self.name] = "ok"

    def _vpcs(self, env: Any) -> None:
        try:
            entities = self.conn.v3_list("vpcs")
        except Exception:  # noqa: BLE001 — Flow VN may be disabled
            env.collection_notes[self.name] = "skipped: Flow Virtual Networking not enabled"
            return
        for entity in entities:
            spec = entity.get("spec", {})
            resources = spec.get("resources", {})
            ext_subnets = [
                s.get("external_subnet_reference", {}).get("name")
                for s in resources.get("external_subnet_list", []) or []
            ]
            prefixes = [_prefix_str(p) for p in
                        resources.get("externally_routable_prefix_list", []) or []]
            env.vpcs.append(
                Vpc(
                    uuid=entity.get("metadata", {}).get("uuid", ""),
                    name=spec.get("name"),
                    external_subnets=[s for s in ext_subnets if s],
                    externally_routable_prefixes=[p for p in prefixes if p],
                    # Each DNS entry is a {'ip': ...} dict in v3, not a bare string.
                    dns_servers=[
                        _ip_of(d)
                        for d in resources.get("common_domain_name_server_ip_list", []) or []
                        if _ip_of(d)
                    ],
                    api_source="v3",
                )
            )

    def _floating_ips(self, env: Any) -> None:
        try:
            entities = self.conn.v3_list("floating_ips")
        except Exception:  # noqa: BLE001
            return
        for entity in entities:
            spec = entity.get("spec", {})
            resources = spec.get("resources", {})
            env.floating_ips.append(
                FloatingIp(
                    uuid=entity.get("metadata", {}).get("uuid"),
                    floating_ip=entity.get("status", {})
                    .get("resources", {})
                    .get("floating_ip"),
                    vpc_name=resources.get("vpc_reference", {}).get("name"),
                    external_subnet=resources.get("external_subnet_reference", {}).get("name"),
                    associated_private_ip=resources.get("private_ip"),
                    api_source="v3",
                )
            )


# ─── helpers ───────────────────────────────────────────────────────────


def _ip_of(entry: Any) -> str | None:
    """A DNS/IP entry may be a bare string or a {'ip': ...} dict."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return entry.get("ip")
    return None


def _prefix_str(p: Any) -> str | None:
    """Format an externally-routable prefix, which may nest ip/prefix_length in an 'ip' dict."""
    if not isinstance(p, dict):
        return None
    ip = p.get("ip")
    prefix_len = p.get("prefix_length")
    if isinstance(ip, dict):  # some payloads nest as {'ip': {'ip': ..., 'prefix_length': ...}}
        prefix_len = ip.get("prefix_length", prefix_len)
        ip = ip.get("ip")
    if not ip:
        return None
    return f"{ip}/{prefix_len}" if prefix_len is not None else str(ip)


def _target_group(filter_spec: dict[str, Any]) -> str | None:
    params = filter_spec.get("params", {}) or {}
    parts = [f"{k}:{','.join(v)}" for k, v in params.items()]
    return "; ".join(parts) if parts else None


def _service_ports(svc: dict[str, Any]) -> tuple[str | None, str | None, bool]:
    tcp: list[str] = []
    udp: list[str] = []
    icmp = False
    for entry in svc.get("service_list", []) or []:
        proto = entry.get("protocol")
        if proto == "TCP":
            tcp += _port_ranges(entry.get("tcp_port_range_list", []))
        elif proto == "UDP":
            udp += _port_ranges(entry.get("udp_port_range_list", []))
        elif proto == "ICMP":
            icmp = True
    return (", ".join(tcp) or None, ", ".join(udp) or None, icmp)


def _port_ranges(ranges: list[dict[str, Any]]) -> list[str]:
    out = []
    for r in ranges or []:
        start, end = r.get("start_port"), r.get("end_port")
        out.append(str(start) if start == end else f"{start}-{end}")
    return out
