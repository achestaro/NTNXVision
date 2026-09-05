"""The v4 `microseg` Flow path: what v4 serves, what it hides, and where it 404s.

Fixtures are trimmed from live pc.7.5.1.1 responses.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.collectors.flow import FlowSecurityCollector
from ntnxvision_core.models import Environment

# `rules` and the true `isSystemDefined` are absent from the list payload — see POLICY_DETAIL.
POLICIES = [
    {
        "$objectType": "microseg.v4.config.NetworkSecurityPolicy",
        "extId": "728939f6-7143-4d5c-96c6-5f1d16e469f3",
        "securedGroups": ["252887b5-1462-501e-9e1d-aac971085a45"],
        "name": "Quarantine Forensic Policy",
        "type": "QUARANTINE",
        "state": "ENFORCE",
        "scope": "ALL_VLAN",
    },
    {
        "$objectType": "microseg.v4.config.NetworkSecurityPolicy",
        "extId": "1a72b38c-063d-4c31-9745-0e3a821d0fdc",
        # An isolation policy names *both* isolated groups here, in payload order.
        "securedGroups": [
            "ad7ff39b-267c-55db-bc97-5c3462c152a8",
            "201f3f7d-f006-50ce-aef4-61184d8efb78",
        ],
        "name": "test",
        "type": "ISOLATION",
        "state": "SAVE",
        "scope": "GLOBAL",
    },
]

#: The per-policy detail response, which is the only place `rules` appears.
POLICY_DETAIL: dict[str, dict[str, Any]] = {
    "728939f6-7143-4d5c-96c6-5f1d16e469f3": {
        "extId": "728939f6-7143-4d5c-96c6-5f1d16e469f3",
        "isSystemDefined": True,
        "rules": [
            {
                "extId": "4f3bab5b-5a67-4207-97fc-c02633cdfa97",
                "spec": {
                    "$objectType": "microseg.v4.config.ApplicationRuleSpec",
                    "securedGroupCategoryReferences": [
                        "252887b5-1462-501e-9e1d-aac971085a45"
                    ],
                    "srcAllowSpec": "NONE",
                },
                "type": "QUARANTINE",
            },
            {
                "extId": "61850987-7221-4de8-8ba5-43abaa044dba",
                "spec": {
                    "$objectType": "microseg.v4.config.ApplicationRuleSpec",
                    "securedGroupCategoryReferences": [
                        "252887b5-1462-501e-9e1d-aac971085a45"
                    ],
                    "destAllowSpec": "NONE",
                },
                "type": "QUARANTINE",
            },
            {
                "extId": "312124a7-e1c7-4414-89c3-3f6a4e4f217a",
                "spec": {
                    "$objectType": "microseg.v4.config.IntraEntityGroupRuleSpec",
                    "securedGroupCategoryReferences": [
                        "252887b5-1462-501e-9e1d-aac971085a45"
                    ],
                    "securedGroupAction": "DENY",
                },
                "type": "INTRA_GROUP",
            },
        ],
    },
    "1a72b38c-063d-4c31-9745-0e3a821d0fdc": {
        "extId": "1a72b38c-063d-4c31-9745-0e3a821d0fdc",
        "isSystemDefined": False,
        "rules": [
            {
                "extId": "53848539-be5f-4726-b4ca-70dd9bd885ee",
                "spec": {
                    "$objectType": "microseg.v4.config.MultiEnvIsolationRuleSpec",
                    "spec": {
                        "$objectType": "microseg.v4.config.AllToAllIsolationGroup",
                        "isolationGroups": [
                            {"groupCategoryReferences": ["201f3f7d-f006-50ce-aef4-61184d8efb78"]},
                            {"groupCategoryReferences": ["ad7ff39b-267c-55db-bc97-5c3462c152a8"]},
                        ],
                    },
                },
                "type": "MULTI_ENV_ISOLATION",
            }
        ],
    },
}

# prism/v4.0/config/categories — one row per key *value*.
V4_CATEGORIES = [
    {
        "extId": "252887b5-1462-501e-9e1d-aac971085a45",
        "key": "Quarantine",
        "value": "Forensics",
        "type": "SYSTEM",
        "description": "Forensic Quarantine",
    },
    {
        "extId": "201f3f7d-f006-50ce-aef4-61184d8efb78",
        "key": "Environment",
        "value": "Staging",
        "type": "SYSTEM",
    },
    {
        "extId": "ad7ff39b-267c-55db-bc97-5c3462c152a8",
        "key": "Environment",
        "value": "Testing",
        "type": "SYSTEM",
        "description": "Testing Environment",
    },
]

# v3 categories/list — one row per key, no values at all.
V3_CATEGORIES = [
    {"name": "Environment", "description": "Environment type.", "system_defined": True},
    {"name": "Quarantine", "description": "Quarantine.", "system_defined": True},
    # Nobody has given this key a value, so v4 cannot see it.
    {"name": "OSType", "description": "OS Type of the VM.", "system_defined": True},
]

V4_SERVICE_GROUPS = [
    {
        "$objectType": "microseg.v4.config.ServiceGroup",
        "extId": "d8d29a2f-1c41-4a05-bbbd-209c5b0050fc",
        "isSystemDefined": True,
        "name": "http-alt",
        "description": "HTTP Alternate",
        "tcpServices": [
            {"startPort": 8008, "endPort": 8008},
            {"startPort": 8080, "endPort": 8090},
        ],
        "udpServices": [{"startPort": 8008, "endPort": 8008}],
    },
]

V4_ADDRESS_GROUPS = [
    {
        "$objectType": "microseg.v4.config.AddressGroup",
        "extId": "b1d0e4c1-0000-4000-8000-000000000001",
        "name": "datacentre-edge",
        # No isSystemDefined: the field is not in the v4 address-group schema.
        "ipv4Addresses": [{"value": "198.51.100.0", "prefixLength": 24}],
        "ipRanges": [{"startIp": "10.20.0.10", "endIp": "10.20.0.20"}],
    },
]


class _Conn:
    """Records the v4 paths asked for so tests can assert what was and was not called."""

    def __init__(
        self,
        v4: bool = True,
        v4_categories: list[dict[str, Any]] | None = None,
        v3_categories: list[dict[str, Any]] | None = None,
        v3_address_groups: list[dict[str, Any]] | None = None,
        detail_fails: bool = False,
    ) -> None:
        self._v4 = v4
        self.v4_categories = V4_CATEGORIES if v4_categories is None else v4_categories
        self.v3_categories = V3_CATEGORIES if v3_categories is None else v3_categories
        self.v3_address_groups = v3_address_groups or []
        self.detail_fails = detail_fails
        self.paths: list[str] = []

    def v4_ready(self, area: str) -> bool:
        return self._v4

    def v4_list(self, path: str) -> list[dict[str, Any]]:
        self.paths.append(path)
        if path == "microseg/v4.0/config/policies":
            return [dict(p) for p in POLICIES]
        if path == "prism/v4.0/config/categories":
            if not self.v4_categories:
                raise NutanixApiError("not found", status_code=404)
            return [dict(c) for c in self.v4_categories]
        if path == "microseg/v4.0/config/service-groups":
            return [dict(s) for s in V4_SERVICE_GROUPS]
        if path == "microseg/v4.0/config/address-groups":
            return [dict(a) for a in V4_ADDRESS_GROUPS]
        raise NutanixApiError(f"unexpected {path}", status_code=404)

    def v4_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.paths.append(path)
        if self.detail_fails:
            raise NutanixApiError("boom", status_code=500)
        return {"data": POLICY_DETAIL[path.rsplit("/", 1)[-1]]}

    def v3_list(self, kind_path: str, page_size: int = 250) -> list[dict[str, Any]]:
        if kind_path == "categories":
            return [dict(c) for c in self.v3_categories]
        if kind_path == "address_groups":
            return [dict(a) for a in self.v3_address_groups]
        if kind_path == "network_security_rules":
            # The live PC returns nothing here once microseg owns policies.
            return []
        if kind_path == "service_groups":
            return []
        raise NutanixApiError(f"unexpected {kind_path}", status_code=404)


def _collect(conn: _Conn) -> Environment:
    env = Environment()
    FlowSecurityCollector(conn).collect(env)  # type: ignore[arg-type]
    return env


def _policy(env: Environment, name: str) -> Any:
    return next(p for p in env.security_policies if p.name == name)


def test_v4_policies_exist_where_v3_reports_none() -> None:
    """The whole point: v3's network_security_rules is empty on a microseg PC."""
    env = _collect(_Conn())
    assert [p.name for p in env.security_policies] == ["Quarantine Forensic Policy", "test"]
    assert env.flow.security_policy_count == 2
    assert all(p.api_source == "v4" for p in env.security_policies)
    assert env.collection_notes["flow_security"] == "ok"


def test_secured_groups_resolve_to_readable_category_pairs() -> None:
    """v4 names secured groups by UUID; the column has always shown "Key:value"."""
    assert _policy(_collect(_Conn()), "Quarantine Forensic Policy").secured_group == (
        "Quarantine:Forensics"
    )


def test_secured_group_values_are_sorted_not_payload_ordered() -> None:
    """The isolation fixture lists Testing before Staging; the column must not wobble."""
    assert _policy(_collect(_Conn()), "test").secured_group == "Environment:Staging,Testing"


def test_unresolvable_category_reference_is_kept_as_its_uuid() -> None:
    """A reference the category list cannot name is still a reference worth showing."""
    env = _collect(_Conn(v4_categories=[dict(V4_CATEGORIES[1])]))
    assert _policy(env, "Quarantine Forensic Policy").secured_group == (
        "252887b5-1462-501e-9e1d-aac971085a45"
    )


def test_rule_counts_come_from_the_detail_endpoint_by_direction() -> None:
    """The trap: the list payload has no `rules`, and $select=rules is rejected.

    Direction is not a field — it is which half of the rule spec is populated.
    """
    env = _collect(_Conn())
    quarantine = _policy(env, "Quarantine Forensic Policy")
    assert (quarantine.inbound_rule_count, quarantine.outbound_rule_count) == (1, 1)


def test_isolation_rules_belong_to_no_direction() -> None:
    """Isolation and intra-group rules keep the counts at 0, as the v3 path reported."""
    isolation = _policy(_collect(_Conn()), "test")
    assert (isolation.inbound_rule_count, isolation.outbound_rule_count) == (0, 0)


def test_unreadable_policy_detail_leaves_counts_unknown_not_zero() -> None:
    env = _collect(_Conn(detail_fails=True))
    assert _policy(env, "test").inbound_rule_count is None
    assert _policy(env, "test").outbound_rule_count is None


def test_app_rule_action_only_applies_to_application_policies() -> None:
    """v3 left it null for isolation/quarantine; v4 has no per-rule action to read."""
    assert all(p.app_rule_action is None for p in _collect(_Conn()).security_policies)


def test_categories_gain_the_value_column_v3_could_never_fill() -> None:
    env = _collect(_Conn())
    pairs = [(c.name, c.value) for c in env.categories]
    assert ("Environment", "Staging") in pairs
    assert ("Quarantine", "Forensics") in pairs


def test_valueless_v3_keys_survive_the_move() -> None:
    """v4 has no row for a key nobody has assigned a value to — v3 is the only source."""
    env = _collect(_Conn())
    ostype = next(c for c in env.categories if c.name == "OSType")
    assert ostype.value is None
    assert ostype.description == "OS Type of the VM."
    assert ostype.api_source == "v3"
    # Every key v3 knows about is still represented.
    assert {c["name"] for c in V3_CATEGORIES} <= {c.name for c in env.categories}


def test_categories_are_ordered_explicitly_not_by_payload() -> None:
    env = _collect(_Conn())
    names = [(c.name.lower(), (c.value or "").lower()) for c in env.categories]
    assert names == sorted(names)


def test_categories_fall_back_to_v3_when_prism_404s() -> None:
    """`microseg/v4.0/config/categories` does not exist; `prism` may not be routed either."""
    env = _collect(_Conn(v4_categories=[]))
    assert [c.name for c in env.categories] == [c["name"] for c in V3_CATEGORIES]
    assert all(c.api_source == "v3" for c in env.categories)
    # Policies still resolve, just without readable labels.
    assert _policy(env, "test").secured_group is not None


def test_service_group_ports_render_exactly_as_v3_did() -> None:
    """541/541 service groups matched v3 byte-for-byte on the lab PC; keep it that way."""
    sg = _collect(_Conn()).service_groups[0]
    assert sg.uuid == "d8d29a2f-1c41-4a05-bbbd-209c5b0050fc"
    assert sg.tcp_ports == "8008, 8080-8090"
    assert sg.udp_ports == "8008"
    assert sg.icmp is False
    assert sg.is_system is True
    assert sg.api_source == "v4"


def test_address_group_addresses_cover_prefixes_and_ranges() -> None:
    """v4 splits v3's single ip_address_block_list into prefixes plus start/end ranges."""
    ag = _collect(_Conn()).address_groups[0]
    assert ag.ip_ranges == ["198.51.100.0/24", "10.20.0.10-10.20.0.20"]


def test_address_group_system_flag_is_backfilled_from_v3() -> None:
    """v4's address-group schema has no isSystemDefined at all, unlike service groups."""
    v3 = [
        {
            "uuid": "b1d0e4c1-0000-4000-8000-000000000001",
            "address_group": {"name": "datacentre-edge", "is_system_defined": True},
        }
    ]
    assert _collect(_Conn(v3_address_groups=v3)).address_groups[0].is_system is True
    # And blank rather than wrong once v3 is gone.
    assert _collect(_Conn()).address_groups[0].is_system is None


def test_v3_path_is_used_untouched_when_microseg_is_not_served() -> None:
    conn = _Conn(v4=False)
    env = _collect(conn)
    assert conn.paths == []
    assert env.security_policies == []
    assert [c.name for c in env.categories] == [c["name"] for c in V3_CATEGORIES]
    assert env.collection_notes["flow_security"] == "ok"
