"""Flow collectors — Network Security (microsegmentation) and Virtual Networking.

Network Security reads the v4 `microseg` namespace when the PC serves it, and the v3 API
otherwise. VPCs and floating IPs stay on v3 (v4 `networking` covers these on newer PC,
but v3 is universally present where Flow VN is enabled).

**v3 is not merely older here, it is empty.** On the lab pc.7.5.1.1
``network_security_rules/list`` answers with zero entities while
``microseg/v4.0/config/policies`` returns the three policies the UI shows: once the
microseg service owns policies, the legacy endpoint stops reporting them. So for policies
the v4 path is not a like-for-like swap, it is the difference between a populated Flow
section and an empty one.

**Categories are not under `microseg`.** ``microseg/v4.0/config/categories`` 404s; the v4
source is ``prism/v4.0/config/categories``, which lists one row per key *value* where v3
lists one row per key with no values at all. That fills the Value column the report has
always had and v3 could never populate — but a key that has no values is invisible to v4
(seven Calm/OSType keys on the lab PC), so the v4 path merges those back in from v3 and
no key is lost.

**Rule counts cost one request per policy.** They live only on the per-policy detail
endpoint: the list payload carries no `rules`, and `$select=rules` is rejected. Policies
number in the tens, so the extra round-trips are cheap next to blanking two columns.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.models import (
    AddressGroup,
    Category,
    FloatingIp,
    SecurityPolicy,
    ServiceGroup,
    Vpc,
)

#: Rule-spec keys that place a v4 policy rule on one side of the traffic flow. v4 flattens
#: v3's inbound/outbound allow lists into individual rules and identifies the direction by
#: which half of the spec is populated rather than by a field of its own.
_INBOUND_KEYS = (
    "srcAllowSpec",
    "srcCategoryReferences",
    "srcAddressGroupReferences",
    "srcSubnet",
)
_OUTBOUND_KEYS = (
    "destAllowSpec",
    "destCategoryReferences",
    "destAddressGroupReferences",
    "destSubnet",
)


class FlowSecurityCollector(Collector):
    name = "flow_security"
    label = "Flow Network Security"

    def collect(self, env: Any) -> None:
        if self.conn.v4_ready("microseg"):
            self._collect_v4(env)
        else:
            self._collect_v3(env)

        env.flow.security_policy_count = len(env.security_policies)
        env.flow.category_count = len(env.categories)
        env.flow.service_group_count = len(env.service_groups)
        env.flow.address_group_count = len(env.address_groups)
        env.collection_notes[self.name] = "ok"

    # ─── v4 ────────────────────────────────────────────────────────

    def _collect_v4(self, env: Any) -> None:
        # Categories are fetched once and used twice: as rows of their own, and to turn
        # the category UUIDs a policy names into the readable "key:value" the v3 path
        # produced from its filter params.
        category_rows = self._v4_categories()
        self._policies_v4(env, _category_labels(category_rows))
        self._categories_v4(env, category_rows)
        self._service_groups_v4(env)
        self._address_groups_v4(env)

    def _v4_categories(self) -> list[dict[str, Any]]:
        """All v4 category key/value pairs, or empty if this PC does not serve them.

        `prism` is outside the negotiated `microseg` namespace, so it is tried rather
        than assumed: an absent endpoint drops the caller back onto v3 categories instead
        of failing the run.
        """
        try:
            return self.conn.v4_list("prism/v4.0/config/categories")
        except NutanixApiError:
            return []

    def _policies_v4(self, env: Any, labels: dict[str, str]) -> None:
        for entity in self.conn.v4_list("microseg/v4.0/config/policies"):
            ext_id = entity.get("extId") or ""
            inbound, outbound = self._v4_rule_counts(ext_id)
            policy_type = entity.get("type")
            env.security_policies.append(
                SecurityPolicy(
                    uuid=ext_id,
                    name=entity.get("name"),
                    policy_type=policy_type,
                    # v4 states are MONITOR / ENFORCE / SAVE, which is the vocabulary the
                    # model documents. Passed through rather than re-spelled: the lab PC
                    # has no v3 policies left to confirm a mapping against, and inventing
                    # one would be guessing at v3's enum.
                    state=entity.get("state"),
                    # v3 only ever filled this for application policies, where it equalled
                    # the policy state; v4 has no separate per-rule action to read.
                    app_rule_action=(
                        entity.get("state") if policy_type == "APPLICATION" else None
                    ),
                    inbound_rule_count=inbound,
                    outbound_rule_count=outbound,
                    # For an isolation policy this names *both* isolated groups where v3
                    # showed only the first — v4's securedGroups spans the whole policy.
                    secured_group=_label_for(entity.get("securedGroups"), labels),
                    api_source="v4",
                )
            )

    def _v4_rule_counts(self, ext_id: str) -> tuple[int | None, int | None]:
        """(inbound, outbound) rule counts for one policy, from its detail endpoint.

        None rather than 0 when the policy cannot be read, so a failed request reads as
        "unknown" instead of "no rules".
        """
        if not ext_id:
            return (None, None)
        try:
            payload = self.conn.v4_get(f"microseg/v4.0/config/policies/{ext_id}")
        except NutanixApiError:
            return (None, None)
        data = payload.get("data") or {}
        inbound = outbound = 0
        for rule in data.get("rules") or []:
            direction = _rule_direction(rule.get("spec") or {})
            if direction == "IN":
                inbound += 1
            elif direction == "OUT":
                outbound += 1
        return (inbound, outbound)

    def _categories_v4(self, env: Any, rows: list[dict[str, Any]]) -> None:
        """One row per category key/value, with valueless keys merged in from v3.

        v4 has no notion of a key on its own, so a key nobody has assigned a value to
        simply does not appear. Those are exactly the keys v3 does list, which is why
        this stays a hybrid until the legacy sunset makes valueless keys unreportable.
        """
        if not rows:
            self._categories(env)
            return
        for row in rows:
            env.categories.append(
                Category(
                    name=row.get("key", ""),
                    value=row.get("value"),
                    description=row.get("description"),
                    api_source="v4",
                )
            )
        keys_with_values = {row.get("key") for row in rows}
        for name, description in self._v3_valueless_keys(keys_with_values):
            env.categories.append(
                Category(name=name, value=None, description=description, api_source="v3")
            )
        # Payload order is not meaningful and the two sources are concatenated, so the
        # table is ordered explicitly on what it displays.
        env.categories.sort(key=lambda c: (c.name.lower(), (c.value or "").lower()))

    def _v3_valueless_keys(self, known: set[str | None]) -> list[tuple[str, str | None]]:
        """Category keys v3 lists that v4 cannot show because they have no values.

        Best-effort: after the legacy sunset these keys drop off the report rather than
        the run failing.
        """
        try:
            entities = self.conn.v3_list("categories")
        except NutanixApiError:
            return []
        return [
            (e["name"], e.get("description"))
            for e in entities
            if e.get("name") and e["name"] not in known
        ]

    def _service_groups_v4(self, env: Any) -> None:
        for entity in self.conn.v4_list("microseg/v4.0/config/service-groups"):
            env.service_groups.append(
                ServiceGroup(
                    uuid=entity.get("extId") or "",
                    name=entity.get("name"),
                    is_system=entity.get("isSystemDefined"),
                    tcp_ports=_v4_port_ranges(entity.get("tcpServices")),
                    udp_ports=_v4_port_ranges(entity.get("udpServices")),
                    # v3 records only that ICMP is in the group, not its types/codes.
                    icmp=bool(entity.get("icmpServices")),
                    api_source="v4",
                )
            )

    def _address_groups_v4(self, env: Any) -> None:
        entities = self.conn.v4_list("microseg/v4.0/config/address-groups")
        # Only worth a legacy round-trip when there is something to backfill onto.
        system = self._v3_address_group_system_by_uuid() if entities else {}
        for entity in entities:
            ext_id = entity.get("extId") or ""
            env.address_groups.append(
                AddressGroup(
                    uuid=ext_id,
                    name=entity.get("name"),
                    # Absent from the v4 schema entirely (unlike service groups, which do
                    # carry isSystemDefined), so the System column is filled from v3 while
                    # v3 exists and left blank once it does not.
                    is_system=system.get(ext_id),
                    ip_ranges=_v4_addresses(entity),
                    api_source="v4",
                )
            )

    def _v3_address_group_system_by_uuid(self) -> dict[str, bool | None]:
        try:
            entities = self.conn.v3_list("address_groups")
        except NutanixApiError:
            return {}
        out: dict[str, bool | None] = {}
        for entity in entities:
            uuid = entity.get("uuid") or entity.get("metadata", {}).get("uuid")
            ag = entity.get("address_group", entity.get("status", {}).get("resources", {})) or {}
            if uuid:
                out[uuid] = ag.get("is_system_defined")
        return out

    # ─── v3 ────────────────────────────────────────────────────────

    def _collect_v3(self, env: Any) -> None:
        self._policies(env)
        self._categories(env)
        self._service_groups(env)
        self._address_groups(env)

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
        self._vpc_routing(env)
        env.flow.vpc_count = len(env.vpcs)
        env.collection_notes[self.name] = "ok"

    def _vpc_routing(self, env: Any) -> None:
        """Per-VPC route and routing-policy counts (v4 networking, best-effort).

        Subnet membership is backfilled by the engine from the subnet inventory.
        """
        if not env.vpcs:
            return
        by_uuid = {v.uuid: v for v in env.vpcs if v.uuid}
        try:
            tables = self.conn.v4_list("networking/v4.0/config/route-tables")
        except Exception:  # noqa: BLE001 — v4 networking absent
            tables = []
        for table in tables:
            vpc_ref = table.get("vpcReference")
            vpc_uuid = vpc_ref if isinstance(vpc_ref, str) else (vpc_ref or {}).get("extId")
            vpc = by_uuid.get(vpc_uuid or "")
            if not vpc:
                continue
            routes = (
                table.get("staticRoutes")
                or table.get("dynamicRoutes")
                or table.get("routes")
                or []
            )
            vpc.route_count = (vpc.route_count or 0) + len(routes)
        try:
            policies = self.conn.v4_list("networking/v4.0/config/routing-policies")
        except Exception:  # noqa: BLE001
            policies = []
        for policy in policies:
            vpc_ref = policy.get("vpcExtId") or policy.get("vpcReference")
            vpc_uuid = vpc_ref if isinstance(vpc_ref, str) else (vpc_ref or {}).get("extId")
            vpc = by_uuid.get(vpc_uuid or "")
            if vpc:
                vpc.routing_policy_count = (vpc.routing_policy_count or 0) + 1

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


# ─── v4 helpers ────────────────────────────────────────────────────────


def _category_labels(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Category extId → "key:value", the form v4 references categories by.

    v4 policies name their secured groups by UUID; the report has always shown the
    readable pair, so the pairs are resolved once for the whole run.
    """
    return {
        row["extId"]: f"{row.get('key', '')}:{row.get('value', '')}"
        for row in rows
        if row.get("extId")
    }


def _label_for(ext_ids: Any, labels: dict[str, str]) -> str | None:
    """Render category references as v3's "Key:v1,v2; Key2:v3" secured-group string.

    An id with no matching category is kept as its raw UUID rather than dropped — a
    reference the report cannot name is still a reference the reader should see.
    """
    if not isinstance(ext_ids, list) or not ext_ids:
        return None
    grouped: dict[str, list[str]] = {}
    unresolved: list[str] = []
    for ext_id in ext_ids:
        label = labels.get(ext_id)
        if label is None:
            unresolved.append(str(ext_id))
            continue
        key, _, value = label.partition(":")
        grouped.setdefault(key, []).append(value)
    # Sorted because payload order carries no meaning and the column must be stable.
    parts = [f"{k}:{','.join(sorted(v))}" for k, v in sorted(grouped.items())]
    parts += sorted(unresolved)
    return "; ".join(parts) or None


def _rule_direction(spec: dict[str, Any]) -> str | None:
    """"IN"/"OUT" for an application-style rule spec, else None.

    Isolation and intra-group rules belong to no direction, which is what keeps an
    isolation policy's counts at 0 the way the v3 path reported them.
    """
    if any(key in spec for key in _INBOUND_KEYS):
        return "IN"
    if any(key in spec for key in _OUTBOUND_KEYS):
        return "OUT"
    return None


def _v4_port_ranges(services: Any) -> str | None:
    """v4 TCP/UDP port specs as v3's "80, 8000-8080" string."""
    if not isinstance(services, list):
        return None
    out = []
    for entry in services:
        if not isinstance(entry, dict):
            continue
        start, end = entry.get("startPort"), entry.get("endPort")
        if start is None:
            continue
        out.append(str(start) if start == end else f"{start}-{end}")
    return ", ".join(out) or None


def _v4_addresses(entity: dict[str, Any]) -> list[str]:
    """Every address an address group holds, as v3-style "ip/prefix" plus "start-end".

    v4 splits what v3 called `ip_address_block_list` across three fields: prefixed IPv4
    and IPv6 addresses, and explicit start/end ranges that the v3 path had no equivalent
    for at all.
    """
    out: list[str] = []
    for key in ("ipv4Addresses", "ipv6Addresses"):
        for entry in entity.get(key) or []:
            if (rendered := _v4_prefix(entry)) is not None:
                out.append(rendered)
    for entry in entity.get("ipRanges") or []:
        if not isinstance(entry, dict):
            continue
        start, end = _v4_ip_value(entry.get("startIp")), _v4_ip_value(entry.get("endIp"))
        if start and end:
            out.append(f"{start}-{end}")
        elif start:
            out.append(start)
    return out


def _v4_prefix(entry: Any) -> str | None:
    """One prefixed address, tolerating the bare-string form v4 sometimes returns."""
    ip = _v4_ip_value(entry)
    if not ip:
        return None
    prefix_len = _v4_prefix_length(entry)
    return f"{ip}/{prefix_len}" if prefix_len is not None else ip


def _v4_prefix_length(entry: Any) -> int | None:
    """Prefix length, which sits beside the address or inside the same nested wrapper."""
    if not isinstance(entry, dict):
        return None
    if entry.get("prefixLength") is not None:
        length: int = entry["prefixLength"]
        return length
    for key in ("ipv4", "ipv6"):
        if isinstance(entry.get(key), dict):
            return _v4_prefix_length(entry[key])
    return None


def _v4_ip_value(entry: Any) -> str | None:
    """An address that may be a bare string, {'value': ...}, or {'ipv4': {'value': ...}}."""
    if isinstance(entry, str):
        return entry or None
    if not isinstance(entry, dict):
        return None
    for key in ("value", "ipv4", "ipv6"):
        if (nested := entry.get(key)) is not None:
            return _v4_ip_value(nested)
    return None


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
